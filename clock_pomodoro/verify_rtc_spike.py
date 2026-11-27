"""Execute original RTC/date/alarm code; kernel and MMIO are host boundaries.

Run with .venv/bin/python OUTSIDE the macOS sandbox (Unicorn JIT).
No firmware archive, device or system Python is modified.
"""
import argparse
import datetime as dt
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'navigation_qol'))
sys.path.insert(0, str(ROOT / 'menu_scroll'))
from native_fixture import NativeMachine, load_image
from rtc_spike import TABLE, experimental_image
from unicorn import arm_const as arm

INPUT, OUTPUT = 0x10070000, 0x10070100
EPOCH, SELECTED = 0x103c98d8, 0x103902e0
MMIO = 0x60005000


def packed_date(value):
    # Native normalized date: year, month/day, zero-based day-of-year,
    # weekday/time-zone bytes, hour/minute/second, flags.
    result = bytearray(12)
    struct.pack_into('<H', result, 0, value.year)
    struct.pack_into('<H', result, 4, value.timetuple().tm_yday - 1)
    result[8:11] = bytes((value.hour, value.minute, value.second))
    return bytes(result)


def unpacked_date(data):
    year, day = struct.unpack_from('<H', data)[0], struct.unpack_from('<H', data, 4)[0]
    return dt.datetime(year, 1, 1) + dt.timedelta(days=day, hours=data[8],
                                               minutes=data[9], seconds=data[10])


class Clock(NativeMachine):
    def __init__(self, image, *, sixth_slot=False, integrated_table=None):
        super().__init__(image if integrated_table is not None else
                         experimental_image(image, sixth_slot=sixth_slot))
        self.table = TABLE if integrated_table is None else integrated_table
        self.cpu.mem_map(0x60004000, 0x2000)
        if integrated_table is None:
            self.cpu.mem_write(TABLE - 16, b'\xa5' * (16 + 120 + 16))
        else:
            self.cpu.mem_map((integrated_table - 256) & ~0xfff, 0x3000)
        self.cpu.mem_write(self.table, bytes(120))
        self.write(SELECTED, -1)
        self.locks, self.commits = [], []
        self.hook(0x87188, lambda: 0)  # use real PP5020 software-calendar path
        self.hook(0x96710, lambda: self.locks.append(('lock', self.reg(0))))
        self.hook(0x96a70, lambda: self.locks.append(('unlock', self.reg(0))))
        self.hook(0x9a510, lambda: self.commits.append(self.read(MMIO + 0x14)))
        # Boot ROM memory copy is not mapped by this fixture.
        self.hook(0xec, lambda: self.cpu.mem_write(
            self.reg(0), bytes(self.cpu.mem_read(self.reg(1), self.reg(2)))))
        self.set_time(dt.datetime(2026, 11, 24, 12))

    def divide(self):
        # Unlike the UI fixture, calendar hardware packing uses negative
        # offsets as well. Supply ARM signed divmod, truncating toward zero.
        def signed(value):
            return value - 0x100000000 if value & 0x80000000 else value
        divisor, numerator = signed(self.reg(0)), signed(self.reg(1))
        assert divisor != 0
        quotient = abs(numerator) // abs(divisor)
        if (numerator < 0) != (divisor < 0):
            quotient = -quotient
        self.cpu.reg_write(arm.UC_ARM_REG_R1, (numerator - quotient * divisor) & 0xffffffff)
        return quotient

    def set_time(self, value):
        self.cpu.mem_write(EPOCH, packed_date(value))
        # Software RTC reads this field relative to epoch with a two-day bias.
        self.write(MMIO + 0x14, 2 << 24)

    def arm_at(self, slot, deadline, event=0, wake=True):
        record = bytearray(20)
        record[:12] = packed_date(deadline)
        record[15] = int(wake)
        struct.pack_into('<I', record, 16, event)
        self.cpu.mem_write(INPUT, bytes(record))
        return self.call(0xcd21c, slot, INPUT)

    def cancel(self, slot):
        return self.call(0x3caa4, slot)

    def records(self, start=0, count=6):
        return bytes(self.cpu.mem_read(self.table + start * 20, count * 20))

    def guards(self):
        assert bytes(self.cpu.mem_read(TABLE - 16, 16)) == b'\xa5' * 16
        assert bytes(self.cpu.mem_read(TABLE + 120, 16)) == b'\xa5' * 16

    def deliver_rtc_interrupt(self):
        """Run the actual RTC task until it waits for another hardware event."""
        class WaitingForInterrupt(Exception):
            pass
        waits = []
        self.delivered, self.wakes = [], []

        def wait():
            assert self.reg(0) == 0x88
            if waits:
                raise WaitingForInterrupt
            waits.append(True)
            return 0

        self.hook(0x96b18, wait)
        self.hook(0x9698c, lambda: self.delivered.append(self.reg(0)) or 0)
        self.hook(0x99afc, lambda: self.wakes.append(self.reg(0)) or 0)
        # The ISR's captured RTC count signals that the programmed deadline
        # arrived. IRQ generation, kernel signals and physical power are not
        # emulated; record retirement and task control flow are native.
        self.write(0x103c98d4, 2 << 24)
        try:
            self.call(0x1599ac)
        except WaitingForInterrupt:
            return
        raise AssertionError('RTC task did not return to its interrupt wait')


class NativeClockReuse(unittest.TestCase):
    def test_read_time_uses_native_calendar_and_hardware_counter(self):
        m = Clock(self.image)
        now = dt.datetime(2026, 12, 31, 23, 59, 40)
        m.set_time(now)
        m.write(MMIO + 0x14, (2 << 24) | 30)
        m.call(0xb7e10, OUTPUT)
        self.assertEqual(unpacked_date(bytes(m.cpu.mem_read(OUTPUT, 12))),
                         dt.datetime(2027, 1, 1, 0, 0, 10))

    def test_native_duration_arithmetic_crosses_midnight_leap_day_and_year(self):
        m = Clock(self.image)
        for now, minutes in ((dt.datetime(2026, 11, 24, 23, 50), 25),
                             (dt.datetime(2028, 2, 28, 23, 50), 25),
                             (dt.datetime(2028, 2, 29, 23, 50), 25),
                             (dt.datetime(2026, 12, 31, 23, 50), 25)):
            with self.subTest(now=now):
                m.cpu.mem_write(INPUT, packed_date(now))
                m.call(0x3034c, OUTPUT, INPUT, 0, 0, minutes, 0)
                self.assertEqual(unpacked_date(bytes(m.cpu.mem_read(OUTPUT, 12))),
                                 now + dt.timedelta(minutes=minutes))

    def test_stock_accepts_slot_five_but_does_not_schedule_it(self):
        m = Clock(self.image)
        self.assertEqual(m.arm_at(5, dt.datetime(2026, 11, 24, 12, 25)), 0)
        self.assertEqual(m.read(SELECTED), 0xffffffff)
        self.assertEqual(m.records(5, 1)[14], 1)

    def test_sixth_slot_can_be_selected_by_native_scheduler(self):
        m = Clock(self.image, sixth_slot=self.enable_sixth)
        self.assertEqual(m.arm_at(5, dt.datetime(2026, 11, 24, 12, 25)), 0)
        self.assertEqual(m.read(SELECTED), 5)
        self.assertTrue(m.commits)
        m.guards()

    def test_earlier_existing_alarm_wins_then_pomodoro_wins(self):
        m = Clock(self.image, sixth_slot=self.enable_sixth)
        now = dt.datetime(2026, 11, 24, 12)
        for slot, minute in ((0, 24 * 60), (1, 30), (2, 45), (3, 10), (4, 60)):
            self.assertEqual(m.arm_at(slot, now + dt.timedelta(minutes=minute),
                                      event=(0, 0x94, 0x89, 0x8a, 0x8b)[slot]), 0)
        old = m.records(0, 5)
        m.arm_at(5, now + dt.timedelta(minutes=25))
        self.assertEqual(m.read(SELECTED), 3)
        self.assertEqual(m.records(0, 5), old)
        self.assertEqual(m.cancel(3), 0)
        self.assertEqual(m.read(SELECTED), 5)
        m.guards()

    def test_cancel_new_alarm_restores_next_original_alarm_and_preserves_records(self):
        m = Clock(self.image, sixth_slot=self.enable_sixth)
        now = dt.datetime(2026, 11, 24, 12)
        for slot in range(5):
            m.arm_at(slot, now + dt.timedelta(minutes=30 + slot))
        old = m.records(0, 5)
        m.arm_at(5, now + dt.timedelta(minutes=25))
        self.assertEqual(m.read(SELECTED), 5)
        self.assertEqual(m.cancel(5), 0)
        self.assertEqual(m.records(0, 5), old)
        self.assertEqual(m.read(SELECTED), 0)
        self.assertEqual(m.cancel(5), 0xfffffffd)
        m.guards()

    def test_real_rtc_task_retires_sixth_slot_and_requests_wake(self):
        m = Clock(self.image, sixth_slot=self.enable_sixth)
        deadline = dt.datetime(2026, 11, 24, 12, 25)
        m.arm_at(5, deadline)
        self.assertEqual(m.read(SELECTED), 5)
        m.set_time(deadline)
        m.deliver_rtc_interrupt()
        self.assertEqual(m.records(5, 1)[14], 0)
        self.assertEqual(m.read(SELECTED), 0xffffffff)
        self.assertEqual(m.wakes, [1])
        self.assertEqual(m.delivered, [])  # no invented RTOS signal ID
        m.guards()

    def test_simultaneous_system_alarm_and_pomodoro_retire_without_losing_either(self):
        m = Clock(self.image, sixth_slot=self.enable_sixth)
        deadline = dt.datetime(2026, 11, 24, 12, 25)
        m.arm_at(1, deadline, event=0x94, wake=False)
        m.arm_at(5, deadline)
        self.assertEqual(m.read(SELECTED), 1)
        m.set_time(deadline)
        m.deliver_rtc_interrupt()
        self.assertEqual(m.records(1, 1)[14], 0)
        self.assertEqual(m.records(5, 1)[14], 0)
        self.assertEqual(m.delivered, [0x94])
        self.assertEqual(m.wakes, [1])
        self.assertEqual(m.read(SELECTED), 0xffffffff)

    def test_native_reset_loop_clears_six_records_without_touching_adjacent_memory(self):
        m = Clock(self.image, sixth_slot=self.enable_sixth)
        for slot in range(6):
            m.arm_at(slot, dt.datetime(2026, 11, 24, 12, 25))
        m.cpu.reg_write(arm.UC_ARM_REG_R7, 0)
        # Execute only this native straight-line reset loop; surrounding
        # clock-setting code and its caller are outside this focused check.
        m.cpu.emu_start(0x8cf14, 0x8cf34, count=1000)
        self.assertEqual([m.records(i, 1)[14] for i in range(6)], [0] * 6)
        m.guards()

    def test_native_beep_request_reaches_kernel_service_with_original_parameters(self):
        m = Clock(self.image)
        requests = []
        def kernel():
            p = m.reg(0)
            self.assertEqual(m.read(p), 4)  # native synchronous service request
            message = m.read(p + 24)
            requests.append((m.read(p + 12), m.read(p + 8), m.read(p + 20),
                             m.read(message + 20), m.read(message + 24)))
            m.write(message + 28, 0)
            return 0
        m.hook(0x29880, kernel)
        self.assertEqual(m.call(0xa6e6c, 180, 200000), 0)
        self.assertEqual(requests, [(21, 150, 5, 180, 200000)])
        self.assertEqual(m.locks[-2:], [('lock', 300), ('unlock', 300)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-sixth', action='store_true')
    parser.add_argument('--source', type=Path, default=ROOT /
        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r19.ipsw')
    args = parser.parse_args()
    NativeClockReuse.image = load_image(args.source)
    NativeClockReuse.enable_sixth = args.enable_sixth
    unittest.main(argv=[__file__], verbosity=2)
