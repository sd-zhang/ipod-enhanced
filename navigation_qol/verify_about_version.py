"""Execute the 4G About version getter and stock font with release revision r50.

The native string conversions and UI property getter run unchanged. Font
initialization/measurement use the stock implementation; framebuffer storage,
resource lookup and OS display locks are supplied. This is not an LCD test.
Run repository Python outside the macOS sandbox for Unicorn JIT access.
"""
import argparse
from pathlib import Path
import unittest

from unicorn import UC_HOOK_CODE, arm_const as arm

from native_fixture import NativeMachine, load_image
from verify_disc_render import raster_machine, LIST, CONTEXT
from verify_resources import directory

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r36.ipsw'
DISPLAY_VERSION = '3.1.1 / r50'
OUT, UI = 0x10010000, 0x10018000


class AboutVersion(unittest.TestCase):
    def test_builder_rejects_unknown_or_already_patched_native_code(self):
        from about_version import install
        image = bytearray(load_image(BASE))
        before = bytes(image)
        metadata = install(image)
        self.assertEqual(metadata['display_version'], DISPLAY_VERSION)
        self.assertEqual(len(image), len(before))
        self.assertEqual(image[:0x766a4], before[:0x766a4])
        self.assertEqual(image[0x766d0:], before[0x766d0:])
        with self.assertRaisesRegex(ValueError, 'native 4G About'):
            install(image)
        for at in (0x7669c, 0x766a4, 0x766c0, 0x766cc, 0x766d0, 0x766e4):
            with self.subTest(address=hex(at)):
                unknown = bytearray(before)
                unknown[at] ^= 1
                unchanged = bytes(unknown)
                with self.assertRaisesRegex(ValueError, 'native 4G About'):
                    install(unknown)
                self.assertEqual(unknown, unchanged)

    def test_native_version_preserves_apple_version_registers_and_output_guards(self):
        m = NativeMachine(self.image)
        registers = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(4, 12)]
        values = [0xabb00000 + i for i in range(8)]

        def seed(cpu, pc, size, context):
            for register, value in zip(registers, values):
                cpu.reg_write(register, value)

        m.cpu.hook_add(UC_HOOK_CODE, seed, begin=0x7669c, end=0x7669c)
        m.cpu.mem_write(OUT - 16, b'\xa5' * 544)
        self.assertEqual(m.call(0x7669c, OUT), 0)
        count = m.read(OUT) & 0xffff
        actual = bytes(m.cpu.mem_read(OUT + 2, count * 2)).decode('utf-16le')
        self.assertEqual(actual, DISPLAY_VERSION)
        self.assertEqual(bytes(m.cpu.mem_read(OUT - 16, 16)), b'\xa5' * 16)
        end = 2 + count * 2
        self.assertEqual(bytes(m.cpu.mem_read(OUT + end, 528 - end)), b'\xa5' * (528 - end))
        self.assertEqual([m.cpu.reg_read(register) for register in registers], values)

    def test_repeated_native_about_property_reads_show_actual_revision(self):
        m = NativeMachine(self.image)
        m.cpu.mem_write(UI, b'\xa5' * 0x2000)
        for _ in range(3):
            self.assertEqual(m.call(0x7492c, UI, 0x53747220, 0x3345, OUT), 1)
            pointer = m.read(OUT)
            self.assertEqual(pointer, UI + 0x1520)
            actual = bytes(m.cpu.mem_read(pointer, 256)).split(b'\0')[0].decode('utf-8')
            self.assertEqual(actual, DISPLAY_VERSION)
            self.assertEqual(bytes(m.cpu.mem_read(UI, 0x1520)), b'\xa5' * 0x1520)
            end = 0x1520 + len(DISPLAY_VERSION) + 1
            self.assertEqual(bytes(m.cpu.mem_read(UI + end, 0x2000 - end)), b'\xa5' * (0x2000 - end))

    def test_existing_about_controls_resources_and_scripts_are_preserved(self):
        before, after = directory(load_image(BASE)), directory(self.image)
        for tag, (_, entries) in before.items():
            self.assertEqual(after[tag][1], entries, tag)

    def test_native_version_font_fits_existing_english_row(self):
        m = raster_machine(self.image, (0, 0, 128, 160), (0, 0, 128, 160),
                           (0, 0, 128, 160), (0, 0, 128, 160))
        resources = directory(self.image)
        text_at, style_at, align_at = 0x10016000, 0x10016400, 0x10016800
        m.call(0xf745c, CONTEXT)
        m.call(0x66efc, LIST, CONTEXT)
        widths = []
        for key, text in ((0x2576, 'Version'), (0x2578, DISPLAY_VERSION)):
            m.cpu.mem_write(style_at, resources['Type'][1][key])
            m.call(0xcac44, LIST, style_at, CONTEXT, align_at)
            m.cpu.mem_write(text_at, text.encode('ascii') + b'\0')
            widths.append(m.call(0x9b440, CONTEXT, text_at))
        # Native View2633: x=6 label, 10-pixel gap, right edge 160-7.
        self.assertEqual(widths, [49, 69])
        self.assertLessEqual(6 + widths[0] + 10 + widths[1], 160 - 7)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    AboutVersion.image = load_image(args.candidate)
    unittest.main(argv=[__file__], verbosity=2)
