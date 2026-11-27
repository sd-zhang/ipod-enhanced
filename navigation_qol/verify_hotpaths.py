"""Native behavioral/differential checks for the r16 optimizations.

Timer list operations, wheel dispatch, codec conversion and error handling run
as ARM. OS event delivery, LCD invalidation service and codec I/O are supplied.
Run repository Python outside the macOS sandbox for Unicorn's JIT.
"""
import argparse
import random
import unittest
from pathlib import Path

from native_fixture import load_image
from verify_letters import LIST, ROW, MODEL, CACHE
from verify_letter_performance import TimedWheel
from verify_volume_output import machine, DRIVER, PREFS
from unicorn import UC_HOOK_CODE


def boundary(image):
    m = TimedWheel(image)
    m.write(ROW + 0x8c, 41)
    m.write(MODEL + 0x14, 41)
    m.wheel()
    assert m.active()
    return m


def invalidations(m):
    return sum(event[0] == 'invalidate' for event in m.events)


class Hotpaths(unittest.TestCase):
    def test_unchanged_boundary_stays_visible_without_repainting_until_expiry(self):
        m = boundary(self.image)
        self.assertEqual(invalidations(m), 1, 'entry must draw the marker')
        m.events.clear()
        for unused in range(150):
            m.wheel(5, 20)
        self.assertEqual(m.commits, [])
        self.assertEqual(m.read(ROW + 0x8c), 41)
        self.assertTrue(m.active())
        self.assertEqual(m.remaining(), 1000000)
        self.assertEqual(invalidations(m), 0, 'unchanged marker unnecessarily redrawn')
        m.now += 1000
        m.deliver()
        self.assertFalse(m.active())
        self.assertEqual(invalidations(m), 1, 'expiry must erase the marker')
        m.wheel(80, 200)
        self.assertTrue(m.active())
        self.assertEqual(invalidations(m), 2, 'reentry must draw the marker')

    def test_same_row_with_changed_group_still_repaints(self):
        m = boundary(self.image)
        m.events.clear()
        m.cpu.mem_write(CACHE + 4 * 60 + 40, b'D' * 20)
        m.wheel(5, 200)
        self.assertEqual(m.read(ROW + 0x8c), 41)
        self.assertEqual(m.read(m.state + 96), ord('D'))
        self.assertEqual(invalidations(m), 1)

    def test_reversal_changes_selection_and_repaints(self):
        m = boundary(self.image)
        m.events.clear()
        m.wheel(-80, 10)
        self.assertEqual(m.commits, [21])
        self.assertEqual(m.read(m.state + 96), ord('B'))
        self.assertEqual(invalidations(m), 1)
        self.assertEqual(m.remaining(), 1000000)

    def test_differential_wheel_state_and_deadlines_across_wrap_and_pauses(self):
        rng = random.Random(0x4a16)
        before, after = TimedWheel(self.reference), TimedWheel(self.image)
        before.now = after.now = 4294400
        inputs = [(80, 200), *[(rng.choice((-80, -5, 0, 5, 80)),
                                  rng.choice((0, 10, 20, 149, 150, 200, 999, 1001)))
                                 for unused in range(250)]]
        for packet, (delta, wait) in enumerate(inputs):
            with self.subTest(packet=packet):
                before.events.clear()
                after.events.clear()
                self.assertEqual(before.wheel(delta, wait), after.wheel(delta, wait))
                self.assertEqual(after.commits, before.commits)
                self.assertEqual(after.read(ROW + 0x8c), before.read(ROW + 0x8c))
                # State layout and live timer content must remain compatible
                # with the unchanged native allocation/event/destructor paths.
                self.assertEqual(bytes(after.cpu.mem_read(after.state, 128)),
                                 bytes(before.cpu.mem_read(before.state, 128)))
                self.assertEqual([e for e in after.events if e[0] != 'invalidate'],
                                 [e for e in before.events if e[0] != 'invalidate'])

    def test_centered_cap_avoids_redundant_divisions(self):
        for cap, expected in ((120, 2), (0, 0)):
            with self.subTest(cap=cap):
                m = machine(self.image, cap=cap)
                divisions = []
                m.cpu.hook_add(UC_HOOK_CODE, lambda cpu, pc, size, context:
                               divisions.append(pc) if pc == 0x1494c else None)
                self.assertEqual(m.call(0x14fae0, DRIVER, 2, 127, 127), 0)
                self.assertEqual(len(divisions), expected)

    def test_all_cap_values_preserve_stereo_registers_and_cache_behavior(self):
        before, after = machine(self.reference), machine(self.image)
        pairs = ((0, 0), (0, 255), (255, 0), (255, 255), (127, 63),
                 (63, 127), (126, 127), (127, 126), (1, 2), (17, 42))
        for cap in range(257):
            before.write(PREFS + 0xb48, cap)
            after.write(PREFS + 0xb48, cap)
            for left, right in pairs:
                before.registers.clear()
                after.registers.clear()
                got = after.call(0x14fae0, DRIVER, 2, left, right)
                want = before.call(0x14fae0, DRIVER, 2, left, right)
                self.assertEqual((got, after.registers, after.codec_inputs[-1], after.hardware),
                                 (want, before.registers, before.codec_inputs[-1], before.hardware),
                                 (cap, left, right))

    def test_failed_channel_write_and_same_value_retry_match_reference(self):
        for register in (2, 3):
            before, after = machine(self.reference), machine(self.image)
            for failing in (register, None, None):
                before.fail_register = after.fail_register = failing
                before.registers.clear()
                after.registers.clear()
                got = after.call(0x14fae0, DRIVER, 2, 63, 127)
                want = before.call(0x14fae0, DRIVER, 2, 63, 127)
                self.assertEqual((got, after.registers, after.hardware),
                                 (want, before.registers, before.hardware))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--reference', type=Path, default=Path(
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r15.ipsw'))
    args = parser.parse_args()
    Hotpaths.image, Hotpaths.reference = map(load_image, (args.candidate, args.reference))
    unittest.main(argv=[__file__], verbosity=2)
