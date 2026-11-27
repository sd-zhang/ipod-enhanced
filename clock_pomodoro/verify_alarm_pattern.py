"""Execute native alarm requests, piezo queue/timers and backlight policy.

Run repository Python outside the macOS sandbox for Unicorn JIT. Scheduling,
interrupt delivery and elapsed microseconds are host boundaries. These tests
cannot establish audible output or physical buzzer/backlight synchrony.
"""
import argparse
import datetime as dt
import io
import json
import struct
import unittest
from pathlib import Path

from verify_runtime import RuntimeClock, LOCK
from native_fixture import load_image
from verify_alarm_light import (prepare_light, APP, UI, STATE, MASK, DISABLED,
                                TIMER, TIMER_HEAD, STOP, EXPIRED)
from verify_native import STACK, REG
from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn import arm_const as arm


PATTERN = [(180, 200000), (144, 400000)] * 4
TAG = 0x80000000
PIEZO = 0x10419a2c
PERIODS, DURATIONS = PIEZO + 16, PIEZO + 80
MICROSECONDS, TIMER_MMIO, PITCH_MMIO = 0x60005010, 0x60005008, 0x7000a000
REQUEST = 0x10060000


class WaitingForEvent(Exception):
    pass


class PatternClock(RuntimeClock):
    expect_rearmed = False

    def kernel(self):
        request = self.reg(0)
        assert self.read(request) == 4
        assert self.read(LOCK) == self.read(LOCK - 32) == 0
        if self.expect_rearmed:
            assert self.records(5, 1)[14] == 1
        message = self.read(request + 24)
        assert self.cpu.mem_read(message + 16, 1) == b'\0'
        self.beeps.append((self.read(message + 20), self.read(message + 24)))
        self.write(message + 28, 0)
        return 0

    def clock_alarm(self, event=138):
        """Run native event routing and alarm worker until its next event wait."""
        events = [event]
        def wait():
            if not events:
                raise WaitingForEvent
            return events.pop()
        self.hook(0x96b4c, wait)
        self.hook(0x9efe8, lambda: 0)  # audio route, outside the piezo service
        self.hook(0x964d0, lambda: 0)  # native one-second scheduling delay
        self.cpu.mem_write(struct.unpack_from('<I', self.image, 0x158d34)[0], b'\0\0')
        try:
            self.call(0x158bb0)
        except WaitingForEvent:
            return
        raise AssertionError('alarm worker did not wait for another event')


class PiezoClock(PatternClock):
    def __init__(self, image, symbols, **light):
        super().__init__(image, symbols)
        prepare_light(self, **light)
        self.cpu.mem_map(0x70000000, 0x1000)
        self.cpu.mem_map(0x7000a000, 0x1000)
        self.cpu.mem_map(0x60006000, 0x1000)
        self.events, self.pitch_writes, self.timer_writes, self.tone_starts = [], [], [], []
        self.hardware_due = None
        self.hook(0x154c18, self.light_output)
        self.cpu.hook_add(UC_HOOK_MEM_WRITE, self.mmio_write)
        self.set_ticks(self.ticks)
        self.hook(0x963a0, lambda: 0)  # kernel queue/event binding
        self.hook(0x96b18, self.wait)
        # Execute the native task's own initialization before supplying events.
        try:
            self.call(0x1595bc)
        except WaitingForEvent:
            pass
        else:
            raise AssertionError('piezo task did not wait for a kernel event')
        self.pitch_writes.clear()
        self.timer_writes.clear()
        self.events.clear()

    @staticmethod
    def wait():
        raise WaitingForEvent

    def light_output(self):
        value = self.reg(0)
        self.light_calls.append(value)
        self.events.append(('light', self.ticks, value))
        return 0

    def mmio_write(self, cpu, access, address, size, value, context):
        if address == PITCH_MMIO:
            self.pitch_writes.append((self.ticks, value))
            self.events.append(('pitch', self.ticks, value))
            if value:
                self.tone_starts.append((self.ticks, value & 0x7fffff, None))
        elif address == TIMER_MMIO:
            self.timer_writes.append((self.ticks, value))
            if value & TAG and self.tone_starts and self.tone_starts[-1][2] is None:
                ticks, pitch, _ = self.tone_starts[-1]
                # Observe the completed native sound deadline at hardware
                # start; light work may have advanced the clock before this.
                duration = (self.read(PIEZO + 12) - ticks) & 0xffffffff
                self.tone_starts[-1] = (ticks, pitch, duration)
            self.hardware_due = ((self.ticks + (value & 0x1fffffff)) & 0xffffffff
                                 if value & TAG else None)

    def set_ticks(self, ticks):
        self.ticks = ticks & 0xffffffff
        self.write(MICROSECONDS, self.ticks)

    def receive(self, period=0, duration=0, kind=0):
        self.cpu.mem_write(REQUEST, bytes(32))
        self.cpu.mem_write(REQUEST + 16, bytes([kind]))
        self.write(REQUEST + 20, period, duration, 0xdeadbeef)
        self.worker(REQUEST)
        return self.read(REQUEST + 28)

    def worker(self, message=0):
        """Supply one message; native ring acceptance, reply and advance run ARM.

        Registers represent resuming the initialized piezo task after its kernel
        wait. The scheduler and task context switch are supplied, not emulated.
        """
        replies = []
        def dequeue():
            assert (self.reg(0), self.reg(1)) == (21, 0)
            return message
        self.hook(0x967f0, dequeue)
        self.hook(0x96350, lambda: replies.append(self.reg(0)) or 0)
        self.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        for n, value in ((5, PIEZO), (6, 1), (7, 0), (8, PERIODS)):
            self.cpu.reg_write(REG[n], value)
        self.cpu.emu_start(0x159634, 0x15962c, count=20000)
        assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == 0x15962c
        assert self.cpu.reg_read(arm.UC_ARM_REG_SP) == STACK
        assert replies == ([message] if message else [])

    def advance(self, ticks=None):
        self.set_ticks(self.hardware_due if ticks is None else ticks)
        # Run the original timer ISR: ACK read plus its actual event 149.
        assert self.call(0x13ada8) == 149
        self.worker()

    def drain(self):
        for _ in range(64):
            if not self.read(PIEZO + 8):
                return
            self.advance()
        raise AssertionError('native piezo queue did not become idle')

    def tones(self):
        return [(pitch, duration) for _, pitch, duration in self.tone_starts]


class AlarmPattern(unittest.TestCase):
    def machine(self, **light):
        return PiezoClock(self.image, self.symbols, **light)

    def requests(self, m, source='clock'):
        m.beeps.clear()
        if source == 'clock':
            m.clock_alarm()
        else:
            m.expect_rearmed = True
            m.invoke('start')
            m.now = m.deadline()
            m.set_time(m.now)
            m.deliver_rtc_interrupt()
        return list(m.beeps)

    def test_installed_overlay_matches_elf(self):
        self.assertIsNotNone(self.elf_bytes, 'candidate has no alarm_pattern.elf')
        elf = ELFFile(io.BytesIO(self.elf_bytes))
        for section in elf.iter_sections():
            if section['sh_flags'] & 2 and section['sh_size']:
                if section['sh_type'] == 'SHT_NOBITS':
                    continue
                at, size = section['sh_addr'], section['sh_size']
                self.assertEqual(self.image[at:at + size], section.data())

    def test_pomodoro_queues_stock_clock_pattern_after_unlock_and_rearm(self):
        m = PatternClock(self.image, self.symbols)
        m.expect_rearmed = True
        m.invoke('start')
        m.now = m.deadline()
        m.set_time(m.now)
        m.deliver_rtc_interrupt()
        self.assertEqual([(period, duration & ~TAG) for period, duration in m.beeps],
                         PATTERN)
        self.assertEqual(m.phase_round(), (1, 0))
        m.deliver_rtc_interrupt()
        self.assertEqual(len(m.beeps), 8, 'one sequence per phase alert')
        m.guards()

    def test_clock_kind_one_maps_to_native_event_and_original_pattern(self):
        m = PatternClock(self.image, self.symbols)
        # Native kind-1 wrapper converts this compact local date to RTC slot 3.
        m.cpu.mem_write(REQUEST, struct.pack('<H6B', 2026, 11, 25, 12, 30, 0, 0))
        self.assertEqual(m.call(0xc3ecc, 1, REQUEST), 0)
        self.assertEqual(struct.unpack_from('<I', m.records(3, 1), 16)[0], 138)
        m.clock_alarm()
        self.assertEqual([(period, duration & ~TAG) for period, duration in m.beeps],
                         PATTERN)
        self.assertTrue(all(duration & TAG for _, duration in m.beeps),
                        'stock Clock requests must carry alarm-only tagging')

    def test_native_mmio_sound_matches_original_clock_for_both_sources(self):
        for source in ('clock', 'pomodoro'):
            for enabled, inhibited in ((True, 0), (False, 0), (True, 1)):
                with self.subTest(source=source, enabled=enabled, inhibited=inhibited):
                    m = self.machine(enabled=enabled, inhibited=inhibited)
                    requests = self.requests(m, source)
                    # RTC's pre-existing wake cue is separate from tone flashes.
                    m.light_calls.clear()
                    m.events.clear()
                    start = m.ticks
                    for request in requests:
                        self.assertEqual(m.receive(*request), 0)
                    self.assertEqual(m.read(PIEZO + 12), start + 200000,
                                     'tag must be stripped before deadline arithmetic')
                    m.drain()
                    self.assertEqual(m.tones(), PATTERN)
                    expected_starts, elapsed = [], 0
                    for _, duration in PATTERN:
                        expected_starts.append((start + elapsed) & 0xffffffff)
                        elapsed += duration
                    self.assertEqual([ticks for ticks, value in m.pitch_writes if value],
                                     expected_starts)
                    self.assertEqual(m.ticks, (start + 2400000) & 0xffffffff)
                    self.assertEqual(m.read(PITCH_MMIO), 0)
                    self.assertEqual(m.read(TIMER_MMIO) & TAG, 0)
                    self.assertEqual(m.read(PIEZO), m.read(PIEZO + 4))
                    if not enabled or inhibited:
                        self.assertEqual(m.light_calls, [])
                    m.guards()

    def test_delayed_pomodoro_delivery_emits_one_complete_pattern(self):
        m = PatternClock(self.image, self.symbols)
        m.expect_rearmed = True
        m.invoke('start')
        m.now = m.deadline() + dt.timedelta(minutes=130 * 1000, seconds=7)
        m.set_time(m.now)
        m.deliver_rtc_interrupt()
        self.assertEqual([(p, d & ~TAG) for p, d in m.beeps], PATTERN)
        self.assertEqual(m.read(m.state + 28), 8001)
        self.assertEqual(m.invoke('remaining'), 293)
        m.guards()

    def test_simultaneous_clock_pomodoro_and_sleep_keep_both_patterns_and_events(self):
        m = self.machine()
        m.expect_rearmed = True
        m.invoke('start')
        m.now = m.deadline()
        m.arm_at(3, m.now, event=138, wake=True)
        m.arm_at(1, m.now, event=148, wake=False)
        m.set_time(m.now)
        m.deliver_rtc_interrupt()
        self.assertCountEqual(m.delivered, [138, 148])
        self.assertEqual([(p, d & ~TAG) for p, d in m.beeps], PATTERN)
        self.assertEqual(m.phase_round(), (1, 0))
        m.clock_alarm(138)
        self.assertEqual([(p, d & ~TAG) for p, d in m.beeps], PATTERN * 2)
        m.light_calls.clear()
        for request in m.beeps:
            self.assertEqual(m.receive(*request), 0)
        m.drain()
        self.assertEqual(m.tones(), PATTERN * 2)
        self.assertEqual(m.light_calls, [1, 0] * 8 + [1])
        self.assertEqual(m.records(5, 1)[14], 1)
        m.guards()

    def test_other_stock_alarm_beeps_share_tagging_but_audio_only_alarm_does_not(self):
        for event, expected in ((137, [(180, 200000), (144, 200000)]), (139, [])):
            with self.subTest(event=event):
                m = self.machine()
                m.clock_alarm(event)
                self.assertEqual([(p, d & ~TAG) for p, d in m.beeps], expected)
                self.assertTrue(all(d & TAG for _, d in m.beeps))
                for request in m.beeps:
                    self.assertEqual(m.receive(*request), 0)
                m.drain()
                self.assertEqual(m.tones(), expected)
                self.assertEqual(m.light_calls, [1, 0, 1] if expected else [])
                if expected:
                    lights = [event for event in m.events if event[0] == 'light']
                    start = lights[0][1]
                    self.assertEqual(lights, [('light', start, 1),
                                             ('light', start + 350000, 0),
                                             ('light', start + 400000, 1)])

    def test_no_request_time_flash_and_existing_tone_delays_alarm(self):
        m = self.machine()
        requests = self.requests(m)
        self.assertEqual(m.light_calls, [], 'request generation must not flash')
        self.assertEqual(m.receive(270, 3000000), 0)
        for request in requests:
            self.assertEqual(m.receive(*request), 0)
        self.assertEqual(m.light_calls, [], 'queued alarms must wait for actual playback')
        self.assertEqual(m.tones(), [(270, 3000000)])
        deadline = m.read(PIEZO + 12)
        m.advance(deadline - 1)
        self.assertEqual(m.light_calls, [])
        m.advance(deadline)
        self.assertEqual(m.light_calls, [1])
        self.assertEqual(m.tones(), [(270, 3000000), PATTERN[0]])
        m.drain()
        self.assertEqual(m.tones(), [(270, 3000000)] + PATTERN)

    def test_full_queue_rejects_alarm_without_phantom_light(self):
        m = self.machine()
        requests = self.requests(m)
        for _ in range(16):
            self.assertEqual(m.receive(270, 20000), 0)
        before = bytes(m.cpu.mem_read(PIEZO, 144))
        for request in requests:
            self.assertEqual(m.receive(*request), 1)
        self.assertEqual(bytes(m.cpu.mem_read(PIEZO, 144)), before)
        m.drain()
        self.assertEqual(m.tones(), [(270, 20000)] * 16)
        self.assertEqual(m.light_calls, [])

    def test_partial_queue_keeps_only_native_accepted_alarm_tones(self):
        m = self.machine()
        requests = self.requests(m)
        for _ in range(14):
            self.assertEqual(m.receive(270, 20000), 0)
        self.assertEqual([m.receive(*request) for request in requests], [0, 0] + [1] * 6)
        self.assertEqual(m.light_calls, [])
        m.drain()
        self.assertEqual(m.tones(), [(270, 20000)] * 14 + PATTERN[:2])
        self.assertEqual(m.call(0x698ec), 1, 'partial pattern must restore native light')
        self.assertEqual(m.read(TIMER_HEAD), TIMER)

    def test_native_ring_wrap_and_timer_wrap_preserve_each_tone_duration(self):
        m = self.machine(enabled=False)
        requests = self.requests(m)
        # Move the native read/write indices to slot 13 without editing them.
        for _ in range(13):
            self.assertEqual(m.receive(270, 1000), 0)
            m.drain()
        self.assertEqual((m.read(PIEZO), m.read(PIEZO + 4)), (13, 13))
        m.pitch_writes.clear()
        m.timer_writes.clear()
        m.tone_starts.clear()
        m.set_ticks(0xffff0000)
        for request in requests:
            self.assertEqual(m.receive(*request), 0)
        self.assertEqual(m.read(PIEZO), 5)
        self.assertEqual(m.read(PIEZO + 12), (0xffff0000 + 200000) & 0xffffffff)
        m.advance((m.read(PIEZO + 12) - 1) & 0xffffffff)
        self.assertEqual(m.tones(), PATTERN[:1])
        m.drain()
        self.assertEqual(m.tones(), PATTERN)
        self.assertEqual(m.light_calls, [])

    def test_unrelated_native_tones_never_acquire_flash_state(self):
        m = self.machine()
        def unexpected_application_lookup():
            raise AssertionError('unrelated piezo tone reached application/light service')
        m.hook(0x70290, unexpected_application_lookup)
        for period, duration in ((180, 200000), (144, 400000), (270, 150000)):
            self.assertEqual(m.receive(period, duration), 0)
        m.drain()
        self.assertEqual(m.tones(), [(180, 200000), (144, 400000), (270, 150000)])
        self.assertEqual(m.light_calls, [])
        self.assertEqual(m.read(TIMER_HEAD), 0)

    def test_native_cancel_flushes_pending_alarm_and_restores_light(self):
        m = self.machine()
        requests = self.requests(m)
        for request in requests:
            self.assertEqual(m.receive(*request), 0)
        self.assertEqual(m.tones(), PATTERN[:1])
        m.receive(kind=1)
        self.assertEqual(m.read(PIEZO + 8), 0)
        self.assertEqual(m.read(PIEZO), m.read(PIEZO + 4))
        self.assertEqual(m.call(0x698ec), 1)
        calls = list(m.light_calls)
        m.advance(m.ticks + 4000000)
        self.assertEqual(m.tones(), PATTERN[:1])
        self.assertEqual(m.light_calls, calls)
        self.assertEqual(m.receive(270, 100000), 0)
        m.drain()
        self.assertEqual(m.light_calls, calls, 'later unrelated tone must not inherit flash')

    def test_unknown_native_queue_message_does_not_cancel_alarm(self):
        m = self.machine(enabled=False)
        requests = self.requests(m)
        for request in requests:
            self.assertEqual(m.receive(*request), 0)
        before = bytes(m.cpu.mem_read(PIEZO, 144))
        m.receive(kind=2)
        self.assertEqual(bytes(m.cpu.mem_read(PIEZO, 144)), before)
        m.drain()
        self.assertEqual(m.tones(), PATTERN)

    def test_disabled_and_inhibited_native_policy_are_untouched(self):
        for enabled, inhibited in ((False, 0), (True, 1)):
            for on in (0, 1):
                with self.subTest(enabled=enabled, inhibited=inhibited, on=on):
                    m = self.machine(enabled=enabled, inhibited=inhibited, on=on)
                    requests = self.requests(m)
                    state = bytes(m.cpu.mem_read(STATE, 6))
                    timer = bytes(m.cpu.mem_read(TIMER, 0x28))
                    preferences = m.read(MASK)
                    for request in requests:
                        self.assertEqual(m.receive(*request), 0)
                    m.drain()
                    self.assertEqual(m.light_calls, [])
                    self.assertEqual(bytes(m.cpu.mem_read(STATE, 6)), state)
                    self.assertEqual(bytes(m.cpu.mem_read(TIMER, 0x28)), timer)
                    self.assertEqual(m.read(TIMER_HEAD), 0)
                    self.assertEqual(m.read(MASK), preferences)

    def test_each_beep_pair_has_350ms_light_and_250ms_dark(self):
        for source in ('clock', 'pomodoro'):
            with self.subTest(source=source):
                m = self.machine()
                requests = self.requests(m, source)
                m.light_calls.clear()
                m.events.clear()
                start = m.ticks
                for request in requests:
                    self.assertEqual(m.receive(*request), 0)
                m.drain()
                expected = []
                for offset in (0, 600000, 1200000, 1800000):
                    expected += [('light', start + offset, 1),
                                 ('light', start + offset + 350000, 0)]
                expected.append(('light', start + 2400000, 1))
                self.assertEqual([event for event in m.events if event[0] == 'light'], expected)
                self.assertEqual([value & 0x1fffffff for _, value in m.timer_writes if value & TAG],
                                 [200000, 150000, 250000] * 4)
                self.assertEqual(m.tones(), PATTERN)
                self.assertEqual(len([value for _, value in m.pitch_writes if value]), 8,
                                 'light gaps must not retrigger or mute the piezo')

    def test_gap_interrupt_leaves_pitch_active_and_native_deadline_unchanged(self):
        m = self.machine()
        request = self.requests(m)[1]
        self.assertEqual(m.receive(*request), 0)
        deadline, pitch = m.read(PIEZO + 12), m.read(PITCH_MMIO)
        pitch_writes = list(m.pitch_writes)
        reads = []
        m.cpu.hook_add(UC_HOOK_MEM_READ,
                       lambda cpu, access, address, size, value, context: reads.append(address),
                       begin=0x6000500c, end=0x6000500c)
        self.assertEqual(m.hardware_due, (deadline - 250000) & 0xffffffff)
        m.advance()
        self.assertEqual(reads, [0x6000500c])
        self.assertEqual(m.light_calls, [1, 0])
        self.assertEqual(m.read(PIEZO + 12), deadline)
        self.assertEqual(m.read(PITCH_MMIO), pitch)
        self.assertEqual(m.pitch_writes, pitch_writes)
        self.assertEqual(m.hardware_due, deadline)
        self.assertEqual(m.timer_writes[-1][1] & 0x1fffffff, 250000)
        m.drain()
        self.assertEqual(m.tones(), PATTERN[1:2])

    def test_early_notifications_do_not_change_gap_or_native_sound_deadline(self):
        m = self.machine()
        request = self.requests(m)[1]
        m.receive(*request)
        before_timer = list(m.timer_writes)
        deadline, gap = m.read(PIEZO + 12), m.hardware_due
        for offset in (1, 10000, 100000, 149999):
            m.set_ticks(123456 + offset)
            m.worker()
            self.assertEqual(m.timer_writes, before_timer)
            self.assertEqual(m.hardware_due, gap)
            self.assertEqual(m.read(PIEZO + 12), deadline)
            self.assertEqual(m.light_calls, [1])
        m.drain()
        self.assertEqual(m.tones(), PATTERN[1:2])

    def test_late_gap_interrupt_uses_remaining_time_without_extending_tone(self):
        for late in (1, 30000, 249999, 250000, 270000):
            with self.subTest(late=late):
                m = self.machine()
                requests = self.requests(m)
                for request in requests[1:3]:
                    m.receive(*request)
                old_deadline = m.read(PIEZO + 12)
                m.advance(old_deadline - 250000 + late)
                if late < 250000:
                    self.assertEqual(m.read(PIEZO + 12), old_deadline)
                    self.assertEqual(m.timer_writes[-1][1] & 0x1fffffff, 250000 - late)
                    self.assertEqual(m.hardware_due, old_deadline)
                    self.assertEqual(m.light_calls, [1, 0])
                    m.advance()
                    self.assertEqual(m.tone_starts[-1][0], old_deadline)
                else:
                    self.assertEqual(m.tone_starts[-1][0], old_deadline - 250000 + late)
                    self.assertEqual(m.tones(), PATTERN[1:3])
                m.drain()
                self.assertEqual(m.tones(), PATTERN[1:3])

    def test_light_service_latency_keeps_bounded_sound_and_timer_deadlines(self):
        for on_cost, off_cost in ((0, 5000), (4000, 8000), (4000, 250000),
                                  (4000, 260000), (500000, 260000)):
            for start in (123456, 0xffff0000):
                with self.subTest(on_cost=on_cost, off_cost=off_cost, start=start):
                    m = self.machine()
                    request = self.requests(m)[1]
                    def light_with_latency():
                        value = m.reg(0)
                        result = m.light_output()
                        # Output/driver work is a supplied boundary. Advance
                        # its clock to test slow services and missed gaps.
                        m.set_ticks(m.ticks + (on_cost if value else off_cost))
                        return result
                    m.hook(0x154c18, light_with_latency)
                    m.set_ticks(start)
                    m.receive(*request)
                    pitch_start = (start + on_cost) & 0xffffffff
                    sound_deadline = (pitch_start + 400000) & 0xffffffff
                    self.assertEqual(m.tone_starts, [(pitch_start, 144, 400000)])
                    self.assertEqual(m.read(PIEZO + 12), sound_deadline)
                    self.assertEqual(m.hardware_due, (pitch_start + 150000) & 0xffffffff)
                    m.advance()
                    armed = [value & 0x1fffffff for _, value in m.timer_writes if value & TAG]
                    self.assertEqual(armed, [150000] + ([250000 - off_cost] if off_cost < 250000 else []))
                    if off_cost < 250000:
                        self.assertEqual(m.hardware_due, sound_deadline)
                        self.assertEqual(m.read(PIEZO + 12), sound_deadline)
                    m.drain()
                    self.assertEqual(m.tones(), PATTERN[1:2])
                    self.assertEqual(m.read(PIEZO + 8), 0)
                    self.assertEqual(m.read(PITCH_MMIO), 0)
                    self.assertEqual(m.light_calls, [1, 0, 1])
                    self.assertTrue(all(0 < duration <= 400000 for duration in armed))
                    # A service taking longer than the remaining gap delays
                    # execution by its own measured cost; it must never create
                    # an unsigned-underflow timer lasting thousands of seconds.
                    stop = [ticks for ticks, value in m.pitch_writes if not value][-1]
                    self.assertEqual((stop - pitch_start) & 0xffffffff,
                                     400000 + max(0, off_cost - 250000))

    def test_cancel_during_dark_gap_restores_once_and_leaves_no_flash_owner(self):
        m = self.machine()
        for request in self.requests(m):
            m.receive(*request)
        m.advance()
        m.advance()
        self.assertEqual(m.light_calls, [1, 0])
        m.receive(kind=1)
        self.assertEqual(m.call(0x698ec), 1)
        self.assertEqual(m.light_calls, [1, 0, 1])
        m.advance(m.ticks + 4000000)
        m.receive(kind=1)
        self.assertEqual(m.light_calls, [1, 0, 1])
        self.assertEqual(m.tones(), PATTERN[:2])

    def test_short_tagged_durations_do_not_underflow_gap_timer(self):
        for duration in (1, 49999, 50000):
            with self.subTest(duration=duration):
                m = self.machine()
                start = m.ticks
                self.assertEqual(m.receive(180, TAG | duration), 0)
                self.assertEqual(m.read(PIEZO + 12), start + duration)
                self.assertEqual(m.hardware_due, start + duration)
                m.drain()
                self.assertEqual(m.tones(), [(180, duration)])
                self.assertEqual(m.ticks, start + duration)

    def test_second_tone_at_lit_threshold_has_no_underflow_or_state_collision(self):
        for duration in (50000, 50001, 149999, 150000, 150001, 150002, 150003):
            with self.subTest(duration=duration):
                m = self.machine()
                start = m.ticks
                m.receive(144, TAG | duration)
                m.drain()
                self.assertEqual(m.tones(), [(144, duration)])
                self.assertEqual(m.ticks, start + duration)
                timers = [v & 0x1fffffff for _, v in m.timer_writes if v & TAG]
                self.assertEqual(timers, [150000, duration - 150000]
                                 if duration > 150000 else [duration])
                self.assertEqual(m.read(0x14534654), 0)

    def test_unsplit_first_tone_dates_deadline_after_light_service(self):
        m = self.machine()
        start = m.ticks
        def slow_on():
            result = m.light_output()
            m.set_ticks(m.ticks + 250000)
            return result
        m.hook(0x154c18, slow_on)
        m.receive(180, TAG | 200000)
        self.assertEqual(m.tone_starts, [(start + 250000, 180, 200000)])
        self.assertEqual(m.hardware_due, start + 450000)
        self.assertEqual(m.read(PIEZO + 12), start + 450000)
        m.drain()
        self.assertEqual(m.tones(), PATTERN[:1])
        self.assertNotIn(0, m.light_calls)

    def test_second_tone_relights_if_native_ui_switched_light_off(self):
        m = self.machine()
        start = m.ticks
        for period, duration in PATTERN[:2]:
            m.receive(period, duration | TAG)
        m.set_ticks(start + 100000)
        m.call(0xc9734, APP, 0)
        self.assertEqual(m.call(0x698ec), 0)
        m.advance()
        self.assertEqual(m.call(0x698ec), 1)
        m.drain()
        self.assertEqual(m.tones(), PATTERN[:2])
        self.assertEqual([e for e in m.events if e[0] == 'light'], [
            ('light', start, 1), ('light', start + 100000, 0),
            ('light', start + 200000, 1), ('light', start + 350000, 0),
            ('light', start + 600000, 1)])

    def test_queue_truncated_between_pair_tones_finishes_without_extra_flash(self):
        for accepted in (1, 3, 5, 7):
            with self.subTest(accepted=accepted):
                m = self.machine()
                for _ in range(16 - accepted):
                    self.assertEqual(m.receive(270, 1000), 0)
                requests = self.requests(m)
                self.assertEqual([m.receive(*r) for r in requests],
                                 [0] * accepted + [1] * (8 - accepted))
                m.drain()
                self.assertEqual(m.tones(), [(270, 1000)] * (16 - accepted) + PATTERN[:accepted])
                self.assertEqual(m.light_calls.count(0), accepted // 2)
                self.assertEqual(m.call(0x698ec), 1)
                self.assertEqual(m.read(0x14534654), 0)

    def test_enabled_flash_schedule_wraps_the_native_microsecond_counter(self):
        m = self.machine()
        requests = self.requests(m)
        start = 0xffff0000
        m.set_ticks(start)
        for request in requests:
            m.receive(*request)
        m.drain()
        expected = []
        for offset in (0, 600000, 1200000, 1800000):
            expected += [('light', (start + offset) & 0xffffffff, 1),
                         ('light', (start + offset + 350000) & 0xffffffff, 0)]
        expected.append(('light', (start + 2400000) & 0xffffffff, 1))
        self.assertEqual([event for event in m.events if event[0] == 'light'], expected)
        self.assertEqual(m.tones(), PATTERN)

    def test_disabling_or_inhibiting_during_pattern_ends_future_flashes(self):
        for change in ('disable', 'inhibit'):
            for dark in (False, True):
                with self.subTest(change=change, dark=dark):
                    m = self.machine()
                    for request in self.requests(m):
                        m.receive(*request)
                    if dark:
                        m.advance()
                        m.advance()
                    if change == 'disable':
                        m.write(MASK, m.read(MASK) | DISABLED)
                    else:
                        m.cpu.mem_write(UI + 0x189f, b'\1')
                    before = len(m.light_calls)
                    m.drain()
                    self.assertNotIn(0, m.light_calls[before:], 'no later forced dark gaps')
                    self.assertEqual(m.tones(), PATTERN)
                    self.assertEqual(m.read(0x14534654), 0, 'flash ownership must be released')
                    if change == 'disable':
                        self.assertEqual(m.call(0x698ec), 1)
                        self.assertEqual(m.read(MASK), 0xa5 | DISABLED)
                    else:
                        self.assertEqual(len(m.light_calls), before,
                                         'native inhibition must suppress restoration output')

    def test_absent_application_ui_or_preferences_does_not_affect_sound(self):
        for missing in ('application', 'ui', 'preferences'):
            with self.subTest(missing=missing):
                m = self.machine()
                requests = self.requests(m)
                if missing == 'application':
                    m.hook(0x70290, lambda: 0)
                else:
                    m.write(APP + (0x18 if missing == 'ui' else 0x124), 0)
                for request in requests:
                    m.receive(*request)
                m.drain()
                self.assertEqual(m.tones(), PATTERN)
                self.assertEqual(m.light_calls, [])

    def test_finished_pattern_restores_native_policy_timer_and_expiry(self):
        for policy, setting in ((0, 0), (1, 2), (1, 5), (1, 10), (1, 20), (2, 65535)):
            with self.subTest(policy=policy, setting=setting):
                m = self.machine(policy=policy, setting=setting)
                requests = self.requests(m)
                for request in requests:
                    self.assertEqual(m.receive(*request), 0)
                m.drain()
                self.assertEqual(m.call(0x698ec), 1)
                self.assertEqual(m.read(MASK), 0xa5)
                self.assertEqual(m.read(UI + 0x1864), setting)
                if policy == 2:
                    self.assertEqual(m.read(TIMER_HEAD), 0)
                else:
                    interval = 20000 if policy == 0 else setting * 1000
                    self.assertEqual(m.read(TIMER_HEAD), TIMER)
                    self.assertEqual(m.read(TIMER), 0, 'native UI timer must appear once')
                    self.assertEqual(m.read(TIMER + 4), interval)
                    self.assertEqual(m.read(TIMER + 8), (m.ticks + interval * 1000) & 0xffffffff)
                # Deliver the existing UI timer exactly as the light regression
                # suite does; timer interrupts/scheduling remain host boundaries.
                m.write(TIMER_HEAD, 0)
                m.write(TIMER, 0)
                m.write(TIMER + 0x18, STOP, EXPIRED)
                m.light_calls.clear()
                m.call(0x7be2c, APP)
                self.assertEqual(m.light_calls, [] if policy == 2 else [0])
                self.assertEqual(m.call(0x698ec), int(policy == 2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    AlarmPattern.image = load_image(args.candidate)
    AlarmPattern.symbols = json.loads((args.candidate.with_suffix('.artifacts') /
                                     'pomodoro-symbols.json').read_text())
    elf = args.candidate.with_suffix('.artifacts') / 'alarm_pattern.elf'
    AlarmPattern.elf_bytes = elf.read_bytes() if elf.exists() else None
    unittest.main(argv=[__file__], verbosity=2)
