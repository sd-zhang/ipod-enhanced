"""Independently check the r16 image, hook boundaries and preserved resources."""
import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile
from verify_package import Package, MEMBER, archive, payload, word
from verify_resources import directory


class HotpathsPackage(unittest.TestCase):
    test_updater_padding_headers_and_other_archive_members_preserved = (
        Package.test_updater_padding_headers_and_other_archive_members_preserved)

    def test_only_two_entry_hooks_and_image_sizes_change_existing_bytes(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         '8801763928f4f931b5a979029f571b39ee286cce202cf5ac1ce9dcea02fdf021')
        restored = bytearray(self.after[:len(self.before) - 4])
        for at in (0xe8, 0x2e8, 0x4a8, 0x86700, 0x14fae0):
            restored[at:at + 4] = self.before[at:at + 4]
        self.assertEqual(restored, self.before[:-4])
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        self.assertEqual(len(self.after) % 4, 0)
        self.assertGreater(len(self.after), len(self.before))
        self.assertLessEqual(len(self.after) - len(self.before), 4096)
        self.assertLess(len(self.after), 0x390000)
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)

    def test_every_resource_and_script_is_byte_identical(self):
        self.assertEqual(directory(self.after), directory(self.before))

    def test_new_hooks_resolve_to_exact_appended_elf(self):
        elf = ELFFile(io.BytesIO((self.artifacts / 'hotpaths.elf').read_bytes()))
        section = elf.get_section_by_name('.text')
        self.assertEqual(section['sh_addr'], len(self.before) - 4)
        self.assertEqual(section.data(), self.after[len(self.before) - 4:-4])
        symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
                   if s['st_info']['type'] == 'STT_FUNC' and s['st_shndx'] != 'SHN_UNDEF'}
        self.assertEqual(json.loads((self.artifacts / 'hotpaths-symbols.json').read_text()), symbols)
        for name in ('letters_draw', 'letters_import_sort', 'letters_accel_create',
                     'letters_group', 'volume_owner_register', 'volume_load', 'volume_control'):
            self.assertNotIn(name, symbols, 'unrelated code should be discarded')
        for at, name in ((0x86700, 'letters_wheel_hook'), (0x14fae0, 'volume_codec')):
            instruction = word(self.after, at)
            self.assertEqual(instruction >> 24, 0xea)
            delta = (instruction & 0xffffff) << 2
            if delta & 0x2000000:
                delta -= 0x4000000
            target = at + 8 + delta
            self.assertEqual(target, symbols[name])
            self.assertEqual(target % 4, 0)
            self.assertTrue(len(self.before) - 4 <= target < len(self.after) - 4)
        for section in elf.iter_sections():
            if section.name in ('.data', '.bss'):
                self.assertEqual(section['sh_size'], 0)

    def test_original_code_and_elf_sidecars_remain_identical(self):
        for name in ('letters', 'actions', 'volume'):
            raw = (self.artifacts / (name + '.elf')).read_bytes()
            self.assertEqual(raw, (self.source_artifacts / (name + '.elf')).read_bytes())
            elf = ELFFile(io.BytesIO(raw))
            text = elf.get_section_by_name('.text')
            at, size = text['sh_addr'], text['sh_size']
            self.assertEqual(self.after[at:at + size], text.data())
            self.assertEqual(self.before[at:at + size], text.data())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--source', type=Path, default=Path(
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r15.ipsw'))
    parser.add_argument('--stock', type=Path, default=Path('iPod_4.3.1.1.ipsw'))
    args = parser.parse_args()
    HotpathsPackage.source, HotpathsPackage.candidate, HotpathsPackage.stock = map(archive, (args.source, args.candidate, args.stock))
    HotpathsPackage.before = payload(HotpathsPackage.source[MEMBER], 0x4200)
    HotpathsPackage.after = payload(HotpathsPackage.candidate[MEMBER], 0x4200)
    HotpathsPackage.artifacts = args.candidate.with_suffix('.artifacts')
    HotpathsPackage.source_artifacts = args.source.with_suffix('.artifacts')
    unittest.main(argv=[__file__], defaultTest='HotpathsPackage', verbosity=2)
