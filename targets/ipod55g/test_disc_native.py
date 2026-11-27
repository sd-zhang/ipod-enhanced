"""Run the 5.5G album separators through stock ARM list, query and script code."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from unicorn import arm_const as arm
from image import load_stock, resources, word
from layout import SOURCE, finish, install_elf, link
from actions import records
from research_actions import APP, QUERY, RENDERER, UI, VMWidgets, STACK
from test_actions_native import HEADER, ORDER, QUEUE, TRUE, seed

ROOT = Path(__file__).resolve().parents[2]
SONGS = 0x5fa3
ALL_SONGS = 0x5fa2
MODEL_GET = 0x66b628 + 0x5c
LIST_VTABLE = 0x677290
RENDERER_VTABLE = 0x66fa6c
DB = 0x10080000
OUT = 0x10090000
EVENT = OUT + 0x100
VM_MAX = 0x564d6178
STR = 0x53747220
BMAP = 0x424d6170


def signed(value):
    return struct.unpack('<i', struct.pack('<I', value))[0]


def half(m, address, value):
    m.cpu.mem_write(address, struct.pack('<H', value))


def import_track_metadata(m, track, disc, total, *, year=2023, bitrate=256,
                          endian='<', header_length=0x1b4, track_id=123):
    """Run Apple's header read, normalization and scalar import, not field seeding.

    The file-read and track-constructor boundaries are supplied. The native
    importer chooses the destination fields, including endian conversion and
    zero-filling fields outside a short declared header.
    """
    header = bytearray(0x1b4)
    header[:4] = b'mhit' if endian == '<' else b'tihm'
    for offset, value in ((4, header_length), (8, len(header)), (0x10, track_id),
                          (0x14, 1), (0x34, year), (0x38, bitrate),
                          (0x5c, disc), (0x60, total)):
        struct.pack_into(endian + 'I', header, offset, value)
    context = OUT + 0x1400
    m.cpu.mem_write(context + 0x40000 + 0xd7, bytes([endian == '>']))
    previous = {at: m.hooks.get(at) for at in (0xbfa84, 0xa96c0)}
    def read_header():
        assert m.reg(0) == context and m.reg(2) == len(header)
        m.cpu.mem_write(m.reg(1), bytes(header))
        return 0
    m.hook(0xbfa84, read_header)
    m.hook(0xa96c0, lambda: track)
    try:
        m.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        m.cpu.reg_write(arm.UC_ARM_REG_R6, DB)
        m.cpu.reg_write(arm.UC_ARM_REG_R7, context + 0x40000)
        m.cpu.reg_write(arm.UC_ARM_REG_R9, context)
        m.cpu.emu_start(0xd798c, 0xd7a94, count=10000)
        assert m.cpu.reg_read(arm.UC_ARM_REG_PC) == 0xd7a94, 'native metadata importer did not finish'
        assert m.cpu.reg_read(arm.UC_ARM_REG_SP) == STACK
    finally:
        for at, hook in previous.items():
            if hook is None: m.hooks.pop(at, None)
            else: m.hooks[at] = hook


class DiscSeparatorsNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = load_stock(ROOT / 'iPod_25.1.3.ipsw').osos
        cls.symbols = {}
        if '--stock-only' in sys.argv:
            cls.image = cls.stock
            return
        import disc_patch
        evidence = ROOT / 'build/ipod55g-disc-separators-01/runtime'
        evidence.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='native-', dir=evidence))
        sources = [SOURCE / name for name in ('disc_separators.c', 'disc_hooks.S', 'disc_render.c')]
        if not sources[-1].exists():
            sources[-1] = directory / 'renderer_boundary.c'
            sources[-1].write_text('void discs_header_draw(void *l,const int *c,const int *r,unsigned d)'
                                   '{(void)l;(void)c;(void)r;(void)d;}\n')
        elf = link(sources, directory, 'discs_draw_row')
        image, cls.symbols = install_elf(cls.stock, elf)
        disc_patch.apply(image, cls.symbols)
        cls.image = finish(image)

    def library(self, rows, album='Alpha', image=None):
        m = VMWidgets(self.image if image is None else image)
        seed(m)
        m.hooks.pop(0x1e7f08)
        m.write(UI, 0x670f54)
        m.write(UI + 0xc0, -1)
        m.write(UI + 0x3ec, -1)
        m.write(QUERY + 0xcf8, DB)
        m.write(QUERY + 0xce4, QUEUE)
        m.write(QUERY + 0xc34, 3)
        m.write(QUERY + 0xc28, -1)
        m.write(0x1081da08 + 0x20, 1)
        m.cpu.mem_write(UI + 0x48d, b'\x09' if album else b'\0')
        m.hook(0x1d3050, lambda: {8: RENDERER, 0x5e00: RENDERER, 0x6380: UI}.get(m.reg(0), 0))
        m.hook(0x193090, lambda: 0)
        m.hook(0x13d524, lambda: 0)  # UI event delivery.
        m.hook(0x214140, lambda: OUT + 0x600)  # Initialized message pool.
        m.hook(0x141e30, lambda: 0)  # Display invalidation delivery.
        m.hook(0x151c98, lambda: 0)  # OS timer cancellation.
        m.hook(0x151694, lambda: 0)  # OS timer scheduling.
        for address in (0xa8178, 0x7a794):
            m.hook(address, m.allocate)
        for address in (0xa7200, 0xb9f20):
            m.hook(address, lambda: 0)
        entries = []
        titles = {}
        names = {}
        for i, (name, disc, total) in enumerate(rows):
            track = 0x10092000 + i * 0x140
            entry = 0x100b0000 + i * 0x40
            m.write(track, DB)
            m.write(track + 0x28, i + 1)
            m.write(track + 0x34, i + 1)
            import_track_metadata(m, track, disc, total, track_id=i + 1)
            m.write(entry, QUEUE)
            m.write(entry + 0x10, 100 + i)
            m.write(entry + 0x20, track)
            name_bytes = name.encode('utf-16le')
            address = 0x100c0000 + i * 0x20
            m.cpu.mem_write(address, name_bytes)
            names[i + 1] = (address, name_bytes)
            titles[i + 1] = f'Track {i + 1}'.encode('utf-16le')
            entries.append(entry)

        def storage_pointer():
            address, data = names[m.reg(1)]
            self.assertEqual(m.reg(0), DB + 0x138)
            m.write(m.reg(2), address)
            m.write(m.reg(3), len(data))
            return 0

        def storage_copy():
            if m.reg(0) == DB + 0xb4:
                data = titles[m.reg(1)]
            else:
                self.assertEqual(m.reg(0), DB + 0x138)
                data = names[m.reg(1)][1]
            maximum = m.read(m.cpu.reg_read(arm.UC_ARM_REG_SP))
            data = data[:maximum]
            m.cpu.mem_write(m.reg(2), data)
            m.write(m.reg(3), len(data))
            return 0

        m.hook(0x9efe0, storage_pointer)
        m.hook(0x9eff8, storage_copy)
        half(m, HEADER + 0x32, len(entries))
        m.write(ORDER + 0x10, *entries)
        m.write(QUEUE + 0x308, ORDER)
        m.write(QUEUE + 0x30c, ORDER)
        m.write(QUEUE + 0x310, ORDER)
        m.write(QUEUE + 0x314, ORDER)
        m.cpu.mem_write(QUERY + 0x628, struct.pack('<H', len(album)) + album.encode('utf-16le'))
        return m, entries

    def build_index(self, m):
        self.assertEqual(m.call(0xcc7a8, QUERY), 0)
        count = m.read(QUERY + 0xc90)
        m.write(UI + 0xc0, count)
        return m.read(QUERY + 0xc88), count

    def view(self, m, screen=SONGS):
        a, n = resources(m.image)['View'][0x5e15]
        record = next(r for r in records(m.image[a:a+n]) if word(r, 12) == screen)
        root = m.construct(record)
        scroll = m.children(root)[1]
        listing = m.children(scroll)[0]
        self.assertEqual(m.read(listing), LIST_VTABLE)
        m.write(RENDERER + 0x24, root, listing)
        # Supply framebuffer-dependent frame calculation. The stock View
        # factory, list bounds, row template, and selection still execute ARM.
        m.write(root + 0x58, 0, 0, 48, 320)
        m.write(root + 0x88, 0, 0, 48, 320)
        m.write(scroll + 0x58, 0, 0, 48, 320)
        m.write(scroll + 0x88, 0, 0, 48, 320)
        m.write(listing + 0x58, 0, 0, 24 * m.read(listing + 0xb8), 320)
        m.write(listing + 0x88, 0, 0, 48, 320)
        m.write(listing + 0x20, (m.read(listing + 0x20) & ~0x1800) | 0x800)
        for off in (0xa8, 0xac):
            m.write(m.read(listing + off) + 0x58, 0, 0, 24, 300)
        return root, listing

    def select(self, m, listing, row):
        m.call(m.read(LIST_VTABLE + 0x138), listing, row)

    def selected(self, m, listing):
        return signed(m.call(0x257260, listing))

    def visible_count(self, m, key=0x6381):
        return m.call(0x21f254, UI, VM_MAX, key)

    def logical(self, m):
        return signed(m.call(m.read(RENDERER_VTABLE + 0x160), RENDERER))

    def test_native_import_distinguishes_disc_fields_from_year_and_bitrate(self):
        m, entries = self.library([('Alpha', 1, 1)])
        track = m.read(entries[0] + 0x20)
        for endian in ('<', '>'):
            for length, expected in ((0x1b4, (10, 12)), (0x60, (10, 0)), (0x5c, (0, 0))):
                with self.subTest(endian=endian, header_length=hex(length)):
                    import_track_metadata(m, track, 10, 12, year=2023, bitrate=320,
                                          endian=endian, header_length=length)
                    self.assertEqual(struct.unpack('<2H', m.cpu.mem_read(track + 0x84, 4)), expected)
                    self.assertEqual(struct.unpack('<2H', m.cpu.mem_read(track + 0x74, 4)), (320, 2023))

    def test_twelve_imported_discs_share_a_year_but_have_distinct_headings(self):
        m, _ = self.library([('Alpha', disc, 12) for disc in range(1, 13)])
        index, songs = self.build_index(m)
        footer = index + 4 * songs
        self.assertEqual(m.read(footer + 4), 12)
        self.assertEqual([struct.unpack('<2H', m.cpu.mem_read(footer + 8 + 4*i, 4))
                          for i in range(12)], [(i, i + 1) for i in range(12)])
        _, listing = self.view(m)
        self.assertEqual(m.read(listing + 0xb8), 24)
        for disc in range(1, 13):
            row = 2 * disc - 1
            self.select(m, listing, row)
            self.assertEqual(self.logical(m), disc - 1)
            self.assertEqual(self.paint(m, listing), ([(disc, 0, 24)], [row]))

    def test_single_disc_and_missing_tags_ignore_nonzero_year_and_bitrate(self):
        for disc, total in ((1, 1), (1, 0), (0, 0), (0, 12)):
            with self.subTest(disc=disc, total=total):
                m, entries = self.library([('Alpha', disc, total)] * 3)
                for i, entry in enumerate(entries):
                    import_track_metadata(m, m.read(entry + 0x20), disc, total,
                                          year=2021 + i, bitrate=128 * (i + 1))
                index, songs = self.build_index(m)
                self.assertEqual(m.read(index + 4*songs + 4), 0)
                _, listing = self.view(m)
                self.assertEqual(m.read(listing + 0xb8), 3)
                self.select(m, listing, 0)
                self.assertEqual(self.logical(m), 0)

    def test_album_constructor_publishes_visual_count_with_native_song_index(self):
        m, entries = self.library([('Alpha', 1, 2), ('Other', 3, 3), ('Alpha', 2, 2)])
        index, count = self.build_index(m)
        self.assertEqual(count, 2)
        self.assertEqual([m.read(index + 4*i) for i in range(count)], [entries[0], entries[2]])
        for screen in (SONGS, ALL_SONGS):
            with self.subTest(screen=hex(screen)):
                _, listing = self.view(m, screen)
                self.assertEqual(m.read(listing + 0xb8), 4)
        # Alternate stock property adds two native synthetic rows to songs.
        self.assertEqual(self.visible_count(m, 0x6399), 4)

    def test_tag_and_bounded_run_policy(self):
        for discs, album, extra in (
            ([(1, 1), (1, 1)], 'Alpha', 0), ([(1, 1), (0, 2), (2, 2)], 'Alpha', 0),
            ([(2, 2)], 'Alpha', 1), ([(1, 2)], 'Alpha', 1),
            ([(3, 0), (1, 0), (3, 0)], 'Alpha', 3), ([(1, 2), (2, 2)], '', 0),
            ([(1+i%2, 2) for i in range(256)], 'Alpha', 256),
            ([(1+i%2, 2) for i in range(257)], 'Alpha', 0),
        ):
            with self.subTest(tracks=len(discs), first=discs[0], album=album):
                m, _ = self.library([('Alpha', n, total) for n, total in discs], album)
                self.build_index(m)
                self.assertEqual(self.visible_count(m), len(discs) + extra)

    def test_native_invalidation_frees_group_data_and_allocation_failure_retries(self):
        m, _ = self.library([('Alpha', 1, 2), ('Alpha', 2, 2), ('Other', 1, 1), ('Other', 1, 1)])
        m.hook(0xa8178, lambda: 0)
        self.assertEqual(signed(m.call(0xcc7a8, QUERY)), -108)
        self.assertEqual(m.read(QUERY + 0xc88), 0)
        self.assertEqual(self.visible_count(m), 0)
        m.hook(0xa8178, m.allocate)
        old, _ = self.build_index(m)
        self.assertEqual(self.visible_count(m), 4)
        m.call(0xa376c, QUERY)
        self.assertIn(old, m.freed)
        self.assertEqual(m.read(QUERY + 0xc88), 0)
        self.assertEqual(self.visible_count(m), 2)
        # This fixture supplies the native playlist cache's traversal boundary.
        def reset_cache():
            for i in range(4):
                m.cpu.mem_write(m.read(ORDER + 0x10 + 4*i) + 0x1c, b'\0')
            return 0
        m.hook(0xb79e0, reset_cache)
        m.call(0xa37a8, QUERY)
        m.cpu.mem_write(QUERY + 0x628, b'\x05\0O\0t\0h\0e\0r\0')
        fresh, count = self.build_index(m)
        self.assertNotEqual(fresh, old)
        self.assertEqual(count, 2)
        self.assertEqual(self.visible_count(m), 2)

    def test_model_uses_native_titles_and_icons_for_the_correct_song(self):
        m, entries = self.library([('Alpha', 1, 2), ('Alpha', 2, 2)])
        self.build_index(m)
        _, listing = self.view(m)
        model = m.read(listing + 0xa4)
        self.assertEqual(m.read(model + 0x1c), listing)
        # The provider returns a pointer to its UTF-8 caption scratch storage.
        for visual, logical in ((1, 0), (3, 1)):
            m.write(model + 0x14, visual)
            self.assertEqual(m.call(m.read(MODEL_GET), model, STR, 0x641e, OUT), 1)
            address = m.read(OUT)
            self.assertIn(f'Track {logical + 1}'.encode(), bytes(m.cpu.mem_read(address, 16)))
        for visual in (0, 2):
            m.write(model + 0x14, visual)
            for key in (0x641f, 0x63eb):
                self.assertEqual(m.call(m.read(MODEL_GET), model, BMAP, key, OUT), 1)
                self.assertEqual(m.read(OUT), 0)
        # An unrelated/wrong-owner list must use the original row even if its
        # model is borrowed from the live Songs view.
        m.write(model + 0x14, 1)
        m.write(listing + 0x1c, 0x6399)
        self.assertEqual(m.call(m.read(MODEL_GET), model, STR, 0x641e, OUT), 1)
        address = m.read(OUT)
        self.assertIn(b'Track 2', bytes(m.cpu.mem_read(address, 16)))
        self.assertEqual(m.read(entries[1] + 0x10), 101)

    def paint(self, m, listing):
        headers, ordinary = [], []

        def header():
            rect = struct.unpack('<4i', m.cpu.mem_read(m.reg(2), 16))
            headers.append((m.reg(3), rect[0], rect[2]))

        def song():
            ordinary.append(m.reg(3))

        m.hook(self.symbols['discs_header_draw'], header)
        m.hook(0x2397a8, song)
        m.write(OUT, 0, 0, 48, 320)
        m.call(0x23b024, listing, 0, OUT)
        return headers, ordinary

    def test_initial_selection_and_first_heading_remain_visible_on_return(self):
        rows = [('Alpha', 1, 2)] * 3 + [('Alpha', 2, 2)] * 3
        m, _ = self.library(rows)
        self.build_index(m)
        _, listing = self.view(m)
        m.hook(0x13ce54, lambda: 0)
        m.hook(0x1d2a7c, lambda: 0)
        m.hook(0x1c2428, lambda: 0)
        m.call(0x19de04, RENDERER, listing)
        self.assertEqual(self.selected(m, listing), 1)
        self.assertEqual(self.logical(m), 0)
        self.assertEqual(self.paint(m, listing), ([(1, 0, 24)], [1]))
        m.call(0x23ab00, listing, 1, 0)
        self.assertEqual(self.selected(m, listing), 2)
        self.assertEqual(signed(m.read(listing + 0x58)), -24)
        m.call(0x23ab00, listing, -1, 0)
        self.assertEqual(self.selected(m, listing), 1)
        self.assertEqual(self.paint(m, listing), ([(1, 0, 24)], [1]))

    def test_second_heading_becomes_visible_with_its_first_song(self):
        rows = [('Alpha', 1, 2)] + [('Alpha', 2, 2)] * 4
        m, _ = self.library(rows)
        self.build_index(m)
        _, listing = self.view(m)
        self.select(m, listing, 6)
        self.assertEqual(self.selected(m, listing), 6)
        m.call(0x23a120, listing, 3)
        self.assertEqual(self.selected(m, listing), 3)
        self.assertEqual(self.paint(m, listing), ([(2, 0, 24)], [3]))
    def test_wheel_skips_headers_before_click_or_boundary_notification(self):
        m, _ = self.library([('Alpha', 1, 2), ('Alpha', 2, 2), ('Alpha', 2, 2)])
        self.build_index(m)
        _, listing = self.view(m)
        feedback = []

        def notify():
            feedback.append(m.reg(1))
            return 0

        m.hook(0x23b60c, notify)
        m.hook(0x1c2428, lambda: feedback.append(('click', m.reg(0))) or 0)
        m.hook(0x151c98, lambda: 0)
        m.hook(0x151694, lambda: 0)
        self.select(m, listing, 1)
        feedback.clear()
        self.assertEqual(m.call(0x23ab00, listing, -1, 0), 0)
        self.assertEqual(self.selected(m, listing), 1)
        self.assertEqual(feedback, [0x4c737453, 0x4c737446])
        feedback.clear()
        m.call(0x23ab00, listing, 1, 0)
        self.assertEqual(self.selected(m, listing), 3)
        self.assertEqual(feedback, [0x4c737453, ('click', 1)])
        feedback.clear()
        m.call(0x23ab00, listing, -1, 0)
        self.assertEqual(self.selected(m, listing), 1)
        self.assertEqual(feedback, [0x4c737453, ('click', 1)])
        # Native new bounds are logical even when a wheel event takes two rows.
        self.assertEqual(m.call(0x23a318, listing, 2), 1)
        self.assertEqual(self.selected(m, listing), 4)

    def test_complete_wheel_dispatch_preserves_native_clamp_and_wrap_feedback(self):
        for wrap, disc_tags, cases in (
            (False, ((1, 2), (2, 2), (2, 2)), (
                (-4, 1, (0x4c737453, 0x4c737446)), (4, 3, (0x4c737453, ('click', 1))),
                (-4, 1, (0x4c737453, ('click', 1))), (4, 3, (0x4c737453, ('click', 1))),
            )),
            (True, ((2, 2),), ((-4, 1, (0x4c737453,)), (4, 1, (0x4c737453,)))),
        ):
            with self.subTest(wrap=wrap):
                m, _ = self.library([('Alpha', disc, total) for disc, total in disc_tags])
                self.build_index(m)
                _, listing = self.view(m)
                if wrap:
                    m.write(listing + 0x20, m.read(listing + 0x20) & ~0x04000000)
                seen = []
                # Provide the receiver registry and clock, while running the
                # stock accelerator constructor, event and movement routines.
                m.write(OUT + 0x700, OUT + 0x720)
                m.write(OUT + 0x73c, 0x222dfc)
                m.hook(0xea594, lambda: OUT + 0x700)
                m.hook(0x1d0aa8, lambda: 0)  # Property notification delivery.
                clock = [1000]
                m.hook(0xcced0, lambda: m.write(m.reg(0), clock[0] * 1000) or 0)
                m.hook(0x1c2428, lambda: seen.append(('click', m.reg(0))) or 0)
                m.hook(0x22c434, lambda: seen.append(m.reg(0)) or 0)
                self.select(m, listing, 1)
                for delta, row, expected in cases:
                    seen.clear()
                    clock[0] += 20
                    m.write(EVENT, 0x677aec, 15, 1, delta, 0, 800)
                    self.assertEqual(m.call(0x23b704, listing, EVENT), 1)
                    self.assertEqual(self.selected(m, listing), row)
                    self.assertEqual(tuple(seen), expected)

    def test_real_songs_scripts_select_playback_and_otg_rows_and_flash_visual(self):
        m, _ = self.library([('Alpha', 1, 2), ('Alpha', 2, 2), ('Alpha', 2, 2)])
        self.build_index(m)
        _, listing = self.view(m)
        m.write(APP, 0x66fe10)
        m.bind(0x1392, 2, APP)
        m.bind(0x1393, 3, RENDERER)
        m.bind(0x13a4, 33, UI)
        m.hook(0x1d27f8, lambda: 0)
        m.hook(0x151c98, lambda: 0)
        m.hook(0x151694, lambda: 0)
        calls = []
        for selector in (0x115, 0x116, 0x117, 0x118, 0x165):
            ptr, virtual = m.member(33, selector)
            m.hook(m.read(0x670f54 + ptr) if virtual else ptr,
                   lambda selector=selector: calls.append((selector, signed(m.reg(1)))) or 1)
        self.select(m, listing, 3)
        for atom, setter in ((4166, 0x115), (4167, 0x116), (4168, 0x117), (4169, 0x118)):
            with self.subTest(script=atom):
                calls.clear()
                # The playback boundary returns false to avoid entering Now
                # Playing. OTG reports success so the native blink still runs.
                if atom in (4166, 4167):
                    ptr, _ = m.member(33, setter)
                    m.hook(ptr, lambda setter=setter: calls.append((setter, signed(m.reg(1)))) or 0)
                self.assertEqual(m.run(atom, 0), TRUE)
                self.assertIn((setter, 1), calls)
                if atom in (4168, 4169):
                    self.assertEqual(m.read(listing + 0xf4), 3)

    def test_restore_keeps_visual_selection(self):
        m, _ = self.library([('Alpha', 1, 2), ('Alpha', 2, 2)])
        self.build_index(m)
        _, listing = self.view(m)
        self.select(m, listing, 3)
        visual = self.selected(m, listing)
        m.hook(0x13ce54, lambda: 0)
        m.hook(0x1d2a7c, lambda: 0)
        m.write(RENDERER + 0x28, 0)
        m.call(0x19de04, RENDERER, listing)
        self.assertEqual(self.selected(m, listing), visual)
        self.assertEqual(visual, 3)
        self.assertEqual(self.logical(m), 1)

    def test_patch_rejects_changed_hook_or_unaligned_symbol_without_partial_edits(self):
        from disc_patch import FALLTHROUGHS, HOOKS, apply

        if not self.symbols:
            self.skipTest('checks extension hook contract')
        source = self.stock.ljust(len(self.image), b'\0')
        for address, *_ in HOOKS + FALLTHROUGHS:
            image = bytearray(source)
            image[address] ^= 1
            before = bytes(image)
            with self.subTest(address=hex(address)), self.assertRaisesRegex(ValueError, hex(address)):
                apply(image, self.symbols)
            self.assertEqual(image, before)
        image = bytearray(source)
        with self.assertRaisesRegex(ValueError, 'placement for discs_draw_row'):
            apply(image, {**self.symbols, 'discs_draw_row': self.symbols['discs_draw_row'] | 1})
        self.assertEqual(image, source)


if __name__ == '__main__':
    unittest.main(argv=[arg for arg in sys.argv if arg != '--stock-only'], verbosity=2)
