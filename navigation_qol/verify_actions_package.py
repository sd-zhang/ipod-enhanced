"""Independent final-archive checks for the Now Playing actions candidate."""
import argparse
import hashlib
import struct
import unittest
from pathlib import Path

from verify_package import Package, MEMBER, archive, payload, word
from native_fixture import NativeMachine
from verify_resources import directory, records


def script_records(blob):
    head = 8 + 4 * struct.unpack_from('<H', blob, 6)[0]
    count, explicit = struct.unpack_from('<HH', blob, head)
    ids = struct.unpack_from(f'<{explicit}H', blob, head + 4)
    at = (head + 4 + explicit * 2 + 3) & ~3
    offsets = struct.unpack_from(f'<{count + 1}I', blob, at)
    start = at + 4 * (count + 1)
    functions = {}
    for off in offsets[:-1]:
        atom, argc, local, stack, size = struct.unpack_from('<5H', blob, start + off)
        functions[atom] = ((argc, local, stack), blob[start + off + 10:start + off + 10 + size])
    return ids, functions, blob[start + offsets[-1]:]


class ActionsPackage(Package):
    def test_only_documented_navigation_and_size_bytes_change(self):
        self.assertIn(hashlib.sha256(self.before).hexdigest(), {
            'a6502680a3f61ec4beb4e77f0cf13e56dbe5c67737a6047a6c7bf0b5ee5d6b5e',
            '8680368e5c05ac1524af2e7db4e0933901dd183a62be90f00974f2c5ee47919a',
            '9a94877874e76200fc607d2c408181b58b5496ac3136d828f6945cd72f924816',
            'ab3bc5e9543eac184397bba15f250abd69de18fbdba41863d16cfe41887271ac',
            'e07537e2ce7df8a89a2907e0499ea3dc57c340791c3c76c497d96dbcb6ed3e46',
            '4e518282e420ee3cf19108c91c08a82d3d31d7bca61860f65720a1710dfde26a',
            '7ccf5a13fa6c9290aebbc4cc280d9caf8ca786f6291cb9eb3fcacd5b2994e6ca',
            'a717a783c5650ba997ff6eef85ed278c6bc07a69042b9876b8f91025055b1d02',
        })
        allowed = [0xe8, 0x2e8, 0x4a8, 0x1e0060, 0x1e0068, 0xd2ad8]
        for tag in ('View', 'Menu', 'Str ', 'TCmd'):
            header = directory(self.before)[tag][0]
            allowed += [header + 4, header + 12]
        restored = bytearray(self.after[:len(self.before) - 4])
        for at in allowed:
            restored[at:at + 4] = self.before[at:at + 4]
        self.assertEqual(restored, self.before[:-4])
        self.assertLess(len(self.after), 0x390000)
        self.assertGreater(len(self.after), len(self.before))
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)

    def test_script_stays_inside_original_record_and_preserves_every_other_resource(self):
        old, new = directory(self.before), directory(self.after)
        for tag in old:
            for key, data in old[tag][1].items():
                if (tag, key) not in (('TCmd', 0x2380), ('View', 0x250d)):
                    self.assertEqual(new[tag][1][key], data, (tag, hex(key)))
            self.assertEqual(len(new[tag][1]) - len(old[tag][1]),
                             {'View': 6, 'Menu': 2, 'Str ': 7}.get(tag, 0))
        root_before, root_after = (records(d['View'][1][0x250d]) for d in (old, new))
        self.assertEqual(root_after[:-2], root_before)
        self.assertEqual([word(row, 12) for row in root_after[-2:]], [0x6100, 0x6107])
        old_ids, before, old_footer = script_records(old['TCmd'][1][0x2380])
        new_ids, after, new_footer = script_records(new['TCmd'][1][0x2380])
        self.assertEqual(new_footer, old_footer)
        self.assertEqual(new_ids, old_ids + tuple(range(2536, 2547)))
        self.assertEqual(after.keys() - before.keys(), set(range(2536, 2547)))
        for atom, (header, code) in before.items():
            actual_header, actual = after[atom]
            if atom in (2015, 2019):
                padding = b'\0' if atom == 2019 else b''
                self.assertTrue(actual.endswith(code + padding))
                self.assertEqual(actual_header[:2], header[:2])
            else:
                self.assertEqual((actual_header, actual), (header, code), atom)

        # Native 68f00 loads the u16 extension count directly after code.
        self.assertTrue(all(len(code) % 2 == 0 for _, code in after.values()))

    def test_compilation_hook_has_correct_native_branches_and_register_contract(self):
        # Every earlier executable byte, including gapless and letter hooks, is
        # covered by the complete-image comparison above. The two new bindings
        # must target appended ARM code, never an old instruction or resource.
        for at in (0x1e0060, 0x1e0068):
            target = word(self.after, at)
            self.assertTrue(len(self.before) - 4 <= target < len(self.before) + 2048)
            self.assertEqual(target % 4, 0)

    def test_marquee_call_targets_appended_helper_and_preserves_other_profiles(self):
        self.assertEqual(word(self.before, 0xd2ad8), 0xebfd07cb)
        instruction = word(self.after, 0xd2ad8)
        self.assertEqual(instruction >> 24, 0xeb)
        displacement = (instruction & 0xffffff) << 2
        if displacement & 0x2000000:
            displacement -= 0x4000000
        target = 0xd2ae0 + displacement
        self.assertTrue(len(self.before) - 4 <= target < len(self.before) + 2048)
        m = NativeMachine(self.after)
        for argument, expected in ((3750, 840), (15000, 1500), (3000, 300), (0, 0)):
            self.assertEqual(m.call(target, argument), expected)

    def test_shuffle_row_preserves_native_command_value_provider_and_label(self):
        old = directory(self.before)['Menu'][1][0x278d][84:124]
        menu = directory(self.after)['Menu'][1][0x6102]
        self.assertEqual(word(menu, 0), 4)
        self.assertEqual(len(menu), 164)
        row = bytearray(menu[84:124])
        self.assertEqual(word(row, 4), 0x22)
        self.assertEqual(struct.unpack_from('<H', row, 22)[0], 0)
        row[4:8], row[22:24] = old[4:8], old[22:24]
        self.assertEqual(row, old)
        for i, command in enumerate((2538, 2539)):
            self.assertEqual(word(menu, 4 + 40 * i + 12), command * 4 + 2)
            self.assertEqual(word(menu, 4 + 40 * i + 8), 1892 * 4 + 2)

    def test_native_resource_manager_resolves_all_new_and_relocated_resources(self):
        m = NativeMachine(self.after)
        manager, out = 0x10001000, 0x10002000
        root = 0x1a507c
        self.assertEqual(m.call(0xee004, manager, root, word(self.after, root - 4)), manager)
        for tag in ('View', 'Menu', 'Str ', 'TCmd'):
            for key, data in directory(self.after)[tag][1].items():
                if not (0x6100 <= key <= 0x610b or (tag, key) in (('View', 0x250d), ('TCmd', 0x2380))):
                    continue
                at = m.call(0x7ae24, manager, int.from_bytes(tag.encode(), 'big'), key, out)
                self.assertNotEqual(at, 0, (tag, hex(key)))
                self.assertEqual(m.read(out), len(data))
                self.assertEqual(bytes(m.cpu.mem_read(at, len(data))), data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--source', type=Path, default=Path('build/navigation-qol-letters/iPod_4.3.1.1-gapless-navigation-letters-r10.ipsw'))
    parser.add_argument('--stock', type=Path, default=Path('iPod_4.3.1.1.ipsw'))
    args = parser.parse_args()
    ActionsPackage.source, ActionsPackage.candidate, ActionsPackage.stock = map(archive, (args.source, args.candidate, args.stock))
    ActionsPackage.before = payload(ActionsPackage.source[MEMBER], 0x4200)
    ActionsPackage.after = payload(ActionsPackage.candidate[MEMBER], 0x4200)
    unittest.main(argv=[__file__], defaultTest='ActionsPackage', verbosity=2)
