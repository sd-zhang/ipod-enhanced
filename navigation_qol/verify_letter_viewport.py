"""Native long-list layout and monochrome indicator raster regressions.

The original list-content paint, device scanout and OS are supplied boundaries.
Actual geometry growth, cached viewport clipping and overlay raster run in ARM.
Run repository Python outside the macOS sandbox for Unicorn JIT access.
"""
import argparse
import unittest
from pathlib import Path

from verify_letters import WheelMachine, LIST, ROW, MODEL
from native_fixture import QUERY, load_image

PARENT = 0x10034000


class LetterViewport(unittest.TestCase):
    def frame(self, top, clip=(20, 0, 128, 150), view=0x3304,
              viewport=(20, 0, 128, 150)):
        m = WheelMachine(self.image)
        if view == 0x3302:
            m.write(LIST + 0x1c, view)
            m.write(QUERY + 0xa9c, m.read(QUERY + 0xa8c), 60)
        m.wheel()
        state = m.read(LIST + 0xe4)
        self.assertEqual(m.read(state + 92), 1)
        self.assertEqual(m.read(state + 96), ord('B'))
        framebuffer, clip_at = m.graphics(clip)
        m.write(PARENT, 0x22eac)
        m.write(PARENT + 0x20, 0x800)
        m.write(PARENT + 0x58, *viewport)
        m.write(PARENT + 0x78, *viewport)
        m.write(LIST + 0x10, PARENT)
        m.write(LIST + 0x58, top, 0, top + 108, 150)
        m.write(ROW + 0x58, top, 0, top + 18, 150)
        m.write(LIST + 0x20, m.read(LIST + 0x20) | 0x10)
        m.write(LIST + 0x68, 0x286d8, 0, 0, 0)
        m.call(0x3bd34, LIST)  # real layout: 61 rows * 18px, not screen height
        self.assertEqual((m.read(LIST + 0x60) - m.read(LIST + 0x58)) & 0xffffffff, 1098)
        m.call(0x3c62c, LIST)  # real ancestor clipping into cached visible rect
        self.assertEqual(tuple(m.read(LIST + 0x78 + i * 4) for i in range(4)), viewport)
        m.virtual(0x60, MODEL, clip_at)
        pixels = bytes(m.cpu.mem_read(framebuffer, 2560))
        self.assertEqual(bytes(m.cpu.mem_read(framebuffer - 16, 16)), b'\xa5' * 16)
        self.assertEqual(bytes(m.cpu.mem_read(framebuffer + 2560, 16)), b'\xa5' * 16)
        return {(y, x) for y in range(128) for x in range(160)
                if pixels[y * 20 + x // 8] & (128 >> (x % 8))}

    def test_long_artists_and_albums_keep_marker_centered_at_every_scroll_position(self):
        for view in (0x3304, 0x3302):
            baseline = None
            for top in (20, -268, -484, -970):
                with self.subTest(view=hex(view), content_top=top):
                    points = self.frame(top, view=view)
                    self.assertTrue(points, 'active letter was drawn outside the viewport')
                    self.assertIn((52, 53), points)  # centered 44px border
                    self.assertIn((60, 65), points)  # B top stroke
                    self.assertTrue(all(52 <= y < 96 and 53 <= x < 97 for y, x in points))
                    if baseline is not None:
                        self.assertEqual(points, baseline)
                    baseline = points

    def test_partial_repaint_clips_a_fixed_marker_without_recentering_it(self):
        full = self.frame(-268)
        self.assertTrue(full)
        for clip in ((58, 60, 80, 90), (20, 0, 40, 150), (90, 0, 128, 150)):
            with self.subTest(clip=clip):
                points = self.frame(-268, clip)
                self.assertEqual(points, {(y, x) for y, x in full
                                         if clip[0] <= y < clip[2] and clip[1] <= x < clip[3]})

    def test_too_small_visible_viewport_suppresses_marker(self):
        self.assertEqual(self.frame(-268, viewport=(20, 0, 60, 150)), set())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    LetterViewport.image = load_image(args.candidate)
    unittest.main(argv=[__file__], defaultTest='LetterViewport', verbosity=2)
