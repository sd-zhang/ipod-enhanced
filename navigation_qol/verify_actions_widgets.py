"""Construct the packaged actions widgets with the stock 4G ARM factories.

Runs class registration, widget/menu construction, initialization, visibility,
focus search, row selection and Renderer animated-show dispatch. Allocation,
resource lookup, display geometry, font/icon layout, OS focus/notifications and
animation rasterization/timers are supplied. This does not emulate the LCD or
physical input timing. Run with .venv/bin/python outside the macOS sandbox.
"""
import argparse
import unittest
from pathlib import Path

from native_fixture import NativeMachine, ScriptVM, load_image
from verify_resources import directory, records, word


class ActionsDisplayFixture:
    TASK = 0x10018000
    RENDERER = 0x10012000
    PARENT = 0x1001b000

    def __init__(self, image, settings=0, view=0x6100):
        super().__init__(image)
        self.instruction_limit = 500000
        self.hook(0x9f9a8, lambda: self.TASK)
        self.hook(0xd9a60, self.allocate)
        self.hook(0xd97b8, self.allocate)
        self.hook(0xdda9c, self.reallocate)
        self.hook(0xec, lambda: self.cpu.mem_write(
            self.reg(0), bytes(self.cpu.mem_read(self.reg(1), self.reg(2)))))
        self.hook(0x6c760, lambda: 0)  # enclosing application resource provider
        if settings:
            self.write(settings, 0x23a48)
        self.hook(0x64a48, lambda: settings if self.reg(0) == 0x2780 else 0)
        self.hook(0x3be44, lambda: None)  # font/icon layout outside isolated tree
        resources = directory(image)
        self.resource_addresses = {
            (int.from_bytes(tag.encode(), 'big'), key & 0xffffffff): image.find(data)
            for tag, (_, entries) in resources.items() for key, data in entries.items()
        }
        self.hook(0x72a88, lambda: self.resource_addresses.get((self.reg(0), self.reg(1)), 0))
        # Register actual class factory functions, then construct the new child
        # in View250d. Child-resource recursion and Menu item creation are native.
        self.call(0xba5f0)
        record = next(row for row in records(resources['View'][1][0x250d]) if word(row, 12) == view)
        self.cpu.mem_write(0x1001a000, record[4:])
        self.display_root = self.call(0x4c454, 0, 0, 0x1001a000, 0x707a8)
        self.scrollbar, self.viewport = self.children(self.display_root)
        self.menu, = self.children(self.viewport)
        self.row = self.read(self.menu + 0x90)
        # No display ancestor is instantiated: supply its resolved rectangles.
        for obj, height, width in ((self.display_root, 108, 160), (self.viewport, 108, 150),
                                   (self.menu, 108, 150), (self.row, 18, 150)):
            self.write(obj + 0x58, 0, 0, height, width)
        self.call(0x660f0, self.display_root)

    def children(self, obj):
        count, array = self.read(obj + 0x6c), self.read(obj + 0x70)
        assert count < 20
        return tuple(self.read(array + 4 * i) for i in range(count))

    def reallocate(self):
        old, size = self.reg(0), self.reg(1)
        at = self.cursor
        self.cursor += (size + 15) & ~15
        assert self.cursor < 0x100d0000
        self.allocations[at] = size
        if old:
            self.cpu.mem_write(at, bytes(self.cpu.mem_read(old, min(size, self.allocations[old]))))
            self.freed.append(old)
        return at

    def prepare_renderer(self):
        self.write(self.RENDERER, 0x23d7c)
        self.write(self.RENDERER + 0x18, self.display_root)
        # The stock animation controller is the enclosing display boundary.
        self.write(self.PARENT, 0x272a0)
        self.write(self.PARENT + 0x20, 0x800)
        self.write(self.display_root + 0x10, self.PARENT)
        self.events = []
        self.hook(0xd2c80, lambda: self.events.append(('animate', self.reg(2))))
        self.hook(0xd3b2c, lambda: None)  # cancel previous display animation
        self.hook(0xd648c, lambda: None)  # OS focus delivery
        self.hook(0xc7da8, lambda: None)  # system notification
        self.hook(0xaba84, lambda: self.events.append((
            'menu_event', self.reg(1), self.read(self.read(self.menu + 0x8c) + 0x14))))
        self.call(0xd08fc, self.display_root, 1)
        self.call(0xa4c1c, self.display_root, 1)


class ActionsDisplay(ActionsDisplayFixture, NativeMachine):
    pass


class ActionsVMDisplay(ActionsDisplayFixture, ScriptVM):
    pass


class NativeActionsWidgets(unittest.TestCase):
    with_sleep = False
    stock_repeat = False
    with_otg = False

    def test_stock_factories_construct_and_initialize_all_actions(self):
        m = ActionsDisplay(self.image)
        self.assertEqual([m.read(o) for o in (m.display_root, m.scrollbar, m.viewport, m.menu, m.row)],
                         [0x22cd0, 0x24c68, 0x22770, 0x291a8, 0x22eac])
        self.assertEqual([m.read(o + 0x1c) & 0xffff for o in (m.display_root, m.viewport, m.menu)],
                         [0x6100, 0x6101, 0x6102])
        commands = (2538, 2539, 2155, 2155 if self.stock_repeat else 2542) + ((2160,) if self.with_sleep else ())
        commands += (2542,) if self.with_otg else ()
        self.assertEqual(m.read(m.menu + 0xa0), len(commands))
        group = m.read(m.menu + 0xe8)
        self.assertEqual(m.call(0x6ce68, group), len(commands))
        for i, command in enumerate(commands):
            item = m.call(0x6cebc, group, i)
            record = m.read(item + 0x10)
            self.assertEqual(m.read(record + 8), command * 4 + 2)

    def test_animated_show_selects_first_row_before_returning(self):
        m = ActionsDisplay(self.image)
        m.prepare_renderer()
        m.call(0xdd19c, m.RENDERER, 0x6100, 0, 30, 300)
        self.assertEqual(m.read(m.RENDERER + 0x24), m.menu)
        self.assertEqual(m.call(0x96fc4, m.menu), 0)
        self.assertEqual(m.events, [('animate', m.display_root), ('menu_event', 0x4d6e7553, 0)])
        # MnuS dispatch is synchronous here, before OPEN's final registration of
        # release consumer1888. Animation raster/timer completion is not executed.

    def test_native_selection_and_reopening_preserve_each_row(self):
        m = ActionsDisplay(self.image)
        m.prepare_renderer()
        m.call(0xdd19c, m.RENDERER, 0x6100, 0, 30, 300)
        rows = (1, 2, 3, 4, 0, 4, 3, 2, 1, 0) if self.with_sleep else (1, 2, 3, 0, 3, 2, 1, 0)
        if self.with_otg:
            rows += (5, 0, 5, 4, 0)
        for row in rows:
            m.call(0xd11c8, m.menu, row)
            self.assertEqual(m.call(0x96fc4, m.menu), row)
            m.events.clear()
            m.call(0xdd19c, m.RENDERER, 0x6100, 0, 30, 300)
            self.assertEqual(m.read(m.RENDERER + 0x24), m.menu)
            self.assertEqual(m.call(0x96fc4, m.menu), row)
            self.assertEqual(m.events, [('animate', m.display_root), ('menu_event', 0x4d6e7553, row)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--with-sleep', action='store_true')
    parser.add_argument('--stock-repeat', action='store_true')
    parser.add_argument('--with-otg', action='store_true')
    args = parser.parse_args()
    NativeActionsWidgets.with_sleep = args.with_sleep
    NativeActionsWidgets.stock_repeat = args.stock_repeat
    NativeActionsWidgets.with_otg = args.with_otg
    NativeActionsWidgets.image = load_image(args.candidate)
    unittest.main(argv=[__file__], defaultTest='NativeActionsWidgets', verbosity=2)
