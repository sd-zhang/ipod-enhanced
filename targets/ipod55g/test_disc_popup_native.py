"""Stock Quick Scroll publication and popup pixels for numeric 5.5G captions."""
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unicorn import UC_HOOK_CODE

import disc_fast_scroll
import disc_patch
from image import resources, word
from layout import SOURCE, finish, install_elf, link
from pomodoro_ui import records
from research_actions import APP, RENDERER, WIDGET_TASK, Widgets
from research_clock import stock_image

SURFACE, RECT, TEXT, STRING, PROVIDER, MODEL = (
    0x10018000, 0x10018100, 0x10018160, 0x10018180, 0x10018200, 0x10018300,
)
DISPLAY, DISPLAY_BACKEND = 0x1001b000, 0x1001b400
LIST = 0x10018400
PIXELS = 0x10140000
WIDTH, HEIGHT = 320, 240
SCREEN = (0, 0, HEIGHT, WIDTH)
POPUP = (91, 112, 173, 207)
BACKDROP = 0x6a35
VISIBLE, STRING_TAG, BITMAP = 0x56566973, 0x53747220, 0x424d6170


class Popup(Widgets):
    def __init__(self, image):
        super().__init__(image)
        self.instruction_limit = 2_500_000  # Stock software alpha blend for 95x82 pixels.
        self.hook(0xf0, lambda: self.cpu.mem_write(
            self.reg(0), bytes(self.cpu.mem_read(self.reg(1), self.reg(2)))))
        self.hook(0xd6aac, lambda: WIDGET_TASK)
        for address in (0xa09b0, 0xa0bcc, 0xa0c00, 0x25aa40, 0x141e30):
            self.hook(address, lambda: 0)  # Mutex, atexit, and LCD invalidation boundaries.

        self.cpu.mem_map(PIXELS, 0x26000)
        self.write(RECT, *SCREEN)
        self.call(0x13b970, SURFACE, RECT, PIXELS, WIDTH * 2, 0, 0x565, 0, 0)
        self.write(DISPLAY_BACKEND + 0x80, SURFACE)
        self.write(DISPLAY, 0x668fb0)
        self.write(DISPLAY + 0x20, 0xa00)
        self.write(DISPLAY + 0x58, *SCREEN)
        self.write(DISPLAY + 0x88, *SCREEN)
        self.write(DISPLAY + 0x98, DISPLAY)
        self.write(DISPLAY + 0xbc, 1)

        self.call(0x25e844)
        # The optional on-disk PodiumSans18.ttf precedes the built-in 28px
        # font in the registry. Omit just that filesystem-backed candidate.
        optional = 0x108858e4 + 10 * 16
        assert self.read(optional + 4) == 0x63a4a0
        self.cpu.mem_write(optional, bytes(self.cpu.mem_read(optional + 16, 0x100)))

        # Native startup constructs this alias provider as App's provider 0x2b00.
        # Model registration, alias lookup and observer delivery all execute ARM.
        self.provider = self.call(0x207284, PROVIDER)
        self.write(0x1081da80, APP)
        self.write(0x1081da94, self.provider)
        self.write(APP, 0x66fe10)
        self.write(WIDGET_TASK + 0x30, RENDERER)
        providers = {8: RENDERER, 0x5e00: RENDERER, 0x2b00: self.provider}
        self.hook(0x1d3050, lambda: providers.get(self.reg(0), 0))
        self.model = self.call(0x17f860, MODEL, 0, 0)
        self.bind()

        at, size = resources(image)['View'][0x5e04]
        self.view = self.construct(next(
            record for record in records(image[at:at + size])
            if word(record, 12) & 0xffff == 0xfed4
        ))
        _, self.background, self.label = self.children(self.view)
        # Supply the enclosing display after the stock popup tree is built.
        self.write(self.view + 0x10, DISPLAY)
        self.write(self.view + 0x98, DISPLAY)
        self.call(0x21a5e4, self.view, RECT)
        self.invoke(self.view, 0x64, RECT, 1)
        self.call(0x17de1c, self.view, 1)

    def invoke(self, instance, slot, *args):
        return self.call(self.read(self.read(instance) + slot), instance, *args)

    def bind(self, model=None):
        self.invoke(self.model if model is None else model, 0x84, 1)

    def publish(self, text, *, visible=True, model=None):
        self.cpu.mem_write(TEXT, text + b'\0')
        self.write(STRING, 0x676a18, TEXT)
        self.invoke(self.model if model is None else model, 0x88, visible, STRING)
        self.cpu.mem_write(TEXT, b'!!!!!!\0')

    def property(self, tag, key):
        return self.call(0x21f254, self.provider, tag, key)

    def frame(self, widget):
        return struct.unpack('<4i', self.cpu.mem_read(widget + 0x58, 16))

    def draw(self, clip=SCREEN):
        self.cpu.mem_write(PIXELS, struct.pack('<H', BACKDROP) * (WIDTH * HEIGHT))
        self.write(RECT, *clip)
        self.invoke(self.view, 0x6c, 0, RECT)
        return struct.unpack(f'<{WIDTH * HEIGHT}H', self.cpu.mem_read(PIXELS, WIDTH * HEIGHT * 2))


def changed_pixels(pixels, reference):
    return {(index % WIDTH, index // WIDTH)
            for index, (actual, expected) in enumerate(zip(pixels, reference))
            if actual != expected}


class DiscPopupNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = stock_image()
        sources = ('disc_separators.c', 'disc_hooks.S', 'disc_render.c',
                   'disc_fast_scroll.c', 'disc_fast_scroll.S')
        with TemporaryDirectory() as directory:
            elf = link([SOURCE / name for name in sources], Path(directory), 'discs_fast_cancel')
            image, cls.symbols = install_elf(cls.stock, elf)
        disc_patch.apply(image, cls.symbols)
        disc_fast_scroll.apply(image, cls.symbols)
        cls.image = finish(image)

    def gesture(self, popup):
        # The wheel integration suite supplies live gesture events. This
        # raster fixture represents a Songs list whose mode is already off.
        popup.write(LIST, 0x677290)
        popup.write(LIST + 0x1c, 0x6381)
        popup.write(LIST + 0xa4, popup.model)
        popup.write(LIST + 0xfc, 0x40000000, 1)

    def test_numeric_publication_draws_full_stock_popup(self):
        popup = Popup(self.image)
        popup.publish(b'')
        background = popup.draw()
        self.assertEqual(popup.frame(popup.view), POPUP)
        self.assertEqual(popup.frame(popup.background), POPUP)
        self.assertEqual(popup.property(BITMAP, 0x284), popup.addresses[(BITMAP, 0x5f5d)])
        self.assertGreater(len(changed_pixels(background, (BACKDROP,) * len(background))), 6000)

        for text, left, right in ((b'1', 152, 167), (b'10', 144, 174),
                                  (b'101', 137, 182), (b'65535', 122, 197)):
            with self.subTest(text=text):
                popup.publish(text)
                self.assertEqual(popup.property(VISIBLE, 0x281), 1)
                caption = popup.property(STRING_TAG, 0x282)
                self.assertNotEqual(caption, TEXT)
                self.assertEqual(bytes(popup.cpu.mem_read(caption, len(text) + 1)), text + b'\0')
                self.assertEqual(popup.frame(popup.label), (117, left, 148, right))
                digits = changed_pixels(popup.draw(), background)
                self.assertTrue(all(left <= x < right and 117 <= y < 148 for x, y in digits))
                for index in range(len(text)):
                    start = left + 15 * index
                    self.assertGreater(sum(start <= x < start + 15 for x, _ in digits), 35)

    def test_dirty_rectangle_clips_the_stock_bitmap_and_label(self):
        popup = Popup(self.image)
        popup.publish(b'65535')
        complete = popup.draw()
        dirty = (121, 126, 143, 189)
        partial = popup.draw(dirty)
        self.assertEqual(partial, tuple(
            color if 121 <= index // WIDTH < 143 and 126 <= index % WIDTH < 189 else BACKDROP
            for index, color in enumerate(complete)
        ))
        self.assertEqual(popup.frame(popup.view), POPUP)
        self.assertEqual(popup.frame(popup.label), (117, 122, 148, 197))

    def test_hide_and_unbind_clear_the_popup_until_rebind(self):
        popup = Popup(self.image)
        popup.publish(b'10')
        visible = popup.draw()
        blank = (BACKDROP,) * len(visible)
        self.assertNotEqual(visible, blank)

        popup.publish(b'', visible=False)
        self.assertEqual(popup.draw(), blank)
        self.gesture(popup)
        self.assertEqual(popup.call(0x149ec4, popup.model, popup.provider), 1)
        for _ in range(2):
            popup.call(self.symbols['discs_fast_cancel'], LIST)
        self.assertEqual(popup.call(0x149ec4, popup.model, popup.provider), 0)
        for key in range(0x281, 0x285):
            self.assertEqual(popup.call(0x206ed4, popup.provider, key), 0)
        self.assertEqual(popup.read(popup.provider + 0x68), 0)
        self.assertEqual(popup.property(VISIBLE, 0x281), 0)
        self.assertEqual(popup.property(STRING_TAG, 0x282), 0)
        self.assertEqual(popup.property(BITMAP, 0x284), 0)

        popup.publish(b'65535')
        self.assertEqual(popup.draw(), blank)
        popup.bind()
        popup.publish(b'10')
        self.assertEqual(popup.draw(), visible)

    def test_repeated_numeric_publication_preserves_pixels_and_rebind_redraw(self):
        popup = Popup(self.image)
        popup.publish(b'10')
        original = popup.draw()
        self.gesture(popup)
        popup.write(LIST + 0xa8, LIST + 0x200)
        popup.cpu.mem_write(LIST + 0x12c, b'\1')
        # The wheel suite executes the real selection/run lookup. Supply
        # that boundary here while running the actual publisher and widgets.
        popup.hook(0x257250, lambda: 1)
        popup.hook(0x257260, lambda: 1)
        number = [10]
        popup.hook(self.symbols['discs_fast_target'],
                   lambda: popup.write(popup.reg(3), number[0]) or 1)
        published = []
        popup.cpu.hook_add(UC_HOOK_CODE, lambda cpu, pc, size, context:
                          published.append(pc) if pc == 0x17f6a4 else None)
        for _ in range(4):
            popup.call(self.symbols['discs_fast_publish'], LIST)
        self.assertEqual(len(published), 4)
        self.assertEqual(popup.draw(), original)

        number[0] = 65535
        popup.call(self.symbols['discs_fast_publish'], LIST)
        self.assertEqual(len(published), 5)
        changed = popup.draw()
        self.assertNotEqual(changed, original)
        self.assertEqual(popup.frame(popup.label), (117, 122, 148, 197))

        replacement = popup.call(0x17f860, MODEL + 0x80, 0, 0)
        popup.bind(replacement)
        popup.publish(b'', visible=False, model=replacement)
        self.assertEqual(popup.draw(), (BACKDROP,) * (WIDTH * HEIGHT))
        before = len(published)
        popup.call(self.symbols['discs_fast_publish'], LIST)
        self.assertEqual(len(published), before + 1)
        self.assertEqual(popup.draw(), changed)

        # Alias registration sends wildcard notifications. The publisher's
        # typed notifications must still refresh retained widgets afterward.
        popup.bind(replacement)
        popup.publish(b'', visible=False, model=replacement)
        popup.bind(popup.model)
        before = len(published)
        popup.call(self.symbols['discs_fast_publish'], LIST)
        self.assertEqual(len(published), before + 1)
        self.assertEqual(popup.draw(), changed)

    def test_old_list_cancel_preserves_popup_rebound_to_another_model(self):
        popup = Popup(self.image)
        self.gesture(popup)
        replacement = popup.call(0x17f860, MODEL + 0x80, 0, 0)
        popup.bind(replacement)
        popup.publish(b'101', model=replacement)
        replacement_pixels = popup.draw()

        popup.call(self.symbols['discs_fast_cancel'], LIST)
        for key in range(0x281, 0x285):
            self.assertEqual(popup.call(0x206ed4, popup.provider, key), replacement)
        self.assertEqual(popup.read(popup.provider + 0x68), 4)
        self.assertEqual(popup.draw(), replacement_pixels)


if __name__ == '__main__':
    unittest.main(verbosity=2)
