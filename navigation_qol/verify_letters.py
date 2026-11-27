"""Execute the letter candidate's ARM and original 4G paths (not device emulation)."""
import argparse
import json
import struct
import unittest
from pathlib import Path

from native_fixture import Library, NativeMachine, QUERY, load_image

LIST, ROW, MODEL, UI = 0x10011000, 0x10012000, 0x10013000, 0x10014000
CACHE, EVENT, TASK = 0x10060000, 0x10016000, 0x10017000


class WheelMachine(NativeMachine):
    def __init__(self, image, groups=b"A" * 20 + b"B" * 20 + b"C" * 20):
        super().__init__(image)
        self.now = 1000
        self.events, self.commits, self.registered = [], [], []
        self.write(LIST, 0x29030)
        self.write(LIST + 0x1c, 0x3304, 0x04000800)
        self.write(LIST + 0x8c, MODEL, ROW, 0)
        self.write(LIST + 0x9c, 0, len(groups) + 1)
        self.write(ROW, 0x22eac)
        self.write(ROW + 0x10, LIST)
        self.write(ROW + 0x8c, 0, 1)
        self.write(MODEL, 0x22fd4)
        self.write(MODEL + 0x18, UI)
        self.write(UI, 0x26294)
        self.write(UI + 0x1c, QUERY)
        self.write(QUERY + 0xa8c, CACHE, len(groups))
        self.cpu.mem_write(CACHE + 4 * len(groups), groups)
        self.hook(0xd9a60, self.allocate)
        self.hook(0xd9a50, self.free)
        self.hook(0x6c218, lambda: ((self.now * 1000) & 0xffffffff) // 1000)
        self.hook(0x87cec, lambda: (self.now * 1000) & 0xffffffff)
        self.hook(0x87654, lambda: 0)
        self.hook(0x79c9c, lambda: 0x10018000)
        self.write(0x10018000, 0x10019000)
        self.write(0x10019018, 0x1000)
        self.write(0x10019024, 0x1004)
        self.hook(0x1001a000, lambda: self.registered.append(self.read(self.reg(1))))
        self.hook(0x1001a004, lambda: self.registered.remove(self.read(self.reg(1))))
        self.write(TASK + 0x1c, TASK + 0x100)
        self.hook(0x9f9a8, lambda: TASK)
        self.hook(0xd10e8, self.select)
        self.hook(0x625f4, lambda: self.events.append(("click", self.reg(0))))
        self.hook(0x9107c, lambda: self.events.append(("invalidate", self.reg(0))))
        for at, name in ((0xcdd84, "interval"), (0xd2bd0, "start"), (0xd3aa0, "stop")):
            self.hook(at, lambda name=name: self.events.append((name, self.reg(0), self.reg(1))))

    def divide(self):
        d, n = self.reg(0), self.reg(1)
        d = d if d < 0x80000000 else d - 0x100000000
        n = n if n < 0x80000000 else n - 0x100000000
        assert d
        q = abs(n) // abs(d) * (-1 if (n < 0) != (d < 0) else 1)
        from unicorn.arm_const import UC_ARM_REG_R1
        self.cpu.reg_write(UC_ARM_REG_R1, (n - q * d) & 0xffffffff)
        return q & 0xffffffff

    def select(self):
        row = self.reg(1)
        assert self.read(MODEL + 0x14) == self.read(ROW + 0x8c)
        assert self.read(LIST + 0x9c) <= row < self.read(LIST + 0xa0)
        self.write(ROW + 0x8c, row)
        self.write(MODEL + 0x14, row)
        self.commits.append(row)

    def virtual(self, slot, *args):
        table = self.read(LIST)
        return self.call((table + self.read(table + slot)) & 0xffffffff, LIST, *args)

    def wheel(self, raw=80, advance=200):
        self.now = (self.now + advance) & 0xffffffff
        self.write(EVENT + 4, 0x5765656c)
        self.cpu.mem_write(EVENT + 8, struct.pack("<BBhI", 1, 0, raw, 100))
        return self.call(0x86670, LIST, EVENT)

    def timer(self, advance=1000):
        self.now = (self.now + advance) & 0xffffffff
        self.write(EVENT + 4, 0x74696d65, self.read(LIST + 0xe4) + 48)
        return self.virtual(0x24, EVENT)

    def graphics(self, clip=(20, 0, 110, 160)):
        """Native context, clipping, color conversion and 1bpp rectangle raster.

        Supplies a RAM framebuffer and display locks, not a screenshot/device.
        The original list-content draw is supplied separately in marquee tests.
        """
        context, surface, locks = 0x10030000, 0x10031000, 0x10032000
        framebuffer, clip_at = 0x10070000, 0x10033000
        self.write(LIST + 0x58, 20, 0, 110, 160)
        self.write(LIST + 0x78, 20, 0, 110, 160)  # native cached visible bounds
        self.write(clip_at, *clip)
        self.write(context + 0xa4, 1)  # clip enabled
        self.write(0x103cdcc8, surface)
        # Native bitmap descriptor: data, stride, bpp, log2(bpp), bounds, locks.
        self.write(surface + 4, framebuffer, 20, 1, 0, 0, 0, 128, 160, locks)
        self.hook(0xc0634, lambda: context)
        self.hook(0x2e384, lambda: None)  # display lock
        self.hook(0xbb734, lambda: None)  # display unlock
        self.hook(0x5cdc4, lambda: None)  # existing list content, tested separately
        self.cpu.mem_write(framebuffer - 16, b"\xa5" * 16 + bytes(2560) + b"\xa5" * 16)
        return framebuffer, clip_at


class LetterWheel(unittest.TestCase):
    def test_fast_wheel_moves_to_next_letter_not_sixteen_rows(self):
        m = WheelMachine(self.image)
        self.assertEqual(m.wheel(), 1)
        self.assertEqual(m.commits, [21])
        self.assertTrue(any(e[0] == "start" for e in m.events))

    def test_direction_reversal_and_first_group_all_row(self):
        m = WheelMachine(self.image)
        m.wheel()
        m.wheel(-80)
        self.assertEqual(m.commits, [21, 0])

    def test_nonmonotonic_groups_are_not_skipped(self):
        m = WheelMachine(self.image, b"A" * 5 + b"B" * 3 + b"A" * 60 + b"C")
        m.wheel()
        self.assertEqual(m.commits, [6])
        m.wheel()
        self.assertEqual(m.commits, [6, 9])

    def test_slow_wheel_and_small_lists_remain_native(self):
        m = WheelMachine(self.image)
        m.wheel(5)
        self.assertEqual(m.commits, [1])
        self.assertFalse(any(e[0] == "start" for e in m.events))
        for groups, expected in ((b"ABCDEFGH", 8), (b"", 0), (b"A", 1)):
            m = WheelMachine(self.image, groups)
            m.wheel()
            self.assertEqual(m.read(ROW + 0x8c), expected)
            self.assertFalse(any(e[0] == "start" for e in m.events))

    def test_native_event_dispatch_reaches_letter_wheel(self):
        m = WheelMachine(self.image)
        m.write(EVENT + 4, 0x5765656c)
        m.cpu.mem_write(EVENT + 8, struct.pack("<BBhI", 1, 0, 80, 100))
        casts = []

        def event_cast():
            casts.append(m.reg(1))
            return m.reg(0)

        m.hook(0x5d4e4, event_cast)  # event runtime type service
        m.hook(0x813a8, lambda: 0)  # generic base-widget event tail
        self.assertEqual(m.virtual(0x24, EVENT), 1)
        self.assertEqual(casts, [0x3500])
        self.assertEqual(m.commits, [21])

    def test_allocator_failure_returns_without_dereferencing_null(self):
        m = WheelMachine(self.image)
        m.hook(0xd9a60, lambda: 0)
        self.assertEqual(m.wheel(), 1)
        self.assertEqual(m.read(LIST + 0xe4), 0)
        self.assertEqual(m.commits, [])
        self.assertEqual(m.registered, [])

    def test_timeout_returns_to_single_row_scrolling(self):
        m = WheelMachine(self.image)
        m.wheel()
        self.assertEqual(m.timer(), 1)
        self.assertTrue(any(e[0] == "stop" for e in m.events))
        m.wheel(5, 1)
        self.assertEqual(m.commits, [21, 22])
        before = len([e for e in m.events if e[0] == "start"])
        m.timer()  # stray delivery after mode closed
        self.assertEqual(len([e for e in m.events if e[0] == "start"]), before)

    def test_stale_timeout_cannot_close_refreshed_mode(self):
        m = WheelMachine(self.image)
        m.wheel()
        m.wheel(80, 700)
        m.timer(300)
        self.assertEqual(m.events[-2][0], "interval")
        self.assertEqual(m.events[-2][2], 700)
        m.wheel(-5, 1)
        self.assertEqual(m.commits[-1], 21)

    def test_rate_limit_and_reversal_remain_responsive(self):
        m = WheelMachine(self.image)
        m.wheel()
        m.wheel(80, 10)
        self.assertEqual(m.commits, [21])
        m.wheel(-80, 10)
        self.assertEqual(m.commits, [21, 0])

    def test_clock_wrap_preserves_timeout(self):
        m = WheelMachine(self.image)
        # 6c218 divides the wrapping 32-bit hardware microsecond counter by
        # 1000. Its millisecond value therefore does NOT wrap at 2**32.
        m.now = 4294960
        m.wheel(80, 0)
        m.timer(900)
        self.assertEqual(m.events[-2][2], 100)
        m.timer(100)
        m.wheel(5, 1)
        self.assertEqual(m.commits, [21, 22])

    def test_native_hide_cancels_timer_and_show_does_not_reenter(self):
        m = WheelMachine(self.image)
        m.write(LIST + 0x68, 0x286d8, 0, 0, 0)
        m.wheel()
        m.virtual(0x94, 0)
        self.assertEqual(m.call(0xe3ddc, LIST), 0)
        before = len([e for e in m.events if e[0] == "start"])
        m.timer()
        m.virtual(0x94, 1)
        self.assertEqual(m.call(0xe3ddc, LIST), 1)
        self.assertEqual(len([e for e in m.events if e[0] == "start"]), before)
        m.wheel(5, 1)
        self.assertEqual(m.commits, [21, 22])

    def test_native_key_moves_one_row_after_closing_letters(self):
        m = WheelMachine(self.image)
        m.wheel()
        m.write(EVENT + 0x10, 0x57 << 16)  # native previous-row key
        self.assertEqual(m.virtual(0x160, EVENT), 1)
        self.assertEqual(m.commits, [21, 20])
        self.assertTrue(any(e[0] == "stop" for e in m.events))

    def test_native_destructor_stops_timer_before_accelerator_release(self):
        m = WheelMachine(self.image)
        m.wheel()
        accel = m.read(LIST + 0xe4)
        m.write(LIST + 0x8c, 0)  # model destructor is outside this ownership test
        m.hook(0xfd904, lambda: None)  # base widget destruction, unchanged
        order = []
        m.hook(0xd3aa0, lambda: order.append(("stop", m.reg(0))))
        m.hook(0xd9a50, lambda: order.append(("free", m.reg(0))))
        m.call(0x103140, LIST, 1)
        self.assertEqual(order, [("stop", accel + 48), ("free", accel), ("free", LIST)])
        self.assertEqual(m.registered, [])

    def test_native_stop_purges_its_queued_event_only(self):
        m = WheelMachine(self.image)
        m.wheel()
        timer = m.read(LIST + 0xe4) + 48
        queue, node1, node2, message1, message2 = (0x10017100, 0x10034000,
                                                  0x10034100, 0x10034200, 0x10034300)
        m.write(timer + 0x1c, 0x65787069)  # native 'expi': posted to task queue
        m.write(queue + 0x40, node1, node2)
        m.write(node1, node2)
        m.write(node1 + 0xc, message1, 1)
        m.write(node2 + 0xc, message2, 1)
        for message, token in ((message1, timer), (message2, timer + 1000)):
            m.write(message + 4, 0x74696d65)
            m.write(message + 0x10, token)
        released = []
        del m.hooks[0xd3aa0]  # execute real stop, queue scan and cancel predicate
        for address in (0x9f5d8, 0x9fce4, 0x87b88, 0x87d60, 0x8f7b4):
            m.hook(address, lambda: None)  # OS mutex and hardware timer unlink
        m.hook(0xb1774, lambda: released.append(m.reg(0)))  # OS event pool return
        m.write(LIST + 0x68, 0x286d8, 0, 0, 0)
        m.virtual(0x94, 0)
        self.assertEqual(released, [message1])
        self.assertEqual(m.read(node1 + 0xc), 0)
        self.assertEqual(m.read(node1 + 0x10), 0)
        self.assertEqual(m.read(node2 + 0xc), message2)
        self.assertEqual(m.read(node2 + 0x10), 1)
        self.assertEqual(m.read(timer + 0x1c), 0x73746f70)

    def test_actual_selection_updates_row_model_before_notification(self):
        m = WheelMachine(self.image)
        del m.hooks[0xd10e8]
        m.hook(0xc0634, lambda: 0x10030000)
        m.hook(0xd13a8, lambda: 0)  # no synchronous display transaction
        m.hook(0xe00e4, lambda: None)  # old-row repaint boundary
        m.hook(0xbfdd4, lambda: None)  # viewport repaint boundary
        snapshots = []
        m.hook(0xaba1c, lambda: snapshots.append((m.read(ROW + 0x8c), m.read(MODEL + 0x14))))
        m.wheel()
        self.assertEqual(snapshots, [(21, 21)])
        m.wheel(-80)
        self.assertEqual(snapshots, [(21, 21), (0, 0)])

    def test_unrelated_views_and_invalidated_query_do_not_enter(self):
        for view in (0x3301, 0x3307, 0x330f, 0x3300):
            m = WheelMachine(self.image)
            m.write(LIST + 0x1c, view)
            m.wheel()
            self.assertEqual(m.commits, [16])
            self.assertFalse(any(e[0] == "start" for e in m.events))
        m = WheelMachine(self.image)
        m.wheel()
        m.write(QUERY + 0xa8c, 0, 0)
        m.wheel(5)
        self.assertEqual(m.commits, [21, 22])

    def test_long_group_search_needs_no_name_or_storage_calls(self):
        m = WheelMachine(self.image, b"A" * 5000 + b"B")
        for address in (0x75584, 0x76af8, 0x76c90, 0x6e60c):
            m.hook(address, lambda: self.fail("letter search attempted a name/storage lookup"))
        m.wheel()
        self.assertEqual(m.commits, [5001])
        m.wheel()  # last letter cannot wrap or select count+1
        self.assertEqual(m.commits, [5001])
        m.wheel(-80)
        self.assertEqual(m.commits, [5001, 0])

    def test_native_monochrome_raster_draws_and_clips_letter_box(self):
        for clip in ((20, 0, 110, 160), (50, 70, 70, 90), (20, 0, 40, 160)):
            with self.subTest(clip=clip):
                m = WheelMachine(self.image)
                m.wheel()  # selected letter B
                framebuffer, clip_at = m.graphics(clip)
                m.virtual(0x60, MODEL, clip_at)
                pixels = bytes(m.cpu.mem_read(framebuffer, 2560))
                self.assertEqual(bytes(m.cpu.mem_read(framebuffer - 16, 16)), b"\xa5" * 16)
                self.assertEqual(bytes(m.cpu.mem_read(framebuffer + 2560, 16)), b"\xa5" * 16)
                # Native framebuffer packs pixels most-significant bit first.
                points = {(y, x) for y in range(128) for x in range(160)
                          if pixels[y * 20 + x // 8] & (128 >> (x % 8))}
                for y, x in points:
                    self.assertTrue(max(43, clip[0]) <= y < min(87, clip[2]))
                    self.assertTrue(max(58, clip[1]) <= x < min(102, clip[3]))
                if clip[2] < 43:
                    self.assertEqual(points, set())
                else:
                    self.assertTrue(points)
                if clip == (20, 0, 110, 160):
                    self.assertIn((43, 58), points)  # border
                    self.assertNotIn((46, 61), points)  # white inset
                    self.assertIn((51, 70), points)  # B top stroke
                    self.assertNotIn((51, 86), points)  # B rounded corner


class LetterIndex(unittest.TestCase):
    def test_letters_follow_filter_and_deduplication(self):
        m = Library(self.image, [("Absent", True), ("Alpha", False),
                                ("Alpha", True), ("Beta", False), ("Éclair", False)])
        self.assertEqual(m.build_index(), ["Alpha", "Beta", "Éclair"])
        pointer, count = m.read(QUERY + 0xa8c), m.read(QUERY + 0xa90)
        self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), b"ABE")
        self.assertEqual(m.allocations[pointer], 25)

    def test_every_named_index_has_letters(self):
        for mask, offset in ((4, 0xa8c), (2, 0xa9c), (0x40, 0xa84), (0x4000000, 0xa94)):
            with self.subTest(mask=mask):
                m = Library(self.image, [("Ångström", False), ("Beta", False),
                                        ("9 Lives", False), ("中", False)])
                m.build_index(mask)
                pointer, count = m.read(QUERY + offset), m.read(QUERY + offset + 4)
                self.assertEqual(bytes(m.cpu.mem_read(pointer + 4 * count, count)), b"AB#!")

    def test_reused_index_does_not_rebuild_or_read_names(self):
        m = Library(self.image, [("Alpha", False), ("Beta", False)])
        m.build_index()
        reads, allocations = m.storage_reads, dict(m.allocations)
        m.build_index()
        self.assertEqual(m.storage_reads, reads)
        self.assertEqual(m.allocations, allocations)

    def test_overlap_compaction_and_allocation_guards(self):
        tracks = []
        for i in range(100):
            name = chr(65 + i // 10) + str(i)
            tracks.append((name, False))
            if i % 5 == 0:
                tracks.append((name, False))
        m = Library(self.image, tracks)
        original_allocate = m.allocate

        def guarded_allocate():
            pointer = original_allocate()
            size = m.allocations[pointer]
            m.cpu.mem_write(pointer - 4, b"\xa5" * (size + 8))
            return pointer

        m.hook(0x9b8e0, guarded_allocate)
        m.build_index()
        pointer, count = m.read(QUERY + 0xa8c), m.read(QUERY + 0xa90)
        self.assertEqual(count, 100)
        self.assertEqual(bytes(m.cpu.mem_read(pointer + count * 4, count)),
                         b"".join(bytes([65 + i]) * 10 for i in range(10)))
        self.assertEqual(bytes(m.cpu.mem_read(pointer - 4, 4)), b"\xa5" * 4)
        self.assertEqual(bytes(m.cpu.mem_read(pointer + 600, 4)), b"\xa5" * 4)


class LetterGrouping(unittest.TestCase):
    def test_compiled_utf16_grouping_matches_actual_5g_fallback(self):
        from research_letters import Reference, reference_image
        reference = Reference(reference_image(Path("iPod_25.1.3.ipsw")))
        m = NativeMachine(self.image)
        for codepoint in [*range(512), 0x2e7f, 0x2e80, 0x4e2d, 0xffff, 0x1f600]:
            name = chr(codepoint)
            encoded = name.encode("utf-16le")
            m.cpu.mem_write(0x10020000, encoded)
            actual = m.call(self.symbols["letters_group"], 0x10020000, len(encoded) // 2)
            self.assertEqual(chr(actual), reference.group(name), hex(codepoint))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    LetterIndex.image = load_image(args.candidate)
    LetterWheel.image = LetterIndex.image
    LetterGrouping.image = LetterIndex.image
    LetterGrouping.symbols = json.loads((args.candidate.with_suffix(".artifacts") / "letters-symbols.json").read_text())
    unittest.main(argv=[__file__], verbosity=2)
