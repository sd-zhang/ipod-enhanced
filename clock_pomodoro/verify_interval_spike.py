"""Run the compiled interval helper with native 4G calendar and RTC functions.

Unicorn must run outside the macOS sandbox, with the repository .venv.
Expiry delivery and elapsed wall time are provided by the fixture.
"""
import datetime as dt
import json
import unittest

from build_spike import CODE, SOURCE
from verify_rtc_spike import Clock, INPUT, SELECTED, packed_date, unpacked_date
from native_fixture import load_image

STATE = 0x10070200


class Interval(Clock):
    def __init__(self, image, artifacts):
        super().__init__(image, sixth_slot=True)
        self.cpu.mem_map(CODE, 0x10000)
        self.cpu.mem_write(CODE, (artifacts / 'interval.bin').read_bytes())
        self.symbols = json.loads((artifacts / 'symbols.json').read_text())
        self.now = dt.datetime(2026, 11, 24, 12)
        self.invoke('reset')

    def invoke(self, operation):
        self.set_time(self.now)
        self.cpu.mem_write(INPUT, packed_date(self.now))
        return self.call(self.symbols['pomo_' + operation], STATE, INPUT)

    def deadline(self):
        return unpacked_date(bytes(self.cpu.mem_read(STATE, 12)))

    def phase_round(self):
        return self.read(STATE + 12), self.read(STATE + 16)


class PomodoroIntervals(unittest.TestCase):
    def setUp(self):
        self.m = Interval(self.image, self.artifacts)

    def test_four_focus_periods_then_long_break_and_next_round(self):
        m = self.m
        self.assertEqual(m.invoke('start'), 1)
        self.assertEqual(m.deadline(), m.now + dt.timedelta(minutes=25))
        for phase, round_, duration in ((1, 0, 5), (0, 1, 25), (1, 1, 5),
                                         (0, 2, 25), (1, 2, 5), (0, 3, 25),
                                         (2, 3, 15), (0, 0, 25)):
            m.now = m.deadline()
            self.assertEqual(m.invoke('expire'), 1)
            self.assertEqual(m.phase_round(), (phase, round_))
            self.assertEqual(m.deadline(), m.now + dt.timedelta(minutes=duration))
        self.assertEqual(m.read(STATE + 28), 8)
        m.guards()

    def test_pause_resume_keeps_remainder_and_restores_existing_alarm(self):
        m = self.m
        m.arm_at(1, m.now + dt.timedelta(minutes=30), event=0x94)
        stock = m.records(0, 5)
        m.invoke('start')
        m.now += dt.timedelta(minutes=10, seconds=17)
        self.assertEqual(m.invoke('remaining'), 883)
        m.invoke('pause')
        self.assertEqual(m.read(SELECTED), 1)
        self.assertEqual(m.invoke('remaining'), 883)
        m.now += dt.timedelta(hours=2)
        self.assertEqual(m.invoke('expire'), 0)
        m.invoke('start')
        self.assertEqual(m.deadline(), m.now + dt.timedelta(seconds=883))
        self.assertEqual(m.records(0, 5), stock)

    def test_native_rtc_expiry_then_interval_handler_rearms_the_next_phase(self):
        m = self.m
        m.invoke('start')
        for phase, round_ in ((1, 0), (0, 1), (1, 1), (0, 2), (1, 2),
                              (0, 3), (2, 3), (0, 0)):
            m.now = m.deadline()
            m.set_time(m.now)
            m.deliver_rtc_interrupt()
            self.assertEqual(m.records(5, 1)[14], 0)
            # This handoff is supplied. Installing its real event routing is
            # intentionally outside the prototype's evidence.
            self.assertEqual(m.invoke('expire'), 1)
            self.assertEqual(m.records(5, 1)[14], 1)
            self.assertEqual(m.phase_round(), (phase, round_))
        m.guards()

    def test_native_sleep_timer_setting_does_not_change_pomodoro_alarm_or_state(self):
        m = self.m
        settings, prefs, table, notify = 0x10075000, 0x10078000, 0x1007b000, 0x1007c000
        m.write(settings + 0x18, prefs)
        m.write(prefs, table)
        m.write(table + 0x48, notify - table)
        m.hook(notify, lambda: 0)  # UI property-change delivery boundary
        m.hook(0xd3aa0, lambda: 0)  # OS timer stop boundary
        requests = []
        m.hook(0xcdd84, lambda: requests.append((m.reg(0), m.reg(1))) or 0)
        m.hook(0xd2bd0, lambda: 0)  # OS timer arm boundary
        m.invoke('start')
        old_alarm, old_state = m.records(), bytes(m.cpu.mem_read(STATE, 32))
        for minutes in (15, 30, 60, 90, 120, 65535):
            with self.subTest(minutes=minutes):
                requests.clear()
                m.call(0xca38c, settings, minutes)  # native Sleep Timer setter
                expected = [] if minutes == 65535 else [(settings + 0x70,
                                                         min(minutes, 30) * 60000)]
                self.assertEqual(requests, expected)
                self.assertEqual(m.records(), old_alarm)
                self.assertEqual(bytes(m.cpu.mem_read(STATE, 32)), old_state)

    def test_stale_early_duplicate_and_reset_notifications_do_not_skip_phase(self):
        m = self.m
        self.assertEqual(m.invoke('expire'), 0)
        m.invoke('start')
        first = m.deadline()
        m.now = first - dt.timedelta(seconds=1)
        self.assertEqual(m.invoke('expire'), 0)
        m.now = first
        self.assertEqual(m.invoke('expire'), 1)
        self.assertEqual(m.invoke('expire'), 0)
        m.invoke('reset')
        m.now += dt.timedelta(hours=3)
        self.assertEqual(m.invoke('expire'), 0)
        self.assertEqual(m.phase_round(), (0, 0))
        self.assertEqual(m.invoke('remaining'), 1500)

    def test_late_delivery_preserves_schedule_instead_of_drifting(self):
        m = self.m
        m.invoke('start')
        first = m.deadline()
        m.now = first + dt.timedelta(seconds=17)
        self.assertEqual(m.invoke('expire'), 1)
        self.assertEqual(m.deadline(), first + dt.timedelta(minutes=5))
        self.assertEqual(m.invoke('remaining'), 283)

    def test_long_delays_catch_up_in_bounded_time(self):
        m = self.m
        m.invoke('start')
        first = m.deadline()
        m.now = first + dt.timedelta(minutes=130 * 1000, seconds=7)
        self.assertEqual(m.invoke('expire'), 8001)
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(m.invoke('remaining'), 293)

    def test_cycles_span_leap_day_and_year_rollover(self):
        m = self.m
        for now in (dt.datetime(2028, 2, 28, 23, 50), dt.datetime(2026, 12, 31, 23, 50)):
            m.invoke('reset')
            m.now = now
            m.invoke('start')
            self.assertEqual(m.invoke('remaining'), 1500)
            m.now += dt.timedelta(minutes=25)
            self.assertEqual(m.invoke('expire'), 1)
            self.assertEqual(m.invoke('remaining'), 300)


if __name__ == '__main__':
    PomodoroIntervals.artifacts = SOURCE.parent / 'build/research/pomodoro-spike'
    PomodoroIntervals.image = load_image(SOURCE.parent /
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r19.ipsw')
    unittest.main(verbosity=2)
