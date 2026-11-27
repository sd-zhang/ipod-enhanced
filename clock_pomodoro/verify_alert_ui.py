"""Exercise Alarm Backlight through the native Settings menu and command VM.

The stock menu factories, Center event, command2155, property getter/setter,
localized value selection and redraw dispatch execute from the candidate.
Graphics, OS notification delivery and the final resource service are supplied.
Run with the repository virtualenv outside the macOS sandbox (Unicorn JIT).
"""
import argparse
import unittest
from pathlib import Path

from verify_runtime import load_image
from verify_home import preferences, SETTINGS, PREFS, OUT, VALUE
from native_fixture import APP
from verify_actions_widgets import ActionsVMDisplay
from verify_resources import directory, records
from verify_alert_package import ALERT_ROW, alert_caption
from unicorn import UC_HOOK_CODE

DISABLED, KEY = 0x01000000, 0x6150


class AlertUI(unittest.TestCase):
    def test_caption_retains_the_exact_native_settings_row_and_allocation(self):
        # Accept historical r27 and the reviewed rename, with the same Str/prID
        # key and native command. The helper pins the caption address and span.
        alert_caption(self.image)
        rows = records(directory(self.image)['Menu'][1][0x278d])
        self.assertEqual(len(rows), 17)
        self.assertEqual(rows[6], ALERT_ROW)

    def test_native_settings_center_cycles_displayed_value_without_leaving_or_playback(self):
        settings, bridge, prefs = 0x10060000, 0x10062000, 0x10064000
        for initial in (0x81201, 0x01ffffff):
            with self.subTest(initial=hex(initial)):
                m = ActionsVMDisplay(self.image, settings=settings, view=0x278e)
                m.write(settings + 0x58, bridge)
                m.write(bridge, prefs)
                m.write(prefs + 0xb34, initial)
                m.call(0xe7658, m.global_slot(0x9ba), m.object(27, settings))
                for address in (0xc77d4, 0xcb554, 0xcdde8):
                    m.hook(address, lambda: 0)  # OS observer/dirty delivery
                def forbidden():
                    raise AssertionError('Settings toggle attempted playback or illumination')
                for address in (0x9d92c, 0x537bc, 0xa91b0, 0xa8be8, 0x3408c, 0x99afc):
                    m.hook(address, forbidden)
                m.prepare_renderer()
                m.call(0xdd19c, m.RENDERER, 0x278e, 0, 30, 300)
                m.call(0xc60ac, m.menu, 6)
                self.assertEqual(m.call(0x96fc4, m.menu), 6)
                item = m.call(0x6cebc, m.read(m.menu + 0xe8), 6)
                self.assertEqual(m.read(item + 0x18), settings)
                native_events, invalidations = [], []
                del m.hooks[0xaba84]
                def post_event():
                    event = m.reg(0)
                    if m.read(event + 4) != 0x6d656e75:
                        # Row6 is below this fixture's six visible rows. The
                        # real viewport sends PSsv via c4254/aba04 before MnuC;
                        # final scroll-event delivery belongs to the OS boundary.
                        self.assertEqual(m.read(event + 4), 0x50537376)
                        return 1
                    native_events.append(event)
                    return 1
                m.hook(0xab854, post_event)
                m.hook(0xd915c, m.free)
                m.hook(0xd9a50, m.free)
                def trace(cpu, pc, size, context):
                    if pc == 0x9100c:
                        invalidations.append(m.reg(0))
                m.cpu.hook_add(UC_HOOK_CODE, trace)

                service, table, getter = 0x1006f000, 0x1006f100, 0x1006f200
                m.write(service, table)
                m.write(table + 0x50, getter - table)
                m.hook(0x6c770, lambda: service)
                strings = directory(self.image)['Str '][1]
                keys = []
                def lookup():
                    self.assertEqual(m.reg(1), 0x53747220)
                    key = m.reg(2)
                    keys.append(key)
                    m.write(m.reg(3), self.image.find(strings[key]))
                    return 1
                m.hook(getter, lookup)
                history = m.read(APP + 0x104)
                for iteration in range(7):
                    expected = initial ^ (DISABLED if iteration % 2 else 0)
                    self.assertEqual(m.read(prefs + 0xb34), expected)
                    pointer = m.call(0x6aec0, item)
                    label = bytes(m.cpu.mem_read(pointer, 4)).split(b'\0', 1)[0]
                    self.assertEqual(label, b'Off' if expected & DISABLED else b'On')
                    self.assertEqual(keys[-1], 0x27a2 if expected & DISABLED else 0x27a1)
                    if iteration == 6:
                        break
                    native_events.clear()
                    invalidations.clear()
                    m.call(0x1b518, m.RENDERER)
                    self.assertEqual(len(native_events), 1,
                                     [[hex(m.read(e + o)) for o in (4, 8, 12, 16, 20)]
                                      for e in native_events])
                    event, = native_events
                    self.assertEqual([m.read(event + offset) for offset in (4, 8, 12, 16, 20)],
                                     [0x6d656e75, 0x4d6e7543, 2155 * 4 + 2, m.menu, item])
                    self.assertIn(m.menu, invalidations)
                    self.assertEqual(m.call(0x80870, m.RENDERER, event), 1)
                    self.assertEqual(m.read(APP + 0x104), history)
                    self.assertEqual(m.read(m.RENDERER + 0x24), m.menu)
                    self.assertEqual(m.call(0x96fc4, m.menu), 6)
                    self.assertEqual(m.read(prefs + 0xb44), 1)

    def test_explicit_values_and_unknown_inputs_preserve_other_preferences(self):
        for initial in (0, 0x81201, 0xffffffff):
            m = preferences(self.image, initial)
            for value, disabled in ((0x27a2, True), (0x27a2, True), (0x27a1, False),
                                    (0x27a1, False), (0x283f, True), (0x283f, False)):
                before = m.read(PREFS + 0xb34)
                m.write(PREFS + 0xb44, 0)
                m.write(VALUE, value)
                self.assertEqual(m.call(0xcc164, SETTINGS, 0x70724944, KEY, VALUE), 1)
                expected = (before & ~DISABLED) | (DISABLED if disabled else 0)
                self.assertEqual(m.read(PREFS + 0xb34), expected)
                self.assertEqual(m.read(PREFS + 0xb44), int(expected != before))
                self.assertEqual(m.call(0x732d0, SETTINGS, 0x70724944, KEY, OUT), 1)
                self.assertEqual(m.read(m.read(OUT)) & 0xffff, 0x27a2 if disabled else 0x27a1)
            snapshot = bytes(m.cpu.mem_read(PREFS, 0xb4c))
            for tag, key, value, pointer in ((0x70724944, KEY, 0x9999, VALUE),
                                            (0x53747220, KEY, 0x283f, VALUE),
                                            (0x70724944, 0x6151, 0x283f, VALUE),
                                            (0x70724944, KEY, 0, 0)):
                m.write(VALUE, value)
                self.assertEqual(m.call(0xcc164, SETTINGS, tag, key, pointer), 0)
                self.assertEqual(bytes(m.cpu.mem_read(PREFS, 0xb4c)), snapshot)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    AlertUI.image = load_image(parser.parse_args().candidate)
    unittest.main(argv=[__file__], verbosity=2)
