"""Exercise native Pomodoro timer expiry, failed UI delivery and retry.

Both timer workers, due-list removal, queue ownership, native mutex recursion,
UI publication/cleanup and cached countdown widgets execute ARM. Clock passage,
task switches, blocking/wakeup, and physical drawing are supplied. This does
not diagnose the separate physical wheel-freeze report. Run .venv Python with
Unicorn JIT allowed, outside the macOS sandbox.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
import unittest

from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "navigation_qol"), str(ROOT / "menu_scroll")]

from native_fixture import load_image
from ui_fixture import machine, renderer_event
from verify_native import STACK, RETURN, REG
from verify_ui import NativeClockScreen

HEAD, UI_LOCK, SCHED_LOCK = 0x103cf108, 0x103c9f44, 0x103cf118
OWNER, LOCK_TABLE = 0x1039e3c4, 0x10413c6c
TASK_UI, TASK_WORKER, TASK_SCHED = 0x10001000, 0x10002000, 0x10003000
SCHEDULER, DELIVERY, UI_QUEUE = 0x10060000, 0x10060400, 0x1001d000
NODE_S, NODE_U, MESSAGE = 0x10061000, 0x10061200, 0x10061400
SCHED_ID, UI_ID = 0x134, 0x135
RUN, STOP, EXPIRED = 0x72756e20, 0x73746f70, 0x65787069


class Halt(Exception):
    pass


class Blocked(Exception):
    pass


class TimerMachine:
    """Full timer delivery over the existing native Clock widget fixture."""
    def __init__(self, candidate):
        self.image = load_image(candidate)
        symbols = json.loads(candidate.with_suffix('.artifacts').joinpath(
            'pomodoro-symbols.json').read_text())
        self.screen = NativeClockScreen()
        self.screen.image, self.screen.symbols = self.image, symbols
        self.m = m = machine(self.image, symbols)
        self.timer = symbols['pomo_ui_timer']
        self.symbols = symbols
        self.now_us = 1000000
        self.cleanup = []
        self.lock_depths = []
        self.pause_at = None
        m.hook(0x87cec, lambda: self.now_us & 0xffffffff)
        # Queue nodes have native publisher owners; enqueue/dequeue/free run.
        m.write(0x10018000 + 0x1c, UI_QUEUE)
        m.write(0x103c9f18 + 0x18, DELIVERY)
        m.write(HEAD + 4, SCHEDULER)
        m.write(SCHEDULER + 0x14, NODE_S, NODE_S)
        m.write(NODE_S, 0, SCHEDULER)
        m.write(DELIVERY + 0x14, NODE_U, NODE_U)
        m.write(NODE_U, 0, DELIVERY)
        # Real recursive mutexes protect the timer list and UI publication.
        for address in (0x9f5d8, 0x9fce4, 0x87b88, 0x87d60):
            m.hooks.pop(address, None)
        for wrapper, handle, ident in ((SCHED_LOCK, 0x10064000, SCHED_ID),
                                      (UI_LOCK, 0x10064010, UI_ID)):
            m.write(wrapper, handle)
            m.write(handle, ident)
        # Native scheduler conditions/wakeup are kernel boundaries.
        m.hook(0x9fcdc, lambda: 0)
        m.hooks.pop(0x9f600, None)
        m.write(0x103cf120, SCHED_LOCK, 0, 0)
        m.hook(0x87e00, lambda: 0x10064200)
        m.hook(0x87b6c, lambda: 0)
        m.hook(0x87e70, lambda: 0)
        m.hook(0x9ff28, self.halt)
        m.cpu.hook_add(UC_HOOK_CODE, self.trace)
        m.write(OWNER, TASK_UI)
        m.run(2553, m.event)
        self.screen.activate(m, 0)
        self.screen.assert_countdown(m, '25:00')

    @staticmethod
    def halt():
        raise Halt

    def trace(self, cpu, pc, size, context):
        m = self.m
        if pc == self.pause_at:
            raise Halt
        if pc == 0x96710 and m.reg(0) in (SCHED_ID, UI_ID):
            lock = LOCK_TABLE + 32 * m.reg(0)
            owner = m.read(lock)
            if owner and owner != m.read(OWNER):
                raise Blocked
        if pc == 0x9f688:
            self.cleanup.append(m.reg(0))
        if pc == 0x9fd90:
            self.lock_depths.append(m.read(LOCK_TABLE + 32 * SCHED_ID + 4) & 0xffff)
        if pc == 0xd94bc:
            raise Halt  # delivery completed, before next blocking dequeue

    def save(self, end=RETURN):
        m = self.m
        base = {TASK_UI: STACK, TASK_WORKER: STACK - 0x20000,
                TASK_SCHED: STACK - 0x10000}[m.read(OWNER)]
        return (m.cpu.context_save(), bytes(m.cpu.mem_read(base - 8192, 8192)),
                m.read(OWNER), end, base)

    def resume(self, saved):
        m = self.m
        context, stack, task, end, base = saved
        m.cpu.mem_write(base - 8192, stack)
        m.cpu.context_restore(context)
        m.write(OWNER, task)
        try:
            m.cpu.emu_start(m.cpu.reg_read(arm.UC_ARM_REG_PC), end, count=300000)
        except Halt:
            pass
        assert m.cpu.reg_read(arm.UC_ARM_REG_PC) in (end, 0xd94bc)

    def run_task(self, entry, task):
        m = self.m
        for reg in REG:
            m.cpu.reg_write(reg, 0)
        base = STACK - (0x20000 if task == TASK_WORKER else 0x10000)
        m.cpu.reg_write(arm.UC_ARM_REG_SP, base)
        m.cpu.reg_write(arm.UC_ARM_REG_LR, RETURN)
        m.write(OWNER, task)
        m.cpu.emu_start(entry, RETURN, count=300000)

    def elapse(self, seconds=1):
        self.now_us += seconds * 1000000
        self.m.now += dt.timedelta(seconds=seconds)
        self.m.set_time(self.m.now)

    def expire(self):
        m = self.m
        assert m.read(HEAD) == self.timer
        self.elapse()
        m.write(OWNER, TASK_SCHED)
        try:
            self.run_task(0xd933c, TASK_SCHED)
        except Halt:
            pass
        assert m.read(HEAD) == 0, 'native scheduler must dequeue the due timer'
        assert m.read(self.timer + 0x18) == EXPIRED
        assert m.read(NODE_S + 12) == self.timer

    def pressure(self, kind):
        m = self.m
        m.write(0x103c9f3c, 0 if kind == 'pool' else MESSAGE,
                0 if kind == 'pool' else MESSAGE)
        m.write(MESSAGE, 0)
        m.write(DELIVERY + 0x14, 0 if kind == 'queue' else NODE_U,
                0 if kind == 'queue' else NODE_U)
        m.write(NODE_U, 0, DELIVERY)

    def deliver(self, pause_at=None):
        m = self.m
        self.pause_at = pause_at
        m.write(OWNER, TASK_WORKER)
        try:
            self.run_task(0xd949c, TASK_WORKER)
        except Halt:
            saved = self.save()
        else:
            raise AssertionError('delivery worker did not reach its boundary')
        finally:
            self.pause_at = None
        return saved

    def render_queued_tick(self):
        m = self.m
        m.write(OWNER, TASK_UI)
        # Actual queue consumption returns the native published time message.
        # The enclosing application's message-to-renderer adapter is supplied,
        # as in the existing Clock UI fixture. Widget notifications and cached
        # bitmap decoding execute through the real renderer.
        event, raw = 0x1007f000, 0x1007f100
        m.call(0x9ffac, UI_QUEUE, raw, raw + 16)
        message = m.read(raw + 4)
        assert message == MESSAGE and m.read(message + 4) == 0x74696d65
        assert m.read(message + 16) == self.timer
        assert [m.read(message + offset) for offset in (20, 24, 28)] == [0, 0, 0]
        m.write(event, 0, 0x74696d65, self.timer)
        assert renderer_event(m, event) == 1
        m.call(0xb1774, message)

    def cancel(self, action):
        m = self.m
        m.write(OWNER, TASK_UI)
        if action == 'leave':
            m.call(self.symbols['pomo_dispatch'], 2555 * 4 + 2, 0)
        else:
            m.call(self.symbols['pomo_dispatch'], (2556 if action == 'pause' else 2557) * 4 + 2, 0)

    def check_idle_locks(self):
        for ident in (SCHED_ID, UI_ID):
            assert self.m.read(LOCK_TABLE + 32 * ident) == 0


class TimerRecovery(unittest.TestCase):
    def test_native_workers_retry_failed_pool_and_queue_publication(self):
        for kind in ('pool', 'queue'):
            with self.subTest(pressure=kind):
                f = TimerMachine(self.candidate); m = f.m
                f.expire(); f.pressure(kind); f.deliver()
                self.assertEqual(f.cleanup, [f.timer])
                self.assertEqual(m.read(HEAD), f.timer, 'lost tick must rearm its one-shot timer')
                self.assertEqual(m.read(f.timer), 0)
                self.assertEqual(m.read(f.timer + 0x18), RUN)
                self.assertEqual(m.read(f.timer + 0x1c), RUN)
                self.assertEqual(m.read(f.timer + 8), f.now_us + 1000000)
                self.assertIn(1, f.lock_depths, 'retry must enter native schedule under its mutex')
                f.screen.assert_countdown(m, '25:00')
                f.expire(); f.pressure(None); f.deliver()
                self.assertEqual(m.read(HEAD), 0, 'accepted tick waits for UI to rearm')
                f.render_queued_tick()
                f.screen.assert_countdown(m, '24:58')
                self.assertEqual(m.read(HEAD), f.timer)
                self.assertEqual(m.read(f.timer), 0)
                f.check_idle_locks()

    def test_accepted_tick_keeps_single_normal_ui_rearm(self):
        f = TimerMachine(self.candidate); m = f.m
        f.expire(); f.pressure(None); f.deliver()
        self.assertEqual(f.cleanup, [f.timer])
        self.assertEqual(m.read(HEAD), 0)
        self.assertEqual(m.read(f.timer + 0x18), STOP)
        self.assertEqual(m.read(f.timer + 0x1c), EXPIRED)
        f.render_queued_tick()
        f.screen.assert_countdown(m, '24:59')
        self.assertEqual(m.read(HEAD), f.timer)
        self.assertEqual(m.read(f.timer), 0)
        f.check_idle_locks()

    def test_persistent_pressure_retries_once_per_second_without_duplicates(self):
        f = TimerMachine(self.candidate); m = f.m
        for _ in range(12):
            f.expire(); f.pressure('pool'); f.deliver()
            self.assertEqual(m.read(HEAD), f.timer)
            self.assertEqual(m.read(f.timer), 0)
            self.assertEqual(m.read(f.timer + 8), f.now_us + 1000000)
        self.assertEqual(len(f.cleanup), 12)
        self.assertEqual(m.invoke('remaining'), 1488)
        f.expire(); f.pressure(None); f.deliver(); f.render_queued_tick()
        f.screen.assert_countdown(m, '24:47')
        f.check_idle_locks()

    def test_leave_pause_reset_after_cleanup_suppress_retry(self):
        for action in ('leave', 'pause', 'reset'):
            with self.subTest(action=action):
                f = TimerMachine(self.candidate); m = f.m
                f.expire(); f.pressure('pool')
                # Stop exactly after native cleanup releases the scheduler lock.
                # The native cleanup is the helper's first call to 9f688; pause
                # on its return using the saved LR, then run the real UI action.
                saved = f.deliver(pause_at=0x9f688)
                return_pc = m.cpu.reg_read(arm.UC_ARM_REG_LR)
                f.pause_at = return_pc
                context, stack, task, end, base = saved
                m.cpu.context_restore(context); m.cpu.mem_write(base - 8192, stack)
                try:m.cpu.emu_start(0x9f688, RETURN, count=300000)
                except Halt:pass
                f.pause_at = None
                saved = f.save()
                self.assertEqual(m.read(f.timer + 0x18), STOP)
                f.cancel(action)
                f.resume(saved)
                self.assertEqual(m.read(HEAD), 0)
                self.assertEqual(m.read(f.timer + 0x1c), STOP)
                self.assertEqual(f.cleanup, [f.timer])
                f.check_idle_locks()

    def test_leave_pause_reset_while_expiry_is_in_flight_wait_then_cancel(self):
        for action in ('leave', 'pause', 'reset'):
            with self.subTest(action=action):
                f = TimerMachine(self.candidate); m = f.m
                f.expire(); f.pressure('pool')
                worker = f.deliver(pause_at=0x9f688)
                m.hook(0x8807c, f.halt)  # actual condition wait released its mutex
                with self.assertRaises(Halt):
                    f.cancel(action)
                ui = f.save()
                self.assertEqual(m.read(LOCK_TABLE + 32 * SCHED_ID), 0)
                self.assertEqual(m.read(f.timer + 0x18), EXPIRED)
                f.resume(worker)
                self.assertEqual(m.read(HEAD), 0)
                m.hook(0x8807c, lambda: 0)  # supplied wakeup resumes real reacquire
                f.resume(ui)
                self.assertEqual(m.read(f.timer + 0x18), STOP)
                self.assertEqual(m.read(f.timer + 0x1c), STOP)
                self.assertEqual(m.read(0x103cf124), 0, 'native cleanup drained waiters')
                self.assertEqual(f.cleanup, [f.timer])
                f.check_idle_locks()

    def test_cancel_after_retry_check_blocks_then_removes_the_new_link(self):
        for action in ('leave', 'pause', 'reset'):
            with self.subTest(action=action):
                f = TimerMachine(self.candidate); m = f.m
                f.expire(); f.pressure('pool')
                worker = f.deliver(pause_at=0x9fd90)
                self.assertEqual(m.read(LOCK_TABLE + 32 * SCHED_ID), TASK_WORKER)
                with self.assertRaises(Blocked):
                    f.cancel(action)
                ui = f.save()
                f.resume(worker)
                self.assertEqual(m.read(HEAD), f.timer)
                f.resume(ui)
                self.assertEqual(m.read(HEAD), 0)
                self.assertEqual(m.read(f.timer + 0x1c), STOP)
                self.assertEqual(f.cleanup, [f.timer])
                f.check_idle_locks()

    def test_later_manual_restart_keeps_its_deadline(self):
        f = TimerMachine(self.candidate); m = f.m
        f.expire(); f.pressure('pool')
        saved = f.deliver(pause_at=0x9f688)
        return_pc = m.cpu.reg_read(arm.UC_ARM_REG_LR)
        f.pause_at = return_pc
        try:m.cpu.emu_start(0x9f688, RETURN, count=300000)
        except Halt:pass
        f.pause_at = None
        saved = f.save()
        f.cancel('pause')
        f.elapse(3)
        m.call(f.symbols['pomo_dispatch'], 2556 * 4 + 2, 0)
        deadline = m.read(f.timer + 8)
        f.elapse(1)
        f.resume(saved)
        self.assertEqual(m.read(HEAD), f.timer)
        self.assertEqual(m.read(f.timer), 0)
        self.assertEqual(m.read(f.timer + 8), deadline)
        f.check_idle_locks()

    def test_retry_across_counter_wrap_and_rtc_phase_change_refreshes_the_screen(self):
        f = TimerMachine(self.candidate); m = f.m
        f.now_us = 0xffff8000
        m.call(0xd2bd0, f.timer)
        m.now = m.deadline() - dt.timedelta(seconds=1)
        m.set_time(m.now)
        f.expire(); f.pressure('pool'); f.deliver()
        self.assertEqual(m.read(f.timer + 8), (f.now_us + 1000000) & 0xffffffff)
        m.deliver_rtc_interrupt()
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(len(m.beeps), 1)
        f.screen.assert_countdown(m, '25:00')
        f.expire(); f.pressure(None); f.deliver(); f.render_queued_tick()
        f.screen.assert_countdown(m, '04:59')
        m.call(0xcf744, m.phase)
        self.assertEqual(bytes(m.cpu.mem_read(m.read(m.phase + 0xb8), 5)), b'Break')
        self.assertEqual(m.read(HEAD), f.timer)
        self.assertEqual(m.read(f.timer), 0)
        self.assertEqual(f.cleanup, [f.timer, f.timer])
        f.check_idle_locks()

    def test_unrelated_timer_retains_native_loss_and_cleanup(self):
        f = TimerMachine(self.candidate); m = f.m
        f.cancel('pause')
        other = 0x10065000
        m.call(0x8bddc, 0x10012000, other, 0)
        m.call(0xcdd84, other, 1000)
        m.call(0xd2bd0, other)
        f.timer = other
        f.expire(); f.pressure('pool'); f.deliver()
        self.assertEqual(m.read(HEAD), 0)
        self.assertEqual(m.read(other + 0x18), STOP)
        self.assertEqual(m.read(other + 0x1c), EXPIRED)
        self.assertEqual(f.cleanup, [other])
        f.check_idle_locks()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args, rest = parser.parse_known_args()
    TimerRecovery.candidate = args.candidate
    unittest.main(argv=[__file__, *rest], defaultTest='TimerRecovery', verbosity=2)
