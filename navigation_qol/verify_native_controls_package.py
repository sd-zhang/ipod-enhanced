"""Independently verify the resource-only r17 changes and exact code retention."""
import argparse
import hashlib
import io
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile
from verify_package import Package, MEMBER, archive, payload, word
from verify_resources import directory, records


class NativeControlsPackage(unittest.TestCase):
    test_updater_padding_headers_and_other_archive_members_preserved = (
        Package.test_updater_padding_headers_and_other_archive_members_preserved)

    def test_only_two_resource_table_pointers_and_image_sizes_change_prior_bytes(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         'a8153864583ca312cfceb43c57cb4b0b869d38e55f99f6634d0ff14fe22dc0ce')
        groups = directory(self.before)
        restored = bytearray(self.after[:len(self.before) - 4])
        for at in (0xe8, 0x2e8, 0x4a8, groups['View'][0] + 12, groups['Menu'][0] + 12):
            restored[at:at + 4] = self.before[at:at + 4]
        self.assertEqual(restored, self.before[:-4])
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        self.assertEqual(len(self.after) % 4, 0)
        self.assertGreater(len(self.after), len(self.before))
        self.assertLess(len(self.after), 0x390000)
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)

    def test_all_scripts_and_resources_except_three_ui_records_are_identical(self):
        old, new = directory(self.before), directory(self.after)
        self.assertEqual(old.keys(), new.keys())
        changes = {('View', 0x6120), ('Menu', 0x278d), ('Menu', 0x6102)}
        for tag in old:
            self.assertEqual(old[tag][1].keys(), new[tag][1].keys())
            for key, data in old[tag][1].items():
                if (tag, key) not in changes:
                    self.assertEqual(new[tag][1][key], data, (tag, hex(key)))
        for key, row in ((0x278d, 8), (0x6102, 3)):
            old_rows, new_rows = records(old['Menu'][1][key]), records(new['Menu'][1][key])
            self.assertEqual(len(old_rows), len(new_rows))
            self.assertEqual(old_rows[:row] + old_rows[row + 1:], new_rows[:row] + new_rows[row + 1:])

    def test_repeat_is_the_stock_toggle_and_volume_is_the_stock_contrast_entry(self):
        groups = directory(self.after)
        settings = records(groups['Menu'][1][0x278d])
        volume, contrast = bytearray(settings[8]), bytearray(settings[9])
        self.assertEqual((word(volume, 4), word(volume, 12), word(volume, 20), word(volume, 32)),
                         (0x22, 2547 * 4 + 2, 0x6120, 0x6120))
        for at in (4, 12, 20, 32):
            volume[at:at + 4] = contrast[at:at + 4]
        self.assertEqual(volume, contrast)
        actions = records(groups['Menu'][1][0x6102])
        self.assertEqual([word(r, 12) for r in actions],
                         [n * 4 + 2 for n in (2538, 2539, 2155, 2155, 2160)])
        repeat = bytearray(actions[3])
        self.assertEqual(word(repeat, 4), 0x22)
        self.assertEqual(word(repeat, 20), 0xff94)
        repeat[4:8], repeat[20:24] = settings[3][4:8], settings[3][20:24]
        self.assertEqual(repeat, settings[3])

    def test_native_icons_and_private_slider_draft_contract_are_preserved(self):
        old, new = directory(self.before), directory(self.after)
        small, slider, large = records(new['View'][1][0x6120])
        stock_small, _, stock_large = records(old['View'][1][0x2637])
        for icon, source in ((small, stock_small), (large, stock_large)):
            self.assertEqual(icon[:0x1c] + icon[0x28:], source[:0x1c] + source[0x28:])
        previous = next(r for r in records(old['View'][1][0x6120]) if word(r, 12) == 0x6121)
        self.assertEqual(slider[:0x1c], previous[:0x1c])  # class, ID, ownership, focus flags
        self.assertEqual(slider[0x4c:], previous[0x4c:])  # owned draft, min/max, no live provider
        self.assertEqual([word(r, 4) for r in (small, slider, large)], [0x1280, 0x3080, 0x1280])

    def test_all_four_code_payloads_and_review_sidecars_are_identical(self):
        for name in ('letters', 'actions', 'volume', 'hotpaths'):
            for suffix in ('.elf', '-symbols.json'):
                self.assertEqual((self.artifacts / (name + suffix)).read_bytes(),
                                 (self.source_artifacts / (name + suffix)).read_bytes())
            raw = (self.artifacts / (name + '.elf')).read_bytes()
            section = ELFFile(io.BytesIO(raw)).get_section_by_name('.text')
            at, size = section['sh_addr'], section['sh_size']
            self.assertEqual(section.data(), self.before[at:at + size])
            self.assertEqual(section.data(), self.after[at:at + size])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--source', type=Path, default=Path(
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r16.ipsw'))
    parser.add_argument('--stock', type=Path, default=Path('iPod_4.3.1.1.ipsw'))
    args = parser.parse_args()
    NativeControlsPackage.source, NativeControlsPackage.candidate, NativeControlsPackage.stock = map(
        archive, (args.source, args.candidate, args.stock))
    NativeControlsPackage.before = payload(NativeControlsPackage.source[MEMBER], 0x4200)
    NativeControlsPackage.after = payload(NativeControlsPackage.candidate[MEMBER], 0x4200)
    NativeControlsPackage.artifacts = args.candidate.with_suffix('.artifacts')
    NativeControlsPackage.source_artifacts = args.source.with_suffix('.artifacts')
    unittest.main(argv=[__file__], defaultTest='NativeControlsPackage', verbosity=2)
