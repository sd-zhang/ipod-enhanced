"""Native Sleep Timer shortcut dispatch; graphics/OS timer boundary supplied."""
import argparse
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_actions
from native_fixture import APP, load_image
from verify_actions_widgets import ActionsVMDisplay
from verify_resources import directory, word

SETTINGS, QUERY, PREFS = 0x10060000, 0x10062000, 0x10064000


def machine(image):
    with patch.object(verify_actions, 'ScriptVM', lambda image: ActionsVMDisplay(image, settings=SETTINGS)):
        m = verify_actions.menu_machine(image)
    assert m.read(m.menu + 0xa0) >= 5, 'Sleep Timer row is missing'
    item = m.call(0x6cebc, m.read(m.menu + 0xe8), 4)
    assert m.read(m.read(item + 0x10) + 8) == 2160 * 4 + 2, 'Sleep Timer command is missing'
    m.write(SETTINGS + 0x58, QUERY)
    m.write(QUERY, PREFS)
    # Property-change delivery uses the OS application event queue. Its live
    # task/scheduler are absent; the setter/getter and value storage stay native.
    for address in (0xc77d4, 0xcb554, 0xcdde8, 0xc7d20):
        m.hook(address, lambda: 0)
    m.call(0xe7658, m.global_slot(0x9ba), m.object(27, SETTINGS))
    m.supply_services(((2, (0x5f,)), (3, (0xa0,))))
    m.prepare_renderer()
    m.call(0xdd19c, m.RENDERER, 0x6100, 0, 30, 300)
    m.run(2536, m.event)
    m.run(1888, m.event)
    m.call(0xc60ac, m.menu, 4)
    m.timer_requests = []
    m.submenu_selections = []
    m.hook(m.method_address(3, 0xa0), lambda: m.submenu_selections.append(
        (m.reg(1), m.reg(2), m.reg(3))) or 0)
    m.hook(m.method_address(25, 0xd4), lambda: m.timer_requests.append(m.reg(1)) or 0)
    return m


class SleepShortcut(unittest.TestCase):
    def test_row_reuses_native_submenu_command_property_and_value_provider(self):
        g = directory(self.image)
        old = g['Menu'][1][0x4581][44:84]
        menu = g['Menu'][1][0x6102]
        self.assertGreaterEqual(word(menu, 0), 5)
        row = bytearray(menu[164:204])
        self.assertEqual(word(row, 4), 0x22)
        self.assertEqual(struct.unpack_from('<H', row, 22)[0], 0)
        row[4:8], row[22:24] = old[4:8], old[22:24]
        self.assertEqual(row, old)
        self.assertEqual(word(menu, 176), 2160 * 4 + 2)

    def test_center_opens_stock_sleep_menu_and_back_returns_to_actions(self):
        m = machine(self.image)
        parent = m.read(APP + 0x104)
        events = []
        del m.hooks[0xaba84]
        m.hook(0xab854, lambda: events.append(m.reg(0)) or 1)
        m.hook(0xd915c, m.free)
        m.hook(0xd9a50, m.free)
        m.call(0x1b518, m.RENDERER)
        self.assertEqual(len(events), 1)
        self.assertEqual(m.read(events[0] + 12), 2160 * 4 + 2)
        self.assertEqual(m.call(0x80870, m.RENDERER, events[0]), 1)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2162 * 4 + 2)
        self.assertEqual(m.timer_requests, [])
        m.run(1860, m.event)
        self.assertEqual(m.read(APP + 0x104), parent)
        self.assertEqual(m.timer_requests, [])

    def test_all_stock_choices_apply_minutes_and_return_to_actions(self):
        m = machine(self.image)
        item = m.call(0x6cebc, m.read(m.menu + 0xe8), 4)
        self.assertEqual(m.read(item + 0x18), SETTINGS)
        for key, minutes in ((0x27da, 65535), (0x27db, 15), (0x27dc, 30),
                             (0x27dd, 60), (0x27de, 90), (0x27df, 120)):
            with self.subTest(minutes=minutes):
                m.hooks.pop(0x1b6f8, None)
                parent = m.read(APP + 0x104)
                m.run(2160, m.event)
                # The immutable stock submenu supplies property5 of its chosen
                # row. The generic menu's graphics/rebinding are a boundary.
                m.hook(0x1b6f8, lambda: key)
                m.run(2167, m.event)
                self.assertEqual(m.timer_requests[-1], minutes)
                self.assertEqual(m.read(APP + 0x104), parent)
                # Reopening must preselect the same stock setting and must
                # neither reset nor cancel the countdown until a choice is made.
                m.hooks.pop(0x1b6f8, None)
                previous_requests = m.timer_requests[:]
                m.run(2160, m.event)
                self.assertEqual(m.submenu_selections[-1], (0x2789, key, 5))
                m.run(1860, m.event)
                self.assertEqual(m.timer_requests, previous_requests)
                self.assertEqual(m.read(APP + 0x104), parent)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    SleepShortcut.image = load_image(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
