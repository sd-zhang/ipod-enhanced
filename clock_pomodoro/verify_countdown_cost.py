"""Clock bitmap pixels and compiled ARM work versus immutable r24.

The native UI fixture supplies resource lookup, OS scheduling and LCD delivery.
Instruction counts are emulator work, not device timings. Run repository Python
outside the macOS sandbox for Unicorn JIT.
"""
import argparse
import json
import struct
import unittest
from pathlib import Path

from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from ui_fixture import machine
from native_fixture import RENDERER, load_image
from verify_resources import directory

REFERENCE = Path('build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r24.ipsw')


def open_machine(candidate):
    image = load_image(candidate)
    symbols = json.loads((candidate.with_suffix('.artifacts') / 'pomodoro-symbols.json').read_text())
    return machine(image, symbols)


def pixels(data, width, stride):
    return [[(data[y * stride + x // 4] >> (6 - 2 * (x % 4))) & 3
             for x in range(width)] for y in range(30)]


def measure(candidate):
    m = open_machine(candidate)
    first = m.symbols['pomo_ui_bitmap'] + 28
    count = {'instructions': 0, 'pixel_bytes_written': 0, 'glyph_lookups': 0}

    def instruction(cpu, pc, size, context):
        count['instructions'] += 1
        if pc == 0x72a88:
            count['glyph_lookups'] += 1

    def write(cpu, access, address, size, value, context):
        count['pixel_bytes_written'] += size

    m.cpu.hook_add(UC_HOOK_CODE, instruction)
    m.cpu.hook_add(UC_HOOK_MEM_WRITE, write, begin=first, end=first + 719)
    samples = []
    for value in (1500, 1499, 900, 899, 60, 59, 1, 0):
        m.write(m.state + 24, value)
        count.update(dict.fromkeys(count, 0))
        result = m.call(0x75584, RENDERER, 0x424d6170, 0x6143)
        assert result == first - 28
        samples.append({'remaining': value, **count})
        m.guards()
    return samples


class CountdownCost(unittest.TestCase):
    def test_all_countdown_pixels_and_padding_match_clock_assets(self):
        m = open_machine(self.candidate)
        bitmaps = directory(load_image(REFERENCE))['BMap'][1]
        glyphs = {}
        for char in '0123456789:':
            data = bitmaps[0x25b9 if char == ':' else 0x25ba + int(char)]
            glyphs[char] = pixels(data[28:], struct.unpack_from('<I', data, 12)[0],
                                 struct.unpack_from('<H', data, 16)[0])
        start = m.symbols['pomo_ui_bitmap']
        before = bytes(m.cpu.mem_read(start - 16, 16))
        after = bytes(m.cpu.mem_read(start + 748, 16))
        for remaining in range(1500, -1, -1):
            m.write(m.state + 24, remaining)
            self.assertEqual(m.call(0x75584, RENDERER, 0x424d6170, 0x6143), start)
            self.assertEqual(struct.unpack('<4I2H2I', bytes(m.cpu.mem_read(start, 28))),
                             (0, 0, 30, 90, 24, 2, 0, 720))
            data = bytes(m.cpu.mem_read(start + 28, 720))
            # All 96 packed pixels per row, including six white padding pixels.
            actual = pixels(data, 96, 24)
            expected = [[] for _ in range(30)]
            text = f'{remaining // 60:02}:{remaining % 60:02}'
            for i, char in enumerate(text):
                for y in range(30):
                    expected[y].extend(glyphs[char][y])
                    if i in (1, 2):
                        expected[y].extend([0] * 3)
            for row in expected:
                row.extend([0] * 6)
            self.assertEqual(actual, expected, text)
            self.assertEqual(bytes(m.cpu.mem_read(start - 16, 16)), before)
            self.assertEqual(bytes(m.cpu.mem_read(start + 748, 16)), after)
        m.guards()

    def test_each_image_byte_is_written_once_with_less_arm_work(self):
        old, new = measure(REFERENCE), measure(self.candidate)
        print(json.dumps({'reference': old, 'candidate': new}), flush=True)
        for baseline, candidate in zip(old, new):
            self.assertEqual(candidate['pixel_bytes_written'], 720)
            self.assertEqual(candidate['glyph_lookups'], baseline['glyph_lookups'])
            self.assertLess(candidate['instructions'], baseline['instructions'] * 0.75)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    CountdownCost.candidate = parser.parse_args().candidate
    unittest.main(argv=[__file__], verbosity=2)
