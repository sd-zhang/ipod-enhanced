"""Native 4G disc-jump wheel and numeric popup regressions."""
import argparse
import struct
import types
import unittest
from pathlib import Path

from native_fixture import load_image
from verify_disc_separators import fixture, constructed_viewport, virtual, LIST, ROW, MODEL, UI
from verify_letters import WheelMachine, EVENT, TASK


def wheel_fixture(image, discs=None, totals=None, album=True, viewport=False):
    if discs is None:
        discs = (1,) * 10 + (2,) * 10 + (10,) * 10 + (65535,) * 10
    m = (constructed_viewport if viewport else fixture)(image, discs, totals, album)
    listing = m.disc_list if viewport else LIST
    row = m.disc_row if viewport else ROW
    m.now = 1000
    m.events, m.registered = [], []
    m.write(row + 0x90, 1)
    if viewport:
        m.disc_select(0)
    else:
        virtual(m, 0x130, 0, 0)
    m.hook(0xd9a60, m.allocate)
    m.hook(0xd9a50, m.free)
    m.hook(0x6c218, lambda: ((m.now * 1000) & 0xffffffff) // 1000)
    m.hook(0x87cec, lambda: (m.now * 1000) & 0xffffffff)
    m.hook(0x348, lambda: WheelMachine.divide(m))
    m.hook(0x87654, lambda: 0)
    m.hook(0x79c9c, lambda: 0x10018000)
    m.write(0x10018000, 0x10019000)
    m.write(0x10019018, 0x1000)
    m.write(0x10019024, 0x1004)
    m.hook(0x1001a000, lambda: m.registered.append(m.read(m.reg(1))))
    m.hook(0x1001a004, lambda: m.registered.remove(m.read(m.reg(1))))
    m.write(TASK + 0x1c, TASK + 0x100)
    m.hook(0x9f9a8, lambda: TASK)
    m.hook(0x625f4, lambda: m.events.append(('click', m.reg(0))))
    m.hook(0x9107c, lambda: m.events.append(('invalidate', m.reg(0))))
    for at, name in ((0xcdd84, 'interval'), (0xd2bd0, 'start'), (0xd3aa0, 'stop')):
        m.hook(at, lambda name=name: m.events.append((name, m.reg(0), m.reg(1))))
    for name in ('wheel', 'timer', 'virtual', 'graphics'):
        setattr(m, name, types.MethodType(getattr(WheelMachine, name), m))
    if viewport:
        def wheel(raw=80, advance=200):
            m.now = (m.now + advance) & 0xffffffff
            m.write(EVENT + 4, 0x5765656c)
            m.cpu.mem_write(EVENT + 8, struct.pack('<BBhI', 1, 0, raw, 100))
            return m.call(0x86670, listing, EVENT)
        def timer(advance=1000):
            m.now = (m.now + advance) & 0xffffffff
            m.write(EVENT + 4, 0x74696d65, m.read(listing + 0xe4) + 48)
            table = m.read(listing)
            return m.call((table + m.read(table + 0x24)) & 0xffffffff, listing, EVENT)
        m.wheel, m.timer = wheel, timer
    return m


def active(m):
    state = m.read(LIST + 0xe4)
    return bool(state and m.read(state + 92))


class DiscFastScroll(unittest.TestCase):
    def test_small_reverse_packets_can_enter_from_final_disc(self):
        from verify_song_scroll import physical_input
        from unicorn import UC_HOOK_CODE
        for mode in (0,1):
            for direction in (-1,1):
                with self.subTest(mode=mode,direction=direction):
                    m=physical_input(wheel_fixture(self.image,(1,)*10+(2,)*100),mode,10)
                    virtual(m,0x130,70,0)
                    effective=[]
                    def observe(cpu,pc,size,context):
                        effective.append(m.reg(5))
                    m.cpu.hook_add(UC_HOOK_CODE,observe,begin=0x86700,end=0x86700)
                    for _ in range(36):m.move(direction*2,10)
                    self.assertIn(0,effective,'exercise sub-row native packets')
                    if direction<0:
                        self.assertTrue(active(m),'zero-row samples must retain reverse stroke distance')
                        self.assertEqual(m.read(ROW+0x8c),12)
                        self.assertEqual(m.read(m.read(LIST+0xe4)+96),2)
                    else:
                        self.assertFalse(active(m))
                        self.assertGreater(m.read(ROW+0x8c),70)
                        self.assertFalse(any(e[0] in ('start','interval') for e in m.events))

    def test_large_disc_list_jump_stays_within_wheel_work_budget(self):
        from unicorn import UC_HOOK_CODE
        m = wheel_fixture(self.image, tuple(d for d in range(1, 257) for _ in range(2)))
        m.wheel(5, 200)
        m.wheel(80, 200)
        m.wheel(80, 10)
        instructions = [0]
        hook = m.cpu.hook_add(UC_HOOK_CODE,
            lambda *args: instructions.__setitem__(0, instructions[0] + 1))
        try:
            m.wheel(80, 200)
        finally:
            m.cpu.hook_del(hook)
        self.assertEqual(m.read(ROW + 0x8c), 7)
        self.assertEqual(m.read(m.read(LIST + 0xe4) + 96), 3)
        self.assertLessEqual(instructions[0], 900,
            'disc target and popup bookkeeping exceeded the measured ARM work budget')

    def test_idle_popup_drawing_has_small_constant_overhead(self):
        from unicorn import UC_HOOK_CODE
        for groups in (2, 256):
            m = wheel_fixture(self.image, tuple(d for d in range(1, groups + 1)
                                                for _ in range(2)))
            framebuffer, clip = m.graphics()
            for warmed in (False, True):
                if warmed:
                    m.wheel()
                    m.timer()
                before = dict(m.allocations), m.storage_reads, m.read(ROW + 0x8c)
                instructions = [0]
                hook = m.cpu.hook_add(UC_HOOK_CODE,
                    lambda *args: instructions.__setitem__(0, instructions[0] + 1))
                m.virtual(0x60, MODEL, clip)
                m.cpu.hook_del(hook)
                self.assertEqual((dict(m.allocations), m.storage_reads, m.read(ROW + 0x8c)), before)
                self.assertFalse(any(m.cpu.mem_read(framebuffer, 2560)))
                self.assertLessEqual(instructions[0], 100,
                    'idle popup overhead should not scale with disc group navigation')

    def test_pending_stroke_survives_paint_but_invalid_query_cancels_it(self):
        from native_fixture import QUERY
        m = wheel_fixture(self.image)
        m.wheel(8, 40)
        state = m.read(LIST + 0xe4)
        self.assertFalse(active(m))
        self.assertNotEqual(m.read(state + 120), 0)
        _, clip = m.graphics()
        before = bytes(m.cpu.mem_read(state, 128))
        m.virtual(0x60, MODEL, clip)
        self.assertEqual(bytes(m.cpu.mem_read(state, 128)), before)
        m.call(0x9129c, QUERY)
        m.virtual(0x60, MODEL, clip)
        self.assertEqual(m.read(state + 120), 0)
        self.assertFalse(active(m))

    def test_forward_inside_final_disc_stays_in_native_song_scrolling(self):
        from verify_disc_package import branch_target
        entry=branch_target(self.image,0x86700,0xea)
        target=branch_target(self.image,entry+8,0xeb)
        for row in (34,35,40,43):
            with self.subTest(row=row):
                m=wheel_fixture(self.image,viewport=True)
                ordinary=wheel_fixture(self.image,viewport=True)
                # Keep native acceleration, selection and viewport; bypass
                # only the extension's mode choice in the reference fixture.
                ordinary.hook(target,lambda:0)
                for machine in (m,ordinary): machine.disc_select(row)
                for raw in (80,80,0,5):
                    m.wheel(raw)
                    ordinary.wheel(raw)
                    self.assertEqual(m.disc_position(),ordinary.disc_position())
                    state=m.read(m.disc_list+0xe4)
                    self.assertFalse(m.read(state+92))
                self.assertFalse(any(e[0] in ('start','interval') for e in m.events))

    def test_forward_final_disc_boundary_still_allows_immediate_reverse(self):
        m=wheel_fixture(self.image,viewport=True)
        m.disc_select(40)
        m.wheel()
        self.assertGreaterEqual(m.disc_position()[0],40)
        self.assertFalse(m.read(m.read(m.disc_list+0xe4)+92))
        m.wheel(-80,10)
        self.assertEqual(m.disc_position(),(34,33))
        m.wheel(-80)
        self.assertEqual(m.disc_position(),(23,22))

    def test_disc_jumps_align_heading_to_viewport_top_in_both_directions(self):
        for height in (108, 95):
            m = wheel_fixture(self.image, viewport=True)
            m.write(m.disc_parent + 0x60, 20 + height)
            for raw, selected, heading in ((80, 12, 11), (80, 23, 22),
                                            (80, 34, 33), (-80, 23, 22),
                                            (-80, 12, 11), (-80, 1, 0)):
                m.wheel(raw)
                self.assertEqual(m.disc_position(), (selected, heading))
                self.assertEqual(m.read(m.disc_list + 0x58), (20 - 18 * heading) & 0xffffffff)

    def test_active_final_disc_clamps_then_timeout_restores_ordinary_scrolling(self):
        m=wheel_fixture(self.image,viewport=True)
        for _ in range(3): m.wheel()
        self.assertEqual(m.disc_position(),(34,33))
        self.assertTrue(m.read(m.read(m.disc_list+0xe4)+92))
        for raw in (80,80,0):
            m.wheel(raw)
            self.assertEqual(m.disc_position(),(34,33))
        # Expiry inside the wheel handler must also apply the no-entry rule.
        m.wheel(80,1001)
        state=m.read(m.disc_list+0xe4)
        self.assertFalse(m.read(state+92))
        self.assertGreater(m.disc_position()[0],34)

    def test_final_disc_keeps_native_bottom_limit_and_short_album_stays_unscrolled(self):
        for discs, selected, top_row in (((1,) * 10 + (2,) * 2, 12, 8),
                                         ((1,) * 2 + (2,) * 2, 4, 0)):
            m = wheel_fixture(self.image, discs, viewport=True)
            for raw in (80, 80, 0):
                m.wheel(raw)
                self.assertEqual(m.disc_position(), (selected, top_row))
            m.timer()
            self.assertEqual(m.disc_position(), (selected, top_row))

    def test_empty_and_unselected_rows_keep_native_draw_and_wheel_fallback(self):
        for absent in (True, False):
            with self.subTest(absent=absent):
                m = wheel_fixture(self.image)
                if absent: m.write(LIST + 0x90, 0)
                else: m.write(ROW + 0x90, 0)
                _, clip = m.graphics()
                paints = []
                m.hook(0x5cdc4, lambda: paints.append(m.reg(0)))
                m.virtual(0x60, MODEL, clip)
                self.assertEqual(paints, [LIST])
                if absent:
                    # Native ordinary row movement itself requires a row;
                    # exercise only the installed quick-mode dispatch here.
                    from verify_disc_package import branch_target
                    entry = branch_target(self.image, 0x86700, 0xea)
                    target = branch_target(self.image, entry + 8, 0xeb)
                    self.assertEqual(m.call(target, LIST, 16), 0)
                else:
                    m.wheel()
                self.assertFalse(active(m))

    def test_fast_wheel_jumps_disc_starts_with_numeric_popup_state(self):
        m = wheel_fixture(self.image)
        before = m.storage_reads
        for row, number in ((12, 2), (23, 10), (34, 65535), (34, 65535)):
            m.wheel()
            self.assertEqual(m.read(ROW + 0x8c), row)
            self.assertTrue(active(m))
            self.assertEqual(m.read(m.read(LIST + 0xe4) + 96), number)
        self.assertEqual(m.storage_reads, before)

    def test_reverse_inside_disc_then_previous_disc_and_first_boundary(self):
        m = wheel_fixture(self.image)
        virtual(m, 0x130, 28, 0)
        for row in (23, 12, 1, 1):
            m.wheel(-80)
            self.assertEqual(m.read(ROW + 0x8c), row)
            self.assertEqual(m.read(MODEL + 0x14), row)

    def test_rate_limit_reversal_and_timeout_preserve_track_scrolling(self):
        m = wheel_fixture(self.image)
        m.wheel()
        m.wheel(80, 10)
        self.assertEqual(m.read(ROW + 0x8c), 12)
        m.wheel(-80, 10)
        self.assertEqual(m.read(ROW + 0x8c), 1)
        m.timer()
        self.assertFalse(active(m))
        m.wheel(5, 1)
        self.assertEqual(m.read(ROW + 0x8c), 2)

    def test_short_multi_disc_albums_can_jump_but_single_or_missing_tags_cannot(self):
        m = wheel_fixture(self.image, (1, 1, 2, 2))
        m.wheel()
        self.assertEqual(m.read(ROW + 0x8c), 4)
        self.assertTrue(active(m))
        for discs, totals, album in (((1,) * 40, (1,) * 40, True),
                                    ((2,) * 40, None, True),
                                    ((1, 0, 2) * 14, None, True),
                                    ((1, 2) * 20, None, False)):
            with self.subTest(discs=discs[:3], album=album):
                m = wheel_fixture(self.image, discs, totals, album)
                m.wheel()
                self.assertFalse(active(m))

    def test_key_hide_and_destruction_reuse_existing_timer_lifetime(self):
        m = wheel_fixture(self.image)
        m.wheel()
        self.assertTrue(active(m))
        m.write(EVENT + 0x10, 0x57 << 16)
        m.virtual(0x160, EVENT)
        self.assertFalse(active(m))
        self.assertEqual(m.read(ROW + 0x8c), 10)
        m.wheel()
        m.write(LIST + 0x68, 0x286d8, 0, 0, 0)
        m.virtual(0x94, 0)
        self.assertFalse(active(m))
        m.virtual(0x94, 1)
        m.wheel()
        state = m.read(LIST + 0xe4)
        m.write(LIST + 0x8c, 0)
        m.hook(0xfd904, lambda: None)
        order = []
        m.hook(0xd3aa0, lambda: order.append(('stop', m.reg(0))))
        m.hook(0xd9a50, lambda: order.append(('free', m.reg(0))))
        m.call(0x103140, LIST, 1)
        self.assertEqual(order, [('stop', state + 48), ('free', state), ('free', LIST)])
        self.assertEqual(m.registered, [])

    def test_numeric_popup_uses_album_frame_and_distinct_clipped_digits(self):
        pictures = []
        for number in (1, 2, 10, 65535):
            m = wheel_fixture(self.image, (number,) * 10 + (3,) * 10)
            m.wheel()
            virtual(m, 0x130, 1, 0)
            state = m.read(LIST + 0xe4)
            m.write(state + 92, 1, number)
            m.write(state + 100, (m.now * 1000) & 0xffffffff)
            framebuffer, clip = m.graphics()
            m.virtual(0x60, MODEL, clip)
            pixels = bytes(m.cpu.mem_read(framebuffer, 2560))
            points = {(y, x) for y in range(128) for x in range(160)
                      if pixels[y * 20 + x // 8] & (128 >> (x % 8))}
            self.assertTrue(points)
            self.assertTrue(all(43 <= y < 87 and 58 <= x < 102 for y, x in points))
            self.assertTrue(all((43, x) in points for x in range(58, 102)))
            self.assertTrue(any(47 < y < 83 and 62 < x < 98 for y, x in points))
            pictures.append(pixels)
            m.cpu.mem_write(framebuffer, bytes(2560))
            m.write(clip, 50, 70, 70, 90)
            m.virtual(0x60, MODEL, clip)
            partial = bytes(m.cpu.mem_read(framebuffer, 2560))
            partial_points = {(y, x) for y in range(128) for x in range(160)
                              if partial[y * 20 + x // 8] & (128 >> (x % 8))}
            self.assertEqual(partial_points, {(y, x) for y, x in points
                                             if 50 <= y < 70 and 70 <= x < 90})
            self.assertEqual(bytes(m.cpu.mem_read(framebuffer - 16, 16)), b'\xa5' * 16)
            self.assertEqual(bytes(m.cpu.mem_read(framebuffer + 2560, 16)), b'\xa5' * 16)
        self.assertEqual(len(set(pictures)), 4, 'different disc numbers must not collapse to #')

    def test_stale_timer_and_paint_expiry_restore_normal_scroll(self):
        for start in (1000, 4294400):
            m = wheel_fixture(self.image)
            m.now = start
            m.wheel()
            self.assertTrue(active(m))
            m.wheel(80, 700)
            m.timer(300)
            self.assertTrue(active(m))
            self.assertEqual(m.events[-2][2], 700)
            _, clip = m.graphics()
            m.now += 700
            m.virtual(0x60, MODEL, clip)
            self.assertFalse(active(m))
            m.wheel(5, 1)
            self.assertEqual(m.read(ROW + 0x8c), 24)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args, rest = parser.parse_known_args()
    DiscFastScroll.image = load_image(args.candidate)
    unittest.main(argv=[__file__, *rest], verbosity=2)
