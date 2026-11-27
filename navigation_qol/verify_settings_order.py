"""Verify the 5.5G-style Volume Limit position and otherwise identical r17 image."""
import argparse
import hashlib
import unittest
from pathlib import Path

from verify_native_controls_package import NativeControlsPackage
from verify_package import MEMBER, archive, payload
from verify_resources import directory, records, word


class SettingsOrder(unittest.TestCase):
    test_updater_padding_headers_and_other_archive_members_preserved = (
        NativeControlsPackage.test_updater_padding_headers_and_other_archive_members_preserved)
    test_all_four_code_payloads_and_review_sidecars_are_identical = (
        NativeControlsPackage.test_all_four_code_payloads_and_review_sidecars_are_identical)

    def test_volume_limit_follows_repeat_without_changing_any_row_content(self):
        old = records(directory(self.before)['Menu'][1][0x278d])
        new = records(directory(self.after)['Menu'][1][0x278d])
        self.assertEqual([word(r, 20) & 0xffff for r in new[:6]],
                         [0xff97, 0xff96, 0xff95, 0xff94, 0x6120, 0xff93])
        self.assertEqual(new, old[:4] + [old[8]] + old[4:8] + old[9:])

    def test_only_active_settings_resource_bytes_change(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         '29fe823f71dfe794346eef436121c9fe841932fd940b66ca87d66e6890a9f3ed')
        old, new = directory(self.before), directory(self.after)
        self.assertEqual(len(self.before), len(self.after))
        self.assertEqual(old.keys(), new.keys())
        for tag in old:
            self.assertEqual(old[tag][0], new[tag][0])
            self.assertEqual(old[tag][1].keys(), new[tag][1].keys())
            for key, data in old[tag][1].items():
                if (tag, key) != ('Menu', 0x278d):
                    self.assertEqual(new[tag][1][key], data)
        blob = old['Menu'][1][0x278d]
        at = self.before.find(blob)
        self.assertNotEqual(at, -1)
        self.assertEqual(self.before.find(blob, at + 1), -1)
        restored = bytearray(self.after)
        restored[at:at + len(blob)] = blob
        self.assertEqual(restored, self.before)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--source', type=Path, default=Path(
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r17.ipsw'))
    parser.add_argument('--stock', type=Path, default=Path('iPod_4.3.1.1.ipsw'))
    args = parser.parse_args()
    SettingsOrder.source, SettingsOrder.candidate, SettingsOrder.stock = map(
        archive, (args.source, args.candidate, args.stock))
    SettingsOrder.before = payload(SettingsOrder.source[MEMBER], 0x4200)
    SettingsOrder.after = payload(SettingsOrder.candidate[MEMBER], 0x4200)
    SettingsOrder.artifacts = args.candidate.with_suffix('.artifacts')
    SettingsOrder.source_artifacts = args.source.with_suffix('.artifacts')
    unittest.main(argv=[__file__], defaultTest='SettingsOrder', verbosity=2)
