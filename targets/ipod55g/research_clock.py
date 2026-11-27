"""Execute stock 5.5G RTC/calendar contracts; no firmware/device writes.

Run with the existing .venv outside the sandbox because Unicorn needs JIT.
Only locks, event delivery, physical counter and ROM memcpy are supplied.
The sixth-slot experiment modifies emulator-owned bytes only, relocating the
calendar epoch and table together; it is not a boot-safe firmware patch.
"""
import datetime as dt
import hashlib
import struct
import unittest
from pathlib import Path
from zipfile import ZipFile

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm_const as arm

ROOT = Path(__file__).resolve().parents[2]
STOCK_SHA = '784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251'
EPOCH = 0x10882298
TABLE = EPOCH + 12
SELECTED = 0x1081df8c
MMIO = 0x60005000
INPUT, OUTPUT, EXPERIMENT = 0x10070000, 0x10070100, 0x10080000
STACK, RETURN = 0x100fe000, 0x100ff000
REG = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(13)]
EPOCH_LITERALS = (0xa22ec, 0xa7a28, 0xb01d8, 0xb3864, 0xbb21c, 0x1135d8)
TABLE_LITERALS = (0x90730, 0x9e890, 0xa22f4, 0xb38e0, 0xfff38, 0x1020d0, 0x285580)


def stock_image():
    with ZipFile(ROOT / 'iPod_25.1.3.ipsw') as archive:
        firmware = archive.read('Firmware-25.6.3')
    image = firmware[0x5000:0x73b000]
    assert hashlib.sha256(image).hexdigest() == STOCK_SHA
    return image


def packed_date(value):
    data = bytearray(12)
    struct.pack_into('<H', data, 0, value.year)
    struct.pack_into('<H', data, 4, value.timetuple().tm_yday - 1)
    data[8:11] = bytes((value.hour, value.minute, value.second))
    return bytes(data)


def unpacked_date(data):
    year, day = struct.unpack_from('<H', data)[0], struct.unpack_from('<H', data, 4)[0]
    return dt.datetime(year, 1, 1) + dt.timedelta(days=day, hours=data[8], minutes=data[9], seconds=data[10])


class Machine:
    def __init__(self, image):
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.cpu.mem_map(0, 0x800000)
        self.cpu.mem_write(0, image)
        self.cpu.mem_map(0x10000000, 0x800000)
        self.cpu.mem_write(0x107358c0, image[0x7358c0:])
        self.cpu.mem_map(0x10800000, 0x200000)
        # Real target startup data copy. BSS remains initially zero.
        source, destination, size = struct.unpack_from('<3I', image, 0x8406c)
        self.cpu.mem_write(destination, image[source:source + size])
        self.hooks = {}
        self.cpu.hook_add(UC_HOOK_CODE, self.dispatch)
        self.hook(0xf0, lambda: self.cpu.mem_write(self.reg(0), bytes(self.cpu.mem_read(self.reg(1), self.reg(2)))))

    def reg(self, n):
        return self.cpu.reg_read(REG[n])

    def read(self, at):
        return struct.unpack('<I', self.cpu.mem_read(at, 4))[0]

    def write(self, at, *values):
        self.cpu.mem_write(at, struct.pack(f'<{len(values)}I', *(v & 0xffffffff for v in values)))

    def hook(self, at, callback):
        self.hooks[at] = callback

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
            self.cpu.reg_write(REG[i], value & 0xffffffff)
        if len(args) > 4:
            self.write(STACK, *args[4:])
        self.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        self.cpu.reg_write(arm.UC_ARM_REG_LR, RETURN)
        self.cpu.emu_start(address, RETURN, count=200000)
        assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == RETURN, f'instruction limit at {self.cpu.reg_read(arm.UC_ARM_REG_PC):#x}'
        assert self.cpu.reg_read(arm.UC_ARM_REG_SP) == STACK
        return self.reg(0)


class Clock(Machine):
    def __init__(self, image, *, sixth=False, epoch=None):
        image = bytearray(image)
        self.epoch, self.table = ((epoch, epoch + 12) if epoch is not None else
                                  (EXPERIMENT, EXPERIMENT + 12) if sixth else (EPOCH, TABLE))
        integrated = epoch is not None
        if sixth:
            for sites, expected, replacement in ((EPOCH_LITERALS, EPOCH, self.epoch), (TABLE_LITERALS, TABLE, self.table)):
                for site in sites:
                    assert struct.unpack_from('<I', image, site)[0] == expected
                    struct.pack_into('<I', image, site, replacement)
            for site, expected in ((0xfff28, 0xe3540005), (0xa229c, 0xe3500005)):
                assert struct.unpack_from('<I', image, site)[0] == expected
                struct.pack_into('<I', image, site, expected + 1)
        super().__init__(bytes(image))
        self.cpu.mem_map(0x60004000, 0x2000)
        if not integrated:
            self.cpu.mem_write(self.epoch - 16, b'\xa5' * 16)
        self.cpu.mem_write(self.table, bytes(120 if sixth or integrated else 100))
        if not integrated:
            self.cpu.mem_write(self.table + (120 if sixth else 100), b'\xa5' * 16)
        self.write(SELECTED, -1)
        self.write(SELECTED + 16, 0)  # native selector: software calendar route
        self.locks, self.commits = [], []
        self.hook(0xa6540, lambda: self.locks.append(('lock', self.reg(0))))
        self.hook(0xa687c, lambda: self.locks.append(('unlock', self.reg(0))))
        self.hook(0xa79a0, lambda: self.commits.append(self.read(MMIO + 0x14)))
        self.set_time(dt.datetime(2026, 11, 25, 12))

    def set_time(self, value):
        self.cpu.mem_write(self.epoch, packed_date(value))
        self.write(MMIO + 0x14, 2 << 24)

    def arm_at(self, slot, value, event=0, wake=True):
        record = bytearray(20)
        record[:12] = packed_date(value)
        record[15] = int(wake)
        struct.pack_into('<I', record, 16, event)
        self.cpu.mem_write(INPUT, bytes(record))
        return self.call(0xb3870, slot, INPUT)

    def records(self, start=0, count=5):
        return bytes(self.cpu.mem_read(self.table + 20 * start, 20 * count))

    def cancel(self, slot):
        return self.call(0x906e0, slot)

    def deliver(self):
        class Wait(Exception):
            pass
        self.delivered, self.wakes = [], []
        waits = []
        def wait():
            assert self.reg(0) == 0x88
            if waits:
                raise Wait
            waits.append(True)
            return 0
        self.hook(0xa6924, wait)
        self.hook(0xa67b0, lambda: self.delivered.append(self.reg(0)))
        self.hook(0xa77e8, lambda: self.wakes.append(self.reg(0)))
        self.write(SELECTED + 20, 2 << 24)
        try:
            self.call(0x285460)
        except Wait:
            return
        raise AssertionError('RTC task did not return to interrupt wait')


class NativeClock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = stock_image()

    def test_read_counter_and_normalize_year(self):
        m = Clock(self.image)
        m.set_time(dt.datetime(2026, 12, 31, 23, 59, 40))
        m.write(MMIO + 0x14, (2 << 24) | 30)
        m.call(0xb0124, OUTPUT)
        self.assertEqual(unpacked_date(bytes(m.cpu.mem_read(OUTPUT, 12))), dt.datetime(2027, 1, 1, 0, 0, 10))

    def test_native_duration_crosses_day_leap_day_and_year(self):
        m = Clock(self.image)
        for value in (dt.datetime(2026, 11, 25, 23, 50), dt.datetime(2028, 2, 28, 23, 50), dt.datetime(2028, 2, 29, 23, 50), dt.datetime(2026, 12, 31, 23, 50)):
            with self.subTest(value=value):
                m.cpu.mem_write(INPUT, packed_date(value))
                m.call(0x88454, OUTPUT, INPUT, 0, 0, 25, 0)
                self.assertEqual(unpacked_date(bytes(m.cpu.mem_read(OUTPUT, 12))), value + dt.timedelta(minutes=25))

    def test_stock_slot_five_accepts_but_corrupts_neighbor_and_never_schedules(self):
        m = Clock(self.image)
        guard = bytes(m.cpu.mem_read(TABLE + 100, 16))
        self.assertEqual(m.arm_at(5, dt.datetime(2026, 11, 25, 12, 25)), 0)
        self.assertEqual(m.read(SELECTED), 0xffffffff)
        self.assertNotEqual(bytes(m.cpu.mem_read(TABLE + 100, 16)), guard)
        self.assertEqual(m.records(5, 1)[14], 1)

    def test_invalid_slots_and_cancel_inactive(self):
        m = Clock(self.image)
        self.assertEqual(m.arm_at(6, dt.datetime(2026, 11, 25, 12, 25)), 0xffffffff)
        self.assertEqual(m.cancel(6), 0xffffffff)
        self.assertEqual(m.cancel(4), 0xfffffffd)
        self.assertEqual(m.call(0x9e850, 4, 0), 0xfffffffc)

    def test_getter_copies_full_record(self):
        m = Clock(self.image)
        m.arm_at(1, dt.datetime(2026, 11, 25, 12, 25), event=0x94)
        self.assertEqual(m.call(0x9e850, 1, OUTPUT), 0)
        self.assertEqual(bytes(m.cpu.mem_read(OUTPUT, 20)), m.records(1, 1))

    def test_earliest_deadline_and_cancel_reschedule_preserve_other_records(self):
        m = Clock(self.image, sixth=True)
        now = dt.datetime(2026, 11, 25, 12)
        for slot in range(1, 5):
            m.arm_at(slot, now + dt.timedelta(minutes=30 + slot))
        before = m.records()
        m.arm_at(5, now + dt.timedelta(minutes=25))
        self.assertEqual(m.read(SELECTED), 5)
        self.assertEqual(m.records(), before)
        self.assertEqual(m.cancel(5), 0)
        self.assertEqual(m.read(SELECTED), 1)
        self.assertEqual(m.records(), before)
        self.assertEqual(bytes(m.cpu.mem_read(m.table + 120, 16)), b'\xa5' * 16)
        self.assertTrue(m.commits)

    def test_task_retires_simultaneous_existing_and_experimental_records(self):
        m = Clock(self.image, sixth=True)
        deadline = dt.datetime(2026, 11, 25, 12, 25)
        m.arm_at(1, deadline, event=0x94, wake=False)
        m.arm_at(5, deadline)
        self.assertEqual(m.read(SELECTED), 1)
        m.set_time(deadline)
        m.deliver()
        self.assertEqual(m.delivered, [0x94])
        self.assertEqual(m.wakes, [1])
        self.assertEqual(m.records(1, 1)[14], 0)
        self.assertEqual(m.records(5, 1)[14], 0)
        self.assertEqual(m.read(SELECTED), 0xffffffff)

    def test_stock_reset_clears_only_five_experiment_clears_six(self):
        for sixth in (False, True):
            with self.subTest(sixth=sixth):
                m = Clock(self.image, sixth=sixth)
                m.cpu.mem_write(m.table, b'\xa5' * 120)
                m.cpu.reg_write(arm.UC_ARM_REG_R1, m.table)
                m.cpu.reg_write(arm.UC_ARM_REG_R0, 0)
                m.cpu.reg_write(arm.UC_ARM_REG_R7, 0)
                m.cpu.emu_start(0xa2290, 0xa22a8, count=1000)
                self.assertEqual([m.records(i, 1)[14] for i in range(6)], [0] * (6 if sixth else 5) + ([] if sixth else [0xa5]))


TIMER, OWNER, TASK, QUEUE = 0x10071000, 0x10072000, 0x10073000, 0x10074000
RUN, STOP, EXPIRED, TIME = 0x72756e20, 0x73746f70, 0x65787069, 0x74696d65


class Timer(Machine):
    def __init__(self, image):
        super().__init__(image)
        self.now, self.returned, self.posts = 100000, [], []
        self.write(TASK + 0x1c, QUEUE)
        self.write(0x10800004, 0)  # scheduler list initially empty
        self.write(0x10800010, 0xffffffff)
        self.hook(0x101800, lambda: TASK)
        self.hook(0xa0b10, lambda: self.now)
        for at in (0xc9a84, 0xc9b58, 0xa09b0, 0xa0bcc, 0xd1e70):
            self.hook(at, lambda: 0)
        # The pool allocator and RTOS queue insertion are boundaries. Native
        # cancellation still locates and clears the exact queued event node.
        self.hook(0x151904, lambda: self.returned.append(self.reg(0)))
        def post():
            self.posts.append((self.reg(1), bytes(self.cpu.mem_read(self.reg(2), 16))))
            return 1
        self.hook(0x1517d4, post)
        self.call(0x13d7c8, OWNER, TIMER, 0, 0)


class NativeTimers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = stock_image()

    def test_bind_uses_current_task_queue_and_keeps_recipient(self):
        m = Timer(self.image)
        self.assertEqual(m.read(TIMER + 0x20), OWNER)
        self.assertEqual(m.read(TIMER + 0x24), QUEUE)
        self.assertEqual(m.read(TIMER + 0x1c), STOP)
        self.assertEqual(m.read(TIMER + 4), 0)

    def test_start_interval_stop_and_restart_use_native_deadline_list(self):
        m = Timer(self.image)
        m.call(0x151c24, TIMER, 1000)
        m.call(0x151694, TIMER)
        self.assertEqual(m.read(TIMER + 8), m.now + 1000000)
        self.assertEqual(m.read(0x10800004), TIMER)
        self.assertEqual(m.call(0x1519ac, TIMER), 1)
        m.call(0x151c98, TIMER)
        self.assertEqual(m.read(0x10800004), 0)
        self.assertEqual(m.read(TIMER + 0x1c), STOP)
        self.assertEqual(m.call(0x1519ac, TIMER), 0)
        m.now += 400000
        m.call(0x151694, TIMER)
        self.assertEqual(m.read(TIMER + 8), m.now + 1000000)

    def test_native_expiry_posts_native_time_event_with_exact_timer_identity(self):
        m = Timer(self.image)
        m.call(0x151934, TIMER)
        self.assertEqual(m.read(TIMER + 0x1c), EXPIRED)
        self.assertEqual(m.posts, [(QUEUE, struct.pack('<4I', TIME, m.now, 0, TIMER))])

    def test_stop_removes_only_matching_queued_expiry_and_is_idempotent(self):
        m = Timer(self.image)
        other, node, other_event, event = 0x10075000, 0x10075100, 0x10075200, 0x10075300
        m.write(QUEUE + 0x40, other, node)
        m.write(other, node)
        m.write(other + 0xc, other_event, 1)
        m.write(node + 0xc, event, 1)
        m.write(other_event + 4, TIME, m.now, 0, TIMER + 0x100)
        m.write(event + 4, TIME, m.now, 0, TIMER)
        m.write(TIMER + 0x1c, EXPIRED)
        m.call(0x151c98, TIMER)
        self.assertEqual(m.returned, [event])
        self.assertEqual(m.read(node + 0xc), 0)
        self.assertEqual(m.read(node + 0x10), 0)
        self.assertEqual(m.read(other + 0xc), other_event)
        self.assertEqual(m.read(other + 0x10), 1)
        self.assertEqual(m.read(TIMER + 0x1c), STOP)
        m.call(0x151c98, TIMER)
        self.assertEqual(m.returned, [event])


if __name__ == '__main__':
    unittest.main(verbosity=2)
