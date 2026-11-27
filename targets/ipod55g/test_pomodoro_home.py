"""Exercise native Main Menu customization, filtering and preferences as ARM.

IPOD55G_HOME_BASELINE names an existing candidate archive for regression proof.
Only resource/OS/device boundaries are supplied; no iPod is accessed.
"""
import json
import os
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from image import load_stock, resources, word, MEMBER, OSOS_START
from layout import link, install_elf, extend_resources, finish
from research_actions import QUERY, PREFS, OUT
from test_alarm_settings_native import PreferencesDisk, SETTINGS, DEFAULT, DISABLED
from pomodoro_ui import records, sequence
from scripts import merge_scripts, make_record

SOURCE = Path(__file__).resolve().parent
PIN, KEY, LABEL = 0x40000000, 0x7120, 0x7126


def payload(image, tag, key):
    at, size = resources(image)[tag][key]
    return image[at:at + size]


class PomodoroHome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = load_stock(Path('iPod_25.1.3.ipsw')).osos
        baseline = os.environ.get('IPOD55G_HOME_BASELINE')
        if baseline:
            path = Path(baseline)
            with ZipFile(path) as archive:
                firmware = archive.read(MEMBER)
            cls.image = firmware[OSOS_START:OSOS_START + word(firmware, 0x4210)]
            cls.symbols = json.loads((path.with_suffix('.artifacts') / 'extension-symbols.json').read_text())
            return
        import alarm_settings
        import pomodoro_home
        cls.build = TemporaryDirectory()
        directory = Path(cls.build.name)
        stub = directory / 'alarm.c'
        stub.write_text('void ipod55g_alarm_setting_changed(void) {}\n')
        elf = link([SOURCE / 'alarm_settings.c', SOURCE / 'alarm_settings_hooks.S',
                    SOURCE / 'pomodoro_home.c', stub], directory, 'pomo_home_mask')
        image, cls.symbols = install_elf(cls.stock, elf)
        alarm_settings.apply(image, cls.symbols)
        pomodoro_home.apply(image, cls.symbols)
        updates = pomodoro_home.resource_updates(image)
        updates['Str '][KEY] = b'Pomodoro\0'
        updates['TCmd'] = {0x2380: merge_scripts(image, pomodoro_home.script_updates(image))}
        extend_resources(image, updates)
        cls.image = finish(image)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'build'):
            cls.build.cleanup()

    def test_resources_insert_only_pomodoro_immediately_below_stopwatch(self):
        for key, offset, previous in ((0x5e1e, 24, 0x5f85), (0x6018, 28, 0x60005f85)):
            old = records(payload(self.stock, 'Menu', key))
            new = records(payload(self.image, 'Menu', key))
            added = [i for i, row in enumerate(new)
                     if (word(row, 24) == KEY if key == 0x5e1e else word(row, 28) == 0x60007120)]
            self.assertEqual(len(added), 1, 'Pomodoro is absent from the native menu')
            index = added[0]
            self.assertEqual(word(new[index - 1], offset), previous)
            self.assertEqual(new[:index] + new[index + 1:], old)
        self.assertEqual(payload(self.image, 'Str ', LABEL).decode(), '\u2003\u2060Pomodoro\0')

    def test_native_property_toggle_and_explicit_on_off_preserve_alarm(self):
        m = PreferencesDisk(self.image, self.symbols, DEFAULT | DISABLED)
        for command, expected in ((0x60be, True), (0x60be, False), (0x602e, True),
                                  (0x602e, True), (0x602f, False)):
            m.call(0x156ef4, SETTINGS, KEY, 0x6000, command)
            self.assertEqual(m.read(PREFS + 0xb34), DEFAULT | DISABLED | (PIN if expected else 0))
            self.assertEqual(m.call(0x156ec4, SETTINGS, KEY, 0x6000), 0x602e if expected else 0x602f)
            self.assertEqual(m.enabled(), 0)
        self.assertTrue(m.notifications)
        self.assertTrue(all(n == (SETTINGS, 0x53747220, KEY) for n in m.notifications))

    def widgets(self, view):
        from research_actions import VMWidgets, RENDERER, WIDGET_SETTINGS, APP
        # These four stock helpers query non-menu devices (accessories, alarm,
        # games and playback). The complete visibility loop3996 and native
        # helpers3993..3995, menu models and settings execute unchanged.
        image = bytearray(self.image)
        external = {n: make_record(n, b'\x43\x05') for n in (4001, 3997, 3998, 3999)}
        extend_resources(image, {'TCmd': {0x2380: merge_scripts(image, external)}})
        image = bytes(image)
        m = VMWidgets(image)
        m.instruction_limit = 2000000
        m.hook(0x1d3050, lambda: {8: RENDERER, 0x5e00: RENDERER,
                                0x2b00: RENDERER, 0x6000: WIDGET_SETTINGS}.get(m.reg(0), 0))
        for address in (0x21f37c, 0x13d6ac, 0x22d5c0):
            m.hook(address, lambda: 0)  # external property/redraw/selection event publication
        m.bind(0x1393, 3, RENDERER)
        m.bind(0x13b3, 35, WIDGET_SETTINGS)
        m.bind(0x1392, 2, APP)
        m.write(APP, 0x66fe10)
        m.current_screen = 4002 * 4 + 2
        m.hook(m.read(0x66fe10 + 0x150), lambda: m.current_screen)
        m.hook(0x21f3b8, lambda: RENDERER)
        root_record = next(r for r in records(payload(image, 'View', 0x5e15)) if word(r, 12) == view)
        root = m.construct(root_record)
        menu = m.children(m.children(root)[1])[0]
        m.call(0x23cb0c, menu)
        m.write(RENDERER + 0x24, root)
        m.write(RENDERER + 0x28, menu)
        return m, menu, WIDGET_SETTINGS

    def visible_keys(self, m, menu):
        count = m.call(0x21845c, m.read(menu + 0x144))
        return [m.read(m.read(m.item(menu, i) + 0x10) + 0x14) & 0xffff for i in range(count)]

    def test_native_customization_widget_labels_and_selected_stock_cycle_script(self):
        m, menu, settings = self.widgets(0x6019)
        keys = self.visible_keys(m, menu)
        self.assertIn(LABEL, keys, 'native customization widget is missing Pomodoro')
        index = keys.index(LABEL)
        self.assertEqual(keys[index - 1], 0xff50)  # indented stock Stopwatch
        item = m.item(menu, index)
        vtable = m.read(item)
        caption = m.call(m.read(vtable + 0x18), item)
        self.assertEqual(bytes(m.cpu.mem_read(caption, 32)).split(b'\0')[0].decode(), '\u2003\u2060Pomodoro')
        m.write(m.read(menu + 0xa8) + 0xa4, index)
        for pinned in (False, True, False):
            secondary = m.call(m.read(vtable + 0x24), item)
            self.assertEqual(bytes(m.cpu.mem_read(secondary, 4)).split(b'\0')[0], b'On' if pinned else b'Off')
            self.assertEqual(m.read(PREFS + 0xb34) & PIN, PIN if pinned else 0)
            self.assertEqual(m.run(4320, 0), 0xc0000002)

    def test_full_native_main_menu_visibility_script_hides_and_shows_actual_row(self):
        m, menu, settings = self.widgets(0x5f6e)
        # A real native Stopwatch pin lets the emitted order be checked after
        # native filtering, not just in the resource bytes.
        m.call(0x19351c, settings, 0x5f85, 1)
        for pinned in (False, True, False):
            m.call(0x156ef4, settings, KEY, 0x6000, 0x602e if pinned else 0x602f)
            self.assertEqual(m.run(3996), 0xc0000002)
            keys = self.visible_keys(m, menu)
            self.assertEqual(KEY in keys, pinned)
            self.assertIn(0x5f85, keys)
            self.assertNotIn(0x5f86, keys)  # stock Screen Lock refresh follows the insertion
            if pinned:
                self.assertEqual(keys[keys.index(KEY) - 1], 0x5f85)
                item = m.item(menu, keys.index(KEY))
                vtable = m.read(item)
                self.assertEqual(m.call(m.read(vtable + 0x34), item), 5111 * 4 + 2)
                pointer = m.call(m.read(vtable + 0x18), item)
                self.assertEqual(bytes(m.cpu.mem_read(pointer, 16)).split(b'\0')[0], b'Pomodoro')
                bitmap = m.call(m.read(vtable + 0x2c), item)
                arrow = payload(self.image, 'BMap', 0x5ecc)
                self.assertEqual(bytes(m.cpu.mem_read(bitmap, len(arrow))), arrow)

    def test_visibility_refresh_keeps_stock_guard_outside_main_menu(self):
        m, menu, settings = self.widgets(0x5f6e)
        before = self.visible_keys(m, menu)
        m.current_screen = 4244 * 4 + 2  # Now Playing rather than Main Menu
        self.assertEqual(m.run(3996), 0xc0000002)
        self.assertEqual(self.visible_keys(m, menu), before)

    def test_native_save_reload_retains_pin_and_unrelated_preferences(self):
        m = PreferencesDisk(self.image, self.symbols, DEFAULT | DISABLED)
        for pinned in (True, False):
            m.call(0x156ef4, SETTINGS, KEY, 0x6000, 0x60be)
            expected = DEFAULT | DISABLED | (PIN if pinned else 0)
            self.assertEqual(m.read(PREFS + 0xb34), expected)
            self.assertEqual(bytes(m.cpu.mem_read(PREFS + 0xb74, 1)), b'\1')
            m.call(0xbbf1c, PREFS)
            self.assertEqual(struct.unpack_from('<I', m.stored, 0xb2c)[0], expected)
            m.cpu.mem_write(PREFS + 8, b'\xa5' * 0xb6c)
            m.call(0xaf698, PREFS)
            self.assertEqual(m.read(PREFS + 0xb34), expected)
            self.assertEqual(m.call(0x193470, SETTINGS, KEY), int(pinned))
            self.assertEqual(m.read(PREFS + 0x400), 0x1234abcd)
            self.assertEqual(m.enabled(), 0)

    def test_native_menu_reset_unpins_and_preserves_alarm_full_reset_defaults_off(self):
        m = PreferencesDisk(self.image, self.symbols, DEFAULT | DISABLED | PIN)
        self.assertEqual(m.call(0x193470, SETTINGS, KEY), 1)
        m.call(0x190a4c, SETTINGS)
        self.assertEqual(m.read(PREFS + 0xb34), DEFAULT | DISABLED)
        self.assertEqual(m.call(0x193470, SETTINGS, KEY), 0)
        self.assertEqual(m.enabled(), 0)
        m.write(PREFS + 0xb34, 0xffffffff)
        m.call(0xb0bd8, PREFS)
        self.assertEqual(m.read(PREFS + 0xb34), DEFAULT)
        self.assertEqual(m.call(0x193470, SETTINGS, KEY), 0)
        self.assertEqual(m.enabled(), 1)

    def test_all_stock_keys_and_holes_preserve_pin_and_alarm(self):
        m = PreferencesDisk(self.image, self.symbols, DEFAULT | DISABLED | PIN)
        used = 0
        for key in range(0x5f7c, 0x5fa0):
            bit = m.call(0x105a04, key)
            used |= bit
            before = m.read(PREFS + 0xb34)
            m.call(0x19351c, SETTINGS, key, int(not bool(before & bit)))
            self.assertEqual(m.read(PREFS + 0xb34), before ^ bit)
        self.assertEqual(used, 0x1fffbfff)
        self.assertEqual(m.read(PREFS + 0xb34) & (PIN | DISABLED), PIN | DISABLED)
        self.assertEqual(m.enabled(), 0)

    def test_supported_native_migrations_preserve_pin_and_alarm(self):
        for version in (0x14, 0x15, 0x16, 0x38, 0x39):
            with self.subTest(version=version):
                m = PreferencesDisk(self.image, self.symbols, DEFAULT | DISABLED | PIN)
                m.call(0xbbdf8, PREFS)
                data = bytearray(m.stored)
                struct.pack_into('<I', data, 0, version)
                m.stored = bytes(data)
                m.call(0xaf698, PREFS)
                self.assertEqual(m.read(PREFS + 0xb34) & (PIN | DISABLED), PIN | DISABLED)
                self.assertEqual(m.call(0x193470, SETTINGS, KEY), 1)
                self.assertEqual(m.enabled(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
