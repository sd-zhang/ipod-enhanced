"""Exact shipped PCM copy: samples, overlap, ABI and instruction cost."""
import argparse
import io
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'navigation_qol'))
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP
from native_fixture import load_image
from verify_tx import Machine, REG, QUEUE, BUFFER, STACK


class CopyFrames(unittest.TestCase):
    def machine(self):
        m = Machine(self.stock, io.BytesIO(self.elf), 0x21eb4)
        m.cpu.mem_write(0, self.image)
        return m

    def test_samples_overlap_guards_and_callee_registers(self):
        m = self.machine()
        src, independent = 0x710004, 0x72000c  # word aligned, not 16-byte aligned
        for count in (0, 1, 2, 3, 4, 5, 7, 8, 9, 16, 127, 128, 1024, 1152, 4992):
            data = struct.pack('<' + 'I' * count, *range(1, count + 1))
            for distance in (None, 0, 1, 2, 3, 4, 127, 128, 512):
                with self.subTest(frames=count, forward_overlap=distance):
                    dest = independent if distance is None else src - 4 * distance
                    begin, end = min(src, dest) - 16, max(src, dest) + 4 * count + 16
                    expected = bytearray(b'\xa5' * (end - begin))
                    expected[src - begin:src - begin + len(data)] = data
                    m.cpu.mem_write(begin, bytes(expected))
                    expected[dest - begin:dest - begin + len(data)] = data
                    for i in range(4, 12):
                        m.cpu.reg_write(REG[i], 0x12340000 + i)
                    m.call('copy_frames', dest, src, count)
                    self.assertEqual(m.cpu.reg_read(UC_ARM_REG_SP), STACK)
                    self.assertEqual(bytes(m.cpu.mem_read(begin, end - begin)), expected)
                    for i in range(4, 12):
                        self.assertEqual(m.cpu.reg_read(REG[i]), 0x12340000 + i)
        # Empty input must not dereference either pointer.
        m.call('copy_frames', 0xffffffff, 0xffffffff, 0)

    def test_block_copy_cost(self):
        m = self.machine()
        count = [0]
        m.cpu.hook_add(UC_HOOK_CODE, lambda *args: count.__setitem__(0, count[0] + 1))
        m.call('copy_frames', 0x710000, 0x720000, 1024)
        self.assertLess(count[0], 1500, 'PCM helper still copies one word per instruction')

    def test_complete_output_preserves_emitted_pcm_and_carry(self):
        m = self.machine()
        for frames, held in ((1024, 1152), (1024, 2000), (1024, 3200),
                             (1152, 2304), (576, 2304)):
            with self.subTest(frames=frames, held=held):
                m.reset(8 * frames, wire_bytes=4 * frames)
                m.write(QUEUE + 0xdc, BUFFER)
                pcm = struct.pack('<' + 'I' * (held + frames), *range(1, held + frames + 1))
                m.cpu.mem_write(m.symbols['fifo'], pcm[:4 * held])
                m.write(m.symbols['fifo_len'], held)
                m.write(m.symbols['output_started'], 1)
                m.cpu.mem_write(BUFFER, pcm[4 * held:])
                m.cpu.mem_write(m.symbols['notes'],
                               struct.pack('<7I', BUFFER, 5, 0, frames, frames, held, 0))
                size = m.call('gapless_trim_output', 4 * frames, QUEUE)
                carry = m.read(m.symbols['fifo_len'])
                self.assertEqual(bytes(m.cpu.mem_read(BUFFER, size)) +
                                 bytes(m.cpu.mem_read(m.symbols['fifo'], 4 * carry)), pcm)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    CopyFrames.image = load_image(args.candidate)
    CopyFrames.stock = load_image(Path('iPod_4.3.1.1.ipsw'))
    elf = args.candidate.with_suffix('.artifacts') / 'playback.elf'
    if not elf.exists(): elf = Path('build/gapless-complete-drain/playback.elf')
    CopyFrames.elf = elf.read_bytes()
    unittest.main(argv=[__file__], verbosity=2)
