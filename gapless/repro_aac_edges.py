"""Regression checks for short-AAC and long-priming joins in the native output path.

Numbered PCM pins every sample. --apple additionally encodes short M4As with
afconvert and feeds PCM decoded by the firmware's own AAC decoder.
Exits nonzero if any join is discontinuous. verify.py runs the numbered cases;
--apple also checks real encoded files on macOS.
"""
import math
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from verify_mixed_album import MixedAlbum, File, RETAIL_ARENA_BYTES
from verify_transition import DECODER, CALLBACKS
from verify_tx import Machine
from unicorn import arm_const as arm

stock = Path(sys.argv[2]).read_bytes()
installed = Path(sys.argv[3]).read_bytes()
elf = Path(sys.argv[4])


class Album(MixedAlbum):
    parser_known = True

    def song(self, frames, *, remaining=0):
        # Model the native parser and submitted/completed packet counters.
        m = self.m
        handle, parser = 0x10037000, 0x10037400
        m.write(DECODER + 0x14, handle if self.parser_known else 0)
        m.write(handle + 8, parser)
        m.write(parser + 0x234, len(frames) + 1)
        for offset in (0x2cd4, 0x2cf4, 0x2d10, 0x2d14):
            m.write(DECODER + offset, 0)
        super().song(frames, remaining=remaining)

    def codec(self, cpu, address, size, unused):
        m = self.m
        if address == CALLBACKS + 12:
            for offset in (0x2cd4, 0x2d10):
                m.write(DECODER + offset, m.read(DECODER + offset) + 1)
        super().codec(cpu, address, size, unused)
        if address == CALLBACKS + 16 and cpu.reg_read(arm.UC_ARM_REG_R0) == 1:
            m.write(DECODER + 0x2d14, m.read(DECODER + 0x2d14) + 1)

    def play_trim(self, a, b):
        self.start(a)
        # Leave one A block for the retained-ring handshake. Decode B after
        # A's other blocks are processed so very short A is covered too.
        for _ in range(len(a.blocks) - 1):
            self.worker()
        self.advance(b)
        self.worker()
        self.retire(limit=1)
        self.song(b.blocks, remaining=self.m.read(self.m.symbols['gapless_engine_timing'] + 20))
        for _ in b.blocks:
            self.worker()
        self.retire()
        return bytes(self.delivered)


def numbered(start, count, delay=2112):
    front = delay - 1024
    blocks = (front + count + 1023) // 1024
    pcm = struct.pack(f'<{blocks * 1024}I', *range(start, start + blocks * 1024))
    data = [pcm[i:i + 4096] for i in range(0, len(pcm), 4096)]
    return File(10, delay, count, blocks * 1024 - front - count, data), pcm[front * 4:(front + count) * 4]


def apple(root, name, count, frequency):
    from verify_aac import AACDecoder
    source, encoded = root / f'{name}.wav', root / f'{name}.m4a'
    with wave.open(str(source), 'wb') as w:
        w.setparams((2, 2, 44100, count, 'NONE', ''))
        w.writeframes(b''.join(struct.pack('<hh', int(14000 * math.sin(i * frequency * math.tau / 44100)),
                                          int(12000 * math.cos(i * frequency * math.tau / 44100)))
                               for i in range(count)))
    subprocess.run(['afconvert', '-f', 'm4af', '-d', 'aac ', '-b', '192000', str(source), str(encoded)], check=True)
    data = encoded.read_bytes()
    blocks = AACDecoder(installed, data).decode()
    front = 2112 - 1024
    padding = len(blocks) * 1024 - front - count
    tag = f'00000000 {2112:08X} {padding:08X} {count:016X}'.encode()
    assert b'iTunSMPB' in data and tag in data, 'unexpected encoder timing'
    pcm = b''.join(blocks)
    return File(10, 2112, count, padding, blocks), pcm[front * 4:(front + count) * 4]


def check(name, a, b, *, parser_known=True):
    a, a_pcm = a
    b, b_pcm = b
    passed = True
    for frontend in (0x21eb4, 0x21f3c):
        with elf.open('rb') as binary:
            album = Album(Machine(stock, binary, frontend), installed, arena_bytes=RETAIL_ARENA_BYTES)
        album.parser_known = parser_known
        got = album.play_trim(a, b)
        lead = 0
        while lead < len(got) and got[lead:lead + 4] == b'\0' * 4:
            lead += 4
        body, want = got[lead:], a_pcm + b_pcm
        matched = 0
        while matched < min(len(body), len(want)) and body[matched:matched + 4] == want[matched:matched + 4]:
            matched += 4
        # Must cross the join by at least one full AAC block and account for
        # all audio except the existing, bounded, unflushed album-end carry.
        ok = matched >= len(a_pcm) + 4096 and len(want) - matched <= 3840 * 4 and not any(body[matched:])
        gap = None
        if matched == len(a_pcm):
            at = matched
            while at < len(body) and body[at:at + 4] == b'\0' * 4:
                at += 4
            if body[at:at + 64] == b_pcm[:64]:
                gap = (at - matched) // 4
        print(f'{name} via {frontend:#x}: {"OK" if ok else "MISMATCH"}, '
              f'{matched // 4}/{len(want) // 4} contiguous, join {a.count}, leading {lead // 4}'
              + (f', inserted silence {gap} samples' if gap is not None else ''))
        passed &= ok
    return passed


if __name__ == '__main__':
    passed = True
    for count in (900, 1024, 24, 500, 1536, 2048, 3001, 6000):
        passed &= check(f'short AAC {count}', numbered(0x100000, count), numbered(0x200000, 6000))
    for known in (True, False):
        for delay in (1024, 2048, 2112, 3072, 4096):
            passed &= check(f'AAC priming {delay}, parser {known}', numbered(0x100000, 19000),
                            numbered(0x200000, 3001, delay), parser_known=known)
    if '--apple' in sys.argv[5:]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            b = apple(root, 'next', 6000, 1127)
            for count in (900, 1024):
                passed &= check(f'Apple AAC {count}', apple(root, f'short-{count}', count, 733), b)
    sys.exit(0 if passed else 1)
