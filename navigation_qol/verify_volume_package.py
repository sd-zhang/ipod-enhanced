"""Independent byte/resource/container checks for the Volume Limit layer."""
import argparse
import hashlib
import struct
import unittest
from pathlib import Path

from verify_package import Package, MEMBER, archive, payload, word
from verify_resources import directory, records
from verify_actions_package import script_records


class VolumePackage(unittest.TestCase):
    test_updater_padding_headers_and_other_archive_members_preserved = (
        Package.test_updater_padding_headers_and_other_archive_members_preserved)

    def test_native_hooks_target_appended_arm_code(self):
        for at, expected_top in ((0x8db08, 0xeb), (0xf3368, 0xeb), (0x14fae0, 0xea)):
            instruction = word(self.after, at)
            self.assertEqual(instruction >> 24, expected_top)
            delta = (instruction & 0xffffff) << 2
            if delta & 0x2000000:
                delta -= 0x4000000
            target = at + 8 + delta
            self.assertTrue(len(self.before) - 4 <= target < len(self.before) + 4096)
            self.assertEqual(target % 4, 0)
        self.assertEqual(word(self.before, 0x1dff6c), 0x1b6f8)
        target = word(self.after, 0x1dff6c)
        self.assertTrue(len(self.before) - 4 <= target < len(self.before) + 4096)
        self.assertEqual(target % 4, 0)
    def test_only_volume_hooks_and_resource_headers_change_existing_bytes(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         '39406f8a90c65621a04d5aa569aee65fe861b21d97c537423b000c7cf38989b4')
        allowed = [0xe8, 0x2e8, 0x4a8, 0x8db08, 0x14fae0, 0xf3368, 0x1dff6c]
        for tag in ('View', 'Menu', 'Str ', 'TCmd'):
            header = directory(self.before)[tag][0]
            allowed.extend((header + 4, header + 12))
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

    def test_existing_resources_and_all_old_commands_are_preserved(self):
        old, new = directory(self.before), directory(self.after)
        changed = {('View', 0x250d), ('Menu', 0x278d), ('TCmd', 0x2380)}
        for tag in old:
            for key, data in old[tag][1].items():
                if (tag, key) not in changed:
                    self.assertEqual(new[tag][1][key], data, (tag, hex(key)))
            self.assertEqual(new[tag][1].keys() - old[tag][1].keys(),
                             {'View': {0x6120}, 'Str ': {0x6120, 0x6122, 0x6123}}.get(tag, set()))
        old_rows, new_rows = (records(d['View'][1][0x250d]) for d in (old, new))
        self.assertEqual(new_rows[:-1], old_rows)
        self.assertEqual(word(new_rows[-1], 12), 0x6120)
        old_menu, new_menu = old['Menu'][1][0x278d], new['Menu'][1][0x278d]
        self.assertEqual(old_menu[4:], new_menu[4:324] + new_menu[364:])
        old_ids, before, old_footer = script_records(old['TCmd'][1][0x2380])
        new_ids, after, new_footer = script_records(new['TCmd'][1][0x2380])
        self.assertEqual(old_footer, new_footer)
        self.assertEqual(new_ids, old_ids + tuple(range(2547, 2553)))
        self.assertEqual(after.keys() - before.keys(), set(range(2547, 2553)))
        for atom, record in before.items():
            self.assertEqual(after[atom], record, atom)
        self.assertTrue(all(len(code) % 2 == 0 for _, code in after.values()))

    def test_new_resources_use_owned_slider_and_clear_labels(self):
        groups = directory(self.after)
        slider, caption = records(groups['View'][1][0x6120])
        self.assertEqual((word(slider, 4), word(slider, 12), word(slider, 16)),
                         (0x3080, 0x6121, 0xffffffff))
        self.assertEqual(struct.unpack_from('<4I', slider, 80), (256, 0, 256, 0))
        self.assertEqual(groups['Str '][1][0x6120], b'Volume Limit\0')
        self.assertEqual(groups['Str '][1][0x6122], b"Couldn't Set Limit\0")
        self.assertEqual((word(caption, 4), word(caption, 12), word(caption, 16)),
                         (0x1580, 0x6123, 0xffffffff))
        self.assertEqual(word(caption, 0x4c), 0x25546123)
        self.assertEqual(groups['Str '][1][0x6123], b'Center saves; Menu cancels.\0')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--source', type=Path, default=Path(
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r13.ipsw'))
    parser.add_argument('--stock', type=Path, default=Path('iPod_4.3.1.1.ipsw'))
    args = parser.parse_args()
    VolumePackage.source, VolumePackage.candidate, VolumePackage.stock = map(archive, (args.source, args.candidate, args.stock))
    VolumePackage.before = payload(VolumePackage.source[MEMBER], 0x4200)
    VolumePackage.after = payload(VolumePackage.candidate[MEMBER], 0x4200)
    unittest.main(argv=[__file__], defaultTest='VolumePackage', verbosity=2)
