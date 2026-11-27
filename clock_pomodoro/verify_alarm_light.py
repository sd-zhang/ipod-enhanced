"""Exercise installed alarm illumination, stock backlight policy and RTC paths.

The packaged alarm helper, platform vtable/cache, backlight state machine,
timer list/deadline operations, RTC branches and Pomodoro runtime execute ARM.
Original alarm wake is supplied as an already-awake LCD boundary; these tests
therefore do not claim that disabling added illumination prevents stock wake
from lighting a sleeping LCD. Hardware light output, timer locks/interrupts,
RTC IRQ generation, elapsed timer delivery and kernel scheduling are supplied.
Physical Hold, power transitions and arbitrary thread interleavings are not
emulated. Run repository Python outside the macOS sandbox for Unicorn JIT.
"""
import argparse
import datetime as dt
import io
import json
import struct
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile
from verify_runtime import RuntimeClock, LOCK
from native_fixture import NativeMachine, load_image
from unicorn import arm_const as arm


APP, UI, DEVICE, PREFS, QUEUE = (0x10040000, 0x10042000, 0x10045000,
                                 0x10046000, 0x10048000)
TIMER = APP + 0x1c
STATE = APP + 0x11c
MASK = PREFS + 0xb34
DISABLED = 0x01000000
TIMER_HEAD, LIGHT_CACHE = 0x103cf108, 0x103cdcf8
STOP, EXPIRED = 0x73746f70, 0x65787069
POLICIES = ((0, 0), (1, 2), (1, 5), (1, 10), (1, 20), (2, 65535))


def prepare_light(m, *, policy=1, setting=5, on=0, inhibited=0,
                  enabled=True, special=0):
    """Bind an existing UI-owned timer and actual stock platform vtable."""
    # The broad Pomodoro runtime fixture supplies display output. This suite
    # deliberately executes the native service through its hardware boundary.
    m.hooks.pop(0x3408c, None)
    m.write(APP + 0x18, UI)
    m.write(APP + 0x124, PREFS)
    m.write(MASK, 0xa5 | (0 if enabled else DISABLED))
    m.write(UI + 0x1820, DEVICE)
    m.write(UI + 0x1864, setting)
    m.cpu.mem_write(UI + 0x189f, bytes([inhibited]))
    m.write(DEVICE, 0x2354c)
    m.cpu.mem_write(STATE, bytes([policy, 1, 1, on, 1, special]))
    m.cpu.mem_write(LIGHT_CACHE, bytes([4 if on else 1]))
    m.write(TIMER + 0x18, STOP, STOP, APP, QUEUE)
    m.write(TIMER_HEAD, 0)
    m.ticks = 123456
    m.light_calls, m.wakes = [], []
    m.hook(0x70290, lambda: APP)
    # This preserves the original wake call without pretending to exercise
    # physical wake or the LCD-asleep branch of native 99afc.
    m.hook(0x99afc, lambda: m.wakes.append(m.reg(0)) or 0)
    m.hook(0x154c18, lambda: m.light_calls.append(m.reg(0)) or 0)
    for pc in (0x9f5d8, 0x9fce4, 0x9fcdc, 0x87b88, 0x87d60, 0xb1774):
        m.hook(pc, lambda: 0)
    m.hook(0x87cec, lambda: m.ticks)
    return m


class AlarmClock(RuntimeClock):
    def deliver_alarm(self, *, alternate=False, pending=True):
        """Run either original RTC branch, supplying only IRQ/task boundaries.

        Hardware variant selection applies at the task's initial test only;
        the calendar fixture continues using its supported PP5020 calendar.
        """
        class WaitingForInterrupt(Exception):
            pass

        waits = []
        self.delivered, self.wakes, self.wake_locks = [], [], []

        def wait():
            assert self.reg(0) == 0x88
            if waits:
                raise WaitingForInterrupt
            waits.append(True)
            return 0

        def wake():
            self.wakes.append(self.reg(0))
            self.wake_locks.append(self.read(LOCK))
            assert self.read(LOCK) != 0, 'native alarm wake must retain RTC lock'
            return 0

        self.hook(0x96b18, wait)
        self.hook(0x9698c, lambda: self.delivered.append(self.reg(0)) or 0)
        self.hook(0x99afc, wake)
        self.hook(0x87188, lambda: int(alternate and
                  self.cpu.reg_read(arm.UC_ARM_REG_LR) == 0x1599cc))
        self.write(0x103c98d4, 2 << 24 if pending else 0)
        try:
            self.call(0x1599ac)
        except WaitingForInterrupt:
            self.guards()
            return
        raise AssertionError('RTC task did not return to its interrupt wait')


class AlarmLight(unittest.TestCase):
    def light_machine(self, **kwargs):
        return prepare_light(NativeMachine(self.image), **kwargs)

    def clock_machine(self, **kwargs):
        return prepare_light(AlarmClock(self.image, self.symbols), **kwargs)

    def cue(self, m):
        return m.call(self.entry, 1)

    def test_installed_helper_matches_elf_and_both_conditional_call_sites(self):
        elf = ELFFile(io.BytesIO(self.elf_bytes))
        for section in elf.iter_sections():
            if section['sh_flags'] & 2 and section['sh_size']:
                self.assertNotEqual(section['sh_type'], 'SHT_NOBITS')
                at, size = section['sh_addr'], section['sh_size']
                self.assertEqual(self.image[at:at + size], section.data())
        for site in (0x159a0c, 0x159a84):
            instruction = struct.unpack_from('<I', self.image, site)[0]
            self.assertEqual(instruction >> 24, 0x1b, 'must retain native BLNE')
            displacement = instruction & 0xffffff
            if displacement & 0x800000:
                displacement -= 0x1000000
            self.assertEqual(site + 8 + displacement * 4, self.entry)

    def test_native_policy_matrix_refreshes_one_ui_timer_and_expires(self):
        for policy, setting in POLICIES:
            for on in (0, 1):
                for inhibited in (0, 1):
                    with self.subTest(policy=policy, setting=setting,
                                      on=on, inhibited=inhibited):
                        m = self.light_machine(policy=policy, setting=setting,
                                               on=on, inhibited=inhibited)
                        expected_on = on if inhibited else 1
                        for advance in (0, 350000, 700000):
                            m.ticks = 123456 + advance
                            self.cue(m)
                            self.assertEqual(list(m.cpu.mem_read(STATE, 6)),
                                             [policy, 1, 1, expected_on, 1, 0])
                            self.assertEqual(m.read(MASK), 0xa5)
                            self.assertEqual(m.read(UI + 0x1864), setting)
                            self.assertEqual(m.read(TIMER + 0x20), APP)
                            self.assertEqual(m.read(TIMER + 0x24), QUEUE)
                            if policy == 2:
                                self.assertEqual(m.read(TIMER_HEAD), 0)
                            else:
                                interval = 20000 if policy == 0 else setting * 1000
                                self.assertEqual(m.read(TIMER_HEAD), TIMER)
                                self.assertEqual(m.read(TIMER), 0, 'no duplicate timer')
                                self.assertEqual(m.read(TIMER + 4), interval)
                                self.assertEqual(m.read(TIMER + 8),
                                                 m.ticks + interval * 1000)
                        self.assertEqual(m.wakes, [1, 1, 1])
                        self.assertEqual(m.light_calls, [] if inhibited else [1, 1, 1])
                        # Supply removal/delivery of the due native UI timer.
                        m.write(TIMER_HEAD, 0)
                        m.write(TIMER, 0)
                        m.write(TIMER + 0x18, STOP, EXPIRED)
                        m.light_calls.clear()
                        m.call(0x7be2c, APP)
                        turns_off = bool(expected_on and policy != 2)
                        self.assertEqual(m.light_calls, [0] if turns_off else [])
                        self.assertEqual(m.call(0x698ec),
                                         0 if turns_off else expected_on)

    def test_disabled_preserves_state_and_does_not_refresh_existing_timer(self):
        for policy, setting in POLICIES:
            for on in (0, 1):
                with self.subTest(policy=policy, on=on):
                    m = self.light_machine(policy=policy, setting=setting,
                                           on=on, enabled=False)
                    before = bytes(m.cpu.mem_read(APP, 0x128))
                    self.cue(m)
                    self.assertEqual(m.wakes, [1])
                    self.assertEqual(m.light_calls, [])
                    self.assertEqual(bytes(m.cpu.mem_read(APP, 0x128)), before)
                    self.assertEqual(m.read(MASK), 0xa5 | DISABLED)
                    self.assertEqual(m.read(TIMER_HEAD), 0)
        m = self.light_machine()
        self.cue(m)
        m.write(MASK, m.read(MASK) | DISABLED)
        before = bytes(m.cpu.mem_read(TIMER, 0x28))
        m.ticks += 2000000
        m.light_calls.clear()
        self.cue(m)
        self.assertEqual(m.light_calls, [])
        self.assertEqual(bytes(m.cpu.mem_read(TIMER, 0x28)), before)
        self.assertEqual(m.read(TIMER_HEAD), TIMER)

    def test_native_special_timeout_is_consumed_without_changing_preferences(self):
        for policy, setting in ((0, 0), (1, 5), (2, 65535)):
            with self.subTest(policy=policy):
                m = self.light_machine(policy=policy, setting=setting, special=1)
                self.cue(m)
                self.assertEqual(m.read(TIMER + 4), 30000)
                self.assertEqual(m.read(TIMER + 8), m.ticks + 30000000)
                self.assertEqual(m.cpu.mem_read(STATE + 5, 1), b'\0')
                self.assertEqual(m.read(UI + 0x1864), setting)
                self.assertEqual(m.read(MASK), 0xa5)

    def test_both_rtc_paths_obey_wake_flag_and_alarm_light_setting(self):
        for alternate in (False, True):
            for slot, wake in ((1, False), (2, True), (3, True), (4, True), (5, True)):
                for enabled in (False, True):
                    with self.subTest(alternate=alternate, slot=slot, enabled=enabled):
                        m = self.clock_machine(enabled=enabled)
                        event = 0x94 if slot == 1 else 0x87 + slot
                        self.assertEqual(m.arm_at(slot, m.now, event=event, wake=wake), 0)
                        m.deliver_alarm(alternate=alternate)
                        self.assertEqual(m.wakes, [1] if wake else [])
                        self.assertEqual(m.light_calls, [1] if wake and enabled else [])
                        self.assertEqual(m.delivered, [event])
                        self.assertEqual(m.records(slot, 1)[14], 0)
                        self.assertEqual(m.beeps, [])

    def test_idle_and_daily_housekeeping_do_not_illuminate(self):
        m = self.clock_machine()
        m.deliver_alarm(pending=False)
        self.assertEqual((m.wakes, m.light_calls, m.beeps), ([], [], []))
        for alternate in (False, True):
            with self.subTest(alternate=alternate):
                m = self.clock_machine()
                self.assertEqual(m.arm_at(0, m.now, wake=False), 0)
                m.deliver_alarm(alternate=alternate)
                self.assertEqual((m.wakes, m.light_calls, m.beeps), ([], [], []))

    def test_simultaneous_alarm_pomodoro_and_sleep_preserve_all_delivery(self):
        m = self.clock_machine()
        m.invoke('start')
        m.now = m.deadline()
        m.set_time(m.now)
        m.arm_at(2, m.now, event=0x89, wake=True)
        m.arm_at(1, m.now, event=0x94, wake=False)
        m.deliver_alarm()
        self.assertEqual(m.wakes, [1, 1])
        self.assertEqual(m.light_calls, [1, 1])
        self.assertCountEqual(m.delivered, [0x89, 0x94])
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(len(m.beeps), 1)
        self.assertEqual(m.records(5, 1)[14], 1)
        self.assertEqual(m.read(TIMER_HEAD), TIMER)
        self.assertEqual(m.read(TIMER), 0)

    def test_early_repeated_pomodoro_wake_cues_without_phase_beep(self):
        m = self.clock_machine()
        m.invoke('start')
        m.now += dt.timedelta(seconds=8)
        m.set_time(m.now)
        for count in (1, 2):
            m.deliver_alarm()
            self.assertEqual(m.wakes, [1])
            self.assertEqual(m.light_calls, [1] * count)
            self.assertEqual(m.beeps, [])
            self.assertEqual(m.phase_round(), (0, 0))
            self.assertEqual(m.invoke('remaining'), 1492)
            self.assertEqual(m.records(5, 1)[14], 1)

    def test_disabling_added_light_does_not_suppress_pomodoro_alert(self):
        m = self.clock_machine(enabled=False)
        m.invoke('start')
        m.now = m.deadline()
        m.set_time(m.now)
        m.deliver_alarm()
        self.assertEqual(m.wakes, [1])
        self.assertEqual(m.light_calls, [])
        self.assertEqual(m.phase_round(), (1, 0))
        self.assertEqual(len(m.beeps), 1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    artifacts = args.candidate.with_suffix('.artifacts')
    AlarmLight.image = load_image(args.candidate)
    AlarmLight.elf_bytes = (artifacts / 'alarm_light.elf').read_bytes()
    table = ELFFile(io.BytesIO(AlarmLight.elf_bytes)).get_section_by_name('.symtab')
    AlarmLight.entry = next(s['st_value'] for s in table.iter_symbols()
                            if s.name == 'alarm_wake')
    AlarmLight.symbols = json.loads((artifacts / 'pomodoro-symbols.json').read_text())
    unittest.main(argv=[__file__], verbosity=2)
