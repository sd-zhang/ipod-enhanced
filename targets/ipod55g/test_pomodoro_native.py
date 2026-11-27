"""Native 5.5G interval/runtime verification, with kernel/MMIO boundaries."""
import datetime as dt
import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from unicorn import arm_const as arm
from layout import link, install_elf, finish
from research_clock import Clock, INPUT, OUTPUT, SELECTED, MMIO, packed_date, stock_image, unpacked_date
from pomodoro_patch import apply

SOURCE = Path(__file__).resolve().parent


class Runtime(Clock):
    def __init__(self, image, symbols):
        self.symbols = symbols
        super().__init__(image, epoch=symbols['pomo_calendar'])
        self.alerts = []
        self.hook(symbols['ipod55g_alarm_signal'], lambda: self.alerts.append(True))
        self.call(symbols['pomo_boot'])

    def command(self, action):
        self.call(self.symbols['pomo_control'], action)

    def state(self, remaining=True):
        self.call(self.symbols['pomo_snapshot'], OUTPUT, int(remaining))
        return struct.unpack('<5I', self.cpu.mem_read(OUTPUT + 12, 20))

    def expire(self):
        self.call(self.symbols['pomo_background'])


class PomodoroRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = TemporaryDirectory()
        directory = Path(cls.build.name)
        stub = directory / 'alarm.c'
        stub.write_text('void ipod55g_alarm_signal(void) {}\n')
        elf = link([SOURCE/'pomodoro_runtime.c', SOURCE/'pomodoro_hooks.S', stub], directory, 'pomo_boot')
        image, cls.symbols = install_elf(stock_image(), elf)
        apply(image, cls.symbols)
        cls.image = finish(image)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def test_reboot_starts_stopped_and_reset_cancels(self):
        m = Runtime(self.image, self.symbols)
        self.assertEqual(m.state(), (0, 0, 0, 1500, 0))
        m.command(0)
        self.assertEqual(m.read(SELECTED), 5)
        m.command(1)
        self.assertEqual(m.state(), (0, 0, 0, 1500, 0))
        self.assertEqual(m.records(5, 1)[14], 0)
        self.assertEqual(Runtime(self.image, self.symbols).state(), (0, 0, 0, 1500, 0))

    def test_four_focus_rounds_automatically_advance_to_long_break(self):
        m = Runtime(self.image, self.symbols)
        now = dt.datetime(2026, 11, 25, 12)
        m.command(0)
        for round in range(4):
            now += dt.timedelta(minutes=25)
            m.set_time(now)
            m.deliver()  # actual patched RTC task invokes background hook
            duration = 900 if round == 3 else 300
            self.assertEqual(m.state()[:4], (2 if round == 3 else 1, round, 1, duration))
            self.assertEqual(m.read(SELECTED), 5)
            now += dt.timedelta(seconds=duration)
            m.set_time(now)
            m.deliver()
            self.assertEqual(m.state()[:4], (0, (round + 1) % 4, 1, 1500))
        self.assertEqual(len(m.alerts), 8)

    def test_pause_resume_preserves_seconds_after_leaving_screen(self):
        m = Runtime(self.image, self.symbols)
        m.command(0)
        m.set_time(dt.datetime(2026, 11, 25, 12, 7, 13))
        m.command(0)
        self.assertEqual(m.state()[:4], (0, 0, 0, 1067))
        m.set_time(dt.datetime(2026, 11, 25, 14))
        m.command(0)
        self.assertEqual(m.state()[:4], (0, 0, 1, 1067))
        m.set_time(dt.datetime(2026, 11, 25, 14, 17, 47))
        m.deliver()
        self.assertEqual(m.state()[:4], (1, 0, 1, 300))
        self.assertEqual(len(m.alerts), 1)

    def test_long_absence_catches_up_once_with_bounded_transitions(self):
        m = Runtime(self.image, self.symbols)
        m.command(0)
        m.set_time(dt.datetime(2026, 11, 26, 14, 26))
        m.deliver()
        self.assertEqual(m.state(), (1, 0, 1, 240, 97))
        self.assertEqual(len(m.alerts), 1)

    def test_manual_clock_change_preserves_remaining_duration(self):
        m = Runtime(self.image, self.symbols)
        m.command(0)
        m.set_time(dt.datetime(2026, 11, 25, 12, 7, 13))
        m.cpu.mem_write(INPUT, packed_date(dt.datetime(2027, 1, 1, 9)))
        m.call(0xb3764, INPUT)
        self.assertEqual(m.state()[:4], (0, 0, 1, 1067))
        self.assertEqual(len(m.alerts), 0)

    def test_timezone_change_preserves_remaining_duration(self):
        m = Runtime(self.image, self.symbols)
        m.command(0)
        m.set_time(dt.datetime(2026, 11, 25, 12, 7, 13))
        m.cpu.mem_write(m.epoch + 11, b'\x03')
        m.call(0xbb118, 4, 0)
        self.assertEqual(m.state()[:4], (0, 0, 1, 1067))

    def test_native_rtc_wake_precedes_background_alert_and_other_slots_survive(self):
        m=Runtime(self.image,self.symbols);now=dt.datetime(2026,11,25,12)
        for slot in range(5):m.arm_at(slot,now+dt.timedelta(hours=1,minutes=slot),event=0x94+slot)
        before=m.records();m.command(0)
        self.assertEqual(m.records(),before)
        observed=[]
        m.hook(self.symbols['ipod55g_alarm_signal'],lambda:observed.append(list(m.wakes)))
        m.set_time(now+dt.timedelta(minutes=25));m.deliver()
        self.assertEqual(observed,[[1]])
        self.assertEqual(m.delivered,[])
        self.assertEqual(m.records(),before)
        m.command(1);self.assertEqual(m.records(),before)

    def test_alarm_publication_is_serialized_with_reset(self):
        m=Runtime(self.image,self.symbols);m.command(0)
        m.set_time(dt.datetime(2026,11,25,12,25))
        original_unlock=m.hooks[0xa687c];resets=[];publication_locks=[]
        def signal():
            depth=sum((1 if kind=='lock' else -1) for kind,key in m.locks if key==0x124)
            publication_locks.append(depth);m.alerts.append(True)
        def unlock():
            value=original_unlock();caller=m.cpu.reg_read(arm.UC_ARM_REG_LR)
            if m.reg(0)==0x124 and not resets and self.symbols['pomo_background']<=caller<self.symbols['pomo_set_clock']:
                resets.append(len(m.alerts))
                saved=m.cpu.context_save();m.command(1);m.cpu.context_restore(saved)
            return value
        m.hook(self.symbols['ipod55g_alarm_signal'],signal);m.hook(0xa687c,unlock)
        m.expire()
        self.assertEqual(publication_locks,[1])
        self.assertEqual(resets,[1]) # request already accepted before Reset can take124
        self.assertEqual(m.state(),(0,0,0,1500,0))
        m.expire();self.assertEqual(m.alerts,[True])

    def test_due_clock_edit_defers_once_and_reset_cancels_deferred_alarm(self):
        for reset in (False,True):
            with self.subTest(reset=reset):
                m=Runtime(self.image,self.symbols);m.command(0)
                m.set_time(dt.datetime(2026,11,25,12,25))
                new=dt.datetime(2027,1,1,9)
                m.cpu.mem_write(INPUT,packed_date(new));m.call(0xb3764,INPUT)
                self.assertEqual(m.state()[:4],(1,0,1,300))
                self.assertEqual(m.alerts,[])
                self.assertEqual(unpacked_date(m.records(5,1)[:12]),new+dt.timedelta(seconds=1))
                if reset:m.command(1)
                m.set_time(new+dt.timedelta(seconds=1));m.deliver()
                self.assertEqual(m.alerts,[] if reset else [True])
                if not reset:self.assertEqual(m.state()[:4],(1,0,1,299))

    def test_reset_after_due_suppresses_pending_alert(self):
        m = Runtime(self.image, self.symbols)
        m.command(0)
        m.set_time(dt.datetime(2026, 11, 25, 12, 26))
        m.command(1)
        m.expire()
        self.assertEqual(m.state(), (0, 0, 0, 1500, 0))
        self.assertEqual(m.alerts, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
