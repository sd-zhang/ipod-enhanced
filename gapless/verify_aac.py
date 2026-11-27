"""Measure the stock AAC-LC packet origin, then transmit the decoded PCM with the linked patch."""
import argparse
import hashlib
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE
from unicorn import arm_const as arm

from verify_mp3 import IMPULSE, SOURCE_FRAMES, peak, transmit
from verify_track_metadata import REGS, STACK, STOP, STREAM, TRACK, machine
from verify_tx import ENGINE, OVERLAYS, QUEUE, STOCK_SHA256, Machine

STOCK_ENGINE, CODEC = 0x144a91fc, 0x4000a68c
COP, JOB_QUEUE, MP4_MANAGER = 0x14529df4, 0x14529e28, 0x1450b8cc
PARSER, HANDLE, FILE_INFO = 0x500000, 0x602000, 0x605000
VTABLE, SERVICES, FILE_BYTES, OUTPUT = 0x606000, 0x607000, 0x608000, 0x610000


class AACDecoder:
    def __init__(self, stock, data):
        self.data = data
        self.parser_pos = self.file_pos = 0
        self.first_packet = None
        self.cpu = cpu = machine(stock)
        for address, length in ((0x500000, 0x20000), (0x600000, 0x20000), (0x04000000, 0x400000),
                                (0x10390000, 0x100000), (0x14466000, 0xdb000),
                                (0x40000000, 0x20000), (0x60000000, 0x1000)):
            cpu.mem_map(address, length)
        cpu.mem_write(0x04000000, stock)
        cpu.mem_write(0x60000000, b'\x55')
        for destination, source, length in (*OVERLAYS, (0x14466770, 0x33b770, 0x7a0),
                                             (0x10390004, 0x32de04, 0xd96c)):
            cpu.mem_write(destination, stock[source:source + length])
        self.file_bytes = FILE_BYTES
        if len(data) > OUTPUT - FILE_BYTES:
            # Real lossless album files can exceed 32 MiB. Keep host-supplied
            # file bytes away from the firmware mapping at 0x04000000.
            self.file_bytes = 0x20000000
            cpu.mem_map(self.file_bytes, (len(data) + 4095) & ~4095)
        cpu.mem_write(self.file_bytes, data)
        self.write(HANDLE, STREAM)
        self.write(HANDLE + 8, PARSER)
        self.write(MP4_MANAGER, 0x28d84)
        for offset in (0, 12, 16, 24, 36):
            self.write(VTABLE + offset, SERVICES + offset - VTABLE)
        self.write(STOCK_ENGINE + 0x2e84, VTABLE)
        for address in (0x1064ec, 0x20820, 0x20768, 0x20778, 0x123764, 0x135c8c, 0x1437a4,
                        0x141be0, 0x148820, 0x12c330, 0x400067e8,
                        *(SERVICES + offset for offset in (0, 12, 16, 24, 36))):
            cpu.hook_add(UC_HOOK_CODE, self.service, begin=address, end=address)

    def read(self, address):
        return struct.unpack('<I', self.cpu.mem_read(address, 4))[0]

    def write(self, address, value):
        self.cpu.mem_write(address, struct.pack('<I', value))

    def service(self, cpu, address, size, unused):
        r0, r1, r2, r3 = [cpu.reg_read(reg) for reg in REGS[:4]]
        signed = r1 - (1 << 32) if r1 >= (1 << 31) else r1
        result = 0
        if address == 0x123764:  # Virtual file handle allocation.
            result = HANDLE
        elif address == 0x135c8c:  # Dispatch the parser on the synchronous path.
            assert r1 == HANDLE
            cpu.reg_write(REGS[0], self.read(HANDLE + 8))
            cpu.reg_write(REGS[1], self.read(HANDLE))
            cpu.reg_write(arm.UC_ARM_REG_PC, 0x1175a4)
            return
        elif address == 0x141be0:
            contents = self.data[self.parser_pos:self.parser_pos + r2]
            cpu.mem_write(r1, contents)
            self.parser_pos += len(contents)
            result = len(contents)
        elif address == 0x148820:
            assert r2 in (0, 0xffffffff)
            self.parser_pos = signed + (self.parser_pos if r2 == 0 else 0)
            assert 0 <= self.parser_pos <= len(self.data)
            result = self.parser_pos
        elif address == SERVICES:
            result = 1  # File is available.
        elif address == SERVICES + 36:
            result = self.file_pos
        elif address == SERVICES + 24:
            assert r2 == 1
            self.file_pos += signed
            assert 0 <= self.file_pos <= len(self.data)
            result = self.file_pos
        elif address == SERVICES + 12:
            assert self.file_pos + r2 <= len(self.data)
            self.write(r1, self.file_bytes + self.file_pos)
            result = r2
        elif address == SERVICES + 16:
            self.file_pos += r1
        elif address == 0x400067e8:
            if self.first_packet is None:
                self.first_packet = self.file_pos - r3
            return
        # The remaining services are thread registration/wakeup, parser cache
        # hints, playback-clock notifications and file-handle disposal.
        cpu.reg_write(REGS[0], result)
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    def call(self, address, *args, instruction_limit=1500000):
        for reg, value in zip(REGS, args):
            self.cpu.reg_write(reg, value)
        self.cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
        self.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        self.cpu.emu_start(address, STOP, count=instruction_limit)
        assert self.cpu.reg_read(arm.UC_ARM_REG_PC) == STOP, hex(address)
        return self.cpu.reg_read(REGS[0])

    def decode(self, timing=None, elf=None, *, max_blocks=None):
        self.call(0x1d85c)  # Construct Apple's two-slot AAC coprocessor queue.
        self.call(0x13c124, COP)
        self.write(JOB_QUEUE + 0x7c, 1)
        assert self.call(0x1159e4, TRACK, STOCK_ENGINE) == CODEC
        assert self.call(0x131f60, 77, TRACK, FILE_INFO) == 0
        assert self.cpu.mem_read(FILE_INFO + 24, 1) == b'\x0a'
        assert self.read(FILE_INFO + 4) == 44100
        if timing is not None:
            delay, count, padding = timing
            with elf.open('rb') as file:
                table = ELFFile(file).get_section_by_name('.symtab')
                selected = table.get_symbol_by_name('gapless_engine_timing')[0]['st_value']
                start = table.get_symbol_by_name('gapless_decoder_start')[0]['st_value']
            self.cpu.mem_write(selected, struct.pack('<4I', delay, count, 0, padding))
            self.write(selected + 32, 1)
            self.cpu.mem_write(STOCK_ENGINE+0x2e3c, bytes(self.cpu.mem_read(FILE_INFO, 48)))
        self.call(0xdbdd8, CODEC, FILE_INFO, 77, TRACK)
        assert self.read(CODEC + 0x2cd4) == 0
        if timing is not None:
            queue = 0x1446702c
            self.write(STOCK_ENGINE+0x2e80, queue)
            self.cpu.mem_write(queue+0x94, bytes([2, 16]))
            self.write(queue+0xd0, 1)
            driver, i2s, endpoint = VTABLE+0x800, VTABLE+0x900, VTABLE+0xa00
            self.write(queue+0x70, driver)
            self.write(driver, 0x21f3c)
            self.write(driver+0x38, i2s)
            self.write(i2s, 0x236ec)
            self.write(i2s+0xc, 1)
            self.write(i2s+0x18, endpoint)
            self.call(start, STOCK_ENGINE, CODEC)
        assert self.read(CODEC + 0x2cf4) == 0
        self.first_index = self.read(CODEC + 0x2cd4)
        assert self.first_index in (0, 0xffffffff)
        assert self.read(PARSER + 0x240) == 64  # AAC AudioSpecificConfig.
        block_count = self.read(PARSER + 0x234) - int(self.first_index == 0)
        assert block_count > 0
        if max_blocks is None:
            assert block_count < 100
        else:
            assert 0 < max_blocks <= block_count
        blocks = []
        for number in range(block_count if max_blocks is None else max_blocks):
            self.cpu.mem_write(OUTPUT, bytes(4096))
            assert self.call(0x400065a4, CODEC, OUTPUT, 1000) == 1
            # Execute the real consumer selected by 0x15a650 after each producer
            # call. 0x15a648 supplies completed count modulo the two slots.
            assert self.call(0x11987c, COP, number % 2) == 1
            self.write(JOB_QUEUE + 0x78, number + 1)
            blocks.append(bytes(self.cpu.mem_read(OUTPUT, 4096)))
        if len(blocks) == block_count:
            assert self.call(0x400065a4, CODEC, OUTPUT, 1000) == 0
        return blocks


def verify(stock, elf_path):
    assert hashlib.sha256(stock).hexdigest() == STOCK_SHA256
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        source, output = directory / 'source.wav', directory / 'track.m4a'
        signal = bytearray(4 * SOURCE_FRAMES)
        struct.pack_into('<hh', signal, 4 * IMPULSE, 25000, 25000)
        with wave.open(str(source), 'wb') as wav:
            wav.setparams((2, 2, 44100, SOURCE_FRAMES, 'NONE', ''))
            wav.writeframes(signal)
        encoders = [('FFmpeg', ['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-c:a', 'aac',
                                '-b:a', '192k', str(output)], 1024)]
        if shutil.which('afconvert'):
            encoders.append(('Apple', ['afconvert', '-f', 'm4af', '-d', 'aac ', '-b', '192000',
                                       str(source), str(output)], 2112))
        for name, command, priming in encoders:
            output.unlink(missing_ok=True)
            subprocess.run(command, check=True)
            data = output.read_bytes()
            decoder = AACDecoder(stock, data)
            blocks = decoder.decode()
            native = b''.join(blocks)
            offset = priming - 1024
            padding = len(native) // 4 - offset - SOURCE_FRAMES
            assert padding >= 0 and peak(native) == IMPULSE + offset
            if name == 'Apple':
                expected = f'00000000 {priming:08X} {padding:08X} {SOURCE_FRAMES:016X}'.encode()
                assert b'iTunSMPB' in data and expected in data
            for frontend in (0x21eb4, 0x21f3c):
                with elf_path.open('rb') as file:
                    m = Machine(stock, file, frontend)
                m.write(ENGINE + 0x2e80, QUEUE)
                m.cpu.mem_write(ENGINE + 0x2e54, b'\x0a')
                timing = m.symbols['gapless_engine_timing']
                # These four words model a populated iTunesDB record.
                m.cpu.mem_write(timing, struct.pack('<4I', priming, SOURCE_FRAMES, 0, padding))
                m.write(timing + 32, 1)
                m.call('gapless_decoder_start', ENGINE)
                assert m.read(timing + 28) and m.read(timing + 16) == offset
                wire = transmit(m, bytes(m.cpu.mem_read(timing, 36)), blocks)
                assert wire == native[4 * offset:4 * (offset + SOURCE_FRAMES)]
                assert len(wire) == 4 * SOURCE_FRAMES and peak(wire) == IMPULSE
            print(f'{name} AAC, priming {priming}, first packet at byte {decoder.first_packet}, '
                  f'native peak {peak(native)}, I2S peak {peak(wire)}, {len(wire) // 4} frames')


if __name__ == '__main__':
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('stock_osos', type=Path)
    args.add_argument('playback_elf', type=Path)
    options = args.parse_args()
    verify(options.stock_osos.read_bytes(), options.playback_elf)
