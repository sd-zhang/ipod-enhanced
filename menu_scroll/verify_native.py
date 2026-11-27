"""Run original 4G UI routines with synthetic widgets and OS/graphics boundaries.

This is not a device emulator. It executes list selection/render dispatch,
text-change detection, timer scheduling, tick/wrap and visibility handling.
Font measurement, allocation, rasterization and the OS timer queue are supplied
by the fixture. On macOS run outside the sandbox (Unicorn needs JIT access).
"""
import argparse
import struct
import unittest
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm_const as arm

from verify_resources import ROOT, archive, directory, payload, records, resource_key, word

REG = [getattr(arm, f"UC_ARM_REG_R{i}") for i in range(13)]
LIST, NORMAL, SELECTED = 0x10001000, 0x10002000, 0x10003000
MODEL, CLIP, CONTROL = 0x10004000, 0x10005000, 0x10006000
STRING, CACHED, EVENT = 0x10008000, 0x10009000, 0x1000a000
STACK, RETURN = 0x100fe000, 0x100ff000


class Machine:
    def __init__(self, image):
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        # TI925T is Unicorn's ARMv4T model. Its instruction/return rules match
        # ARM7TDMI here; this is not a model of the iPod's CPU timing/devices.
        # Set it before memory initialization: newer defaults let LDM/POP pc
        # interwork, hiding invalid Thumb calls into Apple's ARM-only code.
        self.cpu.ctl_set_cpu_model(arm.UC_CPU_ARM_TI925T)
        self.cpu.mem_map(0, 0x400000)
        self.cpu.mem_write(0, image)
        self.cpu.mem_map(0x10000000, 0x100000)
        self.hooks = {}
        self.hook(0x348, self.divide)  # boot-ROM signed divmod veneer
        self.cpu.hook_add(UC_HOOK_CODE, self.dispatch)

    def divide(self):
        divisor, numerator = self.reg(0), self.reg(1)
        assert divisor > 0 and numerator < 0x80000000
        self.cpu.reg_write(REG[1], numerator % divisor)
        return numerator // divisor

    def reg(self, n):
        return self.cpu.reg_read(REG[n])

    def read(self, address):
        return struct.unpack("<I", self.cpu.mem_read(address, 4))[0]

    def write(self, address, *values):
        self.cpu.mem_write(address, struct.pack(f"<{len(values)}I", *(v & 0xffffffff for v in values)))

    def hook(self, address, callback):
        self.hooks[address] = callback

    def dispatch(self, cpu, pc, size, context):
        if pc in self.hooks:
            value = self.hooks[pc]()
            if value is not None:
                cpu.reg_write(REG[0], value & 0xffffffff)
            cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    def call(self, address, *args):
        for reg in REG:
            self.cpu.reg_write(reg, 0)
        for i, value in enumerate(args[:4]):
            self.cpu.reg_write(REG[i], value)
        if len(args) > 4:
            self.write(STACK, *args[4:])
        self.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        self.cpu.reg_write(arm.UC_ARM_REG_LR, RETURN)
        self.cpu.emu_start(address, RETURN, count=getattr(self, 'instruction_limit', 200000))
        assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == RETURN, "native routine exceeded instruction limit"
        assert self.cpu.reg_read(arm.UC_ARM_REG_SP) == STACK, "unbalanced ARM stack"
        return self.reg(0)


class NativeUI(unittest.TestCase):
    def setUp(self):
        self.m = Machine(self.image)

    def test_native_resource_manager_resolves_relocated_directory(self):
        m = self.m
        # Run the actual memory-resource constructor with the original blob
        # size, exactly as startup does. Lookup uses its directory/payload
        # pointers; it does not require the appended resources to be adjacent.
        self.assertEqual(m.call(0xee004, CONTROL, ROOT, word(self.image, ROOT - 4)), CONTROL)
        for key, data in directory(self.image)["View"][1].items():
            with self.subTest(resource=hex(key)):
                address = m.call(0x7ae24, CONTROL, 0x56696577, key, MODEL)
                self.assertNotEqual(address, 0)
                self.assertEqual(m.read(MODEL), len(data))
                self.assertEqual(bytes(m.cpu.mem_read(address, len(data))), data)

    def test_native_scroll_constructor_reads_each_added_record(self):
        m = self.m
        m.cpu.mem_map(0x10300000, 0x200000)  # firmware globals
        task, record_at = 0x1000b000, 0x1000c000
        m.write(task + 0x1c, 0x1000b100)
        m.hook(0x9f9a8, lambda: task)  # current OS task / event queue
        m.hook(0x72a88, lambda: 0)  # use native fallback font, no font service
        # Standalone constructor test: track labels' initial layout normally
        # resolves a preceding icon in the parent row, absent from this fixture.
        m.hook(0x3be44, lambda: None)
        m.hook(0xec, lambda: m.cpu.mem_write(m.reg(0), bytes(m.cpu.mem_read(m.reg(1), m.reg(2)))))
        allocations = []

        def allocate():
            at, size = 0x10020000 + len(allocations) * 0x1000, m.reg(0)
            allocations.append((at, size))
            m.cpu.mem_write(at, b"\xa5" * 0x1000)
            return at

        m.hook(0xd9a60, allocate)
        views = directory(self.image)["View"][1]
        for key in range(0x6000, 0x6007):
            with self.subTest(resource=hex(key)):
                record = next(r for r in records(views[key]) if word(r, 4) == 0x3180)
                # Native constructors receive the record after its size word.
                m.cpu.mem_write(record_at, record[4:])
                control = m.call(0xf1950, 0, 0, 0, 0, record_at)
                self.assertEqual(allocations[-1], (control, 368))
                self.assertEqual(m.read(control), 0x248b8)
                self.assertEqual(m.read(control + 0x18), 0x53747220)
                self.assertEqual(m.read(control + 0x1c) & 0xffff, word(record, 12))
                self.assertEqual(bytes(m.cpu.mem_read(control + 0x28, 48)), record[0x1c:0x4c])
                self.assertEqual([m.read(control + o) for o in (0xe0, 0xe4, 0xe8, 0xf8)], [140, 1250, 8, 25])
                self.assertEqual([m.read(control + o) for o in (0xdc, 0xec, 0xf0, 0xf4)], [0] * 4)
                self.assertEqual(m.read(control + 0x11c), control)  # timer recipient
                self.assertEqual(m.read(control + 0x120), 0x1000b100)
                self.assertEqual(bytes(m.cpu.mem_read(control + 368, 0x1000 - 368)), b"\xa5" * (0x1000 - 368))

    def test_parent_list_hide_and_show_reaches_selected_row_timer(self):
        self.scrolling_fixture()
        m = self.m
        # Native child arrays and visibility methods; only timer/graphics
        # boundaries are supplied by scrolling_fixture().
        for widget, table, children, array in (
                (LIST, 0x29030, (NORMAL, SELECTED), 0x1000b000),
                (NORMAL, 0x22eac, (), 0x1000b100),
                (SELECTED, 0x22eac, (CONTROL,), 0x1000b200)):
            m.write(widget, table)
            m.write(widget + 0x20, 0x800)  # visible
            m.write(widget + 0x68, 0x286d8, len(children), array, len(children))
            if children:
                m.write(array, *children)
        m.write(CONTROL + 0x20, 0x800)
        m.call(0xa4d80, LIST, 0)
        self.assertEqual(self.events, [("stop", CONTROL + 0xfc)])
        for widget in (LIST, NORMAL, SELECTED, CONTROL):
            self.assertEqual(m.call(0xe3ddc, widget), 0)
        self.events.clear()
        m.write(CONTROL + 0xec, -64)
        m.call(0xa4d80, LIST, 1)
        self.assertEqual(m.read(CONTROL + 0xec), 0)
        self.assertEqual(self.events, [("interval", CONTROL + 0xfc, 1250), ("start", CONTROL + 0xfc)])

    def test_selected_template_tracks_selection_and_empty_lists(self):
        m = self.m
        m.write(LIST, 0x29030)
        m.write(NORMAL, 0x22eac)
        m.write(SELECTED, 0x22eac)
        m.write(LIST + 0x20, 0x02000000)  # suppress deferred repaint during this draw
        m.write(LIST + 0x58, 0, 0, 72, 146)
        m.write(LIST + 0x78, 0, 0, 72, 146)
        m.write(NORMAL + 0x58, 0, 0, 18, 146)
        m.write(LIST + 0x8c, MODEL, NORMAL, SELECTED)
        m.write(CLIP, 0, 0, 72, 146)
        m.hook(0xe3ddc, lambda: 1)  # widget is visible
        m.hook(0xc9c2c, lambda: None)  # graphics clip
        m.hook(0x5ad60, lambda: None)  # list background
        m.hook(0xc0634, lambda: MODEL)  # drawing context
        m.hook(0x87824, lambda: None)  # highlight colors
        m.hook(0xc98b0, lambda: m.cpu.mem_write(m.reg(0) + 0x58, bytes(m.cpu.mem_read(m.reg(1), 16))))
        drawn = []
        m.hook(0x5c8b0, lambda: drawn.append((m.read(MODEL + 0x14), m.reg(0))))
        views = directory(self.image)["View"][1]
        for key in (0x3301, 0x3302, 0x3303, 0x3304, 0x3305, 0x3307, 0x330f):
            # Child construction is outside this fixture; use the artifact's
            # actual template count to install the native alternate pointer.
            count = len(records(views[key]))
            m.write(LIST + 0x94, SELECTED if count == 2 else 0)
            for selected, total in ((0, 3), (1, 3), (2, 3), (0, 1), (0, 0), (2, 3)):
                with self.subTest(list=hex(key), selected=selected, total=total):
                    m.write(LIST + 0x9c, 0, total)
                    m.write(NORMAL + 0x8c, selected, int(total > 0))
                    drawn.clear()
                    m.call(0x5cdc4, LIST, MODEL, CLIP)
                    self.assertEqual(drawn, [(i, SELECTED if i == selected else NORMAL) for i in range(total)])
                    self.assertEqual(m.read(MODEL + 0x14), selected)
                    if total:
                        self.assertEqual(m.read(SELECTED + 0x58), selected * 18)

    def scrolling_fixture(self, width=140):
        m = self.m
        # Read timing from the output resource, not a second test constant.
        views = directory(self.image)["View"][1]
        selected = records(views[0x3302])[-1]
        label = next(r for r in records(views[resource_key(selected)]) if struct.unpack_from("<I", r, 12)[0] == 0x336a)
        self.assertEqual(struct.unpack_from("<I", label, 4)[0], 0x3180)
        interval, dwell, step, gap = struct.unpack_from("<4I", label, 0x58)
        m.write(CONTROL, 0x248b8)
        m.write(CONTROL + 0x1c, 0x336a)
        m.write(CONTROL + 0xe0, interval, dwell, step, 0, 280, 280 + gap, gap)
        m.hook(0x66cf8, lambda: None)  # graphics context setup
        m.hook(0x9868c, lambda: m.write(m.reg(1), 0, 0, 18, width))
        self.events = []
        m.hook(0xcdd84, lambda: self.events.append(("interval", m.reg(0), m.reg(1))))
        m.hook(0xd2bd0, lambda: self.events.append(("start", m.reg(0))))
        m.hook(0xd3aa0, lambda: self.events.append(("stop", m.reg(0))))
        m.hook(0xd0540, lambda: None)  # base visibility propagation
        m.hook(0x9107c, lambda: None)  # invalidate widget
        return interval, dwell, step, gap

    def test_short_and_exact_fit_text_do_not_schedule(self):
        self.scrolling_fixture()
        for width in (0, 1, 139, 140):
            self.m.write(CONTROL + 0xf0, width)
            self.m.call(0xd2a78, CONTROL, 1)
        self.assertEqual(self.events, [])

    def test_long_text_dwell_ticks_wrap_and_hide(self):
        interval, dwell, step, gap = self.scrolling_fixture()
        m = self.m
        m.call(0xd0410, CONTROL, 1)
        self.assertEqual(self.events, [("interval", CONTROL + 0xfc, dwell), ("start", CONTROL + 0xfc)])
        # The native timer event checks its source and dispatches the real tick.
        m.write(EVENT + 4, 0x74696d65, CONTROL + 0xfc)  # 'time'
        self.events.clear()
        m.call(0x809b0, CONTROL, EVENT)
        self.assertEqual(m.read(CONTROL + 0xec), (-step) & 0xffffffff)
        self.assertEqual(self.events, [("interval", CONTROL + 0xfc, interval), ("start", CONTROL + 0xfc)])
        # Wrap after the entire text and gap have moved through.
        m.write(CONTROL + 0xec, -(280 + gap - 1))
        m.call(0x809b0, CONTROL, EVENT)
        self.assertEqual(m.read(CONTROL + 0xec), 0)
        self.assertEqual(self.events[-2:], [("interval", CONTROL + 0xfc, dwell), ("start", CONTROL + 0xfc)])
        self.events.clear()
        m.call(0xd0410, CONTROL, 0)
        self.assertEqual(self.events, [("stop", CONTROL + 0xfc)])
        m.write(CONTROL + 0xec, -64)
        m.call(0xd0410, CONTROL, 1)
        self.assertEqual(m.read(CONTROL + 0xec), 0)
        self.assertEqual(self.events[-2:], [("interval", CONTROL + 0xfc, dwell), ("start", CONTROL + 0xfc)])

    def test_new_label_resets_dwell_but_repaint_keeps_position(self):
        interval, dwell, step, gap = self.scrolling_fixture()
        m = self.m
        m.cpu.mem_write(STRING, b"Long album name\0")
        m.cpu.mem_write(CACHED, b"Previous album\0")
        m.write(CONTROL + 0xd8, CACHED, 140)
        m.write(CONTROL + 0xec, -64)
        m.hook(0x75584, lambda: STRING)  # database property lookup
        m.hook(0xcec8c, lambda: m.write(m.reg(0), STRING))  # owned string allocation
        m.hook(0x9b440, lambda: 280)  # font measurement
        for address in (0xd1a28, 0xcb9b0, 0x48350):
            m.hook(address, lambda: None)  # bitmap preparation and graphics blits
        m.call(0x5b74c, CONTROL, MODEL, CLIP)
        self.assertEqual(m.read(CONTROL + 0xec), 0)
        self.assertEqual(self.events, [("interval", CONTROL + 0xfc, dwell), ("start", CONTROL + 0xfc)])
        self.events.clear()
        m.write(CONTROL + 0xec, -step)
        m.call(0x5b74c, CONTROL, MODEL, CLIP)
        self.assertEqual(m.read(CONTROL + 0xec), (-step) & 0xffffffff)
        self.assertEqual(self.events, [])
        # A new short name must use static drawing. Deliver one timer event
        # left from the long name and check that it does not rearm itself.
        short, static_draws = 0x1000d000, []
        m.cpu.mem_write(short, b"Short\0")
        m.hook(0x75584, lambda: short)
        m.hook(0xcec8c, lambda: m.write(m.reg(0), m.reg(1)))
        m.hook(0x9b440, lambda: 50)
        m.hook(0x5ba58, lambda: static_draws.append(m.reg(0)))
        m.call(0x5b74c, CONTROL, MODEL, CLIP)
        self.assertEqual(m.read(CONTROL + 0xec), 0)
        self.assertEqual(static_draws, [CONTROL])
        self.assertEqual(self.events, [])
        m.write(EVENT + 4, 0x74696d65, CONTROL + 0xfc)
        m.call(0x809b0, CONTROL, EVENT)
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    NativeUI.image = payload(archive(args.candidate)["Firmware-4.3.1.1"], 0x4200)
    unittest.main(argv=[__file__], verbosity=2)
