"""Exercise Backlight Alerts storage through the packaged native preferences.

Bit24 of the saved Main Menu mask means alerts disabled, so native defaults and
existing files enable alerts. The native setter, serializer, loader/migrations,
Main Menu reset and full reset run as ARM. Only allocation, path discovery and
file I/O are supplied by PreferencesDisk. No candidate instructions are patched.
Run with .venv/bin/python outside the macOS sandbox for Unicorn JIT.
"""
import argparse
import struct
import unittest
from pathlib import Path

from verify_home_flow import PreferencesDisk, SETTINGS, BRIDGE, PREFS
from native_fixture import load_image


ALERTS_DISABLED = 0x01000000
DEFAULT_HOME = 0x00081201


class AlertPreferences(unittest.TestCase):
    def dirty(self, machine):
        return bytes(machine.cpu.mem_read(PREFS + 0xb44, 1))

    def test_native_save_reload_preserves_alert_bit_and_unrelated_options(self):
        for home in (DEFAULT_HOME, 0x00ffffff, 0):
            for disabled in (False, True):
                with self.subTest(home=hex(home), disabled=disabled):
                    m = PreferencesDisk(self.image, home)
                    m.write(PREFS + 0xb48, 123)  # separate runtime volume cap
                    expected = home | (ALERTS_DISABLED if disabled else 0)
                    m.call(0xcb258, BRIDGE, expected)
                    self.assertEqual(self.dirty(m), bytes([int(disabled)]))
                    if not disabled:
                        m.call(0x9e61c, PREFS)
                    m.call(0xe53e0, PREFS)
                    self.assertEqual(m.writes, [0xb3c])
                    self.assertEqual(struct.unpack_from('<I', m.stored, 0xb2c)[0], expected)
                    self.assertEqual(self.dirty(m), b'\0')
                    self.assertEqual(m.read(PREFS + 0xb48), 123)
                    self.assertEqual(m.read(PREFS + 0x400), 0x1234abcd)
                    m.cpu.mem_write(PREFS + 8, b'\xa5' * 0xb3c)
                    m.call(0xb5408, PREFS)
                    self.assertEqual(m.read(PREFS + 8), 22)
                    self.assertEqual(m.read(PREFS + 0xb34), expected)
                    self.assertEqual(m.read(PREFS + 0x400), 0x1234abcd)
                    self.assertEqual(m.reads, [0xb3c])
                    self.assertEqual(m.opens, [2, 1])
                    self.assertEqual(m.closes, [m.HANDLE, m.HANDLE])

    def test_all_home_toggle_keys_preserve_alert_bit(self):
        for home in (DEFAULT_HOME, 0x00ffffff, 0):
            for disabled in (False, True):
                with self.subTest(home=hex(home), disabled=disabled):
                    expected = home | (ALERTS_DISABLED if disabled else 0)
                    m = PreferencesDisk(self.image, expected)
                    used = 0
                    for key in range(0x25f1, 0x260d):
                        bit = m.call(0x6b1b8, key)
                        used |= bit
                        before = m.read(PREFS + 0xb34)
                        m.call(0xcb274, SETTINGS, key, int(not bool(before & bit)))
                        self.assertEqual(m.read(PREFS + 0xb34), before ^ bit)
                        self.assertEqual(m.read(PREFS + 0xb34) & ALERTS_DISABLED,
                                         expected & ALERTS_DISABLED)
                    self.assertEqual(used, 0x00ffffff)

    def test_version20_through22_loader_migrations_preserve_alert_bit(self):
        for version in (20, 21, 22):
            for disabled in (False, True):
                with self.subTest(version=version, disabled=disabled):
                    expected = DEFAULT_HOME | (ALERTS_DISABLED if disabled else 0)
                    m = PreferencesDisk(self.image, expected)
                    m.call(0x9e61c, PREFS)
                    m.call(0xe53e0, PREFS)
                    payload = bytearray(m.stored)
                    struct.pack_into('<I', payload, 0, version)
                    m.stored = bytes(payload)
                    m.call(0xb5408, PREFS)
                    self.assertEqual(m.read(PREFS + 0xb34) & ALERTS_DISABLED,
                                     expected & ALERTS_DISABLED)

    def test_main_menu_reset_preserves_alert_preference(self):
        for disabled in (False, True):
            with self.subTest(disabled=disabled):
                bit = ALERTS_DISABLED if disabled else 0
                m = PreferencesDisk(self.image, 0x00ffffff | bit)
                m.call(0xbeb08, SETTINGS)
                self.assertEqual(m.read(PREFS + 0xb34), DEFAULT_HOME | bit)
                self.assertEqual(self.dirty(m), b'\1')
                m.call(0xe53e0, PREFS)
                self.assertEqual(struct.unpack_from('<I', m.stored, 0xb2c)[0],
                                 DEFAULT_HOME | bit)

    def test_factory_defaults_and_full_reset_enable_alerts(self):
        m = PreferencesDisk(self.image, 0x00ffffff | ALERTS_DISABLED)
        m.call(0x8d9b8, PREFS + 8)
        self.assertEqual(m.read(PREFS + 0xb34), DEFAULT_HOME)
        m.write(PREFS + 0xb34, 0x00ffffff | ALERTS_DISABLED)
        m.call(0xbe738, PREFS)
        self.assertEqual(m.read(PREFS + 0xb34), DEFAULT_HOME)
        self.assertEqual(self.dirty(m), b'\1')
        m.call(0xe53e0, PREFS)
        self.assertEqual(struct.unpack_from('<I', m.stored, 0xb2c)[0], DEFAULT_HOME)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    AlertPreferences.image = load_image(args.candidate)
    unittest.main(argv=[__file__], defaultTest='AlertPreferences', verbosity=2)
