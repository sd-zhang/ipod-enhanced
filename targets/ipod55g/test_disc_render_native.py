"""5.5G disc headings rendered with stock ARM font and RGB565 routines."""
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from image import resources
from layout import finish, install_elf, link
from research_clock import Machine, stock_image

SOURCE = Path(__file__).resolve().parent
LIST, OWNER, DISPLAY = 0x10010000, 0x10010400, 0x10010800
SURFACE, CONTEXT, RECT, CLIP, TEXT = (
    0x10010c00, 0x10011000, 0x10011400, 0x10011500, 0x10011600,
)
FRAME = 0x10040000
SCREEN = (0, 0, 240, 320)
VIEWPORT = (24, 0, 240, 308)
ROW = (24, 0, 48, 308)
WHITE, RULE, SENTINEL = b'\xff\xff', b'\xba\xd6', b'\x5a\xa5'


class Raster(Machine):
    def __init__(self, image):
        super().__init__(image)
        self.cursor = 0x10100000
        self.resources = {
            (int.from_bytes(tag.encode(), 'big'), key): address
            for tag, entries in resources(image).items()
            for key, (address, _) in entries.items()
        }
        self.style = self.resources[(0x54797065, 0x6d43)]

        # Resource registry and heap are host boundaries. The font registry,
        # stock Podium glyphs, mode, surface constructors and raster run as ARM.
        self.hook(0x21f3dc, lambda: self.resources.get((self.reg(0), self.reg(1)), 0))
        self.hook(0x25a974, self.allocate)
        self.hook(0x11ca20, self.allocate)
        self.call(0x25e844)  # Stock startup font registration.
        self.write(RECT, *SCREEN)
        self.call(0x13b970, SURFACE, RECT, FRAME, 640, 0, 0x565, 0, 0)
        self.write(LIST, 0x677290)  # Native fixed-height list vtable.
        self.write(LIST + 0x20, 0x800)
        self.write(LIST + 0x58, *VIEWPORT)
        self.write(LIST + 0x88, *VIEWPORT)
        self.write(LIST + 0x98, OWNER)
        self.write(OWNER + 0xa4, DISPLAY)
        self.write(OWNER + 0xa8, *VIEWPORT)
        self.write(OWNER + 0xbc, 1)
        self.write(DISPLAY + 0x80, SURFACE)
        self.call(0x210638, CONTEXT)
        self.call(0x211a70, CONTEXT, SURFACE)
        self.call(0x21a168, LIST, self.style, CONTEXT, TEXT + 32)

    def allocate(self):
        address = self.cursor
        self.cursor += (self.reg(0) + 15) & -16
        assert self.cursor < 0x10200000
        return address

    def blank(self):
        self.cpu.mem_write(FRAME, SENTINEL * 76800)

    def pixels(self):
        return bytes(self.cpu.mem_read(FRAME, 153600))

    def reference_heading(self, caption, row=ROW):
        """Stock text over the expected opaque row and divider geometry."""
        self.blank()
        top, left, bottom, right = row
        for y in range(top, bottom):
            self.cpu.mem_write(FRAME + (y * 320 + left) * 2, WHITE * (right - left))
        self.cpu.mem_write(TEXT, caption + b'\0')
        width = self.call(0x2108d0, CONTEXT, TEXT)
        x = max(left + 4, left + (right - left - width) // 2)
        line = top + (bottom - top) // 2 - 1
        for a, b in ((left + 4, x - 4), (x + width + 4, right - 4)):
            if a < b:
                self.cpu.mem_write(FRAME + (line * 320 + a) * 2, RULE * (b - a))
        self.write(CONTEXT + 0x34, top, left + 4, bottom, right - 4)
        self.call(0x210290, CONTEXT, 0x21)
        self.call(0x211754, CONTEXT, x, top + 15)
        self.call(0x2101dc, CONTEXT, TEXT)
        return self.pixels()

    def heading(self, entry, disc, row=ROW, clip=VIEWPORT):
        self.blank()
        self.write(RECT, *row)
        self.write(CLIP, *clip)
        self.call(entry, LIST, CLIP, RECT, disc)
        return self.pixels()


class DiscRenderNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = stock_image()
        with TemporaryDirectory() as directory:
            elf = link([SOURCE / 'disc_render.c'], Path(directory), 'discs_header_draw')
            image, cls.symbols = install_elf(cls.stock, elf)
        cls.image = finish(image)

    def setUp(self):
        self.m = Raster(self.image)
        self.entry = self.symbols['discs_header_draw']

    def assert_clipped(self, actual, expected, clip):
        top, left, bottom, right = clip
        for y in range(240):
            first = (y * 320 + left) * 2
            last = (y * 320 + right) * 2
            row = actual[y * 640:(y + 1) * 640]
            clipped = (SENTINEL * left + expected[first:last] + SENTINEL * (320 - right)
                       if top <= y < bottom else SENTINEL * 320)
            self.assertEqual(row, clipped, f'framebuffer row {y} differs')

    def test_first_full_row_uses_centered_contacts_type_and_one_pixel_rules(self):
        expected = self.m.reference_heading(b'Disc 1')
        actual = self.m.heading(self.entry, 1)
        self.assertTrue(actual == expected, 'Disc 1 row differs from native text and rule geometry')
        self.assertEqual(actual.count(RULE), expected.count(RULE))
        self.assertGreater(expected.count(RULE), 200)
        self.assertGreater(expected.count(bytes.fromhex('2c63')), 10)  # opaque #666666 glyph pixels

    def test_decimal_label_covers_disc_numbers_through_65535(self):
        for disc, text in ((9, b'Disc 9'), (10, b'Disc 10'), (99, b'Disc 99'),
                           (100, b'Disc 100'), (1000, b'Disc 1000'),
                           (10000, b'Disc 10000'), (65535, b'Disc 65535')):
            with self.subTest(disc=disc):
                expected = self.m.reference_heading(text)
                self.assertTrue(self.m.heading(self.entry, disc) == expected, text)

    def test_dirty_and_ancestor_clipping_preserve_row_centroid(self):
        expected = self.m.reference_heading(b'Disc 12')
        # Native layout has already intersected the list with its ancestor.
        # The owner's active clip is left over from a different row draw.
        self.m.write(LIST + 0x88, 26, 110, 42, 192)
        self.m.write(OWNER + 0xa8, 48, 0, 72, 308)
        actual = self.m.heading(self.entry, 12, clip=(20, 116, 45, 180))
        self.assert_clipped(actual, expected, (26, 116, 42, 180))

    def test_previous_native_row_clip_does_not_hide_heading(self):
        row = (96, 0, 120, 308)
        expected = self.m.reference_heading(b'Disc 12', row)
        # Each stock row container updates the display owner's active clip
        # through 0x21ac60. It is draw state, not the list's visible bounds.
        for frame_top in (24, -72):
            self.m.write(LIST + 0x58, frame_top, 0, 240, 308)
            for previous in ((24, 0, 48, 308), (120, 0, 144, 308), (0, 0, 0, 0)):
                with self.subTest(previous=previous, frame_top=frame_top):
                    self.m.write(CLIP, *previous)
                    self.m.call(0x21ac60, LIST, CLIP)
                    actual = self.m.heading(self.entry, 12, row=row)
                    self.assertEqual(actual, expected,
                                     'heading inherited the preceding song row clip')

    def test_narrow_row_clips_text_to_its_inset(self):
        row = (76, 15, 100, 52)
        expected = self.m.reference_heading(b'Disc 65535', row)
        actual = self.m.heading(self.entry, 65535, row=row)
        self.assertTrue(actual == expected, 'narrow heading leaked text or lost its row background')
        self.assertGreater(expected.count(bytes.fromhex('2c63')), 4)
        self.assertNotIn(RULE, actual)

    def test_empty_clips_and_invalid_disc_leave_framebuffer_alone(self):
        self.m.blank()
        blank = self.m.pixels()
        for disc, clip in ((0, VIEWPORT), (65536, VIEWPORT), (1, (0, 0, 24, 308))):
            with self.subTest(disc=disc, clip=clip):
                self.assertTrue(self.m.heading(self.entry, disc, clip=clip) == blank)

    def test_paint_changes_only_native_active_clip_without_owned_allocations(self):
        self.m.reference_heading(b'Disc 65535')  # Prewarm the stock font cache.
        spans = ((LIST, 0x118), (OWNER, 0xa8), (OWNER + 0xb8, 8),
                 (DISPLAY, 0x84), (SURFACE, 0xb4),
                 (CONTEXT, 0x44), (0x108858e4, 0x1a0))
        def snapshot():
            return [bytes(self.m.cpu.mem_read(address, size)) for address, size in spans]
        state = snapshot()
        cursor = self.m.cursor
        self.m.heading(self.entry, 65535)
        self.m.heading(self.entry, 1, row=(48, 0, 72, 308))
        self.assertEqual(snapshot(), state)
        self.assertEqual(struct.unpack('<4i', self.m.cpu.mem_read(OWNER + 0xa8, 16)),
                         (48, 0, 72, 308))
        self.assertEqual(self.m.cursor, cursor)
        self.assertEqual(struct.unpack('<4i', self.m.cpu.mem_read(RECT, 16)), (48, 0, 72, 308))
        self.assertEqual(struct.unpack('<4i', self.m.cpu.mem_read(CLIP, 16)), VIEWPORT)


if __name__ == '__main__':
    unittest.main(verbosity=2)
