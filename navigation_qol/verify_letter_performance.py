"""Native timer lifecycle and pixel/cost comparisons against immutable r7.

OS locks, timer interrupts/event delivery, display locks and list text are
supplied boundaries. Native timer list insertion/removal, deadlines, expiry
handler and monochrome rectangle raster execute ARM code. These are not device
timings. Run repository Python outside the sandbox for Unicorn JIT access.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import unittest

from unicorn import UC_HOOK_CODE
from verify_letters import WheelMachine, LIST, MODEL, EVENT
from native_fixture import load_image

REFERENCE = Path('build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r7.ipsw')
RUN, STOP, EXPIRED = 0x72756e20, 0x73746f70, 0x65787069
TIMER_HEAD = 0x103cf108


class TimedWheel(WheelMachine):
    def __init__(self, image):
        super().__init__(image)
        for address in (0xcdd84, 0xd2bd0, 0xd3aa0):
            del self.hooks[address]
        # Locks, hardware timer wakeup and event-pool release only. Keep the
        # native interval, start, stop and timer list operations executable.
        for address in (0x9f5d8, 0x9fce4, 0x9fcdc, 0x87b88, 0x87d60, 0xb1774):
            self.hook(address, lambda: None)
        self.write(TIMER_HEAD, 0)
        self.calls = Counter()

        def trace(cpu, pc, size, context):
            if pc in (0xcdd84, 0xd2bd0, 0xd3aa0):
                self.calls[pc] += 1
        self.cpu.hook_add(UC_HOOK_CODE, trace)

    @property
    def state(self):
        return self.read(LIST + 0xe4)

    @property
    def timer_address(self):
        return self.state + 48

    def active(self):
        return bool(self.state and self.read(self.state + 92))

    def remaining(self):
        assert self.read(TIMER_HEAD) == self.timer_address
        return (self.read(self.timer_address + 8) - self.now * 1000) & 0xffffffff

    def deliver(self):
        # Supply delivery of the single due timer, after the OS removed it
        # from the timer list. The native event handler owns subsequent rearm.
        timer = self.timer_address
        assert self.read(TIMER_HEAD) == timer
        assert ((self.now * 1000 - self.read(timer + 8)) & 0xffffffff) < 0x80000000
        self.write(TIMER_HEAD, self.read(timer))
        self.write(timer, 0)
        self.write(timer + 0x18, STOP)
        self.write(timer + 0x1c, EXPIRED)
        return self.timer(0)


class LetterTimerCost(unittest.TestCase):
    def test_redraw_recovers_lost_final_expiry_without_rearming(self):
        for start in (1000, 4294400):
            for clip in ((20, 0, 128, 150), (20, 0, 30, 30)):
                with self.subTest(start=start, clip=clip):
                    m = TimedWheel(self.image)
                    m.now = start
                    m.wheel()
                    timer = m.timer_address
                    m.now += 1000
                    self.assertEqual(m.remaining(), 0)
                    m.write(TIMER_HEAD, m.read(timer))
                    m.write(timer, 0)
                    m.write(timer + 0x18, STOP)
                    m.write(0x103c9f3c, 0, 0)  # exhaust actual event pool
                    m.call(0xd94c4, timer)
                    self.assertEqual(m.read(timer + 0x1c), EXPIRED)
                    m.now += 30000
                    frame, clip_at = m.graphics(clip)
                    m.events.clear()
                    m.virtual(0x60, MODEL, clip_at)
                    self.assertFalse(m.active(), 'expired marker survived redraw')
                    self.assertEqual(m.read(TIMER_HEAD), 0)
                    self.assertEqual(m.read(m.state + 12), 1)  # ordinary acceleration
                    self.assertEqual(m.events.count(('invalidate', LIST)), 1)
                    m.virtual(0x60, MODEL, clip_at)
                    self.assertEqual(m.events.count(('invalidate', LIST)), 1)
                    self.assertFalse(any(m.cpu.mem_read(frame, 2560)))
                    m.wheel(80, 10)
                    self.assertTrue(m.active())
                    self.assertEqual(m.remaining(), 1000000)

    def test_busy_input_avoids_redundant_interval_resets_and_keeps_real_deadline_current(self):
        m = TimedWheel(self.image)
        m.wheel()
        for _ in range(49):
            m.wheel(5, 20)
        self.assertTrue(m.active())
        self.assertEqual(m.calls[0xcdd84], 1, 'unchanged interval was reset on every packet')
        self.assertLessEqual(m.calls[0xd3aa0], 51)
        self.assertEqual(m.remaining(), 1000000)
        m.now += 999
        self.assertTrue(m.active())
        self.assertEqual(m.remaining(), 1000)
        m.now += 1
        m.deliver()
        self.assertFalse(m.active())
        self.assertEqual(m.read(TIMER_HEAD), 0)

    def test_stale_delivery_and_new_input_restore_full_interval_then_expire(self):
        m = TimedWheel(self.image)
        m.wheel()
        m.wheel(5, 990)
        m.now += 110
        m.timer(0)  # stray delivery from an older arm must not end this mode
        self.assertTrue(m.active())
        self.assertEqual(m.remaining(), 890000)
        m.wheel(5, 10)
        self.assertEqual(m.remaining(), 1000000)
        self.assertEqual(m.read(m.timer_address + 4), 1000)
        m.now += 1100  # ordinary late delivery, after the new real deadline
        m.deliver()
        self.assertFalse(m.active())

    def test_continuous_input_and_reversal_do_not_lose_expiry_across_clock_wrap(self):
        m = TimedWheel(self.image)
        m.now = 4294400
        m.wheel()
        for packet in range(150):
            m.wheel(-5 if packet % 2 else 5, 20)
            if m.remaining() == 0:
                m.deliver()
            self.assertTrue(m.active())
        self.assertEqual(m.calls[0xcdd84], 1)
        self.assertEqual(m.remaining(), 1000000)
        m.now += 1000
        m.deliver()
        self.assertFalse(m.active())
        self.assertEqual(m.read(TIMER_HEAD), 0)

    def test_zero_delta_does_not_extend_inactivity(self):
        m = TimedWheel(self.image)
        m.wheel()
        last_input = m.read(m.state + 100)
        for _ in range(9):
            m.wheel(0, 100)
        self.assertEqual(m.read(m.state + 100), last_input)
        m.now += 100
        m.deliver()
        self.assertFalse(m.active())

    def test_hide_or_key_cancels_real_timer_and_reentry_restarts_it(self):
        for close in ('hide', 'key'):
            with self.subTest(close=close):
                m = TimedWheel(self.image)
                m.write(LIST + 0x68, 0x286d8, 0, 0, 0)
                m.wheel()
                if close == 'hide':
                    m.virtual(0x94, 0)
                    m.virtual(0x94, 1)
                else:
                    m.write(EVENT + 0x10, 0x57 << 16)
                    m.virtual(0x160, EVENT)
                self.assertFalse(m.active())
                self.assertEqual(m.read(TIMER_HEAD), 0)
                self.assertEqual(m.read(m.timer_address + 0x1c), STOP)
                m.wheel(80, 10)
                self.assertTrue(m.active())
                self.assertEqual(m.remaining(), 1000000)
                # A stale event for this timer must use the refreshed deadline.
                m.timer(200)
                self.assertTrue(m.active())
                self.assertEqual(m.remaining(), 800000)
                m.now += 800
                m.deliver()
                self.assertFalse(m.active())

    def test_destructor_unlinks_live_timer_before_free(self):
        m = TimedWheel(self.image)
        m.wheel()
        state = m.state
        m.write(LIST + 0x8c, 0)
        m.hook(0xfd904, lambda: None)  # unchanged base-widget destruction
        m.call(0x103140, LIST, 1)
        self.assertEqual(m.read(TIMER_HEAD), 0)
        self.assertIn(state, m.freed)
        self.assertEqual(m.registered, [])

    def test_no_early_post_strands_marker_during_transient_event_pool_exhaustion(self):
        for more_input in (False, True):
            with self.subTest(input_after_lost_post=more_input):
                m = TimedWheel(self.image)
                m.wheel()
                for _ in range(49):
                    m.wheel(5, 20)
                last_motion = m.now
                m.now += 20
                if m.remaining() == 0:
                    timer = m.timer_address
                    m.write(TIMER_HEAD, 0)
                    m.write(timer, 0)
                    m.write(timer + 0x18, STOP)
                    m.write(0x103c9f3c, 0, 0)  # native event pool temporarily empty
                    m.call(0xd94c4, timer)  # real publisher ignores failed post
                    self.assertEqual(m.read(timer + 0x1c), EXPIRED)
                if more_input:
                    m.wheel(5, 20)
                    last_motion = m.now
                m.now = last_motion + 1000
                if m.read(TIMER_HEAD):
                    m.deliver()
                self.assertFalse(m.active(), 'dropped early post stranded active mode')


class Raster:
    def __init__(self, image):
        self.m = WheelMachine(image)
        self.m.wheel()
        self.calls = Counter()

        def trace(cpu, pc, size, context):
            self.calls['instructions'] += 1
            if pc == 0xa4a38:
                self.calls['fills'] += 1
        self.m.cpu.hook_add(UC_HOOK_CODE, trace)

    def draw(self, letter, top=20, clip=(20, 0, 128, 150)):
        m = self.m
        framebuffer, clip_at = m.graphics(clip)
        m.write(LIST + 0x58, top, 0, top + 1098, 150)
        m.write(LIST + 0x78, 20, 0, 128, 150)
        m.write(m.read(LIST + 0xe4) + 96, ord(letter))
        self.calls.clear()
        m.virtual(0x60, MODEL, clip_at)
        assert bytes(m.cpu.mem_read(framebuffer - 16, 16)) == b'\xa5' * 16
        assert bytes(m.cpu.mem_read(framebuffer + 2560, 16)) == b'\xa5' * 16
        return bytes(m.cpu.mem_read(framebuffer, 2560)), dict(self.calls)


class LetterRasterCost(unittest.TestCase):
    def test_every_glyph_preserves_r7_pixels_at_partial_clips_and_scroll_positions(self):
        before, after = Raster(self.reference), Raster(self.image)
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ#!':
            for top, clip in ((20, (20, 0, 128, 150)), (-268, (58, 60, 80, 90)),
                              (-970, (20, 0, 40, 150)), (-484, (90, 0, 128, 150))):
                with self.subTest(letter=letter, top=top, clip=clip):
                    expected, _ = before.draw(letter, top, clip)
                    actual, _ = after.draw(letter, top, clip)
                    self.assertEqual(actual, expected)

    def test_batching_reduces_native_raster_work_without_changing_pixels(self):
        before, after = Raster(self.reference), Raster(self.image)
        for letter, limit in (('B', 13), ('E', 9), ('!', 8)):
            with self.subTest(letter=letter):
                expected, old = before.draw(letter)
                actual, new = after.draw(letter)
                self.assertEqual(actual, expected)
                self.assertLessEqual(new['fills'], limit)
                if letter != '!':
                    self.assertLess(new['instructions'], old['instructions'])


class IndexCompaction(unittest.TestCase):
    def test_overlap_and_empty_compaction_preserve_all_other_bytes(self):
        from native_fixture import NativeMachine
        m = NativeMachine(self.image)
        m.instruction_limit = 2000000
        ptr = 0x10020000
        for capacity in (0, 1, 2, 3, 4, 7, 32, 60, 5000, 65535):
            for accepted in sorted({0, capacity // 2, max(0, capacity - 1), capacity}):
                with self.subTest(capacity=capacity, accepted=accepted):
                    original = bytes((i * 37 + 11) % 256 for i in range(5 * capacity + 32))
                    expected = bytearray(original)
                    expected[16 + 4 * accepted:16 + 5 * accepted] = \
                        original[16 + 4 * capacity:16 + 4 * capacity + accepted]
                    m.cpu.mem_write(ptr - 16, original)
                    m.call(self.compact, ptr, capacity, accepted)
                    self.assertEqual(bytes(m.cpu.mem_read(ptr - 16, len(original))), expected)

    def test_unchanged_index_skips_data_copy(self):
        from native_fixture import NativeMachine
        m = NativeMachine(self.image)
        m.instruction_limit = 2000000
        count = Counter()
        m.cpu.hook_add(UC_HOOK_CODE, lambda *args: count.update(['instructions']))
        m.call(self.compact, 0x10020000, 65535, 65535)
        self.assertLessEqual(count['instructions'], 8)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    LetterTimerCost.image = LetterRasterCost.image = IndexCompaction.image = load_image(args.candidate)
    IndexCompaction.compact = json.loads((args.candidate.with_suffix('.artifacts') /
                                         'letters-symbols.json').read_text())['letters_index_compact']
    LetterRasterCost.reference = load_image(REFERENCE)
    unittest.main(argv=[__file__], verbosity=2)
