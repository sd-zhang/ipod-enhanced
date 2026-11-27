"""Check the native captions and hierarchy of packaged Main Menu shortcuts.

The ARM item caption getter, signed resource-key conversion and memory-resource
lookup execute from the candidate. Only the enclosing resource-provider object
is supplied. This does not rasterize the LCD. Run Unicorn outside the sandbox.
"""
import argparse
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from verify_runtime import BASELINE
from native_fixture import APP, RENDERER, NativeMachine, load_image
from verify_actions_widgets import ActionsVMDisplay
import verify_actions
from verify_resources import ROOT, directory, records, word
from verify_home import preferences, SETTINGS as SIMPLE_SETTINGS, OUT
from verify_home_flow import PreferencesDisk, visibility_machine, SETTINGS, PREFS, VALUE
from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm

SHORTCUTS = ((0x2607, 0x100000), (0x2608, 0x200000),
             (0x2609, 0x800000), (0x25f4, 0x4000))


def home_actions(image):
    """Construct the real Home widget tree; destination drawing is supplied."""
    with patch.object(verify_actions, 'ScriptVM', lambda data: ActionsVMDisplay(
            data, settings=SETTINGS, view=0x25e4)):
        m = verify_actions.menu_machine(image)
    parent = m.read(APP + 0x104)
    m.write(parent + 8, 1882 * 4 + 2)
    m.write(parent + 0x10, 0, 0)
    m.write(SETTINGS + 0x58, 0x10062000)
    m.write(0x10062000, PREFS)
    m.call(0xe7658, m.global_slot(0x9ba), m.object(27, SETTINGS))
    m.call(0xe7658, m.global_slot(0x9e5), m.object(42, 0x10068000))
    m.supply_services(((2, (0x5f,)), (3, (0xa0,)), (42, (0x1bd,))))
    for address in (0xc77d4, 0xcb554, 0xcdde8, 0xc7d20):
        m.hook(address, lambda: 0)  # OS setting-change notifications
    m.hook(0xcf128, lambda: 0)  # font-style service
    m.hook(0xbac78, lambda: 0)  # external provider registry
    m.call(0xf2c04, RENDERER, 0)
    controller_table = m.read(RENDERER)
    m.prepare_renderer()
    m.write(RENDERER, controller_table)
    m.call(0xdd19c, RENDERER, 0x25e4, 0, 30, 300)
    m.timer_requests, m.submenu_selections = [], []
    m.hook(m.method_address(3, 0xa0), lambda: m.submenu_selections.append(
        (m.reg(1), m.reg(2), m.reg(3))) or 0)
    m.hook(m.method_address(25, 0xd4), lambda: m.timer_requests.append(m.reg(1)) or 0)
    return m


class MenuCaptions(NativeMachine):
    MANAGER, SERVICE, VTABLE, GETTER, OUT, ITEM, RECORD = range(
        0x10001000, 0x10008000, 0x1000)

    def __init__(self, image):
        super().__init__(image)
        self.call(0xee004, self.MANAGER, ROOT, word(image, ROOT - 4))
        self.groups = directory(image)
        self.strings = {}
        for key in self.groups['Str '][1]:
            self.strings[key] = self.call(
                0x7ae24, self.MANAGER, 0x53747220, key, self.OUT)
        self.write(self.SERVICE, self.VTABLE)
        self.write(self.VTABLE + 0x50, self.GETTER - self.VTABLE)
        self.hook(self.GETTER, self.lookup)

    def lookup(self):
        assert self.reg(1) == 0x53747220
        self.write(self.reg(3), self.strings[self.reg(2)])
        return 1

    def caption(self, row):
        # The native item holds the resource record after its size word.
        self.cpu.mem_write(self.RECORD, row[4:])
        self.write(self.ITEM + 0x10, self.RECORD, self.SERVICE)
        pointer = self.call(0x77750, self.ITEM)
        assert pointer
        return bytes(self.cpu.mem_read(pointer, 128)).split(b'\0', 1)[0].decode()


class HomeIndent(unittest.TestCase):
    def test_home_refresh_changes_the_real_shortcut_items_and_visible_count(self):
        m = home_actions(self.image)
        rows = records(directory(self.image)['Menu'][1][0x2512])
        group = m.read(m.menu + 0xe8)
        items = {key: m.call(0x6cebc, group, next(
            i for i, row in enumerate(rows) if word(row, 32) == key))
                 for key, _ in SHORTCUTS}
        m.call(0xe7658, m.global_slot(0x9da), m.object(35, 0x10069000))
        m.hook(m.method_address(35, 0x179), lambda: 0)
        for selector in (0x10c, 0x10d):
            m.hook(m.method_address(26, selector), lambda: 0)
        m.hook(0x9100c, lambda: 0)  # final LCD invalidation

        def resolved_item(cpu, address, size, context):
            if address == 0x5f528 and m.reg(0):
                item = m.read(cpu.reg_read(arm.UC_ARM_REG_SP) + 0x10)
                self.assertNotEqual(item, 0, 'home visibility did not resolve its native menu item')
        m.cpu.hook_add(UC_HOOK_CODE, resolved_item)
        base_count = None
        for enabled in (0, 0x100000, 0x200000, 0x800000, 0x4000, 0xb04000, 0):
            with self.subTest(enabled=hex(enabled)):
                m.write(PREFS + 0xb34, 0x81201 | enabled)
                self.assertEqual(m.run(1879), 0xc0000002)
                for key, bit in SHORTCUTS:
                    self.assertEqual(m.call(0x5f900, items[key]), int(bool(enabled & bit)))
                count = m.call(0x6ce68, group)
                if base_count is None:
                    base_count = count
                self.assertEqual(count, base_count + enabled.bit_count())
                self.assertEqual(m.read(m.menu + 0xa0), count)

    def test_sleep_timer_choices_from_home_keep_native_selection_and_return(self):
        m = home_actions(self.image)
        rows = records(directory(self.image)['Menu'][1][0x2512])
        index = next(i for i, row in enumerate(rows) if word(row, 32) == 0x2608)
        m.call(0xc60ac, m.menu, index)
        parent = m.read(APP + 0x104)
        for choice, minutes in ((0x27da, 65535), (0x27db, 15), (0x27dc, 30),
                                (0x27dd, 60), (0x27de, 90), (0x27df, 120)):
            with self.subTest(minutes=minutes):
                self.assertEqual(m.run(2160, m.event), 0xc0000002)
                m.hook(0x1b6f8, lambda: choice)  # selected stock submenu row
                self.assertEqual(m.run(2167, m.event), 0xc0000002)
                self.assertEqual(m.timer_requests[-1], minutes)
                self.assertEqual(m.read(APP + 0x104), parent)
                del m.hooks[0x1b6f8]
                previous_requests = m.timer_requests[:]
                m.run(2160, m.event)
                self.assertEqual(m.submenu_selections[-1], (0x2789, choice, 5))
                m.run(1860, m.event)
                self.assertEqual(m.timer_requests, previous_requests)
                self.assertEqual(m.read(APP + 0x104), parent)

    def test_native_home_activation_opens_each_stock_clock_function_and_returns(self):
        rows = records(directory(self.image)['Menu'][1][0x2512])
        for key, command, destination in ((0x2607, 2444, 2445),
                                          (0x2608, 2160, 2162),
                                          (0x2609, 2142, 2143)):
            with self.subTest(key=hex(key)):
                m = home_actions(self.image)
                index = next(i for i, row in enumerate(rows) if word(row, 32) == key)
                m.call(0xc60ac, m.menu, index)
                parent = m.read(APP + 0x104)
                events = []
                del m.hooks[0xaba84]
                m.hook(0xab854, lambda: events.append(m.reg(0)) or 1)
                m.hook(0xd915c, m.free)
                m.hook(0xd9a50, m.free)
                m.call(0x1b518, RENDERER)
                # The derived controller also queues a power-save activity
                # notification. Deliver the menu command to its UI consumer.
                events = [event for event in events if m.read(event + 4) == 0x6d656e75]
                self.assertEqual(len(events), 1)
                self.assertEqual(m.read(events[0] + 12), command * 4 + 2)
                table = m.read(RENDERER)
                self.assertEqual(m.call(table + m.read(table + 0x24), RENDERER, events[0]), 1)
                self.assertEqual(m.read(m.read(APP + 0x104) + 8), destination * 4 + 2)
                self.assertEqual(m.timer_requests, [])
                if key == 0x2608:
                    self.assertEqual(m.submenu_selections[-1], (0x2789, 0x27da, 5))
                self.assertEqual(m.run(1860, m.event), 0xc0000002)
                self.assertEqual(m.read(APP + 0x104), parent)
                self.assertEqual(m.read(parent + 8), 1882 * 4 + 2)
                self.assertEqual(m.timer_requests, [])

    def test_each_shortcut_visibility_uses_its_independent_native_preference(self):
        m = visibility_machine(self.image, 0x81201)
        keys = [key for key, _ in SHORTCUTS]
        captions = (0xffffff57, 0xffffff56, 0xffffff4e, 0x25f4)
        for enabled in (0, 0x100000, 0x200000, 0x800000, 0x4000, 0xb04000, 0):
            with self.subTest(enabled=hex(enabled)):
                m.write(PREFS + 0xb34, 0x81201 | enabled)
                m.visibility.clear()
                m.predicates.clear()
                m.run(1879)
                self.assertEqual([row for row in m.visibility if row[1] in captions],
                                 [(0x2512, caption, 3, int(bool(enabled & bit)))
                                  for caption, (_, bit) in zip(captions, SHORTCUTS)])
                self.assertEqual([key for key in m.predicates if key in keys], keys)

    def test_all_shortcut_bits_survive_native_save_reload_and_reset(self):
        m = PreferencesDisk(self.image, 0x81201)
        expected = 0x81201
        for key, bit in SHORTCUTS + SHORTCUTS:
            with self.subTest(key=hex(key), before=hex(expected)):
                m.write(VALUE, 0x283f)
                self.assertEqual(m.call(0xcc164, SETTINGS, 0x70724944, key, VALUE), 1)
                expected ^= bit
                self.assertEqual(m.read(PREFS + 0xb34), expected)
                m.call(0xe53e0, PREFS)
                self.assertEqual(struct.unpack_from('<I', m.stored, 0xb2c)[0], expected)
                m.cpu.mem_write(PREFS + 8, b'\xa5' * 0xb3c)
                m.call(0xb5408, PREFS)
                self.assertEqual(m.read(PREFS + 0xb34), expected)
                self.assertEqual(m.read(PREFS + 0x400), 0x1234abcd)
                for option, flag in SHORTCUTS:
                    self.assertEqual(m.call(0x7032c, SETTINGS, option), int(bool(expected & flag)))
        m.write(PREFS + 0xb34, 0xffffffff)
        m.call(0xbeb08, SETTINGS)
        self.assertEqual(m.read(PREFS + 0xb34),
                         0x81201 | (0x01000000 if self.with_alerts else 0))

    def test_extended_getter_preserves_special_2784_and_unassigned_keys(self):
        old, new = preferences(load_image(BASELINE)), preferences(self.image)
        for m in (old, new):
            m.write(SIMPLE_SETTINGS + 0x5c, 0x12345678)
        # 2784 falls through the original conditional branch. The added
        # whitelist must retain that condition and return its original value.
        for kind in (0x70724944, 0x55693332, 0x53747220, 0x11223344):
            for key in (0x2784, 0x25ff, 0x2601, 0x260a, 0x260d):
                with self.subTest(kind=hex(kind), key=hex(key)):
                    old.write(OUT, 0xa5a5a5a5)
                    new.write(OUT, 0xa5a5a5a5)
                    self.assertEqual(new.call(0x732d0, SIMPLE_SETTINGS, kind, key, OUT),
                                     old.call(0x732d0, SIMPLE_SETTINGS, kind, key, OUT))
                    self.assertEqual(new.read(OUT), old.read(OUT))

    def test_customization_groups_all_clock_shortcuts_at_the_clock_indent(self):
        m = MenuCaptions(self.image)
        rows = records(m.groups['Menu'][1][0x278f])
        clock = next(i for i, row in enumerate(rows) if word(row, 24) == 0x25f8)
        self.assertEqual([m.caption(row) for row in rows[clock - 1:clock + 8]], [
            'Extras', '\u00a0Clock', '\u00a0Alarm Clock', '\u00a0Sleep Timer',
            '\u00a0Date & Time', '\u00a0Pomodoro', '\u00a0Contacts',
            '\u00a0Calendar', '\u00a0Notes'])
        self.assertEqual([word(row, 24) for row in rows[clock + 1:clock + 5]],
                         [0x2607, 0x2608, 0x2609, 0x25f4])

    def test_home_shortcuts_have_plain_captions_and_native_actions(self):
        m = MenuCaptions(self.image)
        rows = records(m.groups['Menu'][1][0x2512])
        clock = next(i for i, row in enumerate(rows) if word(row, 32) == 0x25f8)
        added = rows[clock + 1:clock + 5]
        self.assertEqual([m.caption(row) for row in added],
                         ['Alarm Clock', 'Sleep Timer', 'Date & Time', 'Pomodoro'])
        self.assertEqual([word(row, 12) >> 2 for row in added],
                         [2444, 2160, 2142, 2553])
        self.assertEqual([word(row, 32) for row in added],
                         [0x2607, 0x2608, 0x2609, 0x25f4])
        # The generic native Sleep Timer command reads these selected-row
        # fields to obtain its prompt, preference and property provider.
        self.assertEqual([word(added[1], offset) for offset in (16, 24, 28)],
                         [0xff55, 0x27d9, 0x2780])
        self.assertEqual(m.groups['Str '][1][0x6140], b'Pomodoro\0')

    def test_existing_customization_rows_and_reserved_captions_are_preserved(self):
        old, new = directory(load_image(BASELINE)), directory(self.image)
        before = records(old['Menu'][1][0x278f])
        after = records(new['Menu'][1][0x278f])
        self.assertEqual([row for row in after if word(row, 24) not in
                          (0x2607, 0x2608, 0x2609, 0x25f4)], before)
        # These vacant preference slots already have unrelated string keys.
        for key in (0x2607, 0x2608, 0x2609):
            self.assertEqual(new['Str '][1][key], old['Str '][1][key])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    candidate = parser.parse_args().candidate
    HomeIndent.image = load_image(candidate)
    HomeIndent.with_alerts = (candidate.with_suffix('.artifacts') / 'alarm_light.elf').exists()
    unittest.main(argv=[__file__], verbosity=2)
