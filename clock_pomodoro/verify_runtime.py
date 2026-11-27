"""Execute the packaged Pomodoro runtime and original RTC code.

Use repository Python outside the macOS sandbox for Unicorn JIT. Kernel
blocking, interrupts, audible output and physical wake remain host boundaries.
"""
import argparse
import datetime as dt
import json
import struct
import unittest
from pathlib import Path

from verify_interval_spike import Interval, STATE
from verify_rtc_spike import Clock, INPUT, OUTPUT, SELECTED, EPOCH, packed_date, unpacked_date
from native_fixture import load_image
from unicorn import arm_const as arm

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r19.ipsw'
BSS = 0x14534294
LOCK = 0x10413c6c + 0x124 * 32


class RuntimeClock(Clock):
    def __init__(self, image, symbols):
        super().__init__(image, integrated_table=symbols['pomo_alarms'])
        self.symbols = symbols
        self.state = symbols['pomo_state']
        self.end = struct.unpack_from('<I', image, 0x4b0)[0]
        self.cpu.mem_write(BSS - 16, b'\xa5' * 16)
        self.cpu.mem_write(self.end, b'\xa5' * 16)
        self.now = dt.datetime(2026, 11, 24, 12)
        # Exercise native recursive mutex owner/depth code. Kernel task
        # identity, atomic entry and blocking remain supplied boundaries.
        del self.hooks[0x96710]
        del self.hooks[0x96a70]
        self.hook(0x1618e0, lambda: 0)
        self.hook(0x1618d8, lambda: 0)
        self.write(0x1039e3c4, 0x10001000)
        self.beeps = []
        self.tone_requests = []
        # r29 redirects this private callee literal to the stock eight-tone
        # sequence. Count complete alerts for the existing RTC/UI assertions,
        # while still checking every native request and rejecting partial ones.
        self.beep_pattern = ([(180, 0x80000000 | 200000),
                              (144, 0x80000000 | 400000)] * 4
                             if struct.unpack_from('<I', image, 0x384338)[0] != 0xa6e6c
                             else [(180, 200000)])
        # This runtime fixture supplies the application/display boundary just
        # as it supplies physical wake. r27 consults native app preferences
        # after that wake. verify_alarm_light replaces this display stub and
        # executes the actual backlight service, policy and timer routines.
        app, prefs = 0x10040000, 0x10046000
        self.write(app + 0x124, prefs)
        self.write(prefs + 0xb34, 0x81201)  # Backlight Alerts defaults On.
        self.light_requests = []
        self.hook(0x70290, lambda: app)
        def light_request():
            assert (self.reg(0), self.reg(1)) == (app + 0x11c, app)
            self.light_requests.append(self.now)
            return 0
        self.hook(0x3408c, light_request)
        self.hook(0x29880, self.kernel)
        self.call(0x96710, 0x124)
        self.cpu.reg_write(arm.UC_ARM_REG_R7, 0)
        self.cpu.emu_start(0x8cf14, 0x8cf3c, count=10000)
        assert self.read(self.state + 24) == 1500
        assert self.read(symbols['pomo_ready']) == 1

    def kernel(self):
        request = self.reg(0)
        assert self.read(request) == 4
        assert self.read(LOCK) == self.read(LOCK - 32) == 0
        message = self.read(request + 24)
        tone = self.read(message + 20), self.read(message + 24)
        assert tone == self.beep_pattern[len(self.tone_requests) % len(self.beep_pattern)]
        self.tone_requests.append(tone)
        assert self.records(5, 1)[14] == 1
        if len(self.tone_requests) % len(self.beep_pattern) == 0:
            self.beeps.append(self.now)
        self.write(message + 28, 0)
        return 0

    def invoke(self, operation):
        self.set_time(self.now)
        if operation == 'remaining':
            self.call(self.symbols['pomo_snapshot'], OUTPUT, 1)
            return self.read(OUTPUT + 24)
        assert operation in ('start', 'pause', 'reset')
        self.call(self.symbols['pomo_control'], 1 if operation == 'reset' else 0)

    def deadline(self):
        return unpacked_date(bytes(self.cpu.mem_read(self.state, 12)))

    def phase_round(self):
        return self.read(self.state + 12), self.read(self.state + 16)

    def guards(self):
        assert bytes(self.cpu.mem_read(BSS - 16, 16)) == b'\xa5' * 16
        assert bytes(self.cpu.mem_read(self.end, 16)) == b'\xa5' * 16
        assert self.read(LOCK) == self.read(LOCK - 32) == 0
        assert len(self.tone_requests) % len(self.beep_pattern) == 0, 'partial alarm sequence'


class BackgroundRuntime(unittest.TestCase):
    def test_rtc_delivery_advances_and_rearms_without_host_handoff(self):
        m = self.make_machine()
        m.invoke('start')
        for phase, round_ in ((1, 0), (0, 1), (1, 1), (0, 2),
                              (1, 2), (0, 3), (2, 3), (0, 0)):
            m.now = m.deadline()
            m.set_time(m.now)
            m.deliver_rtc_interrupt()
            self.assertEqual(m.phase_round(), (phase, round_))
            self.assertEqual(m.records(5, 1)[14], 1)
            self.assertEqual(m.read(SELECTED), 5)
        if isinstance(m, RuntimeClock):
            self.assertEqual(len(m.beeps), 8)
            m.guards()

    def test_reset_cancels_and_returns_to_stopped_focus(self):
        m = self.make_machine()
        m.invoke('start')
        m.now += dt.timedelta(minutes=8)
        m.invoke('reset')
        self.assertEqual(m.phase_round(), (0, 0))
        self.assertEqual(m.invoke('remaining'), 1500)
        self.assertEqual(m.records(5, 1)[14], 0)
        if isinstance(m, RuntimeClock): m.guards()

    def test_pause_resumes_exact_remainder(self):
        m = self.make_machine()
        m.invoke('start')
        m.now += dt.timedelta(minutes=8, seconds=17)
        m.invoke('pause')
        self.assertEqual(m.invoke('remaining'), 1003)
        self.assertEqual(m.records(5, 1)[14], 0)
        m.now += dt.timedelta(hours=3)
        m.invoke('start')
        self.assertEqual(m.deadline(), m.now + dt.timedelta(seconds=1003))

    def test_simultaneous_native_alarm_and_pomodoro_are_both_processed(self):
        m = self.make_machine()
        m.invoke('start')
        m.now = m.deadline()
        m.arm_at(1, m.now, event=0x94, wake=False)
        m.set_time(m.now)
        m.deliver_rtc_interrupt()
        self.assertEqual(m.delivered, [0x94])
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(m.records(5, 1)[14], 1)
        if isinstance(m, RuntimeClock): m.guards()

    def test_early_irq_rearms_without_advancing_or_beeping(self):
        m = self.make_machine()
        m.invoke('start')
        m.now += dt.timedelta(seconds=8)
        m.set_time(m.now)
        m.deliver_rtc_interrupt()
        self.assertEqual(m.phase_round(), (0, 0))
        self.assertEqual(m.records(5, 1)[14], 1)
        self.assertEqual(m.invoke('remaining'), 1492)
        if isinstance(m, RuntimeClock):
            self.assertEqual(m.beeps, [])
            m.guards()

    def test_native_clock_edits_preserve_remaining_duration_forward_and_backward(self):
        m = self.make_machine()
        if not isinstance(m, RuntimeClock): self.skipTest('requires integrated setters')
        m.invoke('start')
        m.now += dt.timedelta(minutes=7, seconds=19)
        m.set_time(m.now)
        for new_time in (dt.datetime(2028, 2, 29, 23, 59, 50),
                         dt.datetime(2025, 1, 1, 1, 2, 3)):
            m.cpu.mem_write(INPUT, packed_date(new_time))
            m.call(0xcd118, INPUT)
            m.call(0xb7e10, OUTPUT)
            actual = unpacked_date(bytes(m.cpu.mem_read(OUTPUT, 12)))
            self.assertEqual(actual, new_time)
            m.call(m.symbols['pomo_snapshot'], OUTPUT, 1)
            self.assertEqual(m.read(OUTPUT + 24), 1061)
            self.assertEqual(m.deadline(), new_time + dt.timedelta(seconds=1061))
            self.assertEqual(m.phase_round(), (0, 0))
            self.assertEqual(m.beeps, [])
            m.guards()

    def test_native_timezone_change_preserves_running_and_paused_countdown(self):
        m = self.make_machine()
        if not isinstance(m, RuntimeClock): self.skipTest('requires integrated setters')
        m.invoke('start')
        m.now += dt.timedelta(minutes=9)
        m.set_time(m.now)
        m.cpu.mem_write(EPOCH + 11, b'\x03')
        m.call(0xe21b8, 16, 1)
        m.call(0xb7e10, OUTPUT)
        actual = unpacked_date(bytes(m.cpu.mem_read(OUTPUT, 12)))
        self.assertEqual(actual, m.now + dt.timedelta(hours=4))
        m.call(m.symbols['pomo_snapshot'], OUTPUT, 1)
        self.assertEqual(m.read(OUTPUT + 24), 960)
        self.assertEqual(m.deadline(), actual + dt.timedelta(seconds=960))
        m.call(m.symbols['pomo_control'], 0)
        m.call(0xe21b8, 0, 0)
        m.call(m.symbols['pomo_snapshot'], OUTPUT, 1)
        self.assertEqual(m.read(OUTPUT + 24), 960)
        self.assertEqual(m.read(OUTPUT + 20), 0)
        self.assertEqual(m.records(5, 1)[14], 0)
        m.guards()

    def test_delayed_background_delivery_catches_up_without_a_beep_burst(self):
        m = self.make_machine()
        m.invoke('start')
        m.now = m.deadline() + dt.timedelta(minutes=130 * 1000, seconds=7)
        m.set_time(m.now)
        m.deliver_rtc_interrupt()
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(m.invoke('remaining'), 293)
        if isinstance(m, RuntimeClock):
            self.assertEqual(len(m.beeps), 1)
            self.assertEqual(m.read(m.state + 28), 8001)
            m.guards()

    def test_explicit_pause_at_deadline_wins_over_pending_alert(self):
        m = self.make_machine()
        m.invoke('start')
        m.now = m.deadline()
        m.invoke('pause')
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(m.invoke('remaining'), 300)
        self.assertEqual(m.records(5, 1)[14], 0)
        m.invoke('reset')
        self.assertEqual(m.phase_round(), (0, 0))
        if isinstance(m, RuntimeClock):
            self.assertEqual(m.beeps, [])
            m.guards()

    def test_clock_edit_at_deadline_preserves_one_prompt_background_alert(self):
        for setter in ('clock', 'zone'):
            with self.subTest(setter=setter):
                m = self.make_machine()
                if not isinstance(m, RuntimeClock): self.skipTest('requires integrated setters')
                m.invoke('start')
                m.now = m.deadline()
                m.set_time(m.now)
                if setter == 'clock':
                    new_time = dt.datetime(2028, 2, 29, 12)
                    m.cpu.mem_write(INPUT, packed_date(new_time))
                    m.call(0xcd118, INPUT)
                else:
                    new_time = m.now + dt.timedelta(hours=4)
                    m.cpu.mem_write(EPOCH + 11, b'\x03')
                    m.call(0xe21b8, 16, 1)
                self.assertEqual(m.phase_round(), (1, 0))
                self.assertEqual(m.deadline(), new_time + dt.timedelta(minutes=5))
                self.assertEqual(unpacked_date(m.records(5, 1)[:12]),
                                 new_time + dt.timedelta(seconds=1))
                self.assertEqual(m.beeps, [], 'clock editor must not beep on UI thread')
                m.now = new_time + dt.timedelta(seconds=1)
                m.set_time(m.now)
                m.deliver_rtc_interrupt()
                self.assertEqual(len(m.beeps), 1)
                self.assertEqual(m.phase_round(), (1, 0))
                self.assertEqual(m.invoke('remaining'), 299)
                self.assertEqual(unpacked_date(m.records(5, 1)[:12]), m.deadline())
                m.guards()

    def test_pause_or_reset_cancels_alert_deferred_by_clock_edit(self):
        for action in ('pause', 'reset'):
            with self.subTest(action=action):
                m = self.make_machine()
                if not isinstance(m, RuntimeClock): self.skipTest('requires integrated setters')
                m.invoke('start')
                m.now = m.deadline()
                m.set_time(m.now)
                m.cpu.mem_write(INPUT, packed_date(m.now))
                m.call(0xcd118, INPUT)
                m.invoke(action)
                m.now += dt.timedelta(seconds=1)
                m.set_time(m.now)
                m.call(m.symbols['pomo_background'])
                self.assertEqual(m.beeps, [])
                self.assertEqual(m.read(m.state + 20), 0)
                self.assertEqual(m.records(5, 1)[14], 0)
                self.assertEqual(m.invoke('remaining'), 1500 if action == 'reset' else 300)
                m.guards()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prototype', action='store_true',
                        help='negative control: original prototype has no background handoff')
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--candidate', type=Path)
    args = parser.parse_args()
    if args.prototype:
        BackgroundRuntime.make_machine = staticmethod(lambda: Interval(
            load_image(BASELINE), ROOT / 'build/research/pomodoro-spike'))
    elif args.artifacts or args.candidate:
        artifacts = args.artifacts or args.candidate.with_suffix('.artifacts')
        image = load_image(args.candidate) if args.candidate else (artifacts / 'candidate.osos').read_bytes()
        symbols = json.loads((artifacts / 'pomodoro-symbols.json').read_text())
        BackgroundRuntime.make_machine = staticmethod(lambda: RuntimeClock(image, symbols))
    else:
        parser.error('supply candidate artifacts or --prototype negative control')
    unittest.main(argv=[__file__], verbosity=2)
