"""Run packaged Main Menu commands and native preference save/reload.

The VM, visibility predicate, preference setter, serializer and startup loader
execute ARM code from the candidate. Display calls and filesystem operations
are supplied boundaries. No candidate instructions or resources are patched.
Run with the repository Python outside the macOS sandbox for Unicorn JIT.
"""
import argparse
import json
import struct
import unittest
from pathlib import Path

from verify_runtime import BASELINE
from native_fixture import NativeMachine, ScriptVM, APP, RENDERER, load_image
from verify_resources import directory, records, word
from actions_patch import functions
from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm

KEY, BIT = 0x25f4, 0x4000
SHORTCUT_KEYS = (0x2607, 0x2608, 0x2609, KEY)
SHORTCUT_CAPTIONS = (0xffffff57, 0xffffff56, 0xffffff4e, KEY)
SETTINGS, BRIDGE, PREFS, VALUE = 0x10060000, 0x10062000, 0x10064000, 0x10066000


def attach_preferences(m, mask):
    m.write(SETTINGS, 0x23a48)
    m.write(SETTINGS + 0x58, BRIDGE)
    m.write(BRIDGE, PREFS)
    m.write(PREFS + 0xb34, mask)


def visibility_machine(image, mask, current=1882):
    m = ScriptVM(image)
    m.instruction_limit = 1000000
    attach_preferences(m, mask)
    m.call(0xe7658, m.global_slot(0x9ba), m.object(27, SETTINGS))
    m.call(0xe7658, m.global_slot(0x9da), m.object(35, 0x10068000))
    m.hook(m.method_address(2, 0x78), lambda: current * 4 + 2)
    m.hook(m.method_address(35, 0x179), lambda: 0)  # calendar database count
    m.hook(m.method_address(26, 0x10c), lambda: 0)  # no playing queue
    m.hook(m.method_address(26, 0x10d), lambda: 0)
    m.visibility, m.predicates = [], []

    def show_row():
        m.visibility.append(tuple(m.reg(i) for i in range(1, 4)) +
                            (m.read(m.cpu.reg_read(arm.UC_ARM_REG_SP)),))
        return 0

    m.hook(m.method_address(3, 0xa1), show_row)

    def predicate(cpu, address, size, context):
        if address == 0x7032c:
            m.predicates.append(m.reg(1))

    m.cpu.hook_add(UC_HOOK_CODE, predicate)
    return m


class PreferencesDisk(NativeMachine):
    """Only allocation, path discovery and file I/O are supplied to prefs code."""
    HANDLE, DISPLAY = 0x1234, 0x10070000

    def __init__(self, image, mask):
        super().__init__(image)
        self.stored = None
        self.writes, self.reads, self.opens, self.closes = [], [], [], []
        self.hook(0x9b8e0, self.allocate)
        self.hook(0x9b6fc, self.free)
        self.hook(0x654d0, lambda: 1)  # a mounted prefs path is available
        self.hook(0x93974, self.open_file)
        self.hook(0x938f8, self.file_size)
        self.hook(0x93c50, self.write_file)
        self.hook(0x939e0, self.read_file)
        self.hook(0x938d4, lambda: self.closes.append(self.reg(0)) or 0)
        self.hook(0x799e8, lambda: 0)  # model-dependent contrast default
        attach_preferences(self, mask)
        self.write(PREFS + 4, self.DISPLAY)
        self.call(0x8d9b8, PREFS + 8)  # stock preference initialization
        self.write(PREFS + 0xb34, mask)
        self.write(PREFS + 0x400, 0x1234abcd)  # unrelated saved data

    def open_file(self):
        assert self.reg(1) == 0x64617461  # native 'data' fork
        self.opens.append(self.reg(2))
        self.write(self.reg(3), self.HANDLE)
        return 0

    def file_size(self):
        assert self.reg(0) == self.HANDLE and self.stored is not None
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
        count = self.reg(2)
        self.reads.append(count)
        assert self.stored is not None and count <= len(self.stored)
        self.cpu.mem_write(self.reg(1), self.stored[:count])
        self.write(self.read(self.cpu.reg_read(arm.UC_ARM_REG_SP)), count)
        return 0


class HomeFlow(unittest.TestCase):
    def test_home_refresh_uses_native_preference_for_hidden_on_and_off(self):
        old_image = load_image(BASELINE)
        m = visibility_machine(self.image, 0x81201)
        for enabled in (False, True, False):
            with self.subTest(enabled=enabled):
                mask = 0x81201 | (BIT if enabled else 0)
                m.write(PREFS + 0xb34, mask)
                m.visibility.clear()
                m.predicates.clear()
                m.run(1879)
                self.assertEqual([row for row in m.visibility if row[1] == KEY],
                                 [(0x2512, KEY, 3, int(enabled))])
                self.assertEqual(m.predicates.count(KEY), 1)
                # Every existing row still receives the stock command result.
                before = visibility_machine(old_image, mask)
                before.run(1879)
                self.assertEqual([row for row in m.visibility if row[1] not in SHORTCUT_CAPTIONS],
                                 before.visibility)
                self.assertEqual([key for key in m.predicates if key not in SHORTCUT_KEYS],
                                 before.predicates)

    def test_home_refresh_does_nothing_while_another_screen_is_current(self):
        m = visibility_machine(self.image, 0xffffffff, current=2554)
        m.run(1879)
        self.assertEqual(m.visibility, [])
        self.assertEqual(m.predicates, [])

    def test_all_stock_commands_except_home_refresh_are_preserved(self):
        def commands(image):
            return {a: (argc, local, stack, code) for a, argc, local, stack, code
                    in functions(directory(image)['TCmd'][1][0x2380])[4]}
        old, new = commands(load_image(BASELINE)), commands(self.image)
        for atom, body in old.items():
            if atom != 1879:
                self.assertEqual(new[atom], body, f'changed stock command {atom}')

    def test_native_save_and_startup_reload_preserve_toggle_and_other_options(self):
        m = PreferencesDisk(self.image, 0x004fbfff)
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                before = m.read(PREFS + 0xb34)
                m.write(VALUE, 0x283f)
                self.assertEqual(m.call(0xcc164, SETTINGS, 0x70724944, KEY, VALUE), 1)
                expected = before ^ BIT
                self.assertEqual(bool(expected & BIT), enabled)
                self.assertEqual(m.read(PREFS + 0xb34), expected)
                self.assertEqual(bytes(m.cpu.mem_read(PREFS + 0xb44, 1)), b'\x01')
                m.call(0xe53e0, PREFS)  # native dirty-check and serializer
                self.assertEqual(m.writes[-1], 0xb3c)
                self.assertEqual(struct.unpack_from('<I', m.stored, 0xb2c)[0], expected)
                self.assertEqual(bytes(m.cpu.mem_read(PREFS + 0xb44, 1)), b'\0')
                self.assertEqual(m.read(PREFS + 0x400), 0x1234abcd)
                # Startup clears defaults, reads the file, validates version22,
                # and applies native migration policy. It must keep our bit.
                m.cpu.mem_write(PREFS + 8, b'\xa5' * 0xb3c)
                m.call(0xb5408, PREFS)
                self.assertEqual(m.read(PREFS + 8), 22)
                self.assertEqual(m.read(PREFS + 0xb34), expected)
                self.assertEqual(m.read(PREFS + 0x400), 0x1234abcd)
                self.assertEqual(m.reads[-1], 0xb3c)
                self.assertEqual(m.call(0x7032c, SETTINGS, KEY), int(enabled))
        self.assertEqual(m.opens, [2, 1, 2, 1])
        self.assertEqual(m.closes, [m.HANDLE] * 4)

    def test_home_shortcut_uses_native_open_and_menu_returns_to_home(self):
        from ui_fixture import machine
        m = machine(self.image, self.symbols)
        parent = m.read(APP + 0x104)
        m.write(parent + 8, 1882 * 4 + 2)
        rows = [r for r in records(directory(self.image)['Menu'][1][0x2512])
                if word(r, 32) == KEY]
        self.assertEqual(len(rows), 1, 'home menu must contain one Pomodoro row')
        row = rows[0]
        self.assertEqual(word(row, 12), 2553 * 4 + 2)
        self.assertEqual(m.run(word(row, 12) >> 2, m.event), 0xc0000002)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2554 * 4 + 2)
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']), 1)
        self.assertEqual(m.bindings[3, 0x10], 1948 * 4 + 2)
        m.invoke('start')
        m.run(1860, m.event)  # native Menu binding/history pop and leave callback
        self.assertEqual(m.read(APP + 0x104), parent)
        self.assertEqual(m.read(parent + 8), 1882 * 4 + 2)
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']), 0)
        self.assertEqual(m.read(m.state + 20), 1)
        self.assertEqual(m.read(self.symbols['pomo_ui_timer'] + 28), 0x73746f70)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    HomeFlow.image = load_image(args.candidate)
    artifacts = args.artifacts or args.candidate.with_suffix('.artifacts')
    HomeFlow.symbols = json.loads((artifacts / 'pomodoro-symbols.json').read_text())
    unittest.main(argv=[__file__], verbosity=2)
