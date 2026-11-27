"""Execute native segment/wrap timer events from the actual candidate.

Run repository Python outside the macOS sandbox (Unicorn needs JIT).
Graphics and OS timer delivery are supplied, not a physical LCD simulation.
"""
import argparse
import unittest
from pathlib import Path

from verify_native import NativeUI, CONTROL, EVENT
from verify_resources import archive, payload


class SegmentDwell(unittest.TestCase):
    def fixture(self, now_playing=False):
        ui = NativeUI()
        ui.image = self.image
        ui.setUp()
        ui.scrolling_fixture()
        if now_playing:
            # Actual 3312 / 3313 timing profile, checked separately by package
            # resource preservation. These labels must retain their timing.
            ui.m.write(CONTROL + 0xe0, 140, 5000, 4)
        ui.m.write(EVENT + 4, 0x74696d65, CONTROL + 0xfc)
        return ui

    def check_cycle(self, now_playing=False):
        ui = self.fixture(now_playing)
        m = ui.m
        initial, middle, step = (5000, 1500, 4) if now_playing else (1250, 840, 8)
        m.call(0xd0410, CONTROL, 1)
        self.assertEqual(ui.events, [('interval', CONTROL + 0xfc, initial), ('start', CONTROL + 0xfc)])
        # 140px viewport -> pause after each 126px of travel. Run all events,
        # including intermediate pauses and the final wrap to offset 0.
        seen = []
        for tick in range(1, (305 + step - 1) // step + 1):
            ui.events.clear()
            m.call(0x809b0, CONTROL, EVENT)
            travel = tick * step
            if travel >= 305:
                expected, position = initial, 0
            else:
                position = -travel
                expected = middle if travel % 126 < step and 280 - travel > 25 else 140
            self.assertEqual(m.read(CONTROL + 0xec), position & 0xffffffff)
            self.assertEqual(ui.events, [('interval', CONTROL + 0xfc, expected), ('start', CONTROL + 0xfc)])
            seen.append(expected)
        # The 8px step reaches the second boundary at 256px, leaving only
        # 24px (<25px gap): native code correctly suppresses that last pause.
        # The 4px Now Playing step reaches 252px while 28px still remain.
        self.assertEqual(seen.count(middle), 2 if now_playing else 1)
        self.assertEqual(seen.count(initial), 1)

    def test_menu_segment_pause_is_840ms_with_unchanged_steps_and_wrap(self):
        self.check_cycle()

    def test_now_playing_keeps_1500ms_segment_pause(self):
        self.check_cycle(now_playing=True)

    def test_hiding_during_segment_pause_stops_timer_and_reentry_resets(self):
        ui = self.fixture()
        m = ui.m
        m.write(CONTROL + 0xec, -120)
        m.call(0x809b0, CONTROL, EVENT)
        self.assertEqual(ui.events[-2:], [('interval', CONTROL + 0xfc, 840), ('start', CONTROL + 0xfc)])
        ui.events.clear()
        m.call(0xd0410, CONTROL, 0)
        self.assertEqual(ui.events, [('stop', CONTROL + 0xfc)])
        ui.events.clear()
        m.call(0xd0410, CONTROL, 1)
        self.assertEqual(m.read(CONTROL + 0xec), 0)
        self.assertEqual(ui.events, [('interval', CONTROL + 0xfc, 1250), ('start', CONTROL + 0xfc)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    SegmentDwell.image = payload(archive(args.candidate)['Firmware-4.3.1.1'], 0x4200)
    unittest.main(argv=[__file__], defaultTest='SegmentDwell', verbosity=2)
