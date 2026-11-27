"""Check the installed Clock entry and minimal native Pomodoro screen."""
import argparse
import datetime as dt
import json
import struct
import unittest
from pathlib import Path

from verify_runtime import BASELINE
from native_fixture import load_image
from verify_resources import directory, records, word
from native_fixture import APP, RENDERER
from ui_fixture import renderer_event


class ClockScreen(unittest.TestCase):
    def test_clock_has_one_pomodoro_entry_and_preserves_original_rows(self):
        before = records(directory(load_image(BASELINE))['Menu'][1][0x4581])
        after = records(directory(self.image)['Menu'][1][0x4581])
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[:-1], before)
        self.assertEqual(word(after[-1], 12), 2553 * 4 + 2)

    def test_screen_has_only_phase_countdown_and_two_actions(self):
        groups = directory(self.image)
        self.assertTrue(0x6140 in groups['View'][1], 'missing Pomodoro screen')
        view = records(groups['View'][1][0x6140])
        self.assertEqual(len(view), 3)
        self.assertEqual([word(r, 4) for r in view], [0x1580, 0x1280, 0x1800])
        self.assertEqual([word(r, 12) for r in view], [0x6142, 0x6143, 0x6141])
        menu = records(groups['Menu'][1][0x6141])
        self.assertEqual(len(menu), 2)
        self.assertEqual([word(r, 12) for r in menu], [2556 * 4 + 2, 2557 * 4 + 2])
        self.assertEqual(groups['Str '][1][0x6140], b'Pomodoro\0')
        self.assertEqual(groups['Str '][1][0x6145], b'Reset\0')


class NativeClockScreen(unittest.TestCase):
    def make(self):
        from ui_fixture import machine
        return machine(self.image, self.symbols)

    def assert_countdown(self, m, text):
        # Read the surface produced by the real lazy bitmap decoder. Calling
        # our property getter directly would hide a missed cache invalidation.
        m.call(0x97b14, m.countdown)
        bitmap = m.read(m.countdown + 0x11c)
        self.assertNotEqual(bitmap, 0, 'countdown must supply a native Clock-sized bitmap')
        source = m.read(bitmap + 0x1c)
        self.assertEqual(tuple(m.read(source + 0x94 + i * 4) for i in range(4)), (0, 0, 30, 90))
        at, stride, depth = (m.read(source + i) for i in (4, 8, 12))
        self.assertEqual(depth, 2)
        pixels = bytes(m.cpu.mem_read(at, stride * 30))
        # Decode pixels independently, then compare each glyph with the
        # original Clock resources. This catches stride, packing and digit
        # selection errors without reproducing the compositor's byte copies.
        def decode(data, width, height, rowbytes):
            return [[(data[y * rowbytes + x // 4] >> (6 - 2 * (x % 4))) & 3
                     for x in range(width)] for y in range(height)]
        actual = decode(pixels, 90, 30, stride)
        bitmaps = directory(load_image(BASELINE))['BMap'][1]
        glyphs = [bitmaps[0x25b9 if char == ':' else 0x25ba + int(char)] for char in text]
        expected = [[] for _ in range(30)]
        for index, glyph in enumerate(glyphs):
            width, rowbytes = word(glyph, 12), struct.unpack_from('<H', glyph, 16)[0]
            for y, row in enumerate(decode(glyph[28:], width, 30, rowbytes)):
                expected[y].extend(row)
                if index in (1, 2):
                    expected[y].extend([0] * 3)  # native Clock's colon spacing
        self.assertEqual(actual, expected, text)
        m.guards()

    def test_countdown_uses_original_large_clock_digits(self):
        m = self.make()
        self.assert_countdown(m, '25:00')

    def test_minute_rollovers_and_reset_redraw_all_digits(self):
        m = self.make()
        m.run(2553, m.event)
        self.activate(m, 0)
        start = m.now
        event = 0x1007f000
        m.write(event, 0, 0x74696d65, self.symbols['pomo_ui_timer'])
        for elapsed, expected in ((899, '10:01'), (900, '10:00'), (901, '09:59'),
                                  (1439, '01:01'), (1440, '01:00'), (1441, '00:59'),
                                  (1499, '00:01')):
            m.now = start + dt.timedelta(seconds=elapsed)
            m.set_time(m.now)
            m.invalidations.clear()
            renderer_event(m, event)
            self.assertIn((26, 35, 56, 125), m.invalidations)
            self.assert_countdown(m, expected)
        self.activate(m, 1)
        self.assert_countdown(m, '25:00')

    def label(self, m, row):
        item = m.call(0x6cebc, m.read(m.menu + 0xe8), row)
        at = m.call(0x77750, item)
        return bytes(m.cpu.mem_read(at, 16)).split(b'\0', 1)[0].decode()

    def activate(self, m, row):
        m.call(0xc60ac, m.menu, row)
        events = []
        m.hooks.pop(0xaba84, None)
        m.hook(0xab854, lambda: events.append(m.reg(0)) or 1)
        m.hook(0xd915c, m.free)
        m.hook(0xd9a50, m.free)
        m.call(0x1b518, RENDERER)  # original Center/menu event construction
        self.assertEqual(len(events), 1)
        self.assertEqual(m.read(events[0] + 12), (2556 + row) * 4 + 2)
        self.assertEqual(renderer_event(m, events[0]), 1)

    def test_native_factories_and_layout_fit_large_digits_above_actions(self):
        m = self.make()
        self.assertNotEqual(m.row, 0, 'native Menu requires a row-template View resource')
        self.assertEqual(m.read(m.menu + 0xa0), 2)
        self.assertEqual((self.label(m, 0), self.label(m, 1)), ('Start', 'Reset'))
        del m.hooks[0x3be44]
        m.call(0x3bf98, m.display_root, 0)
        for widget, rect in ((m.phase, (4, 4, 22, 156)),
                             (m.countdown, (26, 35, 56, 125)),
                             (m.menu, (60, 0, 108, 160))):
            self.assertEqual(tuple(m.read(widget + 0x58 + i * 4) for i in range(4)), rect)
        m.call(0xcf744, m.phase)  # native lazy text/property lookup
        self.assertEqual(bytes(m.cpu.mem_read(m.read(m.phase + 0xb8), 5)), b'Focus')
        m.call(0x97b14, m.countdown)  # actual native BMap decoder, no replacement
        self.assertNotEqual(m.read(m.countdown + 0x11c), 0)
        m.call(0x6c810, m.read(m.countdown + 0x11c), 0x1001c000)
        self.assertEqual(tuple(m.read(0x1001c000 + i * 4) for i in range(4)), (0, 0, 30, 90))
        self.assert_countdown(m, '25:00')

    def test_real_controller_preserves_its_stock_type_properties(self):
        m = self.make()
        for key, offset in ((0x257c, 0xe4), (0x257d, 0xf4), (0x257e, 0x104)):
            self.assertEqual(m.call(0x75584, RENDERER, 0x54797065, key), RENDERER + offset)

    def test_clock_fourth_row_scrolls_fully_into_view_and_keeps_clock_header(self):
        from ui_fixture import clock_menu_tree
        m = self.make()
        menu, separator, header = clock_menu_tree(m, self.image)
        row = m.read(menu + 0x90)
        height = m.read(row + 0x60) - m.read(row + 0x58)
        self.assertEqual(height, 18)
        self.assertEqual(m.read(menu + 0xa0), 4)
        for index, expected in ((0, (54, 72)), (3, (90, 108)), (0, (54, 72)), (3, (90, 108))):
            m.call(0xc60ac, menu, index)
            self.assertEqual(m.call(0x96fc4, menu), index)
            top = m.read(row + 0x58) + index * height
            self.assertEqual((top, top + height), expected)
            self.assertEqual((m.read(header + 0x58), m.read(header + 0x60)), (0, 52))
            self.assertEqual((m.read(separator + 0x58), m.read(separator + 0x60)), (52, 53))
        m.write(RENDERER + 0x24, menu)
        events = []
        m.hooks.pop(0xaba84, None)
        m.hook(0xab854, lambda: events.append(m.reg(0)) or 1)
        m.hook(0xd915c, m.free)
        m.hook(0xd9a50, m.free)
        m.call(0x1b518, RENDERER)
        events = [event for event in events if m.read(event + 4) == 0x6d656e75]
        self.assertEqual(len(events), 1)
        self.assertEqual(m.read(events[0] + 12), 2553 * 4 + 2)
        self.assertEqual(renderer_event(m, events[0]), 1)
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']), 1)

    def test_center_start_pause_reset_and_menu_preserve_native_navigation(self):
        m = self.make()
        parent = m.read(APP + 0x104)
        self.assertEqual(m.run(2553, m.event), 0xc0000002)
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']), 1)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2554 * 4 + 2)
        self.assertEqual(m.bindings[3, 0x6f], 1896 * 4 + 2)
        self.assertEqual(m.bindings[3, 0x10], 1948 * 4 + 2)
        m.invalidations.clear()
        self.activate(m, 0)
        self.assertEqual(self.label(m, 0), 'Pause')
        self.assertIn(tuple(m.read(m.menu + 0x58 + i * 4) for i in range(4)),
                      m.invalidations, 'Start/Pause label must request a menu redraw')
        self.assertEqual(m.read(m.state + 20), 1)
        m.now += dt.timedelta(seconds=61)
        m.set_time(m.now)
        self.activate(m, 0)
        self.assertEqual(self.label(m, 0), 'Start')
        self.assertEqual(m.invoke('remaining'), 1439)
        self.activate(m, 0)
        self.activate(m, 1)
        self.assertEqual(m.invoke('remaining'), 1500)
        self.assertEqual(m.records(5, 1)[14], 0)
        self.assertEqual(m.read(m.state + 20), 0)
        self.activate(m, 0)
        m.run(1860, m.event)
        self.assertEqual(m.read(APP + 0x104), parent)
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']), 0)
        self.assertEqual(m.read(m.state + 20), 1)
        self.assertEqual(m.records(5, 1)[14], 1)

    def test_own_ui_timer_stops_on_exit_and_stale_events_cannot_restart_it(self):
        m = self.make()
        timer = self.symbols['pomo_ui_timer']
        m.run(2553, m.event)
        self.assertEqual(m.read(timer + 32), RENDERER)
        self.assertEqual(m.read(timer + 28), 0x73746f70)
        self.activate(m, 0)
        self.assertEqual(m.read(timer + 4), 1000)
        self.assertEqual(m.read(timer + 28), 0x72756e20)
        event = 0x1007f000
        m.write(event, 0, 0x74696d65, timer)
        m.now += dt.timedelta(seconds=1)
        m.set_time(m.now)
        m.properties.clear()
        m.invalidations.clear()
        self.assertEqual(renderer_event(m, event), 1)
        self.assertEqual(m.properties, [(0x424d6170, 0x6143)])
        self.assertIn((26, 35, 56, 125), m.invalidations,
                      'native observer must request a countdown redraw')
        m.call(0x97b14, m.countdown)
        self.assert_countdown(m, '24:59')
        # A second tick must invalidate even though the bitmap buffer address
        # remains unchanged. This catches notification/dirty-state regressions.
        m.now += dt.timedelta(seconds=1)
        m.set_time(m.now)
        m.invalidations.clear()
        self.assertEqual(renderer_event(m, event), 1)
        self.assertIn((26, 35, 56, 125), m.invalidations)
        self.assertEqual(bytes(m.cpu.mem_read(m.countdown + 0x68, 1)), b'\0')
        m.call(0x97b14, m.countdown)
        self.assert_countdown(m, '24:58')
        m.run(1860, m.event)
        self.assertEqual(m.read(timer + 28), 0x73746f70)
        m.properties.clear()
        self.assertEqual(renderer_event(m, event), 1)
        self.assertEqual(m.properties, [])
        self.assertEqual(m.read(timer + 28), 0x73746f70)
        m.call(0x91404, 2557 * 4 + 2, 0x10015000)  # stale Reset after leaving
        self.assertEqual(m.read(m.state + 20), 1)

    def test_ui_timer_keeps_interval_and_deadline_without_reconfiguration(self):
        from unicorn import UC_HOOK_CODE
        m = self.make()
        timer = self.symbols['pomo_ui_timer']
        configurations = []
        def configured(cpu, pc, size, context):
            if m.reg(0) == timer:
                configurations.append(m.reg(1))
        m.cpu.hook_add(UC_HOOK_CODE, configured, begin=0xcdd84, end=0xcdd84)
        now_us = 1000000
        m.hook(0x87cec, lambda: now_us)
        # The event is already dequeued when delivered. Supply an empty OS
        # queue and its lock services for the native expired-event purge.
        queue = 0x1001d000
        m.write(0x10018000 + 0x1c, queue)
        m.hook(0x87b88, lambda: 0)
        m.hook(0x87d60, lambda: 0)
        m.run(2553, m.event)
        self.activate(m, 0)
        event, head = 0x1007f000, 0x103cf108
        m.write(event, 0, 0x74696d65, timer)
        for _ in range(6):
            self.assertEqual(m.read(head), timer)
            self.assertEqual(m.read(timer), 0, 'only one owned UI timer link')
            self.assertEqual(m.read(timer + 8), now_us + 1000000)
            now_us += 1000000
            m.now += dt.timedelta(seconds=1)
            m.set_time(m.now)
            # Supply kernel expiry/removal, then execute actual UI delivery
            # and native rearming; the interval must survive expiry.
            m.write(head, 0)
            m.write(timer + 0x18, 0x73746f70, 0x65787069)
            renderer_event(m, event)
            self.assertEqual(m.read(timer + 4), 1000)
        self.assertEqual(configurations, [1000])
        self.assert_countdown(m, '24:54')
        self.activate(m, 0)
        self.assertEqual(m.read(head), 0)
        self.assertEqual(m.read(timer + 28), 0x73746f70)
        m.guards()

    def test_background_expiry_updates_visible_properties_and_reopen_keeps_countdown(self):
        m = self.make()
        m.run(2553, m.event)
        self.activate(m, 0)
        m.now = m.deadline()
        m.set_time(m.now)
        m.deliver_rtc_interrupt()
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(len(m.beeps), 1)
        event = 0x1007f000
        m.write(event, 0, 0x74696d65, self.symbols['pomo_ui_timer'])
        renderer_event(m, event)
        m.call(0xcf744, m.phase)
        self.assertEqual(bytes(m.cpu.mem_read(m.read(m.phase + 0xb8), 5)), b'Break')
        self.assert_countdown(m, '05:00')
        m.run(1860, m.event)
        m.now += dt.timedelta(seconds=17)
        m.set_time(m.now)
        m.run(2553, m.event)
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']), 1)
        self.assertEqual(m.read(RENDERER + 0x24), m.menu)
        timer = self.symbols['pomo_ui_timer']
        self.assertEqual(m.read(timer + 28), 0x72756e20)
        self.assertEqual(m.invoke('remaining'), 283)
        self.assertEqual(self.label(m, 0), 'Pause')
        self.assert_countdown(m, '04:43')
        event = 0x1007f000
        m.write(event, 0, 0x74696d65, timer)
        m.now += dt.timedelta(seconds=1)
        m.set_time(m.now)
        m.invalidations.clear()
        self.assertEqual(renderer_event(m, event), 1)
        self.assertIn((26, 35, 56, 125), m.invalidations)
        self.assert_countdown(m, '04:42')
        m.guards()

    def test_hold_menu_goes_home_and_keeps_the_interval_running(self):
        m = self.make()
        m.supply_services(((2, (0x6c,)),))
        m.run(2553, m.event)
        self.activate(m, 0)
        m.run(2120, m.event)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 1882 * 4 + 2)
        self.assertEqual(m.read(self.symbols['pomo_ui_visible']), 0)
        self.assertEqual(m.read(self.symbols['pomo_ui_timer'] + 28), 0x73746f70)
        self.assertEqual(m.read(m.state + 20), 1)
        self.assertEqual(m.records(5, 1)[14], 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path, nargs='?', default=BASELINE)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    ClockScreen.image = (args.candidate.read_bytes() if args.candidate.suffix == '.osos'
                         else load_image(args.candidate))
    tests = ['ClockScreen']
    if args.native:
        artifacts = args.artifacts or (args.candidate.parent if args.candidate.suffix == '.osos'
                                      else args.candidate.with_suffix('.artifacts'))
        NativeClockScreen.image = ClockScreen.image
        NativeClockScreen.symbols = json.loads((artifacts / 'pomodoro-symbols.json').read_text())
        tests.append('NativeClockScreen')
    unittest.main(argv=[__file__], defaultTest=tests, verbosity=2)
