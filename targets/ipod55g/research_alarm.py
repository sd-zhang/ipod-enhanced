"""Characterize stock 5.5G alarm and color backlight ARM contracts.

No firmware mutations or device access. Static mode needs no JIT permission;
--native executes original ARM with synthetic OS/MMIO scheduling boundaries.
Run with the repository's .venv; --native requires a JIT-capable environment.
Receipts use exclusive creation so earlier failures are retained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import unittest
from zipfile import ZipFile

OSOS_SHA = '784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251'
SIGNATURES = {
    0x91438: '70402de964409fe5b400a0e30410a0e1',
    0x238070: 'f064f90a',
    0x2381d0: '9864f90b',
    0xcd430: '10402de920d04de20140a0e10010a0e3',
    0x285060: 'f0412de910519fe50060a0e3046085e5',
    0x2850f8: '0400950500008505',
    0x285174: '0808f9eb',
    0xc719c: '70402de98c409fe5080094e5000050e3',
    0xc71d4: '5d5501eb',
    0xc71f0: '050080e00c0084e5400041e2066190e7',
    0xc7220: '080081e5',
    0x27d8a0: '08009fe50c0090e59500a0e31eff2fe1',
    0x2831b4: '70402de90040a0e1a189ffeb60509fe5',
    0x28453c: 'f84f2de93a83ffebbc449fe50000c4e5',
    0x281420: '34109fe5000050e3040081e50010d1e5',
}


def stock(path: Path) -> bytes:
    with ZipFile(path) as z:
        firmware = z.read('Firmware-25.6.3')
    offset, size = struct.unpack_from('<II', firmware, 0x420c)
    image = firmware[offset + 0x800:offset + 0x800 + size]
    if hashlib.sha256(image).hexdigest() != OSOS_SHA:
        raise ValueError('Expected exact stock 5.5G software 1.3 OSOS')
    return image


def branch(image: bytes, at: int) -> int:
    value = struct.unpack_from('<I', image, at)[0]
    assert value & 0x0e000000 == 0x0a000000
    displacement = value & 0xffffff
    if displacement & 0x800000:
        displacement -= 0x1000000
    return at + 8 + 4 * displacement


def inspect(image: bytes) -> dict:
    for at, signature in SIGNATURES.items():
        assert image[at:at + len(signature) // 2].hex() == signature, hex(at)
    for at in (0x238070, 0x2381d0, 0x28449c):
        assert branch(image, at) == 0x91438
    for at in (0x91448, 0x91458, 0x91464, 0x91470,
               0x9147c, 0x91488, 0x91494, 0x914a4):
        assert branch(image, at) == 0xcd430
    assert branch(image, 0x285174) == 0xc719c
    fade_down = list(image[0x4fc084:0x4fc0a4])
    return {
        'osos_sha256': OSOS_SHA,
        'evidence': 'Exact stock signatures and disassembly; native results below only when requested.',
        'signatures': {hex(k): v for k, v in SIGNATURES.items()},
        'alarm_pattern': [[180, 200000], [144, 400000]] * 4,
        'alarm_pattern_total_microseconds': 2400000,
        'clock_pattern_sites': ['0x238070', '0x2381d0'],
        'non_clock_pattern_site': '0x28449c (WatchdogTask)',
        'piezo_state': '0x1081dfb0: head, tail, active, deadline',
        'piezo_periods': '0x10882314',
        'piezo_durations': '0x10882354',
        'piezo_capacity': 15,
        'native_backlight_state': '0x1081ff40: u8 state, +4 requested brightness, +8 current brightness, +12 timer',
        'fade_down_milliseconds': fade_down,
        'fade_down_31_to_power_off_delay_sum': sum(fade_down),
        'limits': [
            'No scheduler latency, GPIO, audible output, physical light, battery or device evidence.',
            'Stock characterization does not execute the added persistent preference; see test_alarm_settings.py.',
            'Stock characterization does not execute the extension; see test_alarm_native.py.',
            'Normal native color backlight Off fades and cannot guarantee a 250ms dark interval.',
        ],
    }


def native_classes(image: bytes):
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn import arm_const as arm
    regs = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(13)]
    stack, return_at, message = 0x100ee000, 0x100ff000, 0x10002000
    piezo, periods, durations = 0x1081dfb0, 0x10882314, 0x10882354
    light = 0x1081ff40

    class Machine:
        def __init__(self):
            self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
            for at, size in ((0, 0x800000), (0x10000000, 0x100000),
                             (0x10800000, 0x200000), (0x60004000, 0x3000),
                             (0x70000000, 0x1000), (0x7000a000, 0x1000)):
                self.cpu.mem_map(at, size)
            self.cpu.mem_write(0, image)
            source, dest, size = struct.unpack_from('<III', image, 0x8406c)
            self.cpu.mem_write(dest, image[source:source + size])
            self.hooks = {}
            self.writes = []
            self.cpu.hook_add(UC_HOOK_CODE, self.dispatch)
            self.cpu.hook_add(UC_HOOK_MEM_WRITE, self.mem_write)
            self.hooks[0xa6540] = lambda: None  # OS lock boundary
            self.hooks[0xa687c] = lambda: None  # OS unlock boundary
            self.hooks[0xa6124] = lambda: None  # synchronous request acknowledgment

        def dispatch(self, cpu, pc, size, _):
            if pc in self.hooks:
                value = self.hooks[pc]()
                if value is not None:
                    cpu.reg_write(regs[0], value & 0xffffffff)
                cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

        def mem_write(self, cpu, access, at, size, value, _):
            if 0x60000000 <= at < 0x80000000:
                self.writes.append((at, size, value))

        def reg(self, n):
            return self.cpu.reg_read(regs[n])

        def read(self, at):
            return struct.unpack('<I', self.cpu.mem_read(at, 4))[0]

        def write(self, at, *values):
            self.cpu.mem_write(at, struct.pack(f'<{len(values)}I', *(v & 0xffffffff for v in values)))

        def call(self, at, *args):
            for r in regs:
                self.cpu.reg_write(r, 0)
            for n, arg in enumerate(args):
                self.cpu.reg_write(regs[n], arg)
            self.cpu.reg_write(arm.UC_ARM_REG_SP, stack)
            self.cpu.reg_write(arm.UC_ARM_REG_LR, return_at)
            self.cpu.emu_start(at, return_at, count=200000)
            assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == return_at
            assert self.cpu.reg_read(arm.UC_ARM_REG_SP) == stack
            return self.reg(0)

        def queue(self, period, duration, cancel=False):
            self.write(message, 0, 0, 0, 0, int(cancel), period, duration, 0xdeadbeef)
            for n, value in ((4, message), (5, piezo), (6, 0), (7, 1)):
                self.cpu.reg_write(regs[n], value)
            self.cpu.reg_write(arm.UC_ARM_REG_SP, stack)
            self.cpu.emu_start(0x2850e4, 0x285174, count=1000)
            assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == 0x285174
            return self.read(message + 0x1c)

        def backlight_event(self, event):
            # Original BacklightTask's fade-capable branch after event wait;
            # preserves native state transitions, lock checks and timer calls.
            for n, value in ((0, event), (4, light), (5, 0x136), (6, 0),
                             (7, 1), (8, 2), (10, 3), (11, 0x4fc084)):
                self.cpu.reg_write(regs[n], value)
            self.cpu.reg_write(arm.UC_ARM_REG_SP, stack)
            self.hooks.setdefault(0xa63e0, lambda: 0)
            self.hooks[0xa6420] = lambda: 1
            self.cpu.emu_start(0x28458c, 0x284584, count=10000)
            assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == 0x284584

    class Contracts(unittest.TestCase):
        def test_stock_pattern_preserves_all_eight_requests(self):
            m = Machine()
            requests = []
            # Stock ignores seven earlier rejection statuses and returns last.
            m.hooks[0xcd430] = lambda: requests.append((m.reg(0), m.reg(1))) or 1
            self.assertEqual(m.call(0x91438), 1)
            self.assertEqual(requests, [(180, 200000), (144, 400000)] * 4)

        def test_request_owns_stack_message_until_sync_ack(self):
            m = Machine()
            actions = []
            m.hooks[0xa6540] = lambda: actions.append(('lock', m.reg(0)))
            m.hooks[0xa687c] = lambda: actions.append(('unlock', m.reg(0)))
            def send():
                self.assertEqual((m.reg(0), m.reg(2), m.reg(3)), (0x15, 5, 0x96))
                p = m.reg(1)
                self.assertEqual(bytes(m.cpu.mem_read(p, 0x10)), bytes(0x10))
                self.assertEqual(m.read(p + 0x10), 0)
                self.assertEqual((m.read(p + 0x14), m.read(p + 0x18)), (180, 200000))
                m.write(p + 0x1c, 7)
            m.hooks[0xa6770] = send
            self.assertEqual(m.call(0xcd430, 180, 200000), 7)
            self.assertEqual(actions, [('lock', 0x12c), ('unlock', 0x12c)])

        def test_native_ring_accepts_fifteen_then_rejects(self):
            m = Machine()
            m.write(piezo, 0, 0, 0, 0)
            m.write(0x60005010, 12345)
            for i in range(15):
                self.assertEqual(m.queue(180 + i, 200000 + i), 0)
            self.assertEqual(m.read(piezo), 15)
            self.assertEqual(m.queue(999, 888), 1)
            self.assertEqual(m.read(piezo), 15)
            self.assertEqual(m.read(piezo + 12), 12345)
            self.assertEqual(m.read(piezo + 8), 1)
            self.assertEqual(m.read(periods + 14 * 4), 194)
            m.write(piezo + 4, 1)
            self.assertEqual(m.queue(201, 300001), 0)
            self.assertEqual(m.read(piezo), 0)
            self.assertEqual(m.read(periods + 15 * 4), 201)

        def test_native_worker_deadline_mmio_and_drain(self):
            m = Machine()
            m.write(piezo, 0, 0, 0, 0)
            m.write(0x60005010, 1000000)
            m.queue(180, 200000)
            m.queue(144, 400000)
            m.call(0xc719c)
            self.assertEqual(m.read(0x7000a000), 0x808000b4)
            self.assertEqual(m.read(0x60005008), 0x80030d40)
            self.assertEqual(m.read(piezo + 12), 1200000)
            before = list(m.writes)
            m.write(0x60005010, 1199999)
            m.call(0xc719c)
            self.assertEqual(m.writes, before)
            m.write(0x60005010, 1200000)
            m.call(0xc719c)
            self.assertEqual(m.read(0x7000a000), 0x80800090)
            self.assertEqual(m.read(piezo + 12), 1600000)
            m.write(0x60005010, 1600000)
            m.call(0xc719c)
            self.assertEqual(m.read(0x7000a000), 0)
            self.assertEqual(m.read(piezo + 8), 0)
            self.assertEqual(m.read(0x60005008) & 0x80000000, 0)

        def test_deadline_survives_microsecond_counter_wrap(self):
            m = Machine()
            m.write(piezo, 1, 0, 1, 0xfffffff0)
            m.write(periods, 180)
            m.write(durations, 200000)
            m.write(0x60005010, 0xfffffff0)
            m.call(0xc719c)
            self.assertEqual(m.read(piezo + 12), (0xfffffff0 + 200000) & 0xffffffff)
            m.write(0x60005010, 0x10)
            before = list(m.writes)
            m.call(0xc719c)
            self.assertEqual(m.writes, before)

        def test_native_cancel_clears_ring_but_does_not_stop_hardware(self):
            m = Machine()
            m.write(piezo, 3, 1, 1, 777)
            m.write(0x7000a000, 0x808000b4)
            m.write(0x60005008, 0x80030d40)
            m.queue(0, 0, cancel=True)
            self.assertEqual(tuple(m.read(piezo + i) for i in (0, 4, 8, 12)), (1, 1, 0, 777))
            m.call(0xc719c)
            self.assertEqual(m.read(0x7000a000), 0x808000b4)
            self.assertEqual(m.read(0x60005008), 0x80030d40)
            self.assertEqual(m.writes, [])

        def test_irq_only_reads_ack_and_returns_signal(self):
            m = Machine()
            m.write(0x6000500c, 0x12345678)
            m.write(0x7000a000, 0x80800090)
            self.assertEqual(m.call(0x27d8a0), 0x95)
            self.assertEqual(m.writes, [])
            self.assertEqual(m.read(0x7000a000), 0x80800090)

        def test_color_output_wrapper_preserves_native_routing(self):
            for model in (0xb, 0xc):
                with self.subTest(model=model):
                    m = Machine()
                    m.write(0x1081de68, model << 16)
                    m.write(0x1081d8c8, 20)  # Native cached brightness.
                    m.write(light, 1, 20, 20, 91)
                    events = []
                    m.hooks[0xa67b0] = lambda: events.append(m.reg(0))
                    m.call(0x2831b4, 0)
                    self.assertEqual(events, [0xa8])
                    self.assertEqual(m.read(light + 4), 0)
                    self.assertEqual(m.read(0x1081d8c4), 0)
                    m.write(light, 0)
                    m.call(0x2831b4, 1)
                    self.assertEqual(events, [0xa8, 0xa9])
                    self.assertEqual(m.read(light + 4), 20)
                    self.assertEqual(m.read(0x1081d8c4), 1)

        def test_native_fade_down_consumes_300ms_before_power_off(self):
            m = Machine()
            m.write(light, 1, 0, 31, 91)
            levels, power, delays, locks = [], [], [], []
            m.hooks[0x26617c] = lambda: levels.append(m.reg(0))
            m.hooks[0x266288] = lambda: power.append(m.reg(0))
            held = [0]
            def lock():
                locks.append(('lock', m.reg(0)))
                held[0] = 1
            def unlock():
                locks.append(('unlock', m.reg(0)))
                held[0] = 0
            m.hooks[0xa63e0] = lambda: held[0]
            m.hooks[0xa6540] = lock
            m.hooks[0xa687c] = unlock
            def timer():
                self.assertEqual((m.reg(0), m.reg(2), m.reg(3)), (91, 0, 0xaa))
                delays.append(m.reg(1))
                return 91
            m.hooks[0xa67e0] = timer
            m.backlight_event(0xa8)
            while m.read(light) & 0xff:
                m.backlight_event(0xaa)
            self.assertEqual(sum(delays), 300)
            self.assertEqual(levels[-1], 0)
            self.assertEqual(power, [0])
            self.assertEqual(locks.count(('lock', 0x136)), locks.count(('unlock', 0x136)))

        def test_existing_a8_event_is_not_an_idempotent_policy_refresh(self):
            for state in (0, 1, 2, 3):
                with self.subTest(state=state):
                    m = Machine()
                    m.write(light, state, 20, 20, 91)
                    power = []
                    m.hooks[0x26617c] = lambda: None
                    m.hooks[0x266288] = lambda: power.append(m.reg(0))
                    m.hooks[0xa67e0] = lambda: 91
                    m.backlight_event(0xa8)
                    self.assertEqual(m.read(light + 4), 20)
                    if state == 3:
                        self.assertEqual(m.read(light) & 0xff, 2)
                    else:
                        self.assertEqual(m.read(light) & 0xff, 0)
                        self.assertEqual(power, [0])

        def test_native_immediate_off_event_depends_on_current_fade_state(self):
            m = Machine()
            m.write(light, 1, 20, 20, 91)
            power, levels = [], []
            m.hooks[0x26617c] = lambda: levels.append(m.reg(0))
            m.hooks[0x266288] = lambda: power.append(m.reg(0))
            m.hooks[0xa67e0] = lambda: 91
            m.backlight_event(0xa7)
            self.assertEqual(power, [0])
            self.assertEqual(m.read(light) & 0xff, 0)
            self.assertEqual(m.read(light + 4), 20)
            # During fade-up, the same event changes direction; no immediate off.
            m.write(light, 3, 20, 10, 91)
            power.clear()
            m.backlight_event(0xa7)
            self.assertEqual(power, [])
            self.assertEqual(m.read(light) & 0xff, 2)

    return Machine, Contracts


def native_suite(image: bytes) -> dict:
    _, Contracts = native_classes(image)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return {'tests': result.testsRun, 'failures': len(result.failures),
            'errors': len(result.errors), 'passed': result.wasSuccessful(),
            'boundaries': 'OS locks, synchronous queue delivery/ack, MMIO registers and timer callbacks are supplied; native alarm, queue blocks, worker, IRQ and backlight state machine execute original ARM.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, default=Path('iPod_25.1.3.ipsw'))
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    image = stock(args.stock)
    report = inspect(image)
    report['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.native:
        report['native'] = native_suite(image)
    encoded = json.dumps(report, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as stream:
            stream.write(encoded)
    else:
        print(encoded)
    if args.native and not report['native']['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
