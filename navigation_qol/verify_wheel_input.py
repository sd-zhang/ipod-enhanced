"""Run native wheel packet decoding and letter activation against an IPSW.

The supplied packets and clock are controlled inputs, not a recording of device
cadence. The 4G interrupt decoder, displacement producer, 16-sample interval
average, lazy event reader and list accelerator execute their original ARM.
Only the hardware registers, OS notification and existing UI fixture services
are supplied. On macOS run outside the sandbox with the repository Python.
"""
import argparse
import struct
import unittest
from pathlib import Path

from unicorn import UC_HOOK_CODE

from verify_letters import EVENT, LIST, MODEL, ROW, WheelMachine, load_image


class WheelInputMachine(WheelMachine):
    def __init__(self, image, mode=1, period=25, start_time=1000):
        super().__init__(image, b"A" * 20 + b"B" * 20 + b"C" * 20
                         + b"D" * 20 + b"E" * 20)
        self.now = start_time
        self.cpu.mem_map(0x60005000, 0x1000)  # microsecond timer
        self.cpu.mem_map(0x7000c000, 0x1000)  # wheel packet registers
        self.hook(0x87654, lambda: mode)  # both native interval thresholds
        self.hook(0xb1394, lambda: None)  # OS input-task notification
        self.position = 0
        self.trace = []
        self.write(0x103c9954, self.now * 1000, 0, 0)
        self.packet(0x8000001a)  # release initializes native motion threshold
        self.packet(0xc000001a)  # touched position zero
        self.call(0x862dc)  # establish previous touched position

        def trace(cpu, pc, size, context):
            if pc == 0x86700:
                state = self.read(LIST + 0xe4)
                self.trace.append({
                    "raw": struct.unpack("<h", self.cpu.mem_read(EVENT + 10, 2))[0],
                    "interval": self.read(EVENT + 12),
                    "multiplier": self.read(state + 12),
                    "effective": self.reg(5),
                })
        self.cpu.hook_add(UC_HOOK_CODE, trace)
        # Prime all 16 timestamps through the producer, draining movement
        # without dispatching to the list. No fabricated event fields/history.
        for _ in range(16):
            self.move(8, period, drain=True)

    def packet(self, value):
        self.write(0x7000c104, 0x04000000)
        self.write(0x7000c140, value)
        assert self.call(0x149638) == 127

    def move(self, distance=8, advance=25, drain=False):
        self.now += advance
        self.write(0x60005010, self.now * 1000)
        self.position = (self.position + distance) % 96
        self.packet(0xc000001a | self.position << 16)
        self.call(0x862dc)
        if drain:
            return self.call(0x6263c)
        if not self.read(0x10419c60):
            return 0  # native producer has not posted a movement notification
        self.write(EVENT + 4, 0x5765656c, 0, 0)
        return self.call(0x86670, LIST, EVENT)

    def active(self):
        state = self.read(LIST + 0xe4)
        return bool(state and self.read(state + 92))


class LetterWheelInput(unittest.TestCase):
    def test_twenty_percent_lower_entry_threshold_is_exactly_64_raw_counts(self):
        for mode in (0, 1):
            for direction in (-1, 1):
                with self.subTest(mode=mode, direction=direction):
                    m = WheelInputMachine(self.image, mode, period=40)
                    m.write(ROW + 0x8c, 50)
                    m.write(MODEL + 0x14, 50)
                    for _ in range(7):
                        m.move(direction * 8, 40)
                    self.assertFalse(m.active(), '56 counts must remain normal scrolling')
                    m.move(direction * 8, 40)
                    self.assertTrue(m.active(), '64 counts must activate fast scrolling')
                    self.assertEqual([t['raw'] for t in m.trace], [direction * 8] * 8)

    def test_fast_physical_motion_enters_despite_native_single_row_clamp(self):
        for mode in (0, 1):
            for period in (25, 40):
                with self.subTest(mode=mode, packet_period_ms=period):
                    m = WheelInputMachine(self.image, mode, period)
                    for _ in range(10):
                        m.move(8, period)
                    # 80/96 revolution in 250 or 400ms. The native interval
                    # threshold clamps every normal list move to one row.
                    self.assertEqual([t["raw"] for t in m.trace], [8] * 10)
                    self.assertEqual([t["interval"] for t in m.trace],
                                     [period * 1000] * 10)
                    self.assertEqual([t["effective"] for t in m.trace], [1] * 10)
                    self.assertTrue(m.active(), "fast native packets never entered letters")
                    self.assertEqual(m.commits[-1], 21)

    def test_slow_motion_at_same_packet_cadence_stays_in_row_mode(self):
        for mode in (0, 1):
            with self.subTest(mode=mode):
                m = WheelInputMachine(self.image, mode)
                for _ in range(100):
                    m.move(1, 25)
                self.assertFalse(m.active())
                self.assertGreater(m.read(ROW + 0x8c), 0)
                self.assertTrue(all(t["raw"] == 1 for t in m.trace))
                self.assertTrue(all(t["effective"] <= 1 for t in m.trace))

    def test_native_multiplier_cannot_turn_slow_motion_into_a_fast_stroke(self):
        m = WheelInputMachine(self.image, period=10)
        for _ in range(200):
            m.move(1, 10)
        self.assertGreater(max(t["multiplier"] for t in m.trace), 1)
        self.assertLess(max(t["effective"] for t in m.trace), 16)
        self.assertFalse(m.active(), "accelerated row count replaced physical movement")

    def test_fast_stroke_survives_microsecond_clock_rollover(self):
        # Integer milliseconds chosen so the tenth movement crosses the
        # 32-bit microsecond clock's wrap, including its fractional-ms edge.
        m = WheelInputMachine(self.image, start_time=4294400)
        for _ in range(10):
            m.move()
        self.assertEqual([t["interval"] for t in m.trace], [25000] * 10)
        self.assertTrue(m.active())
        self.assertEqual(m.commits[-1], 21)

    def test_native_zero_tick_cannot_reuse_previous_displacement(self):
        # After warming, events occur at -25, 0, +25 and +50ms around wrap.
        # Native86394 sees its previous tick==0 on the third event and skips
        # updating native[4]. That stale +32 must not replace the actual -32.
        m = WheelInputMachine(self.image, start_time=-450)
        m.write(ROW + 0x8c, 50)
        m.write(MODEL + 0x14, 50)
        for distance in (8, 32, -32, 8):
            m.move(distance)
        self.assertEqual([t["raw"] for t in m.trace], [8, 32, -32, 8])
        self.assertEqual(m.trace[2]["effective"], 0)
        self.assertFalse(m.active(), "native zero tick reused a stale displacement")
        self.assertEqual(m.read(ROW + 0x8c), 53)
        for _ in range(9):
            m.move()
        self.assertTrue(m.active(), "fresh movement after the skipped event was lost")

    def test_reversal_requires_a_new_fast_stroke(self):
        m = WheelInputMachine(self.image)
        m.write(ROW + 0x8c, 50)
        m.write(MODEL + 0x14, 50)
        for _ in range(6):
            m.move(8)
        for _ in range(6):
            m.move(-8)
        self.assertFalse(m.active(), "opposite strokes were added together")
        for _ in range(4):
            m.move(-8)
        self.assertTrue(m.active())

    def test_pause_requires_a_new_fast_stroke(self):
        m = WheelInputMachine(self.image)
        for _ in range(6):
            m.move()
        m.move(8, 250)
        for _ in range(5):
            m.move()
        self.assertFalse(m.active(), "movement before a pause leaked into the next stroke")
        for _ in range(4):
            m.move()
        self.assertTrue(m.active())

    def test_key_clears_an_incomplete_stroke(self):
        m = WheelInputMachine(self.image)
        for _ in range(6):
            m.move()
        m.write(EVENT + 0x10, 0x57 << 16)
        m.virtual(0x160, EVENT)
        for _ in range(6):
            m.move()
        self.assertFalse(m.active(), "key left pending letter-entry movement")
        for _ in range(4):
            m.move()
        self.assertTrue(m.active())

    def test_hide_clears_an_incomplete_stroke(self):
        m = WheelInputMachine(self.image)
        m.write(LIST + 0x68, 0x286d8, 0, 0, 0)
        for _ in range(6):
            m.move()
        m.virtual(0x94, 0)
        m.virtual(0x94, 1)
        for _ in range(6):
            m.move()
        self.assertFalse(m.active(), "hide left pending letter-entry movement")
        for _ in range(4):
            m.move()
        self.assertTrue(m.active())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    LetterWheelInput.image = load_image(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
