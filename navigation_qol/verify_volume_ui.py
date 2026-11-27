"""Native packaged Volume Limit widgets, Pixo commands and menu history.

Uses the storage/audio boundaries from verify_volume and the existing native
menu VM fixture. Rendering animation and OS event delivery are supplied.
Run repository Python outside the macOS sandbox for Unicorn's JIT.
"""
import argparse
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_actions
from native_fixture import ScriptVM, APP, load_image
from verify_resources import directory, records, word
from verify_volume import VolumeMachine, PREFS, UI, RENDERER, PARENT, DISPLAY, NAMES


class VolumeVM(VolumeMachine, ScriptVM):
    pass


def machine(image):
    with patch.object(verify_actions, 'ScriptVM', VolumeVM):
        m = verify_actions.menu_machine(image)
    # The existing fixture supplies wheel registration (61); supply its paired
    # OS-list removal (6f) as well. Native history/leave dispatch remains real.
    m.supply_services(((2, (0x6f,)),))
    resources = directory(image)
    addresses = {(int.from_bytes(tag.encode(), 'big'), key): image.find(data)
                 for tag, (_, entries) in resources.items() for key, data in entries.items()}
    m.resource_requests = []
    def resource():
        key = m.reg(0), m.reg(1)
        m.resource_requests.append(key)
        return addresses.get(key, 0)
    m.hook(0x72a88, resource)
    row = next(row for row in records(resources['View'][1][0x250d]) if word(row, 12) == 0x6120)
    m.cpu.mem_write(0x1001a000, row[4:])
    m.display_root = m.call(0x4c454, 0, 0, 0x1001a000, 0x707a8)
    count, children = m.read(m.display_root + 0x6c), m.read(m.display_root + 0x70)
    assert count in (2, 3)
    m.widgets = {m.read(m.read(children + 4 * i) + 0x1c) & 0xffff:
                 m.read(children + 4 * i) for i in range(count)}
    m.slider = m.widgets[0x6121]
    m.caption = m.widgets.get(0x6123)
    assert m.read(m.slider) == 0x25250
    m.call(0x660f0, m.display_root)
    m.write(PARENT, 0x272a0)
    m.write(PARENT + 0x88, DISPLAY)
    m.write(m.display_root + 0x10, PARENT)
    m.write(RENDERER + 0x18, m.display_root)
    m.shown = []
    m.hook(m.method_address(3, 0x91), lambda: m.shown.append(m.reg(1)) or 0)
    m.write(PREFS + 0xb48, 120)
    m.write(UI + 0x184c, 120)
    return m


class VolumeUI(unittest.TestCase):
    native_layout = False

    def test_native_layout_places_controls_inside_content_area(self):
        m = machine(self.image)
        m.write(m.display_root + 0x58, 0, 0, 108, 160)
        del m.hooks[0x3be44]
        # Execute the native layout walk, which caches each resolved rectangle
        # before the following sibling uses it (as stock Contrast/Volume do).
        m.call(0x3bf98, m.display_root, 0)
        if self.native_layout:
            self.assertEqual(set(m.widgets), {0x25b4, 0x6121, 0x25b5})
            geometry = ((m.widgets[0x25b4], (48, 6, 61, 16)),
                        (m.slider, (50, 20, 59, 136)),
                        (m.widgets[0x25b5], (48, 140, 61, 154)))
        else:
            geometry = ((m.slider, (50, 25, 59, 135)), (m.caption, (68, 5, 80, 155)))
        for widget, expected in geometry:
            self.assertEqual(tuple(m.read(widget + 0x58 + i) for i in range(0, 16, 4)), expected)
        if self.native_layout:
            self.assertNotIn((0x53747220, 0x6123), m.resource_requests)
            return
        self.assertEqual(directory(self.image)['Str '][1][0x6123], b'Center saves; Menu cancels.\0')
        self.assertIn((0x53747220, 0x6123), m.resource_requests)
        caption = b'Center saves; Menu cancels.\0'
        self.assertEqual(bytes(m.cpu.mem_read(m.read(m.caption + 0xb8), len(caption))), caption)

    def test_settings_entry_and_native_widget_range_are_packaged(self):
        m = machine(self.image)
        menu = directory(self.image)['Menu'][1][0x278d]
        self.assertEqual(word(menu, 0), 17 if self.with_alerts else 16)
        row = next(r for r in records(menu) if word(r, 20) & 0xffff == 0x6120)
        self.assertEqual((word(row, 4), word(row, 12), word(row, 20)),
                         (0x22 if self.native_layout else 0x20, 2547 * 4 + 2, 0x6120))
        self.assertEqual([m.read(m.slider + offset) for offset in (0x6c, 0x70, 0x74)], [256, 0, 256])
        self.assertEqual(m.read(m.slider + 0x1c) & 0xffff, 0x6121)

    def test_settings_volume_entry_uses_the_actual_contrast_chevron(self):
        if not self.native_layout:
            self.skipTest('native appearance revision only')
        from verify_actions_widgets import ActionsDisplay
        m = ActionsDisplay(self.image, settings=0x10060000, view=0x278e)
        service, vtable, getter = 0x1006f000, 0x1006f100, 0x1006f200
        m.write(service, vtable)
        m.write(vtable + 0x50, getter - vtable)
        m.hook(0x6c770, lambda: service)
        requested = []
        def lookup():
            key = m.reg(1), m.reg(2)
            requested.append(key)
            m.write(m.reg(3), m.resource_addresses.get(key, 0))
            return 1
        m.hook(getter, lookup)
        icons = []
        rows = records(directory(self.image)['Menu'][1][0x278d])
        indices = [next(i for i, r in enumerate(rows) if word(r, 20) & 0xffff == key)
                   for key in (0x6120, 0xff86)]  # Volume Limit, Contrast
        for row in indices:
            item = m.call(0x6cebc, m.read(m.menu + 0xe8), row)
            icons.append(m.call(0x6ae88, item))
            self.assertEqual(m.call(0x6aec0, item), 0)
        self.assertNotEqual(icons[0], 0)
        self.assertEqual(icons[0], icons[1])
        self.assertEqual(requested, [(0x424d6170, 0x25ae)] * 2)

    def test_open_wheel_and_menu_cancel_leave_saved_cap_unchanged(self):
        m = machine(self.image)
        parent = m.read(APP + 0x104)
        self.assertEqual(m.run(2547, m.event), 0xc0000002)
        self.assertEqual(m.shown[-1], 0x6120)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 2548 * 4 + 2)
        self.assertEqual(m.read(m.slider + 0x6c), 120)
        self.assertEqual(m.bindings[3, 0x6f], 2552 * 4 + 2)
        wheel = m.object(12, 0x1001e000)
        m.call(0xe7658, 0x10010204, wheel)  # retain the reused native event wrapper
        for delta, expected in ((1, 121), (-1, 120), (0, 120), (8, 128), (-8, 120)):
            m.hook(m.method_address(12, 0xbb), lambda: delta)
            self.assertEqual(m.run(2549, wheel), 0xc0000002)
            self.assertEqual(m.read(m.slider + 0x6c), expected)
        m.run(2550, 3, m.event)  # tagged integer1, like a single wheel step
        self.assertEqual(m.read(m.slider + 0x6c), 121)
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        self.assertEqual(m.io, [])
        m.run(1860, m.event)
        self.assertEqual(m.read(APP + 0x104), parent)
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        self.assertEqual(m.io, [])
        m.run(2547, m.event)
        self.assertEqual(m.read(m.slider + 0x6c), 120)

    def test_center_saves_then_returns_exactly_one_level(self):
        m = machine(self.image)
        parent = m.read(APP + 0x104)
        m.run(2547, m.event)
        m.draft(90)
        self.assertEqual(m.run(2552, m.event), 0xc0000002)
        self.assertEqual(m.read(PREFS + 0xb48), 90)
        self.assertEqual(m.read(APP + 0x104), parent)
        self.assertEqual(struct.unpack('<5I', m.files[NAMES[0]])[3], 90)

    def test_failed_save_stays_on_slider_and_retry_succeeds(self):
        m = machine(self.image)
        m.run(2547, m.event)
        node = m.read(APP + 0x104)
        m.draft(90)
        m.failure = 'open'
        self.assertEqual(m.run(2552, m.event), 0xc0000002)
        self.assertEqual(m.read(APP + 0x104), node)
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        m.failure = None
        m.run(2552, m.event)
        self.assertNotEqual(m.read(APP + 0x104), node)
        self.assertEqual(m.read(PREFS + 0xb48), 90)

    def test_hold_menu_cancels_draft_and_returns_home(self):
        m = machine(self.image)
        m.supply_services(((2, (0x6c,)),))
        m.run(2547, m.event)
        m.draft(90)
        m.run(2120, m.event)
        self.assertEqual(m.read(m.read(APP + 0x104) + 8), 1882 * 4 + 2)
        self.assertEqual(m.read(PREFS + 0xb48), 120)
        self.assertEqual(m.io, [])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--native-layout', action='store_true')
    args = parser.parse_args()
    VolumeUI.image = load_image(args.candidate)
    VolumeUI.native_layout = args.native_layout
    VolumeUI.with_alerts = (args.candidate.with_suffix('.artifacts') / 'alarm_light.elf').exists()
    unittest.main(argv=[__file__], verbosity=2)
