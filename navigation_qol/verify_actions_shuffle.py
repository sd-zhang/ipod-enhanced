"""Exercise the packaged Shuffle row through native UI, VM and settings code.

The display fixture supplies graphics/OS services. OS event delivery, storage
dirty notifications, final resource lookup and playlist reorder are supplied.
Actual event construction/handling, selected-row getters, command2155, setting
cycle, preference/queue flags, value-label lookup and redraw dispatch run as ARM.
Run with .venv/bin/python outside the macOS sandbox (Unicorn JIT).
"""
import argparse
import struct
import unittest
from pathlib import Path

from native_fixture import load_image
from verify_actions_widgets import ActionsVMDisplay
from verify_resources import directory
from unicorn import UC_HOOK_CODE


class NativeActionsShuffle(unittest.TestCase):
    def check_cycle(self, playing):
        settings, query, prefs, queue, db, header, track = [0x10060000 + i * 0x2000 for i in range(7)]
        m = ActionsVMDisplay(self.image, settings=settings)
        m.write(settings + 0x58, query)
        m.write(query, prefs)
        m.write(prefs + 4, db)
        if playing:
            m.write(query + 0x44, queue)
            m.write(queue + 8, prefs)
            m.write(queue + 0x24, header)
            m.cpu.mem_write(header + 0x2e, struct.pack('<H', 3))
            m.write(db + 0x2a4, queue, track)
        changes = []
        m.hook(0x4b5cc, lambda: changes.append(('reorder', *(m.reg(i) for i in range(4)))) or 0)
        m.hook(0xc77d4, lambda: 0)  # observer event delivery
        m.hook(0xcb554, lambda: 0)  # database dirty notification
        m.hook(0xcdde8, lambda: 0)  # storage dirty notification
        m.write(query + 0x3c, 0x1001f000)
        m.hook(0x1001f000, lambda: changes.append(('callback', m.reg(0), m.reg(1))) or 0)
        m.call(0xe7658, m.global_slot(0x9ba), m.object(27, settings))
        m.prepare_renderer()
        m.call(0xdd19c, m.RENDERER, 0x6100, 0, 30, 300)
        m.call(0xc60ac, m.menu, 2)
        self.assertEqual(m.call(0x96fc4, m.menu), 2)
        item = m.call(0x6cebc, m.read(m.menu + 0xe8), 2)
        # Provider resolution ran during actual item construction.
        self.assertEqual(m.read(item + 0x18), settings)
        native_events, invalidations = [], []
        del m.hooks[0xaba84]  # execute real event construction
        m.hook(0xab854, lambda: native_events.append(m.reg(0)) or 1)
        m.hook(0xd915c, m.free)
        m.hook(0xd9a50, m.free)

        def trace(cpu, pc, size, context):
            if pc == 0x9100c:
                invalidations.append((m.reg(0), struct.unpack('<4I', cpu.mem_read(m.reg(1), 16))))
        m.cpu.hook_add(UC_HOOK_CODE, trace)

        # Final localized-resource service boundary; item→provider→property
        # getter and the Off/Songs/Albums key selection all execute natively.
        service, vtable, getter = 0x1006f000, 0x1006f100, 0x1006f200
        m.write(service, vtable)
        m.write(vtable + 0x50, getter - vtable)
        m.hook(0x6c770, lambda: service)
        strings = directory(self.image)['Str '][1]
        requested = []
        def lookup_string():
            self.assertEqual(m.reg(1), 0x53747220)
            key = m.reg(2)
            requested.append(key)
            m.write(m.reg(3), self.image.find(strings[key]))
            return 1
        m.hook(getter, lookup_string)

        for expected in (1, 2, 0, 1, 2, 0):
            with self.subTest(playing=playing, mode=expected):
                native_events.clear()
                invalidations.clear()
                changes.clear()
                m.call(0x1b518, m.RENDERER)
                self.assertEqual(len(native_events), 1)
                event, = native_events
                self.assertEqual([m.read(event + o) for o in (4, 8, 12, 16, 20)],
                                 [0x6d656e75, 0x4d6e7543, 2155 * 4 + 2, m.menu, item])
                self.assertEqual(bytes(m.cpu.mem_read(event + 0x19, 1)), b'\x22')
                self.assertIn((m.menu, (36, 0, 54, 150)), invalidations)
                # Deliver the actual event to the actual registered renderer
                # handler; native script VM and settings handler continue here.
                self.assertEqual(m.call(0x80870, m.RENDERER, event), 1)
                self.assertEqual(bytes(m.cpu.mem_read(settings + 0x1c, 1)), bytes([expected]))
                self.assertEqual(m.call(0x1b6f8, m.RENDERER, 5), 0x27b5)
                self.assertEqual(m.call(0x1b6e8, m.RENDERER, 6), 0x2780)
                self.assertEqual(bytes(m.cpu.mem_read(prefs + 0x20, 1)), bytes([expected != 0]))
                self.assertEqual(bytes(m.cpu.mem_read(prefs + 0x92, 1)), bytes([expected == 2]))
                self.assertEqual(bytes(m.cpu.mem_read(prefs + 0xb44, 1)), b'\x01')
                if playing:
                    self.assertEqual(m.read(queue + 0xe4) & 1, bool(expected))
                    self.assertIn(('reorder', queue, track, int(expected == 2), 0), changes)
                    self.assertEqual(changes[-1], ('callback', 0, 0))
                else:
                    self.assertFalse(any(c[0] == 'reorder' for c in changes))
                ptr = m.call(0x6aec0, item)
                label = bytes(m.cpu.mem_read(ptr, 16)).split(b'\0', 1)[0].decode()
                self.assertEqual(label, ('Off', 'Songs', 'Albums')[expected])
                self.assertEqual(requested[-1], 0x27b6 + expected)

    def test_shuffle_with_active_queue_preserves_current_track_in_reorder_dispatch(self):
        self.check_cycle(playing=True)

    def test_shuffle_without_active_queue_updates_setting_and_displayed_value(self):
        self.check_cycle(playing=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    NativeActionsShuffle.image = load_image(args.candidate)
    unittest.main(argv=[__file__], defaultTest='NativeActionsShuffle', verbosity=2)
