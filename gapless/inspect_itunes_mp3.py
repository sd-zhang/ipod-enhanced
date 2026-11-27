"""Run two historical iTunes MP3 readers and their iPod database writer blocks.

Pass an extracted, thin i386 iTunes executable and one or more MP3s. The host
code runs in Unicorn. The input file and system services are backed by memory.
"""
import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn import x86_const as x86


@dataclass(frozen=True)
class Release:
    name: str
    inspector: int
    metadata_flags: int
    file_services: tuple
    libc: tuple


# Stream methods: size, tell, seek, read, async read. Libc methods are in the
# order used below. All addresses are virtual addresses in the thin Mach-O.
ITUNES_70 = Release('7.0', 0x155681, 0xe50,
                    (0x1c1757, 0x1c17b5, 0x1c1813, 0x1c1cf7, 0x1c1bf5),
                    (0xb08c85, 0xb08c99, 0xb08c80, 0xb08d25, 0xb08d2a,
                     0xb096ad, 0xb08d3e, 0xb08d75))
ITUNES_771 = Release('7.7.1', 0x180aba, 0x44,
                     (0x1ff392, 0x1ff3f0, 0x1ff44e, 0x1ff9c4, 0x1ff8c0),
                     (0xf6d641, 0xf6d655, 0xf6d63c, 0xf6d6dc, 0xf6d6e1,
                      0xf6d83a, 0xf6d6ff, 0xf6d77c))
RELEASES = {
    '69dba387dadeaee32a7af9a6eb860baf06cc376bc03fde28d1fd0b82ac0c9491': ITUNES_70,
    '514adf5e5910a5a0c94dc229e5a4ea103093183a86f4cd98fc4cc52e6cebcae0': ITUNES_771,
}
HANDLE, MPEG_HEADER, MEDIA, METADATA, DURATION, TRACK, RECORD = range(0x2001000, 0x2008000, 0x1000)
STACK, STOP = 0x21f0000, 0x21ff000


class ITunes:
    def __init__(self, binary, audio):
        self.release = RELEASES.get(hashlib.sha256(binary).hexdigest())
        if self.release is None:
            raise ValueError('expected the thin i386 executable from iTunes 7.0 or 7.7.1 for Mac')
        self.audio = audio
        self.position = 0
        self.heap = 0x2020000
        self.cpu = cpu = Uc(UC_ARCH_X86, UC_MODE_32)
        cmd = 28  # Mach-O header, followed by load commands.
        for _ in range(struct.unpack_from('<I', binary, 16)[0]):
            kind, size = struct.unpack_from('<II', binary, cmd)
            if kind == 1:  # LC_SEGMENT
                va, vm_size, offset, length = struct.unpack_from('<4I', binary, cmd + 24)
                if va and vm_size:
                    cpu.mem_map(va, (vm_size + 4095) & ~4095)
                    if length:
                        cpu.mem_write(va, binary[offset:offset + length])
            cmd += size
        cpu.mem_map(0x2000000, 0x200000)
        file_ops = ('size', 'tell', 'seek', 'read', 'async_read')
        libc_ops = ('malloc', 'calloc', 'free', 'memset', 'memcpy', 'memmove', 'sdiv', 'udiv')
        for address, name in zip(self.release.file_services + self.release.libc, file_ops + libc_ops, strict=True):
            cpu.hook_add(UC_HOOK_CODE, self.service, name, begin=address, end=address)

    def put(self, address, value):
        self.cpu.mem_write(address, struct.pack('<I', value & 0xffffffff))

    def word(self, address):
        return int.from_bytes(self.cpu.mem_read(address, 4), 'little')

    def service(self, cpu, address, size, name):
        sp = cpu.reg_read(x86.UC_X86_REG_ESP)
        ret, a, b, c, d, e = struct.unpack('<6I', cpu.mem_read(sp, 24))
        result = 0
        if name in ('size', 'tell', 'seek', 'read', 'async_read'):
            assert a == HANDLE
        if name in ('size', 'tell'):
            value = len(self.audio) if name == 'size' else self.position
            cpu.mem_write(b, struct.pack('<Q', value))
        elif name == 'seek':
            self.position = b | (c << 32)
        elif name in ('read', 'async_read'):
            requested = self.word(b)
            data = self.audio[self.position:self.position + requested]
            if data:
                cpu.mem_write(c, data)
            self.position += len(data)
            self.put(b, len(data))
            status = 0 if len(data) == requested else -39  # eofErr
            if name == 'async_read':
                self.put(e + 0x10, status)
            else:
                result = status
        elif name in ('malloc', 'calloc'):
            length = a * b if name == 'calloc' else a
            result = self.heap
            self.heap += (max(1, length) + 15) & ~15
            assert self.heap < STACK - 0x10000
        elif name == 'memset':
            cpu.mem_write(a, bytes([b & 255]) * c)
            result = a
        elif name in ('memcpy', 'memmove'):
            cpu.mem_write(a, bytes(cpu.mem_read(b, c)))
            result = a
        elif name in ('sdiv', 'udiv'):
            numerator, denominator = struct.unpack('<qq' if name == 'sdiv' else '<QQ',
                                                   struct.pack('<4I', a, b, c, d))
            quotient = abs(numerator) // abs(denominator)
            result = -quotient if (numerator < 0) != (denominator < 0) else quotient
            cpu.reg_write(x86.UC_X86_REG_EDX, (result >> 32) & 0xffffffff)
        else:
            assert name == 'free'  # The fixture's arena lasts for one import.
        cpu.reg_write(x86.UC_X86_REG_EAX, result & 0xffffffff)
        cpu.reg_write(x86.UC_X86_REG_ESP, sp + 4)
        cpu.reg_write(x86.UC_X86_REG_EIP, ret)

    def run(self, start, end):
        self.cpu.emu_start(start, end, count=2_000_000)
        assert self.cpu.reg_read(x86.UC_X86_REG_EIP) == end, hex(start)

    def timing(self):
        cpu = self.cpu
        sp = STACK - 4
        cpu.reg_write(x86.UC_X86_REG_ESP, sp)
        cpu.reg_write(x86.UC_X86_REG_EAX, HANDLE)
        cpu.reg_write(x86.UC_X86_REG_EDX, 0)
        cpu.reg_write(x86.UC_X86_REG_ECX, MPEG_HEADER)
        cpu.mem_write(sp, struct.pack('<11I', STOP, MEDIA, DURATION, 0, METADATA, 0, 0, 0, 0, 0, 0))
        self.run(self.release.inspector, STOP)
        if cpu.reg_read(x86.UC_X86_REG_EAX) or not self.word(METADATA) & 0x40000000:
            raise ValueError('iTunes did not return gapless timing for this input')
        flags = METADATA + self.release.metadata_flags
        assert self.word(flags) & 3 == 1  # Tagged timing, eligible for preservation on rescan.
        imported = bytes(cpu.mem_read(flags + 4, 16))  # delay, padding, 64-bit count
        cpu.reg_write(x86.UC_X86_REG_EBP, STACK)

        # Exercise the scan's final decision with a synthetic nonzero seek
        # offset. Then take the accepted timing through the library-track copy
        # and the field-writing blocks of the iPod's mhit serializer.
        if self.release == ITUNES_70:
            self.put(STACK - 0x638, METADATA)
            self.put(STACK - 0x170, 128)
            self.put(STACK - 0x16c, 0)
            self.run(0x158719, 0x15880a)
            self.put(STACK - 0xdb8, TRACK)
            cpu.reg_write(x86.UC_X86_REG_EBX, METADATA)
            cpu.reg_write(x86.UC_X86_REG_EDI, METADATA - 0x1658)
            self.run(0x15b2ce, 0x15b36b)
            self.put(STACK - 0x720, TRACK)
            cpu.reg_write(x86.UC_X86_REG_EDI, RECORD)
            self.run(0x818a9, 0x818b6)
            cpu.reg_write(x86.UC_X86_REG_EDX, TRACK)
            self.run(0x81cb4, 0x81d20)
        else:
            self.put(STACK + 12, METADATA)
            self.put(STACK - 0x98, 128)
            self.put(STACK - 0x94, 0)
            self.run(0x1829d2, 0x182a9c)
            self.put(STACK - 0xe10, TRACK)
            cpu.reg_write(x86.UC_X86_REG_EBX, METADATA)
            self.run(0x186c9e, 0x186d08)
            cpu.reg_write(x86.UC_X86_REG_EDI, RECORD)
            self.run(0x4755de, 0x4755eb)
            cpu.reg_write(x86.UC_X86_REG_ECX, TRACK)
            self.run(0x475b4d, 0x475ba1)
        assert bytes(cpu.mem_read(RECORD, 4)) == b'mhit' and self.word(RECORD + 0xcc) & 1
        delay, count = struct.unpack('<IQ', cpu.mem_read(RECORD + 0xb8, 12))
        padding = self.word(RECORD + 0xc8)
        assert imported == struct.pack('<IIQ', delay, padding, count)
        return delay, count, padding


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('itunes_i386', type=Path)
    cli.add_argument('mp3', type=Path, nargs='+')
    cli.add_argument('--stock-55g', type=Path, help='also replay short stereo join fixtures through stock OSOS')
    args = cli.parse_args()
    binary = args.itunes_i386.read_bytes()
    if args.stock_55g:
        from verify_mp3_join import ReferenceMP3, five_window, readings
        reference = args.stock_55g.read_bytes()
    for path in args.mp3:
        data = path.read_bytes()
        host = ITunes(binary, data)
        timing = host.timing()
        print(f'iTunes {host.release.name}, {path.name}, mhit delay/count/padding {timing}')
        if args.stock_55g:
            inspector = ReferenceMP3(reference)
            mode, offset = inspector.file_info(data)
            assert inspector.first_codec_input(data) == offset
            blocks = ReferenceMP3(reference).decode(data)
            pcm = five_window(reference, blocks, mode, timing)
            right = readings(pcm, 1)
            peak = max(range(len(right)), key=lambda i: abs(right[i]))
            print(f'  5.5G emitted {len(right)} stereo frames, right-channel peak {right[peak]} at {peak}')
