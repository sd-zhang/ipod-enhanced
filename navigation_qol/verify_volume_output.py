"""Native headphone ceiling tests, including gain, balance and codec restores.

The registry, checked owner cast, song gain, pan math and WM8975 register mapping
execute ARM firmware. Allocation, command transport and physical register I/O are
supplied boundaries. This verifies firmware contracts, not acoustic loudness or
concurrent hardware execution. Run repository Python outside the macOS sandbox.
"""
import argparse
import struct
import unittest
from pathlib import Path

from native_fixture import NativeMachine, load_image
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn import arm_const as arm

REGISTRY = 0x1041a218
OWNER, PREFS, AUDIO = 0x10001000, 0x10002000, 0x10004000
DECODER, WRAPPER, TRACK = 0x10008000, 0x10009000, 0x1000a000
SOUND, DRIVER, TABLE = 0x10040000, 0x1004a000, 0x1004b000


def machine(image, cap=120, *, registry=True, owner=True, prefs=True):
    m = NativeMachine(image)
    for address in (0xd9a60, 0x960e4, 0x9b8e0):
        m.hook(address, m.allocate)
    for address in (0xd9a50, 0xd915c, 0x96070, 0x9b6fc):
        m.hook(address, m.free)
    m.hook(0xd7980, lambda: m.allocations[m.reg(0)])
    if registry:
        assert m.call(0xe9a8c, REGISTRY) == REGISTRY
        if owner:
            m.write(OWNER, 0x25b54)
            m.call(0xbac78, OWNER, 0x3280)
    m.write(OWNER + 0x124, PREFS if prefs else 0)
    m.write(PREFS + 4, AUDIO)
    m.write(PREFS + 0xb48, cap)
    m.write(AUDIO, PREFS)
    m.write(AUDIO + 0xc, DECODER)
    m.cpu.mem_write(DECODER + 0x2b6, b'\x01')
    m.write(DECODER + 0x14, 0x74706c79)
    m.write(AUDIO + 0x2768, 1)
    m.write(AUDIO + 0x2770, 240, 50)
    m.write(AUDIO + 0x2a8, WRAPPER)
    m.write(WRAPPER + 0x20, TRACK)
    m.write(SOUND + 4, DRIVER)
    m.write(DRIVER, TABLE)
    m.write(TABLE + 0x14, 0x14bdb4 - TABLE)
    m.write(DRIVER + 0x34, 1, 1)
    m.write(DRIVER + 0xc0, 1)
    m.cpu.mem_write(DRIVER + 8, bytes([0, 1, 2, 3]))
    m.cpu.mem_write(DRIVER + 0x29, bytes([40, 41]))
    m.write(0x10412f0c, 0, 0)  # normal codec offset and inactive force flag
    m.registers, m.codec_inputs, m.hardware = [], [], {}
    m.fail_register = None
    def write_register():
        register, value = m.reg(1), m.reg(2)
        m.registers.append((register, value))
        if register == m.fail_register:
            return 1
        m.hardware[register] = value
        return 0
    m.hook(0x160478, write_register)
    m.hook(0xa88, lambda: 0)  # execute native main-CPU device branch
    m.hook(0x132b34, lambda: 1)  # native DAC branch of codec mode1
    for address in (0xea168, 0x102494, 0xfb5e4, 0xe1fc4, 0x11b648, 0x11de40):
        m.hook(address, lambda: 0)

    def observe(cpu, pc, size, context):
        if pc == 0x14fae4 and m.reg(1) != 2:
            m.codec_inputs.append(tuple(m.reg(i) for i in (1, 2, 3)))
        elif pc == 0x14fc78:  # actual headphone conversion/write path
            m.codec_inputs.append((2, m.reg(5), m.reg(6)))
        elif pc == 0x14f960:
            # OS command delivery is the boundary; keep setter, gain, native
            # sound-volume/pan and real device dispatcher on both sides.
            cpu.reg_write(arm.UC_ARM_REG_R0, SOUND)
            cpu.reg_write(arm.UC_ARM_REG_PC, 0x14fd30)
    m.cpu.hook_add(UC_HOOK_CODE, observe)
    return m


class VolumeOutput(unittest.TestCase):
    def test_owner_is_safe_as_soon_as_constructor_registers_it(self):
        m = machine(self.image, owner=False)
        m.cpu.mem_write(OWNER, bytes([0xe5]) * 0x12c)
        # Base UI/task and timer constructors are supplied boundaries. Execute
        # the real owner constructor and registry publication before its later
        # prefs initialization; an audio callback can observe this interval.
        m.hook(0xf4cb0, lambda: m.reg(0))
        m.hook(0xf1a18, lambda: m.reg(0))
        m.cpu.reg_write(arm.UC_ARM_REG_R0, OWNER)
        m.cpu.reg_write(arm.UC_ARM_REG_R1, 0)
        m.cpu.reg_write(arm.UC_ARM_REG_SP, 0x100fe000)
        m.cpu.reg_write(arm.UC_ARM_REG_LR, 0x100ff000)
        m.cpu.emu_start(0xf32f4, 0xf336c, count=100000)
        self.assertEqual(m.cpu.reg_read(arm.UC_ARM_REG_PC), 0xf336c)
        self.assertEqual(m.call(0x70290), OWNER)
        self.assertEqual(m.read(OWNER + 0x124), 0)
        self.assertEqual(m.call(0x14fae0, DRIVER, 2, 80, 80), 0)
        self.assertEqual(m.codec_inputs, [(2, 80, 80)])

    def test_positive_song_gain_cannot_exceed_headphone_ceiling(self):
        for state in (0, 1, 2):
            with self.subTest(state=state):
                m = machine(self.image)
                m.write(AUDIO + 0x2768, state)
                m.cpu.mem_write(TRACK + 0x7c, struct.pack('<h', 255))
                self.assertEqual(m.call(0xa9d30, AUDIO, 256, 50, 1), 0)
                self.assertEqual(m.read(AUDIO + 0x2770), 120)
                self.assertEqual(m.codec_inputs, [(2, 60, 60)])
                self.assertEqual(m.registers, [(2, 91), (3, 475)])

    def test_lowered_cap_limits_a_previously_queued_high_command(self):
        m = machine(self.image, cap=256)
        m.call(0x14fd30, SOUND, 120)
        m.write(PREFS + 0xb48, 120)
        m.registers.clear()
        self.assertEqual(m.call(0x14fd30, SOUND, 120), 120)
        self.assertEqual(m.codec_inputs[-1], (2, 60, 60))
        self.assertEqual(m.registers, [(2, 91), (3, 475)])
        self.assertEqual(m.read(0x1039052e) & 0xffff, 60)

    def test_lowered_cap_updates_right_channel_when_left_stays_zero(self):
        for next_master in (60, 120):
            with self.subTest(next_master=next_master):
                m = machine(self.image, cap=256)
                m.call(0x14f824, SOUND, 2, 120, 16)
                self.assertEqual(m.hardware[3], 504)
                m.write(PREFS + 0xb48, 120)
                m.registers.clear()
                m.call(0x14f824, SOUND, 2, next_master, 16)
                self.assertEqual(m.registers, [(2, 0), (3, 475)])
                self.assertEqual(m.hardware[3], 475)
                self.assertEqual(m.read(0x10412f10), 0)

    def test_headphone_io_failure_can_retry_with_unchanged_left_cache(self):
        for failing_register in (2, 3):
            with self.subTest(failing_register=failing_register):
                m = machine(self.image, cap=256)
                self.assertEqual(m.call(0x14fae0, DRIVER, 2, 0, 120), 0)
                m.write(PREFS + 0xb48, 120)
                m.fail_register = failing_register
                m.registers.clear()
                self.assertEqual(m.call(0x14fae0, DRIVER, 2, 0, 60), 1)
                self.assertEqual(m.registers,
                                 [(2, 0)] if failing_register == 2 else [(2, 0), (3, 475)])
                self.assertEqual(m.hardware[3], 504)
                m.fail_register = None
                m.registers.clear()
                self.assertEqual(m.call(0x14fae0, DRIVER, 2, 0, 60), 0)
                self.assertEqual(m.registers, [(2, 0), (3, 475)])
                self.assertEqual(m.hardware[3], 475)
                self.assertEqual(m.read(0x10412f10), 0)

    def test_headphone_refresh_preserves_native_force_flag(self):
        for flag in (0, 1, 7):
            with self.subTest(flag=flag):
                m = machine(self.image)
                m.write(0x10412f10, flag)
                for unused in range(2):
                    self.assertEqual(m.call(0x14fae0, DRIVER, 2, 0, 60), 0)
                    self.assertEqual(m.read(0x10412f10), flag)
                self.assertEqual(m.registers, [(2, 0), (3, 475)] * 2)

    def test_lineout_keeps_native_duplicate_cache_behavior(self):
        m = machine(self.image)
        self.assertEqual(m.call(0x14fae0, DRIVER, 3, 120, 120), 0)
        m.registers.clear()
        self.assertEqual(m.call(0x14fae0, DRIVER, 3, 120, 120), 1)
        self.assertEqual(m.registers, [])

    def test_native_codec_restore_cannot_reapply_old_high_cache(self):
        m = machine(self.image)
        m.cpu.mem_write(0x1039052e, struct.pack('<H', 120))
        # Native codec offset setter replays cached headphone and line-out
        # levels directly, bypassing the sound controller's master setter.
        m.call(0x14a7a4, DRIVER, 1)
        self.assertEqual(m.codec_inputs[0], (2, 60, 60))
        self.assertEqual([r for r, _ in m.registers[:2]], [2, 3])
        self.assertEqual(m.read(0x1039052e) & 0xffff, 60)

    def test_zero_odd_and_full_limits_reach_expected_native_registers(self):
        for cap, bounded, registers in (
                (0, 0, [(2, 0), (3, 384)]),
                (1, 0, [(2, 0), (3, 384)]),
                (119, 59, [(2, 90), (3, 474)]),
                (120, 60, [(2, 91), (3, 475)]),
                (216, 108, [(2, 114), (3, 498)]),
                (256, 127, [(2, 123), (3, 507)])):
            with self.subTest(cap=cap):
                m = machine(self.image, cap)
                self.assertEqual(m.call(0x14fae0, DRIVER, 2, 127, 127), 0)
                self.assertEqual(m.codec_inputs, [(2, bounded, bounded)])
                self.assertEqual(m.registers, registers)

    def test_limiting_keeps_native_pan_within_one_rounding_step(self):
        # Native pan derives channel values from master*pan_factor. Compare
        # against executing native pan at the ceiling, not a copy of helper math.
        for pan in range(-16, 17):
            with self.subTest(pan=pan):
                baseline = machine(self.image, 256)
                # Observe native pan conversion even if its left-only cache
                # happens to match this fresh fixture's initialized value.
                baseline.write(0x10412f10, 1)
                baseline.call(0x14f824, SOUND, 2, 60, pan)
                expected = baseline.codec_inputs[-1][1:]
                limited = machine(self.image, 120)
                limited.call(0x14f824, SOUND, 2, 127, pan)
                self.assertTrue(limited.codec_inputs,
                                'left-only native cache skipped bounded stereo update')
                actual = limited.codec_inputs[-1][1:]
                self.assertLessEqual(max(actual), 60)
                for got, want in zip(actual, expected):
                    self.assertLessEqual(abs(got - want), 1)
                self.assertEqual(actual[0] < actual[1], expected[0] < expected[1])
                self.assertEqual(actual[0] > actual[1], expected[0] > expected[1])

    def test_under_limit_and_other_codec_outputs_keep_stock_behavior(self):
        for mode, left, right in ((2, 17, 42), (0, 90, 70),
                                  (1, 80, 60), (3, 120, 120)):
            with self.subTest(mode=mode):
                limited, stock = machine(self.image, 120), machine(self.stock, 120)
                got = limited.call(0x14fae0, DRIVER, mode, left, right)
                want = stock.call(0x14fae0, DRIVER, mode, left, right)
                self.assertEqual(got, want)
                self.assertEqual(limited.codec_inputs, stock.codec_inputs)
                self.assertEqual(limited.registers, stock.registers)

    def test_other_outputs_do_not_consult_application_registry(self):
        m = machine(self.image, registry=False)
        def reject(cpu, access, address, size, value, context):
            if REGISTRY <= address < REGISTRY + 40:
                raise AssertionError('non-headphone output consulted registry')
        m.cpu.hook_add(UC_HOOK_MEM_READ, reject)
        for mode in (0, 1, 3):
            m.call(0x14fae0, DRIVER, mode, 120, 120)
            self.assertEqual(m.codec_inputs[-1], (mode, 120, 120))

    def test_absent_or_destructing_native_owner_uses_stock_startup_path(self):
        for phase in ('registry', 'owner', 'prefs', 'base-destructor'):
            with self.subTest(phase=phase):
                m = machine(self.image, registry=phase != 'registry',
                            owner=phase != 'owner', prefs=phase != 'prefs')
                if phase == 'base-destructor':
                    m.write(OWNER, 0x26ac4)
                self.assertEqual(m.call(0x14fae0, DRIVER, 2, 80, 80), 0)
                self.assertEqual(m.codec_inputs, [(2, 80, 80)])

    def test_codec_io_error_is_returned_without_extra_writes(self):
        m = machine(self.image)
        m.hook(0x160478, lambda: m.registers.append((m.reg(1), m.reg(2))) or 1)
        self.assertEqual(m.call(0x14fae0, DRIVER, 2, 120, 120), 1)
        self.assertEqual(m.registers, [(2, 91)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--baseline', type=Path, default=Path(__file__).resolve().parent.parent /
                        'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r13.ipsw')
    args = parser.parse_args()
    VolumeOutput.image = load_image(args.candidate)
    VolumeOutput.stock = load_image(args.baseline)
    unittest.main(argv=[__file__], verbosity=2)
