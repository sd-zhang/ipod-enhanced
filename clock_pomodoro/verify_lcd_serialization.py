"""Native LCD transaction regression. Run .venv Python outside macOS sandbox.

The actual ARM LCD driver, mutex fast paths and alarm queue execute. The host
supplies task switches, mutex blocking/wakeup, panel profile and LCD register
routing; this is not an RTOS or electrical LCD emulator.
"""
import argparse
import json
import unittest
from pathlib import Path

from verify_alarm_pattern import PiezoClock, PATTERN, TAG
from native_fixture import load_image
from verify_native import STACK, RETURN, REG
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn import arm_const as arm

PANEL, RECT = 0x10070000, 0x10070100
DRAW = 0xc3f74
OWNER = 0x1039e3c4
TASK_A, TASK_B = 0x10001000, 0x10002000
LOCK_ID = 707  # Supplied free native mutex; no production ID is hardcoded.
LOCK = 0x10413c6c + 32 * LOCK_ID


class Paused(Exception):
    pass


class Blocked(Exception):
    pass


class LCD(PiezoClock):
    def __init__(self, candidate, variant=0):
        art = candidate.with_suffix('.artifacts')
        super().__init__(load_image(candidate), json.loads((art / 'pomodoro-symbols.json').read_text()))
        path = art / 'lcd-symbols.json'
        self.lcd_symbols = json.loads(path.read_text()) if path.exists() else {}
        if self.lcd_symbols:
            self.write(self.lcd_symbols['lcd_mutex'], LOCK_ID)
        self.cpu.mem_map(0x70003000, 0x1000)
        self.cpu.mem_write(PANEL, bytes([0, 0, 0, 0, 0, 0, 4, 0, 0, 70, 80, 0, 0, 0]))
        self.write(0x10419990, PANEL, variant, 0, 0, 0, 0)
        self.write(RECT, 0, 0, 0, 8)
        self.cpu.mem_write(0x103cdcfc, bytes.fromhex('aa55'))
        self.hooks.pop(0x154c18)
        self.hook(0x10ec30, lambda: 0)
        # Board brightness/R7 display-mode boundary; actual R4 contrast runs.
        # Separate power/display-mode tests execute the R7 transaction itself.
        self.hook(0x10f1fc, lambda: 0)
        self.variant, self.selected, self.high = variant, None, {}
        self.registers, self.lcd_writes, self.bus_writes = {4: 0x446}, [], []
        self.depths, self.lcd_times = [], []
        self.cpu.hook_add(UC_HOOK_MEM_WRITE, self.observe)
        self.scheduler_hook = self.cpu.hook_add(UC_HOOK_CODE, self.schedule)

    def schedule(self, cpu, pc, size, context):
        # Suspend at mutex entry, before its frame is created. On wake, rerun
        # the actual native fast path. The host supplies blocking, not success.
        if pc == 0x96710 and self.reg(0) == LOCK_ID:
            owner = self.read(LOCK)
            if owner and owner != self.read(OWNER):
                raise Blocked

    def observe(self, cpu, access, address, size, value, context):
        if address not in (0x70003008, 0x70003010):
            return
        self.bus_writes.append((address, value))
        if self.lcd_symbols and self.read(self.lcd_symbols['lcd_mutex']):
            assert self.read(LOCK) == self.read(OWNER), 'LCD write without mutex ownership'
            self.depths.append(self.read(LOCK + 4) & 0xffff)
        if self.variant:
            kind, value = ('command' if value >> 16 == 0x74 else 'data'), value & 0xffff
        else:
            kind = 'command' if address == 0x70003008 else 'data'
            if kind not in self.high:
                self.high[kind] = value & 0xff
                return
            value = self.high.pop(kind) * 256 + (value & 255)
        if kind == 'command':
            self.selected = value
        else:
            self.registers[self.selected] = value
            self.lcd_writes.append((self.selected, value))
            self.lcd_times.append((self.ticks, self.selected, value))

    def save(self, end=RETURN):
        return (self.cpu.context_save(), bytes(self.cpu.mem_read(STACK - 4096, 4096)),
                self.read(OWNER), end)

    def pause(self, entry, stop, *args):
        def stop_here():
            raise Paused
        self.hook(stop, stop_here)
        try:
            self.call(entry, *args)
        except Paused:
            return self.save()
        finally:
            self.hooks.pop(stop)
        raise AssertionError('interleaving boundary not reached')

    def resume(self, saved):
        context, stack, task, end = saved
        self.cpu.mem_write(STACK - 4096, stack)
        self.cpu.context_restore(context)
        self.write(OWNER, task)
        self.cpu.emu_start(self.cpu.reg_read(arm.UC_ARM_REG_PC), end, count=300000)
        assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == end
        assert self.cpu.reg_read(arm.UC_ARM_REG_SP) == STACK

    def blocked_call(self, entry, *args):
        self.write(OWNER, TASK_B)
        before = list(self.bus_writes)
        try:
            self.call(entry, *args)
        except Blocked:
            assert self.bus_writes == before, 'contender touched the LCD before owning it'
            return self.save()
        raise AssertionError('LCD contender did not block; commands can interleave')


class Serialization(unittest.TestCase):
    def test_alarm_backlight_cannot_interrupt_any_draw_bus_write(self):
        # Includes high/low byte boundaries, GRAM address and pixel streams.
        for variant in (0, 1):
            baseline = LCD(self.candidate, variant)
            baseline.call(DRAW, 0, RECT, 0)
            sites = (0xc3fe4, 0xc3ff0, 0xc3ff8, 0xc400c)
            if not variant:
                sites += (0x147c50, 0x147cb0)  # After high byte, before low byte.
            for stop in sites:
                with self.subTest(bus=variant, stop=hex(stop)):
                    m = LCD(self.candidate, variant)
                    first = m.pause(DRAW, stop, 0, RECT, 0)
                    second = m.blocked_call(0x154c18, 1)
                    m.resume(first)
                    m.resume(second)
                    self.assertEqual(m.lcd_writes, baseline.lcd_writes + [(4, 0x450)])
                    self.assertEqual(m.read(LOCK), 0)

    def test_draw_cannot_interrupt_contrast(self):
        for variant in (0, 1):
            with self.subTest(bus=variant):
                m = LCD(self.candidate, variant)
                first = m.pause(0x154c18, 0x154e7c, 1)
                second = m.blocked_call(DRAW, 0, RECT, 0)
                m.resume(first)
                m.resume(second)
                self.assertEqual(m.lcd_writes, [(4, 0x450), (17, 20), (18, 0xaa55)])
                self.assertEqual(m.read(LOCK), 0)

    def test_native_alarm_sound_and_light_cadence_survive_serialization(self):
        for variant in (0, 1):
            m = LCD(self.candidate, variant)
            start = m.ticks
            for pitch, duration in PATTERN:
                self.assertEqual(m.receive(pitch, duration | TAG), 0)
            m.drain()
            self.assertEqual(m.tones(), PATTERN)
            self.assertEqual(m.registers[4], 0x450)
            self.assertEqual(m.read(LOCK), 0)
            self.assertEqual([(t-start, v) for t, r, v in m.lcd_times if r == 4],
                [(0, 0x450), (350000, 0x446), (600000, 0x450), (950000, 0x446),
                 (1200000, 0x450), (1550000, 0x446), (1800000, 0x450),
                 (2150000, 0x446), (2400000, 0x450)])

    def test_real_piezo_worker_waits_at_start_dark_cancel_and_final_restore(self):
        for edge in ('start', 'dark', 'cancel', 'finish'):
            with self.subTest(edge=edge):
                m = LCD(self.candidate)
                if edge != 'start':
                    for pitch, duration in PATTERN:
                        m.receive(pitch, duration | TAG)
                    target = m.ticks + (200000 if edge == 'dark' else
                                        2150000 if edge == 'finish' else 0)
                    while m.ticks < target:
                        m.advance()
                first = m.pause(DRAW, 0xc3ff8, 0, RECT, 0)
                before = list(m.bus_writes)
                m.write(OWNER, TASK_B)
                with self.assertRaises(Blocked):
                    if edge == 'start':
                        m.receive(180, TAG | 200000)
                    elif edge == 'cancel':
                        m.receive(kind=1)
                    else:
                        m.advance()
                self.assertEqual(m.bus_writes, before)
                worker = m.save(0x15962c)
                m.resume(first)
                m.resume(worker)
                self.assertEqual(m.registers[18], 0xaa55)
                self.assertEqual(m.registers[4], 0x446 if edge == 'dark' else 0x450)
                self.assertEqual(m.read(LOCK), 0)
                m.drain()
                self.assertEqual(m.read(0x14534654), 0)

    def test_power_init_suspend_and_display_mode_serialize_with_drawing(self):
        for entry, args, stop in ((0x1549b4, (1,), 0x154b7c),
                                  (0x154bd4, (), 0x154c00),
                                  (0x154c94, (), 0x154cb0),
                                  (0x154cc4, (1,), 0x154d04),
                                  (0x154cc4, (0,), 0x154d78)):
            with self.subTest(entry=hex(entry), args=args):
                m = LCD(self.candidate)
                m.hook(0x1548d0, lambda: PANEL)  # supplied panel-identification result
                m.hook(0x154830, lambda: 0)  # bus-clock configuration boundary
                m.hook(0x10f984, lambda: 0)  # physical panel/power acknowledgement
                m.hook(0x154154, lambda: m.set_ticks(m.ticks + m.reg(0)*1000) or 0)
                m.hook(0x15419c, lambda: m.set_ticks(m.ticks + m.reg(0)) or 0)
                m.write(0x10419990 + 8, 1)
                first = m.pause(entry, stop, *args)
                second = m.blocked_call(DRAW, 0, RECT, 0)
                m.resume(first)
                m.resume(second)
                self.assertEqual(m.read(LOCK), 0)
                self.assertEqual(m.read(LOCK + 4) & 0xffff, 0)
                if entry == 0x1549b4 or (entry == 0x154cc4 and args == (1,)):
                    self.assertIn(2, m.depths, 'nested native calls should take the recursive mutex')

    def test_initialization_failure_restart_and_single_allocation(self):
        m = LCD(self.candidate)
        state = m.lcd_symbols['lcd_mutex']
        m.write(state, 0)
        requests, result = [], [0, LOCK_ID]

        def kernel():
            request = m.reg(0)
            self.assertEqual(m.read(request), 13)
            self.assertEqual(m.read(request+12), 2)
            destination = m.read(request+16)
            self.assertEqual(m.read(destination), 0)
            requests.append(destination)
            m.write(destination, result[1])
            m.write(request+4, result[0])
            return 0

        # Run the real 96500 syscall marshalling; supply only queue-pop result.
        m.hook(0x29880, kernel)
        for status, ident in ((7, 0), (7, LOCK_ID), (0, 0), (0, 307), (0, 708)):
            result[:] = status, ident
            m.call(m.lcd_symbols['lcd_initialize'])
            self.assertEqual(m.read(state), 0)
        result[:] = 0, LOCK_ID
        m.call(m.lcd_symbols['lcd_initialize'])
        self.assertEqual(m.read(state), LOCK_ID)
        calls = len(requests)
        m.call(m.lcd_symbols['lcd_initialize'])
        self.assertEqual(len(requests), calls)
        observed = []
        m.hook(0x87d04, lambda: observed.append(m.read(state)) or 0)
        m.call(m.lcd_symbols['lcd_pool_reset'])
        self.assertEqual(observed, [0])
        m.call(m.lcd_symbols['lcd_initialize'])
        self.assertEqual(len(requests), calls+1)
        self.assertEqual(m.read(state), LOCK_ID)
        m.hook(0xcf428, lambda: observed.append(m.read(state)) or 0)
        m.call(m.lcd_symbols['lcd_display_start'])
        self.assertEqual(observed[-1], LOCK_ID)

    def test_no_io_draw_callback_still_returns_in_two_instructions(self):
        m = LCD(self.candidate)
        m.write(LOCK, TASK_B, 1)
        executed = []
        m.cpu.hook_add(UC_HOOK_CODE, lambda cpu, pc, size, ctx: executed.append(pc))
        m.call(DRAW, 0, 0, 1)  # Invalid rectangle must never be dereferenced.
        self.assertEqual(executed, [0xc3f74, 0xc3f78])
        self.assertEqual(m.bus_writes, [])

    def test_native_contended_lock_uses_blocking_kernel_request(self):
        m = LCD(self.candidate)
        m.cpu.hook_del(m.scheduler_hook)
        m.write(LOCK, TASK_B, 1)
        requests = []

        def kernel():
            request = m.reg(0)
            self.assertEqual([m.read(request+n) for n in (0, 8, 12, 16)],
                             [14, LOCK_ID, 1, 0])
            self.assertEqual(m.bus_writes, [])
            requests.append(request)
            # Supply kernel ownership transfer/wakeup. The native mutex
            # wrapper, protected LCD operation and unlock all execute ARM.
            m.write(LOCK, TASK_A, 1)
            m.write(request+4, 0)
            return 0

        m.hook(0x29880, kernel)
        m.call(0x154e20)
        self.assertEqual(len(requests), 1)
        self.assertEqual(m.lcd_writes, [(4, 0x446)])
        self.assertEqual(m.read(LOCK), 0)

    def test_abi_early_returns_and_pre_initialization_native_fallback(self):
        for initialized in (False, True):
            for entry, args in ((DRAW, (0, RECT, 0)), (0x154e20, ()),
                                (0x154cc4, (1,)), (0x154c94, ())):
                with self.subTest(initialized=initialized, entry=hex(entry)):
                    m = LCD(self.candidate)
                    if not initialized:
                        m.write(m.lcd_symbols['lcd_mutex'], 0)
                    if entry != DRAW:
                        m.write(0x10419990, 0)
                    values = [0xdead0000 + n for n in range(4, 12)]
                    for reg in REG:
                        m.cpu.reg_write(reg, 0)
                    for n, value in enumerate(values, 4):
                        m.cpu.reg_write(REG[n], value)
                    for n, value in enumerate(args):
                        m.cpu.reg_write(REG[n], value)
                    m.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
                    m.cpu.reg_write(arm.UC_ARM_REG_LR, RETURN)
                    m.cpu.emu_start(entry, RETURN, count=200000)
                    self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC), RETURN)
                    self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_SP), STACK)
                    self.assertEqual([m.cpu.reg_read(REG[n]) for n in range(4, 12)], values)
                    self.assertEqual(m.read(LOCK), 0)

    def test_full_frame_output_and_instruction_overhead(self):
        from verify_lcd_package import BASE
        measured = []
        writes = []
        for package in (BASE, self.candidate):
            m = LCD(package)
            m.instruction_limit = 1000000
            m.write(RECT, 0, 0, 127, 160)
            m.cpu.mem_write(0x103cdcfc, bytes(range(256))*20)
            count = [0]
            m.cpu.hook_add(UC_HOOK_CODE, lambda *args: count.__setitem__(0, count[0]+1))
            m.call(DRAW, 0, RECT, 0)
            measured.append(count[0])
            writes.append(m.bus_writes)
        self.assertEqual(writes[0], writes[1])
        self.assertGreater(measured[1], measured[0])
        self.assertLess(measured[1]-measured[0], 150)
        print('Full-frame ARM instructions (not cycles):', dict(r31=measured[0], candidate=measured[1]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    Serialization.candidate = parser.parse_args().candidate
    unittest.main(argv=[__file__], verbosity=2)
