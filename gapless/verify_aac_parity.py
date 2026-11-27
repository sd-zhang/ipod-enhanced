"""Compare stock 5.5G AAC-LC PCM with patched 4G I2S output for the same M4A bytes."""
import argparse
import hashlib
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn import arm_const as arm

from verify_aac import AACDecoder
from verify_clip import REFERENCE_HASH
from verify_mp3 import IMPULSE, SOURCE_FRAMES, peak, transmit
from verify_pcm_parity import Retail55Pump
from verify_tx import ENGINE, QUEUE, STOCK_SHA256, Machine

REGS = [getattr(arm, f'UC_ARM_REG_R{i}') for i in range(4)]
ENGINE55, AAC, COP = 0x14939f60, 0x4000c2d0, 0x149edfa4
JOB_QUEUE, MANAGER = COP + 0x30, 0x149ee35c
STACK, STOP, PARSER, STREAM = 0x301f000, 0x301fff0, 0x3020000, 0x3030000
HANDLE, TRACK, INFO = STREAM + 0x100, STREAM + 0x200, STREAM + 0x300
VTABLE, SERVICES, FILE_BYTES, OUTPUT = STREAM + 0x1000, STREAM + 0x1400, STREAM + 0x2000, STREAM + 0xb000


class Retail55AAC:
    """Run the 5.5G parser, producer and COP consumer; service file I/O synchronously."""

    def __init__(self, stock, data):
        assert hashlib.sha256(stock).hexdigest() == REFERENCE_HASH
        assert len(data) <= OUTPUT - FILE_BYTES
        self.data = data
        self.parser_pos = self.file_pos = 0
        self.packets = []
        self.cpu = cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for base, size in ((0, 0x800000), (0x4000000, 0x800000), (0x3000000, 0x40000),
                           (0x10800000, 0x200000), (0x14900000, 0x200000),
                           (0x40000000, 0x20000), (0x60000000, 0x4000)):
            cpu.mem_map(base, size)
        cpu.mem_write(0, stock)
        cpu.mem_write(0x4000000, stock)
        # The permanent boot overlays contain arithmetic, initialized globals
        # and the job queue. The AAC constructor loads its own three overlays.
        for entry in (0x84058, 0x8406c, 0x84080, 0x84094, 0x840a8, 0x840bc):
            source, destination, size = struct.unpack_from('<3I', stock, entry)
            source &= 0x03ffffff
            cpu.mem_write(destination, stock[source:source + size])
        cpu.mem_write(0x60000000, b'\x55')
        cpu.mem_write(FILE_BYTES, data)
        self.write(HANDLE, STREAM)
        self.write(HANDLE + 8, PARSER)
        self.write(MANAGER, 0x676f40)  # vtable installed by 0x2329f4
        self.write(ENGINE55 + 0x2e60, VTABLE)
        file_methods = (0, 12, 16, 24, 36)
        for offset in file_methods:
            self.write(VTABLE + offset, SERVICES + offset)
        hooks = (0x228a68, 0x228ad0, 0x228c04, 0x232840, 0x2326f0, 0x231358,
                 0xc9354, 0x11c4d0, 0x12c8ac, 0x84474, 0x235df8, 0x25fcb0,
                 0x400084f8, *(SERVICES + offset for offset in file_methods))
        for address in hooks:
            cpu.hook_add(UC_HOOK_CODE, self.service, begin=address, end=address)

    def read(self, address):
        return struct.unpack('<I', self.cpu.mem_read(address, 4))[0]

    def write(self, address, value):
        self.cpu.mem_write(address, struct.pack('<I', value))

    def service(self, cpu, pc, size, unused):
        r0, r1, r2, r3 = [cpu.reg_read(reg) for reg in REGS]
        result = 0
        if pc == 0x232840:  # Allocate a file handle.
            result = HANDLE
        elif pc == 0x2326f0:  # Schedule MP4 parsing synchronously.
            assert r1 == HANDLE
            cpu.reg_write(REGS[0], self.read(HANDLE + 8))
            cpu.reg_write(REGS[1], self.read(HANDLE))
            cpu.reg_write(arm.UC_ARM_REG_PC, 0x229b48)
            return
        elif pc == 0x228a68:
            assert r0 == STREAM
            contents = self.data[self.parser_pos:self.parser_pos + r2]
            cpu.mem_write(r1, contents)
            result = len(contents)
            self.parser_pos += result
        elif pc == 0x228ad0:
            assert r0 == STREAM
            whence = self.read(cpu.reg_read(arm.UC_ARM_REG_SP))
            assert whence in (0, 0xffffffff)
            offset = struct.unpack('<q', struct.pack('<II', r2, r3))[0]
            self.parser_pos = (self.parser_pos if whence == 0 else 0) + offset
            assert 0 <= self.parser_pos <= len(self.data)
            result = self.parser_pos
            cpu.reg_write(REGS[1], 0)
        elif pc == SERVICES:
            result = 1
        elif pc == SERVICES + 12:  # Expose a byte span to the codec.
            assert 0 <= self.file_pos <= self.file_pos + r2 <= len(self.data)
            self.write(r1, FILE_BYTES + self.file_pos)
            result = r2
        elif pc == SERVICES + 16:  # Release the span.
            self.file_pos += r1
            assert self.file_pos <= len(self.data)
        elif pc == SERVICES + 24:
            assert self.read(cpu.reg_read(arm.UC_ARM_REG_SP)) == 1
            self.file_pos += struct.unpack('<q', struct.pack('<II', r2, r3))[0]
            assert 0 <= self.file_pos <= len(self.data)
            result = self.file_pos
            cpu.reg_write(REGS[1], 0)
        elif pc == SERVICES + 36:
            result = self.file_pos
            cpu.reg_write(REGS[1], 0)
        elif pc == 0xc9354:  # File extension unavailable for optional decryption.
            result = 0xffffffff
        elif pc == 0x11c4d0:  # Run parser-manager getters on their synchronous path.
            result = 1
        elif pc == 0x400084f8:  # Observe the actual bytes entering the AAC core.
            start = self.file_pos - r3
            assert self.cpu.mem_read(self.read(AAC + 0x1c), r3) == self.data[start:self.file_pos]
            self.packets.append((start, r3))
            return
        # The remaining hooks dispose the file handle, notify the parser cache,
        # or schedule a task. The actual COP worker executes for every job.
        cpu.reg_write(REGS[0], result)
        cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

    def call(self, address, *args):
        cpu = self.cpu
        for reg, value in zip(REGS, args):
            cpu.reg_write(reg, value)
        cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
        cpu.emu_start(address, STOP, count=1500000)
        assert cpu.reg_read(arm.UC_ARM_REG_PC) == STOP, hex(address)
        return cpu.reg_read(REGS[0])

    def decode(self):
        assert self.call(0x278058, 77, TRACK, INFO) == 0
        assert self.read(INFO + 4) == 44100
        assert self.cpu.mem_read(INFO + 12, 2) == bytes((2, 16))
        assert self.cpu.mem_read(INFO + 24, 1) == b'\x0a'
        self.call(0x25e364)
        self.call(0x1d9810, COP)
        assert self.call(0x1a9094, TRACK, ENGINE55) == AAC
        self.write(JOB_QUEUE + 0x80, 1)  # AAC worker running.
        self.call(0x18b48c, AAC, INFO, 77, TRACK)
        assert self.read(AAC + 0x2500) == 0 and self.read(AAC + 0x24e0) == 0xffffffff
        assert self.read(PARSER + 0x240) == 64
        count = self.read(PARSER + 0x234)
        assert 1 < count < 100
        blocks = []
        for n in range(count):
            self.cpu.mem_write(OUTPUT, bytes(4096))
            assert self.call(0x40008cec, AAC, OUTPUT, 1000) == 1
            assert self.call(0x1d9608, COP, n % 2) == 1
            self.write(JOB_QUEUE + 0x78, n + 1)
            blocks.append(bytes(self.cpu.mem_read(OUTPUT, 4096)))
        assert self.call(0x40008cec, AAC, OUTPUT, 1000) == 0
        assert len(self.packets) == count
        return blocks


def compare(stock, elf, reference, installed, data, delay, early_signal=False):
    ref_decoder, original = Retail55AAC(reference, data), AACDecoder(stock, data)
    ref_blocks, original_blocks = ref_decoder.decode(), original.decode()
    assert len(original_blocks) == len(ref_blocks)-1
    assert original.first_packet == ref_decoder.packets[1][0]
    if early_signal:
        assert original_blocks[0] != ref_blocks[1] and original_blocks[1:] == ref_blocks[2:]
    else:
        assert original_blocks == ref_blocks[1:]
    native = b''.join(ref_blocks)
    if not early_signal:
        assert peak(native) == IMPULSE + delay
    padding = len(native) // 4 - SOURCE_FRAMES - delay
    assert padding > 0
    candidate = AACDecoder(installed, data)
    candidate_blocks = candidate.decode(timing=(delay, SOURCE_FRAMES, padding), elf=elf)
    assert candidate.first_packet == ref_decoder.packets[0][0], (
        'first AAC packet byte, patched 4G and 5.5G', candidate.first_packet, ref_decoder.packets[0][0])
    assert candidate_blocks == ref_blocks
    if delay == 2112:
        fields = f'00000000 {delay:08X} {padding:08X} {SOURCE_FRAMES:016X}'.encode()
        assert b'iTunSMPB' in data and fields in data
    pump = Retail55Pump(reference)
    expected = pump.track(ref_blocks, codec=10, delay=delay, count=SOURCE_FRAMES, padding=padding)
    assert pump.preroll == delay // 1024
    assert len(expected) == SOURCE_FRAMES * 4
    if not early_signal:
        assert peak(expected) == IMPULSE
    else:
        assert expected == native[4*delay:4*(delay+SOURCE_FRAMES)]
    for frontend in (0x21eb4, 0x21f3c):
        with elf.open('rb') as binary:
            transport = Machine(stock, binary, frontend)
        transport.write(ENGINE + 0x2e80, QUEUE)
        transport.cpu.mem_write(ENGINE + 0x2e54, b'\x0a')
        timing = transport.symbols['gapless_engine_timing']
        transport.cpu.mem_write(timing, struct.pack('<4I', delay, SOURCE_FRAMES, 0, padding))
        transport.write(timing + 32, 1)
        transport.cpu.mem_write(ENGINE+0x2e48, bytes((2, 16)))
        index = ENGINE+0xa000
        assert transport.call('gapless_aac_open', ENGINE, ENGINE+0x2e3c, index) == 1
        transport.call('gapless_decoder_start', ENGINE, index-0x2cd4)
        assert transport.read(index) == 0xffffffff
        wire = transmit(transport, bytes(transport.cpu.mem_read(timing, 36)), candidate_blocks)
        assert wire == expected
    return candidate.first_packet, original.first_packet, len(ref_blocks)


def verify_open_modes(stock, elf):
    decoder = ENGINE+0xa000
    index = decoder+0x2cd4
    for case in ('cold', 'resume', 'seek', 'seek during setup', 'offset', 'untagged',
                 'short timing', 'other codec', 'stale cookie', 'other route',
                 'other decoder', 'advanced index'):
        with elf.open('rb') as file:
            m = Machine(stock, file, 0x21f3c)
        timing = m.symbols['gapless_engine_timing']
        cookie = 0 if case == 'untagged' else 1
        m.cpu.mem_write(timing, struct.pack('<4I', 1024, 400 if case == 'short timing'
                                           else 8000, 0, 100))
        m.write(timing+32, cookie)
        m.cpu.mem_write(ENGINE+0x2e48, bytes([2, 16]))
        m.cpu.mem_write(ENGINE+0x2e54, bytes([0 if case == 'other codec' else 10]))
        if case in ('seek', 'resume'):
            m.cpu.mem_write(ENGINE+0x2994, bytes([3 if case == 'seek' else 4]))
        if case == 'offset':
            m.write(ENGINE+0x29b0, 0xffffffff)
        # At cold open the driver has not yet negotiated B's format.
        m.write(ENGINE+0x2e80, 0)
        m.call('gapless_aac_open', ENGINE, ENGINE+0x2e3c, index)
        m.write(ENGINE+0x2e80, QUEUE)
        initial = 5 if case == 'advanced index' else 0
        m.write(index, initial)
        if case == 'other route':
            m.write(QUEUE+0x70, 0)
        if case == 'stale cookie':
            m.write(timing+32, 2)
        if case == 'seek during setup':
            m.write(ENGINE+0x29c0, 1)
        current_decoder = decoder+4 if case == 'other decoder' else decoder
        m.call('gapless_decoder_start', ENGINE, current_decoder)
        if case == 'cold':
            assert m.read(index) == 0xffffffff and m.read(timing+16) == 1024
            m.write(index, 0)  # Packet zero consumed. A retry must leave it consumed.
            m.call('gapless_aac_open', ENGINE, ENGINE+0x2e3c, index)
            m.call('gapless_decoder_start', ENGINE, decoder)
            assert m.read(index) == 0
        else:
            assert m.read(index) == initial, case
    print('AAC packet zero is enabled after cold output setup and preserved across retry, seek and fallback cases')


def verify(stock, elf, reference, installed):
    assert hashlib.sha256(stock).hexdigest() == STOCK_SHA256
    verify_open_modes(stock, elf)
    with tempfile.TemporaryDirectory() as temp:
        source, output = Path(temp) / 'impulse.wav', Path(temp) / 'track.m4a'
        signal = bytearray(4 * SOURCE_FRAMES)
        struct.pack_into('<hh', signal, 4 * IMPULSE, 25000, 25000)
        with wave.open(str(source), 'wb') as wav:
            wav.setparams((2, 2, 44100, SOURCE_FRAMES, 'NONE', ''))
            wav.writeframes(signal)
        encoders = [('FFmpeg', 1024, ['ffmpeg', '-v', 'error', '-y', '-i', str(source),
                                      '-c:a', 'aac', '-b:a', '192k', str(output)])]
        if shutil.which('afconvert'):
            encoders.append(('Apple', 2112, ['afconvert', '-f', 'm4af', '-d', 'aac ', '-b',
                                             '192000', str(source), str(output)]))
        for encoder, delay, command in encoders:
            output.unlink(missing_ok=True)
            subprocess.run(command, check=True)
            first, old_first, blocks = compare(stock, elf, reference, installed, output.read_bytes(), delay)
            print(f'{encoder} AAC, 5.5G/patched 4G start at byte {first} (stock 4G {old_first}), '
                  f'{blocks} identical raw blocks, '
                  f'{SOURCE_FRAMES} identical stereo frames on both 4G adapters, peak {IMPULSE}')
        # Sound at the very beginning exposes the AAC overlap lost by the
        # original 4G. Keep the louder, later impulse as a timing reference.
        for i in range(700):
            struct.pack_into('<hh', signal, 4*i, (i*347 % 6000)-3000, (i*285 % 6000)-3000)
        with wave.open(str(source), 'wb') as wav:
            wav.setparams((2, 2, 44100, SOURCE_FRAMES, 'NONE', ''))
            wav.writeframes(signal)
        subprocess.run(encoders[0][2], check=True)
        compare(stock, elf, reference, installed, output.read_bytes(), 1024, early_signal=True)
        print('Immediate AAC audio: packet-zero overlap and all audible bytes match the 5.5G')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('stock_4g', 'playback_elf', 'stock_55g', 'installed_4g'):
        cli.add_argument(name, type=Path)
    args = cli.parse_args()
    verify(args.stock_4g.read_bytes(), args.playback_elf, args.stock_55g.read_bytes(),
           args.installed_4g.read_bytes())
