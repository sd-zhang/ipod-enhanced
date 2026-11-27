"""Run packaged fast-scroll alignment through the original ARM viewport paths.

Wheel dispatch, letter selection, top reveal, rectangle visibility and native
end clamping execute in ARM. Final subtree translation/rasterization, display
notifications and inherited OS boundaries are supplied. No candidate code is
patched by this fixture. Run repository Python outside the macOS sandbox for
Unicorn JIT access; this does not emulate physical LCD/input timing.
"""
import argparse
import unittest
from pathlib import Path

from unicorn import UC_HOOK_CODE

from native_fixture import QUERY, load_image
from verify_letters import LIST, ROW, MODEL, WheelMachine

PARENT, CHILDREN = 0x10034000, 0x10035000
VIEW_OFFSETS = {0x3302: 0xa9c, 0x3303: 0xa94, 0x3304: 0xa8c, 0x3305: 0xa84}


def signed(value):
    return value if value < 0x80000000 else value - 0x100000000


class ViewportWheel(WheelMachine):
    def __init__(self, image, groups=b'A' * 20 + b'B' * 20 + b'C' * 20,
                 first=0, selected=0, view=0x3304):
        super().__init__(image, groups)
        self.moves, self.top_reveals, self.notifications = [], [], []
        self.write(LIST + 0x1c, view)
        if view in VIEW_OFFSETS:
            self.write(QUERY + VIEW_OFFSETS[view], self.read(QUERY + 0xa8c), len(groups))
        self.write(ROW + 0x8c, selected)
        self.write(MODEL + 0x14, selected)
        self.hook(0xc0634, lambda: 0x10030000)
        self.hook(0xd13a8, lambda: 0)  # no synchronous display transaction
        self.hook(0xe00e4, lambda: None)  # old-row paint
        self.hook(0x9100c, lambda: None)  # dirty-rectangle publication
        self.hook(0xaba04, lambda: None)  # scrollbar notification delivery
        self.hook(0xaba1c, lambda: self.notifications.append(
            (self.read(ROW + 0x8c), self.read(MODEL + 0x14))))
        self.write(PARENT, 0x22770)  # native viewport: +fc=c00a0, +f8=c4080
        self.write(PARENT + 0x20, 0x802)
        self.write(PARENT + 0x58, 20, 0, 128, 150)
        self.write(PARENT + 0x78, 20, 0, 128, 150)
        self.write(PARENT + 0x68, 0x286d8, 1, CHILDREN, 1)
        self.write(CHILDREN, LIST)
        self.write(LIST + 0x10, PARENT)
        top = 20 - first * 18
        self.write(LIST + 0x58, top, 0, top + (len(groups) + 1) * 18, 150)
        self.write(LIST + 0x78, 20, 0, 128, 150)
        self.write(ROW + 0x58, top, 0, top + 18, 150)
        # Native c4080 has already calculated and clamped the translation.
        # Supply its final child translation/raster boundary, preserving the
        # resulting geometry for the next native selection and viewport call.
        self.hook(0xc4260, self.translate)
        del self.hooks[0xd10e8]  # real selection, model update and viewport reveal
        self.cpu.hook_add(UC_HOOK_CODE, self.record_top_reveal)

    def translate(self):
        dx, dy = signed(self.reg(1)), signed(self.reg(2))
        self.moves.append((dx, dy))
        for obj in (LIST, ROW):
            values = [self.read(obj + 0x58 + i * 4) for i in range(4)]
            self.write(obj + 0x58, *(value + (dy if i % 2 == 0 else dx)
                                      for i, value in enumerate(values)))

    def record_top_reveal(self, cpu, pc, size, context):
        if pc == 0xab52c:
            self.top_reveals.append((self.reg(0), self.reg(1)))

    def position(self):
        top = signed(self.read(LIST + 0x58))
        assert (20 - top) % 18 == 0, 'viewport lost row alignment'
        return self.read(ROW + 0x8c), (20 - top) // 18


class FastScrollTop(unittest.TestCase):
    def test_fast_down_and_up_keep_selection_at_top_in_all_four_views(self):
        for view in VIEW_OFFSETS:
            with self.subTest(view=hex(view)):
                m = ViewportWheel(self.image, view=view)
                for raw, target in ((80, 21), (80, 41), (-80, 21), (-80, 0)):
                    m.wheel(raw)
                    self.assertEqual(m.position(), (target, target))
                    self.assertEqual(m.notifications[-1], (target, target))
                self.assertEqual(m.top_reveals, [(LIST, row) for row in (21, 41, 21, 0)])

    def test_jump_inside_current_page_still_places_selection_at_top(self):
        m = ViewportWheel(self.image, groups=b'A' * 2 + b'B' * 30 + b'C' * 28)
        m.wheel()
        self.assertEqual(m.position(), (3, 3))
        self.assertEqual(m.top_reveals, [(LIST, 3)])

    def test_valid_row_straddling_zero_still_aligns_on_boundary_entry(self):
        m = ViewportWheel(self.image, groups=b'A' * 2 + b'B' * 58, first=2, selected=3)
        m.wheel()
        self.assertEqual(m.position(), (3, 3))
        self.assertEqual(m.top_reveals, [(LIST, 3)])

    def test_unchanged_boundary_entry_aligns_once_and_repeated_strokes_do_no_work(self):
        m = ViewportWheel(self.image, first=36, selected=41)
        m.wheel()
        self.assertEqual(m.position(), (41, 41))
        self.assertEqual(m.notifications, [], 'unchanged selection should not be republished')
        self.assertEqual(m.top_reveals, [(LIST, 41)])
        m.events.clear()
        moves = list(m.moves)
        for unused in range(8):
            m.wheel(5, 20)
        self.assertEqual(m.top_reveals, [(LIST, 41)])
        self.assertEqual(m.moves, moves)
        self.assertFalse(any(event[0] == 'invalidate' for event in m.events))

    def test_timeout_restores_ordinary_wheel_positioning(self):
        m = ViewportWheel(self.image)
        m.wheel()
        self.assertEqual(m.position(), (21, 21))
        m.timer()
        m.wheel(5, 1)
        self.assertEqual(m.position(), (22, 21))
        self.assertEqual(m.top_reveals, [(LIST, 21)])

    def test_normal_wheel_unrelated_view_and_short_list_keep_native_positioning(self):
        for groups, view, raw, expected in (
                (b'A' * 20 + b'B' * 20 + b'C' * 20, 0x3304, 5, (1, 0)),
                (b'A' * 20 + b'B' * 20 + b'C' * 20, 0x3301, 80, (16, 11)),
                (b'ABCDEFGH', 0x3304, 80, (8, 3))):
            with self.subTest(view=hex(view), count=len(groups), raw=raw):
                m = ViewportWheel(self.image, groups=groups, view=view)
                m.wheel(raw)
                self.assertEqual(m.position(), expected)
                self.assertEqual(m.top_reveals, [])

    def test_native_last_page_clamp_prevents_blank_tail(self):
        for target in (56, 60):
            with self.subTest(target=target):
                groups = b'A' * (target - 1) + b'B' * (61 - target)
                m = ViewportWheel(self.image, groups=groups)
                m.wheel()
                self.assertEqual(m.position(), (target, 55))
                self.assertEqual(m.read(LIST + 0x60), 128)
                m.wheel(-80)
                self.assertEqual(m.position(), (0, 0))

    def test_missing_parent_or_unlaid_out_row_still_draws_marker_without_scrolling(self):
        for missing in ('parent', 'row_height'):
            with self.subTest(missing=missing):
                # An unchanged boundary avoids asking native selection to lay
                # out this deliberately incomplete tree before the new hook.
                m = ViewportWheel(self.image, first=36, selected=41)
                if missing == 'parent':
                    m.write(LIST + 0x10, 0)
                else:
                    m.write(ROW + 0x60, m.read(ROW + 0x58))
                m.wheel()
                self.assertEqual(m.position(), (41, 36))
                self.assertEqual(m.top_reveals, [])
                self.assertEqual(sum(event[0] == 'invalidate' for event in m.events), 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    FastScrollTop.image = load_image(args.candidate)
    unittest.main(argv=[__file__], defaultTest='FastScrollTop', verbosity=2)
