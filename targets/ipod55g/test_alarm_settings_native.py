"""Run 5.5G settings, persistence and reset code as ARM; file I/O is supplied."""
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from image import load_stock, word
from layout import link, install_elf, finish, extend_resources
from research_actions import Machine, ScriptVM, RENDERER, QUERY, PREFS, OUT, arm
from alarm_settings import apply, resource_updates

SOURCE = Path(__file__).resolve().parent
SETTINGS, DISPLAY = 0x10011000, 0x10014000
DISABLED, DEFAULT = 0x20000000, 0x0e180201


class PreferencesDisk(Machine):
    HANDLE = 0x1234

    def __init__(self, image, symbols, mask=DEFAULT):
        super().__init__(image)
        self.symbols = symbols
        self.stored = None
        self.writes, self.reads, self.opens, self.closes, self.notifications = [], [], [], [], []
        self.changed = []
        self.hook(symbols['ipod55g_alarm_setting_changed'], lambda: self.changed.append(True))
        self.hook(0xa8178, self.allocate)
        self.hook(0xa7f9c, self.free)
        self.hook(0xd13ac, lambda: 1)
        self.hook(0xa449c, self.open_file)
        self.hook(0xa4424, self.file_size)
        self.hook(0xa4d84, self.write_file)
        self.hook(0xa4504, self.read_file)
        self.hook(0xa4400, lambda: self.closes.append(self.reg(0)) or 0)
        for at in (0x9bcb4, 0x9bd88, 0x9bcd0, 0xa0348):
            self.hook(at, lambda: 0)  # model capability queries
        self.hook(0x21f37c, lambda: self.notifications.append(tuple(self.reg(i) for i in range(3))))
        self.write(SETTINGS, 0x66c8f0)
        self.write(SETTINGS + 0x98, QUERY)
        self.write(QUERY + 0xca0, PREFS)
        self.write(PREFS + 4, DISPLAY)
        self.call(0xf3dc0, PREFS + 8)
        self.write(PREFS + 0xb34, mask)
        self.write(PREFS + 0x400, 0x1234abcd)
        self.call(symbols['alarm_preferences_loaded'], PREFS)

    def open_file(self):
        assert self.reg(1) == 0x64617461
        self.opens.append(self.reg(2)); self.write(self.reg(3), self.HANDLE)
        return 0

    def file_size(self):
        self.write(self.reg(1), len(self.stored), 0)
        return 0

    def write_file(self):
        assert self.reg(0) == self.HANDLE and self.reg(3) == 1
        self.writes.append(self.reg(2))
        self.stored = bytes(self.cpu.mem_read(self.reg(1), self.reg(2)))
        self.write(self.read(self.cpu.reg_read(arm.UC_ARM_REG_SP)), len(self.stored))
        return 0

    def read_file(self):
        assert self.reg(0) == self.HANDLE and self.reg(3) == 0
        count = self.reg(2); self.reads.append(count)
        self.cpu.mem_write(self.reg(1), self.stored[:count])
        self.write(self.read(self.cpu.reg_read(arm.UC_ARM_REG_SP)), count)
        return 0

    def enabled(self):
        return self.call(self.symbols['ipod55g_alarm_enabled'])

    def toggle(self):
        # Actual native property wrapper dispatches through the patched vtable.
        self.call(0x156ef4, SETTINGS, 0x7140, 0x6000, 0x60be)


class AlarmSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = TemporaryDirectory(); directory = Path(cls.build.name)
        stub = directory / 'pulse.c'
        stub.write_text('void ipod55g_alarm_setting_changed(void) {}\n')
        elf = link([SOURCE/'alarm_settings.c', SOURCE/'alarm_settings_hooks.S', stub], directory, 'ipod55g_alarm_enabled')
        cls.stock = load_stock(Path('iPod_25.1.3.ipsw')).osos
        image, cls.symbols = install_elf(cls.stock, elf)
        apply(image, cls.symbols)
        extend_resources(image, resource_updates(image))
        cls.image = finish(image)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def test_native_setting_getter_toggle_and_notification(self):
        m = PreferencesDisk(self.image, self.symbols)
        for enabled in (1, 0, 1):
            self.assertEqual(m.enabled(), enabled)
            self.assertEqual(m.call(0x156ec4, SETTINGS, 0x7140, 0x6000), 0x602e if enabled else 0x602f)
            m.toggle()
        self.assertEqual(m.notifications, [(SETTINGS, 0x53747220, 0x7140)] * 3)
        self.assertEqual(len(m.changed), 3)

    def test_text_property_uses_native_tag_and_localized_on_off(self):
        m = PreferencesDisk(self.image, self.symbols)
        tag = word(self.stock, 0x191444)  # actual native Settings getter tag
        self.assertEqual(tag, 0x53747220)
        labels = []
        m.hook(0x21f3b8, lambda: 0x1001a000)
        m.hook(0x21f418, lambda: labels.append((m.reg(0), m.reg(1))) or OUT+16)
        for label in (0x602e, 0x602f):
            self.assertEqual(m.call(self.symbols['alarm_setting_get'], SETTINGS, tag, 0x7140, OUT), 1)
            self.assertEqual(m.read(OUT), OUT+16)
            self.assertEqual(labels[-1], (0x1001a000, label))
            m.toggle()

    def test_stock_cycle_script_dispatches_actual_setting(self):
        m = ScriptVM(self.image)
        m.write(SETTINGS, 0x66c8f0); m.write(SETTINGS+0x98, QUERY)
        m.write(QUERY+0xca0, PREFS); m.write(PREFS+0xb34, DEFAULT)
        m.write(RENDERER, 0x66fa6c)
        m.bind(0x13b3, 35, SETTINGS); m.bind(0x1393, 3, RENDERER)
        for selector, value in ((0xc4, 0x7140), (0xc5, 0x6000)):
            pointer, virtual = m.member(3, selector)
            m.hook(m.read(0x66fa6c+pointer) if virtual else pointer, lambda value=value: value)
        m.hook(self.symbols['ipod55g_alarm_setting_changed'], lambda: 0)
        m.hook(0x21f37c, lambda: 0)
        m.run(4320, 0)
        self.assertEqual(m.read(PREFS+0xb34), DEFAULT | DISABLED)
        self.assertEqual(m.call(self.symbols['ipod55g_alarm_enabled']), 0)
        m.run(4320, 0)
        self.assertEqual(m.read(PREFS+0xb34), DEFAULT)

    def test_native_save_reload_preserves_bit_and_unrelated_data(self):
        m = PreferencesDisk(self.image, self.symbols)
        for enabled in (0, 1):
            m.toggle()
            self.assertEqual(bytes(m.cpu.mem_read(PREFS+0xb74, 1)), b'\1')
            m.call(0xbbf1c, PREFS)
            expected = DEFAULT | (0 if enabled else DISABLED)
            self.assertEqual(struct.unpack_from('<I', m.stored, 0xb2c)[0], expected)
            self.assertEqual(bytes(m.cpu.mem_read(PREFS+0xb74, 1)), b'\0')
            m.cpu.mem_write(PREFS+8, b'\xa5' * 0xb6c)
            m.call(0xaf698, PREFS)
            self.assertEqual(m.read(PREFS+0xb34), expected)
            self.assertEqual(m.read(PREFS+0x400), 0x1234abcd)
            self.assertEqual(m.enabled(), enabled)
        self.assertEqual(m.writes, [0xb6c] * 2)
        self.assertEqual(m.reads, [0xb6c] * 2)
        self.assertEqual(m.opens, [2, 1, 2, 1])

    def test_all_home_keys_preserve_alarm_setting(self):
        m = PreferencesDisk(self.image, self.symbols, DEFAULT | DISABLED)
        used = 0
        for key in range(0x5f7c, 0x5fa0):
            bit = m.call(0x105a04, key); used |= bit
            before = m.read(PREFS+0xb34)
            m.call(0x19351c, SETTINGS, key, int(not bool(before & bit)))
            self.assertEqual(m.read(PREFS+0xb34), before ^ bit)
        self.assertEqual(used, 0x1fffbfff)
        self.assertEqual(m.enabled(), 0)

    def test_menu_reset_keeps_alarm_preference_full_reset_enables_it(self):
        m = PreferencesDisk(self.image, self.symbols, 0x3fffffff)
        m.call(0x190a4c, SETTINGS)
        self.assertEqual(m.read(PREFS+0xb34), DEFAULT | DISABLED)
        self.assertEqual(m.enabled(), 0)
        m.call(0xb0bd8, PREFS)
        self.assertEqual(m.read(PREFS+0xb34), DEFAULT)
        self.assertEqual(m.enabled(), 1)

    def test_supported_loader_migrations_keep_disabled_bit(self):
        for version in (0x14, 0x15, 0x16, 0x38, 0x39):
            with self.subTest(version=version):
                m = PreferencesDisk(self.image, self.symbols, DEFAULT | DISABLED)
                m.call(0xbbdf8, PREFS)
                payload = bytearray(m.stored); struct.pack_into('<I', payload, 0, version)
                m.stored = bytes(payload)
                m.call(0xaf698, PREFS)
                self.assertEqual(m.read(PREFS+0xb34) & DISABLED, DISABLED)
                self.assertEqual(m.enabled(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
