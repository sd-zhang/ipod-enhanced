"""Check packaged letter-marker recovery after lost or delayed timer delivery.

The installed draw entry, timer publisher/cleanup, invalidation geometry,
dirty-region insertion, marker raster and wheel handling execute ARM code.
Clocks, locks, hardware services, ordinary list-background painting, and
successful repaint posting/delivery are supplied. An actual empty native event
pool drops the final expiry before the repaint service is supplied. This does
not establish idle recovery without another paint or physical display timing.
Run repository Python outside the macOS sandbox for Unicorn JIT access.
"""
import argparse
import unittest
from pathlib import Path

from unicorn import UC_HOOK_CODE

from native_fixture import load_image
from verify_letters import LIST, MODEL, ROW, TASK
from verify_letter_performance import TimedWheel, TIMER_HEAD, STOP, EXPIRED


class PaintedMarker(TimedWheel):
    """Native marker and dirty regions over a supplied white list background."""

    def __init__(self, image):
        super().__init__(image)
        self.wheel()
        self.framebuffer, self.clip = self.graphics()
        context, self.dirty, parent = 0x10030000, 0x10036000, 0x10037000
        self.write(LIST + 0x10, parent)
        self.write(parent + 0x78, 20, 0, 110, 160)
        self.write(context + 0x58, 0, 0, 128, 160)
        self.write(context + 0x8c, self.dirty)
        self.write(self.dirty + 0x14, TASK)
        self.paints, self.posts, self.invalidations = [], [], []
        self.hook(0x5cdc4, self.paint_background)
        del self.hooks[0x9107c]  # Keep native geometry and dirty-region insertion.
        self.hook(0xabcc8, self.post_repaint)
        self.cpu.hook_add(UC_HOOK_CODE, self.trace_invalidation)

    def paint_background(self):
        clip = tuple(self.read(self.reg(2) + 4 * i) for i in range(4))
        self.paints.append((clip, self.active()))
        top, left, bottom, right = clip
        assert 0 <= top <= bottom <= 128 and 0 <= left <= right <= 160
        pixels = bytearray(self.pixels())
        for y in range(top, bottom):
            for x in range(left, right):
                pixels[y * 20 + x // 8] &= ~(128 >> (x % 8))
        self.cpu.mem_write(self.framebuffer, bytes(pixels))

    def post_repaint(self):
        # The native dirty region is retained. Supply successful OS posting;
        # the test separately supplies its later full-region paint delivery.
        self.posts.append('repaint')
        return 1

    def trace_invalidation(self, cpu, pc, size, context):
        if pc == 0x9107c:
            self.invalidations.append(self.reg(0))

    def paint(self, clip=(20, 0, 110, 160)):
        self.write(self.clip, *clip)
        return self.virtual(0x60, MODEL, self.clip)

    def pixels(self):
        return bytes(self.cpu.mem_read(self.framebuffer, 2560))


def drop_final_expiry(machine):
    """Supply native timer dequeue, then run its actual failed publisher."""
    machine.now += 1000
    assert machine.remaining() == 0
    timer = machine.timer_address
    machine.write(TIMER_HEAD, machine.read(timer))
    machine.write(timer, 0)
    machine.write(timer + 0x18, STOP)
    machine.write(0x103c9f3c, 0, 0)
    machine.call(0xd94c4, timer)
    assert machine.read(timer + 0x1c) == EXPIRED


class MarkerRecovery(unittest.TestCase):
    def test_partial_recovery_queues_full_native_region_and_restores_ordinary_input(self):
        m = PaintedMarker(self.image)
        m.paint()
        self.assertTrue(m.active())
        self.assertEqual(m.invalidations, [])
        initial = m.pixels()
        self.assertTrue(any(initial), 'fixture must contain a previously drawn marker')

        drop_final_expiry(m)
        m.now += 30000
        stops = m.calls[0xd3aa0]
        partial_clip = (20, 0, 30, 160)  # Entirely above the old marker.
        m.paint(partial_clip)
        self.assertFalse(m.active(), 'lost expiry left the marker active after paint')
        self.assertEqual(m.paints[-1], (partial_clip, False),
                         'cleanup must precede ordinary list drawing')
        self.assertEqual(m.pixels(), initial,
                         'partial painting must not pretend to erase outside its clip')
        self.assertEqual(m.invalidations, [LIST])
        self.assertEqual(m.posts, ['repaint'])
        region = tuple(m.read(m.dirty + 0x2c + 4 * i) for i in range(4))
        self.assertEqual(region, (20, 0, 110, 160))
        self.assertEqual(m.read(m.dirty + 0x2c + 0x10) & 0xff, 1)
        self.assertEqual(m.read(TIMER_HEAD), 0)
        self.assertEqual(m.calls[0xd3aa0], stops + 1)
        native_tick = ((m.now * 1000) & 0xffffffff) // 1000
        self.assertEqual([m.read(m.state + 4 * i) for i in range(6)],
                         [native_tick, native_tick + 350, native_tick + 200, 1, 0, 0])
        self.assertEqual(m.read(m.state + 124), native_tick)
        self.assertEqual(m.read(m.state + 120), 0)

        m.paint(region)  # Supply delivery of the recorded full-region paint.
        self.assertFalse(any(m.pixels()))
        self.assertEqual(m.invalidations, [LIST])
        self.assertEqual(m.posts, ['repaint'])
        m.timer(0)  # Late/duplicate timer delivery must remain harmless.
        self.assertFalse(m.active())
        self.assertEqual(m.read(TIMER_HEAD), 0)
        self.assertEqual(m.calls[0xd3aa0], stops + 1)
        m.wheel(5, 1)
        self.assertFalse(m.active())
        self.assertEqual(m.read(ROW + 0x8c), 22)

    def test_draw_expiry_boundary_unlinks_running_timer_once_across_clock_wrap(self):
        for start in (1000, 4294400):
            with self.subTest(start_ms=start):
                m = TimedWheel(self.image)
                m.now = start
                m.wheel()
                _, clip = m.graphics()
                m.events.clear()
                calls = dict(m.calls)
                m.now += 999
                m.virtual(0x60, MODEL, clip)
                self.assertTrue(m.active())
                self.assertEqual(m.remaining(), 1000)
                self.assertEqual(dict(m.calls), calls)
                self.assertEqual(m.events, [])

                m.now += 1  # Timer is due but has not been dequeued/delivered.
                m.virtual(0x60, MODEL, clip)
                self.assertFalse(m.active())
                self.assertEqual(m.read(TIMER_HEAD), 0)
                self.assertEqual(m.calls[0xd3aa0], calls[0xd3aa0] + 1)
                self.assertEqual(m.events.count(('invalidate', LIST)), 1)
                m.virtual(0x60, MODEL, clip)
                self.assertEqual(m.calls[0xd3aa0], calls[0xd3aa0] + 1)
                self.assertEqual(m.events.count(('invalidate', LIST)), 1)

    def test_draw_purges_queued_expiry_without_removing_unrelated_event(self):
        m = TimedWheel(self.image)
        m.wheel()
        timer = m.timer_address
        m.now += 1000
        m.write(TIMER_HEAD, 0)
        m.write(timer, 0)
        m.write(timer + 0x18, STOP)
        m.write(timer + 0x1c, EXPIRED)
        queue, node1, node2, message1, message2 = (
            TASK + 0x100, 0x10034000, 0x10034100, 0x10034200, 0x10034300)
        m.write(queue + 0x40, node1, node2)
        m.write(node1, node2)
        m.write(node1 + 0xc, message1, 1)
        m.write(node2 + 0xc, message2, 1)
        for message, token in ((message1, timer), (message2, timer + 1000)):
            m.write(message + 4, 0x74696d65)
            m.write(message + 0x10, token)
        released = []
        m.hook(0xb1774, lambda: released.append(m.reg(0)))
        m.hook(0x8f7b4, lambda: None)  # OS queue-notification boundary.
        _, clip = m.graphics()
        m.virtual(0x60, MODEL, clip)
        self.assertFalse(m.active())
        self.assertEqual(released, [message1])
        self.assertEqual(m.read(node1 + 0xc), 0)
        self.assertEqual(m.read(node1 + 0x10), 0)
        self.assertEqual(m.read(node2 + 0xc), message2)
        self.assertEqual(m.read(node2 + 0x10), 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    MarkerRecovery.image = load_image(args.candidate)
    unittest.main(argv=[__file__], defaultTest='MarkerRecovery', verbosity=2)
