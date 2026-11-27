"""Execute native Clicker settings, ScriptVM cycling and Preferences persistence."""
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from image import load_stock, resources
from research_actions import ScriptVM, RENDERER, QUERY, PREFS, OUT, RETURN, arm
from test_alarm_settings_native import PreferencesDisk, SETTINGS


class ClickerNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from build import compose
        stock = load_stock(Path('iPod_25.1.3.ipsw'))
        with TemporaryDirectory() as directory:
            cls.image, cls.symbols, _ = compose(stock, Path(directory))

    def machine(self):
        m = PreferencesDisk(self.image, self.symbols)
        # C++ static destructor registration belongs to the supplied OS runtime.
        m.hook(0x25aa40, lambda: 0)
        return m

    def choose(self, m, label):
        m.call(0x156ef4, SETTINGS, 0x6088, 0x6000, label)

    def value(self, m):
        return m.call(0x156ec4, SETTINGS, 0x6088, 0x6000)

    def test_existing_labels_select_correct_output_and_preserve_other_bits(self):
        m = self.machine()
        labels = resources(self.image)['Str ']
        for key, name, flags in ((0x6089, b'On', 3), (0x608a, b'Off', 0),
                                 (0x608b, b'Speaker', 1), (0x608c, b'Headphones', 2)):
            with self.subTest(label=name):
                at, length = labels[key]
                self.assertEqual(self.image[at:at+length].rstrip(b'\0'), name)
                m.write(PREFS+0xb04, 0xa4)
                m.write(0x1081d9c4, 0xa4)
                self.choose(m, key)
                self.assertEqual(m.read(PREFS+0xb04), 0xa4 | flags)
                self.assertEqual(m.read(0x1081d9c4), 0xa4 | flags)
                self.assertEqual(self.value(m), key)

    def test_stock_cycle_script_exposes_all_four_modes_and_wraps(self):
        m = ScriptVM(self.image)
        m.hook(0x25aa40, lambda: 0)
        m.write(SETTINGS, 0x66c8f0); m.write(SETTINGS+0x98, QUERY)
        m.write(QUERY+0xca0, PREFS); m.write(PREFS+0xb04, 3)
        m.write(RENDERER, 0x66fa6c)
        m.bind(0x13b3, 35, SETTINGS); m.bind(0x1393, 3, RENDERER)
        for selector, value in ((0xc4, 0x6088), (0xc5, 0x6000)):
            pointer, virtual = m.member(3, selector)
            m.hook(m.read(0x66fa6c+pointer) if virtual else pointer, lambda value=value: value)
        for key, flags in ((0x608a, 0), (0x608b, 1), (0x608c, 2), (0x6089, 3), (0x608a, 0)):
            m.run(4320, 0)
            self.assertEqual((self.value(m), m.read(PREFS+0xb04)), (key, flags))

    def test_native_initial_refresh_restores_label_for_each_saved_output(self):
        m = self.machine()
        for flags, key in ((0, 0x608a), (1, 0x608b), (2, 0x608c), (3, 0x6089)):
            with self.subTest(flags=flags):
                m.write(PREFS+0xb04, flags)
                m.call(0x1937d4, SETTINGS)
                self.assertEqual(self.value(m), key)
                self.assertEqual(tuple(m.cpu.mem_read(SETTINGS+0x36, 2)),
                                 (int(bool(flags & 2)), flags & 1))

    def test_native_live_refresh_restores_label_and_notifies(self):
        m = self.machine()
        # Run the Clicker region of the native live-refresh routine. Surrounding
        # unrelated Settings properties are outside this fixture's scope.
        def enter():
            m.cpu.reg_write(arm.UC_ARM_REG_R4, SETTINGS)
            m.cpu.reg_write(arm.UC_ARM_REG_R6, 0x53747220)
            m.cpu.reg_write(arm.UC_ARM_REG_LR, 0x193dbc)
        def leave():
            m.cpu.reg_write(arm.UC_ARM_REG_LR, RETURN)
        m.hook(OUT, enter); m.hook(0x193e7c, leave)
        for flags, key in ((1, 0x608b), (2, 0x608c), (3, 0x6089), (0, 0x608a)):
            with self.subTest(flags=flags):
                m.write(PREFS+0xb04, flags); m.notifications.clear()
                m.call(OUT)
                self.assertEqual(self.value(m), key)
                self.assertIn((SETTINGS, 0x53747220, 0x6088), m.notifications)

    def test_native_save_reload_retains_output_and_unrelated_preferences(self):
        m = self.machine()
        for key, flags in ((0x608a, 0), (0x608b, 1), (0x608c, 2), (0x6089, 3)):
            with self.subTest(label=hex(key)):
                self.choose(m, key)
                m.call(0xbbf1c, PREFS)
                self.assertEqual(struct.unpack_from('<I', m.stored, 0xafc)[0], flags)
                m.cpu.mem_write(PREFS+8, b'\xa5' * 0xb6c)
                m.call(0xaf698, PREFS)
                self.assertEqual(m.read(PREFS+0xb04), flags)
                self.assertEqual(m.read(0x1081d9c4), flags)
                self.assertEqual(m.read(PREFS+0x400), 0x1234abcd)
                m.call(0x1937d4, SETTINGS)
                self.assertEqual(self.value(m), key)
        self.assertEqual(m.writes, [0xb6c] * 4)
        self.assertEqual(m.reads, [0xb6c] * 4)

    def test_reset_retains_native_on_default(self):
        m = self.machine()
        self.choose(m, 0x608b)
        m.call(0xb0bd8, PREFS)
        self.assertEqual(m.read(PREFS+0xb04), 3)
        m.call(0x1937d4, SETTINGS)
        self.assertEqual(self.value(m), 0x6089)


if __name__ == '__main__':
    unittest.main(verbosity=2)
