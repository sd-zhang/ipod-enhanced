"""Independent r19 byte/resource/container preservation; no patcher imports."""
import argparse
import hashlib
import io
import json
import struct
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile
from verify_package import Package, MEMBER, archive, payload, word
from verify_resources import directory, records


def commands(blob):
    head = 8 + 4 * struct.unpack_from('<H', blob, 6)[0]
    count, explicit = struct.unpack_from('<HH', blob, head)
    table = (head + 4 + 2 * explicit + 3) & ~3
    offsets = struct.unpack_from(f'<{count + 1}I', blob, table)
    body = table + 4 * (count + 1)
    result = {}
    for offset in offsets[:-1]:
        at = body + offset
        atom, argc, local, stack, size = struct.unpack_from('<5H', blob, at)
        result[atom] = (at + 10, size)
    return result


class OTGPackage(unittest.TestCase):
    test_updater_padding_headers_and_other_archive_members_preserved = (
        Package.test_updater_padding_headers_and_other_archive_members_preserved)

    def test_only_selector_script_bodies_directory_and_sizes_change_old_bytes(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         'd8e07e277d6a739d134ffeceb7579311a9ff7c7f88d41488d8d9601feb43f368')
        groups = directory(self.before)
        allowed = [(at, 4) for at in (0xe8, 0x2e8, 0x4a8, 0x1e0068)]
        for tag in ('Menu', 'Str '):
            allowed.extend((groups[tag][0] + off, 4) for off in (4, 12))
        script = groups['TCmd'][1][0x2380]
        root = 0x1a507c
        table = root + word(self.before, groups['TCmd'][0] + 12)
        base = root + word(self.before, root + 4)
        offset, size = next((o, s) for k, o, s in struct.iter_unpack('<III',
            self.before[table:table + len(groups['TCmd'][1]) * 12]) if k == 0x2380)
        self.assertEqual(self.before[base + offset:base + offset + size], script)
        indexed = commands(script)
        for atom in (2537, 2541, 2542, 2543, 2544):
            at, length = indexed[atom]
            allowed.append((base + offset + at, length))
        restored = bytearray(self.after[:len(self.before) - 4])
        for at, length in allowed:
            restored[at:at + length] = self.before[at:at + length]
        self.assertEqual(restored, self.before[:-4], 'undocumented modification to existing firmware')
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        self.assertEqual(len(self.after) % 4, 0)
        self.assertGreater(len(self.after), len(self.before))
        self.assertLess(len(self.after), 0x390000)
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)

    def test_resource_payloads_and_script_record_headers_are_preserved(self):
        old, new = directory(self.before), directory(self.after)
        self.assertEqual(old.keys(), new.keys())
        changed = {('TCmd', 0x2380), ('Menu', 0x6102), ('Menu', 0x6109)}
        for tag in old:
            extra = {0x6130, 0x6131, 0x6132} if tag == 'Str ' else set()
            self.assertEqual(set(new[tag][1]), set(old[tag][1]) | extra)
            for key, data in old[tag][1].items():
                if (tag, key) not in changed:
                    self.assertEqual(new[tag][1][key], data, (tag, hex(key)))
        a, b = old['TCmd'][1][0x2380], bytearray(new['TCmd'][1][0x2380])
        self.assertEqual(commands(a), commands(b))
        for atom in (2537, 2541, 2542, 2543, 2544):
            at, length = commands(a)[atom]
            self.assertEqual(length % 2, 0)
            self.assertEqual((at + length) % 2, 0)
            b[at:at + length] = a[at:at + length]
        self.assertEqual(a, b)
        for key in (0x6102, 0x6109):
            self.assertEqual(records(new['Menu'][1][key])[:5], records(old['Menu'][1][0x6102]))

    def test_old_code_sidecars_are_identical_and_new_selector_matches_packaged_elf(self):
        for name in ('letters', 'actions', 'volume', 'hotpaths'):
            for suffix in ('.elf', '-symbols.json'):
                self.assertEqual((self.artifacts / (name + suffix)).read_bytes(),
                                 (self.source_artifacts / (name + suffix)).read_bytes())
        for name in ('letters', 'actions', 'volume', 'hotpaths', 'otg'):
            elf = ELFFile(io.BytesIO((self.artifacts / (name + '.elf')).read_bytes()))
            section = elf.get_section_by_name('.text')
            at, size = section['sh_addr'], section['sh_size']
            self.assertEqual(section.data(), self.after[at:at + size])
            if name != 'otg':
                self.assertEqual(section.data(), self.before[at:at + size])
            else:
                self.assertEqual(at, len(self.before) - 4)
                self.assertEqual(elf['e_entry'], word(self.after, 0x1e0068))
                self.assertFalse(any(s['sh_size'] for s in elf.iter_sections()
                                     if s.name in ('.data', '.bss')))
                symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
                           if s.name.startswith('otg_')}
                self.assertEqual(json.loads((self.artifacts / 'otg-symbols.json').read_text()), symbols)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--source', type=Path, default=Path(
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r18.ipsw'))
    parser.add_argument('--stock', type=Path, default=Path('iPod_4.3.1.1.ipsw'))
    args = parser.parse_args()
    OTGPackage.source, OTGPackage.candidate, OTGPackage.stock = map(archive, (args.source, args.candidate, args.stock))
    OTGPackage.before, OTGPackage.after = (payload(a[MEMBER], 0x4200)
                                         for a in (OTGPackage.source, OTGPackage.candidate))
    OTGPackage.artifacts, OTGPackage.source_artifacts = (p.with_suffix('.artifacts')
                                                       for p in (args.candidate, args.source))
    unittest.main(argv=[__file__], defaultTest='OTGPackage', verbosity=2)
