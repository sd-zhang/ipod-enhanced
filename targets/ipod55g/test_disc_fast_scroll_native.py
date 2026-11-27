"""Execute 5.5G album Quick Scroll through the native wheel and popup paths."""
from pathlib import Path
import sys
import tempfile
import unittest
from unicorn import UC_HOOK_CODE

import disc_patch
from image import load_stock
from layout import SOURCE, finish, install_elf, link
from research_actions import STACK
import test_disc_native as songs

APP_NATIVE = 0x1081da80
PROVIDER = songs.OUT + 0x800
EVENT = songs.EVENT
WHEEL_VTABLE = 0x677aec
KEY_VTABLE = 0x676974
TIMER_VTABLE = 0x677a28
VISIBLE = 0x56566973
STROKE = 0x40000000


class DiscFastScrollNative(unittest.TestCase):
    library = songs.DiscSeparatorsNative.library
    build_index = songs.DiscSeparatorsNative.build_index
    view = songs.DiscSeparatorsNative.view
    select = songs.DiscSeparatorsNative.select
    selected = songs.DiscSeparatorsNative.selected
    logical = songs.DiscSeparatorsNative.logical
    paint = songs.DiscSeparatorsNative.paint

    @classmethod
    def setUpClass(cls):
        cls.stock = load_stock(songs.ROOT / 'iPod_25.1.3.ipsw').osos
        evidence = songs.ROOT / 'build/ipod55g-disc-fast-scroll-01'
        evidence.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='native-', dir=evidence))
        sources = ['disc_separators.c', 'disc_hooks.S', 'disc_render.c']
        if '--core-only' not in sys.argv:
            sources += ['disc_fast_scroll.c', 'disc_fast_scroll.S']
        elf = link([SOURCE / name for name in sources], directory, 'discs_draw_row')
        image, cls.symbols = install_elf(cls.stock, elf)
        disc_patch.apply(image, cls.symbols)
        if '--core-only' not in sys.argv:
            import disc_fast_scroll
            disc_fast_scroll.apply(image, cls.symbols)
            import quick_scroll
            quick_scroll.apply(image)
        cls.image = finish(image)

    def make_list(self, runs=((1, 20), (10, 20), (11, 20)), *, album='Alpha', screen=songs.SONGS,
                  image=None):
        rows = [('Alpha', number, max(n for n, _ in runs))
                for number, count in runs for _ in range(count)]
        m, _ = self.library(rows, album, image)
        self.build_index(m)
        _, listing = self.view(m, screen)
        self.select(m, listing, 1)
        m.hook(0x25aa40, lambda: 0)  # Process-exit callback registration.
        m.call(0x16b194, songs.APP, 0, 1)  # Concrete App constructed by UI startup.
        m.call(0x207284, PROVIDER)
        m.write(APP_NATIVE + 0x14, PROVIDER)
        m.call(0x21eefc, songs.OUT + 0x700)  # Native accelerator registry.
        m.hook(0xea594, lambda: songs.OUT + 0x700)
        m.hook(0x1d0aa8, lambda: songs.OUT + 0x600)  # Property event pool.
        m.hook(0xa07a4, lambda: 0)  # Use the stock 10 ms profile for decoded events.
        m.clock = 1000
        m.hook(0xcced0, lambda: m.write(m.reg(0), m.clock * 1000) or 0)
        m.clicks, m.timers = [], []
        m.hook(0x1c2428, lambda: m.clicks.append(m.reg(0)) or 0)
        m.hook(0x151694, lambda: m.timers.append(('start', m.reg(0))) or 0)
        m.hook(0x151c98, lambda: m.timers.append(('stop', m.reg(0))) or 0)
        self.visibility(m, listing, 1)
        return m, listing

    def visibility(self, m, listing, shown):
        m.call(m.read(songs.LIST_VTABLE + 0xa4), listing, shown)

    def wheel(self, m, listing, delta, *, flags=STROKE, velocity=800):
        m.clock += 20
        m.write(EVENT, WHEEL_VTABLE, 15, 1, delta, flags, velocity)
        return m.call(m.read(songs.LIST_VTABLE + 0x30), listing, EVENT)

    def popup(self, m):
        visible = m.call(0x21f254, PROVIDER, VISIBLE, 0x281)
        if not visible:
            return ''
        pointer = m.call(0x21f254, PROVIDER, songs.STR, 0x282)
        return bytes(m.cpu.mem_read(pointer, 8)).split(b'\0', 1)[0].decode()

    def test_inactive_final_disc_forward_input_stays_native_without_popup_or_timer(self):
        for row in (43, 44, 50, 62):
            with self.subTest(row=row):
                m, listing = self.make_list()
                ref, normal = self.make_list()
                for machine, view in ((m, listing), (ref, normal)):
                    self.select(machine, view, row)
                    machine.call(0x23a5ec, view)
                    machine.timers.clear()
                for delta in (64, 4, 4):
                    self.wheel(m, listing, delta)
                    self.wheel(ref, normal, delta, flags=0)
                    self.assertFalse(self.popup(m))
                    self.assertEqual(m.cpu.mem_read(listing + 0x12c, 1), b'\0')
                    self.assertEqual(self.selected(m, listing), self.selected(ref, normal))
                    self.assertEqual(self.logical(m), self.logical(ref))
                    self.assertEqual(m.read(listing + 0x58), ref.read(normal + 0x58))
                    self.assertEqual(m.clicks, ref.clicks)
                    self.assertFalse(any(event[0] == 'start' for event in m.timers))

    def test_final_disc_blocked_entry_keeps_native_reverse_activation(self):
        m, listing = self.make_list()
        self.select(m, listing, 50)
        self.wheel(m, listing, 64)
        self.assertFalse(self.popup(m))
        self.assertGreater(self.selected(m, listing), 50)
        self.wheel(m, listing, -64)
        self.assertEqual((self.selected(m, listing), self.popup(m)), (43, '11'))
        self.wheel(m, listing, -4)
        self.assertEqual((self.selected(m, listing), self.popup(m)), (22, '10'))

    def test_final_disc_forward_does_not_reenter_after_release_or_timeout(self):
        for end in ('release', 'timeout'):
            with self.subTest(end=end):
                m, listing = self.make_list()
                self.wheel(m, listing, 64)
                self.wheel(m, listing, 4)
                self.assertEqual((self.selected(m, listing), self.popup(m)), (43, '11'))
                if end == 'release':
                    self.wheel(m, listing, 0, flags=0)
                else:
                    m.clock += 2000
                    self.event(m, listing, TIMER_VTABLE, 9, listing + 0x104)
                self.assert_closed(m, listing)
                m.timers.clear()
                for delta in (64, 4, 4):
                    self.wheel(m, listing, delta)
                    self.assertFalse(self.popup(m))
                    self.assertFalse(any(event[0] == 'start' for event in m.timers))
                self.assertGreater(self.selected(m, listing), 43)

    def test_disc_jumps_align_heading_with_real_viewport_and_keep_bottom_limit(self):
        for top, height in ((0, 216), (24, 216), (24, 210)):
            with self.subTest(top=top, height=height):
                m, listing = self.make_list(((1, 20), (2, 20), (3, 2)))
                scroll = m.read(listing + 0x10)
                root = m.read(scroll + 0x10)
                m.write(root + 0x58, 0, 0, top + height, 320)
                m.write(root + 0x88, 0, 0, top + height, 320)
                m.write(scroll + 0x58, top, 0, top + height, 320)
                m.write(scroll + 0x88, top, 0, top + height, 320)
                m.write(listing + 0x58, top, 0, top + 1080, 320)
                m.write(listing + 0x88, top, 0, top + height, 320)
                for offset in (0xa8, 0xac):
                    m.write(m.read(listing + offset) + 0x58, top, 0, top + 24, 300)
                # Disc starts are visual rows 0, 21 and 42. The short final
                # disc is clamped at the native bottom, not padded with rows.
                for delta, row, frame, caption in (
                    (64, 22, top - 504, '2'),
                    (4, 43, top + height - 1080, '3'),
                    (4, 43, top + height - 1080, '3'),
                    (-4, 22, top - 504, '2'),
                    (-4, 1, top, '1'),
                ):
                    self.wheel(m, listing, delta)
                    self.assertEqual(self.selected(m, listing), row)
                    self.assertEqual(self.popup(m), caption)
                    self.assertEqual(songs.signed(m.read(listing + 0x58)), frame)
                    self.assertEqual(songs.signed(m.read(listing + 0x60)), frame + 1080)
                self.wheel(m, listing, 4)
                self.wheel(m, listing, 0, flags=0)
                self.assert_closed(m, listing)
                self.assertEqual((self.selected(m, listing), self.logical(m)), (22, 20))
                self.assertEqual(songs.signed(m.read(listing + 0x58)), top - 504)
                self.wheel(m, listing, 4, flags=0)
                self.assertEqual(self.selected(m, listing), 23)
                self.wheel(m, listing, 4, flags=0)
                self.assertEqual(self.selected(m, listing), 24)
                self.assertEqual(songs.signed(m.read(listing + 0x58)), top - 504)

    def test_entry_to_final_disc_aligns_then_stationary_input_keeps_selection(self):
        m, listing = self.make_list()
        scroll = m.read(listing + 0x10)
        root = m.read(scroll + 0x10)
        for node in (root, scroll):
            m.write(node + 0x58, 0, 0, 216, 320)
            m.write(node + 0x88, 0, 0, 216, 320)
        m.write(listing + 0x88, 0, 0, 216, 320)
        self.select(m, listing, 22)
        self.wheel(m, listing, 64)
        self.assertEqual(self.selected(m, listing), 43)
        m.clicks.clear()
        for delta in (4, 4, 4):
            self.wheel(m, listing, delta)
            self.assertEqual(self.selected(m, listing), 43)
            self.assertEqual(songs.signed(m.read(listing + 0x58)), -1008)
        self.assertEqual(m.clicks, [])

    def test_aligned_stationary_pulses_do_not_invalidate_rows_but_realign_after_shift(self):
        m, listing = self.make_list()
        scroll = m.read(listing + 0x10)
        root = m.read(scroll + 0x10)
        for node in (root, scroll):
            m.write(node + 0x58, 0, 0, 216, 320)
            m.write(node + 0x88, 0, 0, 216, 320)
        m.write(listing + 0x88, 0, 0, 216, 320)
        self.select(m, listing, 22)
        self.wheel(m, listing, 64)
        self.assertEqual(songs.signed(m.read(listing + 0x58)), -1008)
        invalidations = []
        observer = m.cpu.hook_add(UC_HOOK_CODE,
                                 lambda cpu, pc, size, context: invalidations.append(pc),
                                 begin=0x21ab98, end=0x21ab98)
        for _ in range(3):
            timers = len(m.timers)
            self.wheel(m, listing, 4)
            self.assertEqual((self.selected(m, listing), self.logical(m), self.popup(m)), (43, 40, '11'))
            self.assertIn(('start', listing + 0x104), m.timers[timers:])
        self.assertEqual(invalidations, [], 'stationary input dirtied an unchanged song row')
        # Re-evaluate live geometry after a native container translation.
        # No remembered alignment flag may suppress the required reveal.
        m.call(0x17dc88, scroll, 0, 24, 1)
        self.assertEqual(songs.signed(m.read(listing + 0x58)), -984)
        invalidations.clear()
        self.wheel(m, listing, 4)
        self.assertEqual(songs.signed(m.read(listing + 0x58)), -1008)
        self.assertTrue(invalidations)
        # A pending native direction must still be consumed by reveal.
        m.write(listing + 0xbc, 1)
        invalidations.clear()
        self.wheel(m, listing, 4)
        self.assertEqual(m.read(listing + 0xbc), 0)
        self.assertTrue(invalidations)
        m.cpu.hook_del(observer)

    def test_already_active_final_disc_preserves_song_and_viewport(self):
        for row, end in ((44, 'release'), (50, 'timeout'), (62, 'release')):
            with self.subTest(row=row, end=end):
                m, listing = self.make_list()
                self.select(m, listing, 22)
                self.wheel(m, listing, 64)
                self.select(m, listing, row)
                m.call(0x23a5ec, listing)
                m.clicks.clear()
                frame = m.read(listing + 0x58)
                for delta in (64, 4, 4):
                    self.wheel(m, listing, delta)
                    self.assertEqual((self.selected(m, listing), self.logical(m)), (row, row - 3))
                    self.assertEqual(m.read(listing + 0x58), frame)
                    self.assertEqual(self.popup(m), '11')
                self.assertEqual(m.clicks, [])
                if end == 'release':
                    self.wheel(m, listing, 0, flags=0)
                else:
                    m.clock += 2000
                    self.event(m, listing, TIMER_VTABLE, 9, listing + 0x104)
                self.assert_closed(m, listing)
                self.assertEqual((self.selected(m, listing), self.logical(m)), (row, row - 3))
                self.assertEqual(m.read(listing + 0x58), frame)
                self.wheel(m, listing, 4, flags=0)
                if end == 'timeout':
                    # After timeout the native accelerator uses the first
                    # small event to restart accumulation, then moves a song.
                    self.assertEqual(self.selected(m, listing), row)
                    self.wheel(m, listing, 4, flags=0)
                self.assertEqual(self.selected(m, listing), min(row + 1, 62))

    def test_blocked_final_disc_keeps_native_small_reverse_threshold(self):
        m, listing = self.make_list()
        self.select(m, listing, 50)
        self.wheel(m, listing, 64)
        self.assertEqual(self.selected(m, listing), 62)
        self.wheel(m, listing, -4)
        self.assertFalse(self.popup(m), 'small reverse input must retain native entry gates')
        for delta, row, caption in ((-64, 43, '11'), (-4, 22, '10')):
            self.wheel(m, listing, delta)
            self.assertEqual(self.selected(m, listing), row)
            self.assertEqual(self.popup(m), caption)

    def test_physical_final_disc_forward_stays_ordinary_and_reverse_enters(self):
        m, listing = self.make_list(((1, 20), (10, 20), (11, 200)))
        self.select(m, listing, 50)
        m.hooks.pop(0xa07a4)
        m.write(0x1081de40 + 0x28, 5 << 16)  # Exercise the major >= 5 timing profile.
        m.write(0x1081de40 + 0x40, -1)  # Native board predicate computes its cache.
        m.cpu.mem_map(0x7000c000, 0x1000)  # Wheel packet registers.
        m.cpu.mem_map(0x60005000, 0x1000)  # Hardware microsecond counter.
        m.hook(0xadb54, lambda: 0)  # Kernel wakeup. Dispatch below at 20 ms.
        position = 0
        ticks = m.clock * 1000

        def sample(step):
            nonlocal position, ticks
            ticks += 5000
            m.clock = ticks // 1000
            position = (position + step) % 96
            m.write(0x60005010, ticks)
            m.write(0x7000c104, 1 << 26)
            m.write(0x7000c140, 0xc000001a | (position << 16))
            m.call(0x281350)  # Stock wheel interrupt handler.
            m.call(0xdd018)   # Stock motion accumulator and timing samples.

        def dispatch():
            m.call(0x24910c, EVENT)  # Fresh, undecoded native wheel event.
            self.assertEqual(m.call(m.read(songs.LIST_VTABLE + 0x30), listing, EVENT), 1)

        sample(0)
        for _ in range(60):
            for _ in range(4):
                sample(4)
            dispatch()
            self.assertFalse(self.popup(m))
        self.assertGreater(self.logical(m), 47)
        self.assertFalse(any(event[0] == 'start' for event in m.timers))
        for _ in range(60):
            for _ in range(4):
                sample(-4)
            before = self.selected(m, listing)
            dispatch()
            if self.popup(m):
                break
        # Native reversal/velocity gates may scroll normally before entry.
        # Check the run chosen from the row at the qualifying event, not
        # a fabricated promise that the first reverse packet enters mode.
        target = 43 if before > 43 else 22 if before > 22 else 1
        captions = {43: '11', 22: '10', 1: '1'}
        self.assertEqual((self.selected(m, listing), self.popup(m)), (target, captions[target]))
        while target > 1:
            target -= 21
            sample(-4)
            dispatch()
            self.assertEqual((self.selected(m, listing), self.popup(m)), (target, captions[target]))

    def test_registered_provider_without_app_cancels_disc_mode(self):
        m, listing = self.make_list()
        m.write(APP_NATIVE, 0)
        self.wheel(m, listing, 64)
        self.assert_closed(m, listing)

    def test_native_fast_wheel_jumps_to_next_disc_with_numeric_popup(self):
        m, listing = self.make_list()
        self.assertEqual(self.selected(m, listing), 1)
        self.assertEqual(self.paint(m, listing), ([(1, 0, 24)], [1]))
        self.assertEqual(self.wheel(m, listing, 64), 1)
        self.assertEqual(self.selected(m, listing), 22)
        self.assertEqual(self.logical(m), 20)
        self.assertEqual(self.popup(m), '10')
        self.assertEqual(m.clicks, [1])
        self.assertIn(('start', listing + 0x104), m.timers)
        self.assertEqual(self.paint(m, listing), ([(10, 0, 24)], [22]))

    def event(self, m, listing, *payload):
        m.write(EVENT, *payload)
        return m.call(m.read(songs.LIST_VTABLE + 0x30), listing, EVENT)

    def assert_closed(self, m, listing, model=None):
        self.assertFalse(self.popup(m))
        self.assertEqual(m.cpu.mem_read(listing + 0x12c, 1), b'\0')
        self.assertEqual(m.read(listing + 0xfc), 0xffffffff)
        self.assertIn(('stop', listing + 0x104), m.timers)
        for key in range(0x281, 0x285):
            self.assertEqual(m.call(0x206ed4, PROVIDER, key), 0)
        if model:
            self.assertEqual(m.call(0x149ec4, model, PROVIDER), 0)

    def test_twelve_imported_discs_show_correct_numeric_popup_and_song(self):
        m, listing = self.make_list(tuple((number, 20) for number in range(1, 13)))
        self.assertEqual(self.paint(m, listing), ([(1, 0, 24)], [1]))
        for disc in range(2, 13):
            self.wheel(m, listing, 64 if disc == 2 else 4)
            self.assertEqual(self.popup(m), str(disc))
            self.assertEqual(self.logical(m), (disc - 1) * 20)
            row = (disc - 1) * 21 + 1
            self.assertEqual(self.paint(m, listing), ([(disc, 0, 24)], [row]))

    def test_contact_magnitude_and_native_cadence_gate_remain_in_effect(self):
        for delta, flags, velocity, ordinary in ((48, STROKE, 800, 12), (64, 0, 800, 16),
                                                 (64, STROKE, 0, 1), (64, STROKE, 10000, 16)):
            with self.subTest(delta=delta, flags=flags, velocity=velocity):
                m, listing = self.make_list()
                self.assertEqual(self.wheel(m, listing, delta, flags=flags, velocity=velocity), 1)
                self.assertFalse(self.popup(m))
                self.assertNotIn(('start', listing + 0x104), m.timers)
                self.assertEqual(self.logical(m), ordinary)
        m, listing = self.make_list()
        self.assertEqual(self.wheel(m, listing, 64, velocity=9999), 1)
        self.assertEqual(self.popup(m), '10')

    def test_unfiltered_songs_keep_the_stock_letter_popup(self):
        for image in (self.stock, self.image):
            with self.subTest(stock=image is self.stock):
                m, listing = self.make_list(album='', screen=songs.ALL_SONGS, image=image)
                storage_copy = m.hooks[0x9eff8]

                def alphabetical_title():
                    if m.reg(0) != songs.DB + 0xb4:
                        return storage_copy()
                    name = ('Alpha', 'Beta', 'Charlie')[(m.reg(1) - 1) // 20]
                    data = name.encode('utf-16le')
                    m.cpu.mem_write(m.reg(2), data)
                    m.write(m.reg(3), len(data))
                    return 0

                m.hook(0x9eff8, alphabetical_title)
                self.select(m, listing, 15)
                gestures = ((64, STROKE, 31, ''), (64, STROKE, 47, 'C'),
                            (4, 0, 47, ''), (4, 0, 48, ''))
                for delta, flag, row, label in gestures:
                    self.assertEqual(self.wheel(m, listing, delta, flags=flag), 1)
                    self.assertEqual(self.selected(m, listing), row)
                    self.assertEqual(self.popup(m), label)

    def test_unchanged_disc_caption_avoids_duplicate_copies_but_refreshes_timer(self):
        m, listing = self.make_list()
        published, copies = [], []
        model = m.read(listing + 0xa4)
        def observe(cpu, pc, size, context):
            if pc == 0x17f6a4: published.append(pc)
            if pc == 0x223744 and m.reg(0) == model + 0x24: copies.append(pc)
        m.cpu.hook_add(UC_HOOK_CODE, observe)
        self.wheel(m, listing, 64)
        self.assertEqual(self.popup(m), '10')
        self.assertEqual(len(copies), 1)
        for _ in range(4):
            m.call(self.symbols['discs_fast_publish'], listing)
        self.assertEqual(len(copies), 1)
        self.assertEqual(len(published), 5)
        self.assertEqual(self.popup(m), '10')
        self.wheel(m, listing, 4)
        self.assertEqual(self.popup(m), '11')
        self.assertEqual(len(copies), 2)
        m.clicks.clear(); m.timers.clear()
        for _ in range(4):
            self.wheel(m, listing, 4)
        self.assertEqual(self.selected(m, listing), 43)
        self.assertEqual(self.popup(m), '11')
        self.assertEqual(m.clicks, [])
        self.assertEqual(m.timers, [('start', listing + 0x104)] * 4)
        self.assertEqual(len(copies), 2)
        self.assertEqual(len(published), 10)

    def test_numeric_caption_republishes_after_hide_bitmap_or_binding_change(self):
        m, listing = self.make_list()
        self.wheel(m, listing, 64)
        model = m.read(listing + 0xa4)
        published = []
        m.cpu.hook_add(UC_HOOK_CODE, lambda cpu, pc, size, context:
                       published.append(pc) if pc == 0x17f6a4 else None)
        for reason in ('hidden', 'bitmap', 'rebound'):
            with self.subTest(reason=reason):
                if reason == 'hidden':
                    m.cpu.mem_write(model + 0x20, b'\0')
                elif reason == 'bitmap':
                    songs.half(m, model + 0x2c, 0x5f5d)
                else:
                    other = m.call(0x17f860, songs.OUT + 0x1000, 0, 0)
                    m.call(0x17f58c, other, 1)
                before = len(published)
                m.call(self.symbols['discs_fast_publish'], listing)
                self.assertEqual(len(published), before + 1)
                self.assertEqual(self.popup(m), '10')
                self.assertEqual(bytes(m.cpu.mem_read(model + 0x2c, 2)), b'\0\0')
                for key in range(0x281, 0x285):
                    self.assertEqual(m.call(0x206ed4, PROVIDER, key), model)

    def test_partial_popup_ownership_keeps_native_publication_and_rebind_rules(self):
        for key in range(0x281, 0x285):
            with self.subTest(key=hex(key)):
                m, listing = self.make_list()
                self.wheel(m, listing, 64)
                model = m.read(listing + 0xa4)
                other = m.call(0x17f860, songs.OUT + 0x1000, 0, 0)
                m.call(0x1d2c44, songs.APP, key, key + 4, other)
                published = []
                m.cpu.hook_add(UC_HOOK_CODE, lambda cpu, pc, size, context:
                               published.append(pc) if pc == 0x17f6a4 else None)
                m.call(self.symbols['discs_fast_publish'], listing)
                self.assertEqual(len(published), 1)
                for alias in range(0x281, 0x285):
                    expected = other if key != 0x282 and alias == key else model
                    self.assertEqual(m.call(0x206ed4, PROVIDER, alias), expected)

    def test_digits_and_reversals_follow_contiguous_runs_in_album_order(self):
        m, listing = self.make_list(((1, 20), (10, 20), (65535, 20), (2, 20), (10, 20)))
        for delta, row, number in ((64, 22, '10'), (4, 43, '65535'), (4, 64, '2'),
                                   (4, 85, '10'), (4, 85, '10'), (-4, 64, '2'),
                                   (-4, 43, '65535'), (-4, 22, '10'), (-4, 1, '1')):
            with self.subTest(row=row, number=number):
                self.assertEqual(self.wheel(m, listing, delta), 1)
                self.assertEqual(self.selected(m, listing), row)
                # Invalidate the producer's stack before reading the model's
                # native retained copy through the provider.
                m.cpu.mem_write(STACK - 0x300, b'X' * 0x300)
                self.assertEqual(self.popup(m), number)
                self.assertEqual(self.paint(m, listing), ([(int(number), 0, 24)], [row]))
        self.assertEqual(m.clicks, [1] * 8)

    def test_reverse_inside_disc_and_forward_in_final_disc(self):
        for current, delta, target, number, clicks in ((50, 64, 62, '', [16]),
                                                        (35, -64, 22, '10', [1]),
                                                        (22, -64, 1, '1', [1])):
            with self.subTest(current=current, delta=delta):
                m, listing = self.make_list()
                self.select(m, listing, current)
                self.wheel(m, listing, delta)
                self.assertEqual(self.selected(m, listing), target)
                self.assertEqual(self.popup(m), number)
                self.assertEqual(m.clicks, clicks)

    def test_native_timer_expiry_and_ordinary_wheel_restore_normal_selection(self):
        m, listing = self.make_list()
        self.wheel(m, listing, 64)
        self.assertEqual(self.popup(m), '10')
        # Blink has its own native timer and still reaches the stock handler.
        self.assertEqual(self.event(m, listing, TIMER_VTABLE, 9, listing + 0xcc), 1)
        self.assertEqual(self.popup(m), '10')
        m.clock += 2000
        self.assertEqual(self.event(m, listing, TIMER_VTABLE, 9, listing + 0x104), 1)
        self.assert_closed(m, listing)
        # The stock accelerator reset accumulates the first small event and
        # resumes a one-song move on the second.
        self.assertEqual(self.wheel(m, listing, 4, flags=0), 1)
        self.assertEqual(self.selected(m, listing), 22)
        self.assertEqual(self.wheel(m, listing, 4, flags=0), 1)
        self.assertEqual(self.selected(m, listing), 23)
        self.assertFalse(self.popup(m))
        self.wheel(m, listing, 64)
        self.assertEqual(self.popup(m), '11')
        self.assertEqual(self.wheel(m, listing, -4, flags=0), 1)
        self.assertEqual(self.selected(m, listing), 43)
        self.assertEqual(self.wheel(m, listing, -4, flags=0), 1)
        self.assertEqual(self.selected(m, listing), 41)
        self.assert_closed(m, listing)

    def test_key_hide_disposal_and_query_change_close_only_own_popup(self):
        for trigger in ('key', 'hide', 'destroy', 'freed-query', 'query-notification', 'absent-row'):
            with self.subTest(trigger=trigger):
                m, listing = self.make_list()
                model = m.read(listing + 0xa4)
                self.wheel(m, listing, 64)
                self.assertEqual(self.popup(m), '10')
                if trigger == 'key':
                    # A native type-4 Menu key falls through the ordinary
                    # Songs handler. The extension consumes no key.
                    self.assertEqual(self.event(m, listing, KEY_VTABLE, 4, 0, 0, 0x6e << 16), 0)
                elif trigger == 'hide':
                    self.visibility(m, listing, 0)
                elif trigger == 'destroy':
                    # The synthetic display ancestor only supplies paint
                    # geometry. The OS display is detached during disposal.
                    m.hook(0x255b18, lambda: 0)
                    m.call(m.read(songs.LIST_VTABLE), listing)
                    self.assertEqual(m.read(listing + 0xa4), 0)
                elif trigger == 'freed-query':
                    m.call(0xa376c, songs.QUERY)
                    self.assertEqual(m.read(songs.QUERY + 0xc88), 0)
                    self.event(m, listing, TIMER_VTABLE, 9, listing + 0x104)
                elif trigger == 'query-notification':
                    self.event(m, listing, 0x676994, 13, songs.VM_MAX, 0x6381)
                else:
                    m.write(listing + 0xa8, 0)
                    self.event(m, listing, TIMER_VTABLE, 9, listing + 0x104)
                self.assert_closed(m, listing, model)

    def test_closing_disc_popup_preserves_a_successor_model_binding(self):
        m, listing = self.make_list()
        first_model = m.read(listing + 0xa4)
        self.wheel(m, listing, 64)
        _, successor = self.view(m, songs.ALL_SONGS)
        next_model = m.read(successor + 0xa4)
        self.visibility(m, successor, 1)
        for key in range(0x281, 0x285):
            self.assertEqual(m.call(0x206ed4, PROVIDER, key), next_model)
        self.event(m, listing, TIMER_VTABLE, 9, listing + 0x104)
        for key in range(0x281, 0x285):
            self.assertEqual(m.call(0x206ed4, PROVIDER, key), next_model)
        self.assertEqual(m.call(0x149ec4, first_model, PROVIDER), 0)
        self.assertEqual(m.call(0x149ec4, next_model, PROVIDER), 1)

    def test_single_run_untagged_and_deselected_rows_do_not_enter_disc_mode(self):
        for runs, album in ((((2, 24),), 'Alpha'), (((1, 20), (0, 20), (2, 20)), 'Alpha'),
                            (((1, 20), (2, 20)), '')):
            with self.subTest(runs=runs, album=album):
                m, listing = self.make_list(runs, album=album)
                self.assertEqual(self.wheel(m, listing, 64), 1)
                self.assertEqual(self.selected(m, listing), 17)
                self.assertFalse(self.popup(m))
                self.assertNotIn(('start', listing + 0x104), m.timers)
        m, listing = self.make_list()
        row = m.read(listing + 0xa8)
        m.cpu.mem_write(row + 0xa8, b'\0')  # Native 0x2533f4 selection-valid byte.
        self.assertEqual(m.call(0x257250, listing), 0)
        self.assertEqual(m.call(self.symbols['discs_fast_begin'], listing), 0)
        m.write(listing + 0xa8, 0)
        self.assertEqual(m.call(self.symbols['discs_fast_begin'], listing), 0)


if __name__ == '__main__':
    unittest.main(argv=[arg for arg in sys.argv if arg != '--core-only'], verbosity=2)
