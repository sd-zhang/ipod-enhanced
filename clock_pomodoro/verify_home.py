"""Native Main Menu preference and OEM ordering checks. Run Unicorn unsandboxed."""
import argparse
import unittest
from pathlib import Path

from verify_runtime import BASELINE
from native_fixture import NativeMachine, load_image
from verify_resources import directory, records, word

KEY, BIT = 0x25f4, 0x4000
MASKS = {0x2607: 0x100000, 0x2608: 0x200000, 0x2609: 0x800000, KEY: BIT}
SETTINGS, BRIDGE, PREFS, OUT, VALUE = (0x10001000, 0x10002000, 0x10004000,
                                      0x10008000, 0x10008100)


def preferences(image, mask=0x81201):
    m = NativeMachine(image)
    m.write(SETTINGS, 0x23a48)
    m.write(SETTINGS + 0x58, BRIDGE)
    m.write(BRIDGE, PREFS)
    m.write(PREFS + 0xb34, mask)
    return m


class HomePreference(unittest.TestCase):
    def test_clock_shortcuts_follow_clock_in_both_native_menus(self):
        old, new = directory(load_image(BASELINE)), directory(self.image)
        for menu, field in ((0x2512, 32), (0x278f, 24)):
            before, after = records(old['Menu'][1][menu]), records(new['Menu'][1][menu])
            clock = next(i for i, r in enumerate(before) if word(r, field) & 0xffff == 0x25f8)
            self.assertEqual(len(after), len(before) + 4)
            added = after[clock + 1:clock + 5]
            self.assertEqual([word(row, field) for row in added],
                             [0x2607, 0x2608, 0x2609, KEY])
            self.assertEqual(after[:clock + 1] + after[clock + 5:], before)
            self.assertEqual([word(row, 12) >> 2 for row in added],
                             [2444, 2160, 2142, 2553] if menu == 0x2512 else [2155] * 4)
            self.assertEqual([word(row, 4) for row in added], [0x22] * 4)
        self.assertEqual(new['Str '][1][KEY], b'Pomodoro\0')

    def test_new_masks_use_only_previously_unmapped_bits(self):
        m, old = preferences(self.image), preferences(load_image(BASELINE))
        for key, bit in MASKS.items():
            self.assertEqual(m.call(0x6b1b8, key), bit)
            self.assertEqual(old.call(0x6b1b8, key), 0)
        used = 0
        for key in range(0x25f0, 0x260e):
            if key in MASKS: continue
            mask = old.call(0x6b1b8, key)
            used |= mask
            self.assertEqual(m.call(0x6b1b8, key), mask)
        self.assertEqual(used & sum(MASKS.values()), 0)

    def test_stock_toggle_getter_and_reset_preserve_all_other_options(self):
        for initial in (0x81201, 0x004fbfff, 0xffffffff):
            with self.subTest(initial=hex(initial)):
                m = preferences(self.image, initial)
                for unused in range(2):
                    before = m.read(PREFS + 0xb34)
                    m.write(VALUE, 0x283f)  # native toggle operation
                    self.assertEqual(m.call(0xcc164, SETTINGS, 0x70724944, KEY, VALUE), 1)
                    self.assertEqual(m.read(PREFS + 0xb34), before ^ BIT)
                    self.assertEqual(m.read(PREFS + 0xb44), 1)
                    self.assertEqual(m.call(0x732d0, SETTINGS, 0x70724944, KEY, OUT), 1)
                    self.assertEqual(m.read(m.read(OUT)) & 0xffff,
                                     0x27a1 if (before ^ BIT) & BIT else 0x27a2)
                self.assertEqual(m.read(PREFS + 0xb34), initial)
                m.call(0xbeb08, SETTINGS)
                alert_mask = 0x01000000 if self.with_alerts else 0
                self.assertEqual(m.read(PREFS + 0xb34), 0x81201 | (initial & alert_mask))
                self.assertEqual(m.call(0x7032c, SETTINGS, KEY), 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    candidate = parser.parse_args().candidate
    HomePreference.image = load_image(candidate)
    HomePreference.with_alerts = (candidate.with_suffix('.artifacts') / 'alarm_light.elf').exists()
    unittest.main(argv=[__file__], verbosity=2)
