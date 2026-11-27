"""Independent byte/resource/container checks for the Sleep Timer shortcut."""
import argparse
import hashlib
import unittest
from pathlib import Path

from verify_package import Package, MEMBER, archive, payload, word
from verify_resources import directory


class SleepPackage(unittest.TestCase):
    test_updater_padding_headers_and_other_archive_members_preserved = (
        Package.test_updater_padding_headers_and_other_archive_members_preserved)

    def test_only_menu_table_pointer_and_image_sizes_change_existing_bytes(self):
        self.assertEqual(hashlib.sha256(self.before).hexdigest(),
                         '857a659fac2fba7a4608aea21bad31ccdc9c04ca737d9291778903835eb636d1')
        header = directory(self.before)['Menu'][0]
        restored = bytearray(self.after[:len(self.before) - 4])
        for at in (0xe8, 0x2e8, 0x4a8, header + 12):
            restored[at:at + 4] = self.before[at:at + 4]
        self.assertEqual(restored, self.before[:-4])
        self.assertLess(len(self.after), 0x390000)
        self.assertGreater(len(self.after), len(self.before))
        self.assertEqual(len(self.after) % 4, 0)
        self.assertEqual(self.after[-4:], bytes.fromhex('55aa55aa'))
        self.assertEqual(word(self.after, 0xe8), len(self.after))
        for at in (0x2e8, 0x4a8):
            self.assertEqual(word(self.after, at), 0x04000000 + len(self.after) - 4)

    def test_every_script_and_resource_except_actions_menu_is_identical(self):
        old, new = directory(self.before), directory(self.after)
        self.assertEqual(old.keys(), new.keys())
        for tag in old:
            self.assertEqual(old[tag][1].keys(), new[tag][1].keys())
            for key, data in old[tag][1].items():
                if (tag, key) != ('Menu', 0x6102):
                    self.assertEqual(new[tag][1][key], data, (tag, hex(key)))
        previous, menu = old['Menu'][1][0x6102], new['Menu'][1][0x6102]
        self.assertEqual(word(previous, 0), 4)
        self.assertEqual(word(menu, 0), 5)
        self.assertEqual(len(menu), 204)
        self.assertEqual(menu[4:164], previous[4:])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--source', type=Path, default=Path(
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r14.ipsw'))
    parser.add_argument('--stock', type=Path, default=Path('iPod_4.3.1.1.ipsw'))
    args = parser.parse_args()
    SleepPackage.source, SleepPackage.candidate, SleepPackage.stock = map(archive, (args.source, args.candidate, args.stock))
    SleepPackage.before = payload(SleepPackage.source[MEMBER], 0x4200)
    SleepPackage.after = payload(SleepPackage.candidate[MEMBER], 0x4200)
    unittest.main(argv=[__file__], defaultTest='SleepPackage', verbosity=2)
