"""Measure a generated impulse through 4G RetailOS's MP3 decoder and linked origin hook."""

import argparse
import hashlib
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn import arm_const as arm

from verify_tx import ENGINE, OUT, QUEUE, RING, TX, Machine

STOCK = "e6068744c2922845029591b54834b5447425701441daee1a7f11cfc5ba4bbd2a"
CONTEXT, SYNTH, SUBBANDS = 0x4000f8cc, 0x400035ec, 0x400023ec
COMPRESSED, PCM, OUTPUT_BYTES, PROBE_INFO = 0x10001000, 0x10030000, 0x10040000, 0x10041000
STACK, STOP = 0x100e1000, 0x100f0000
IMPULSE, SOURCE_FRAMES = 3211, 11520
REG = [getattr(arm, f"UC_ARM_REG_R{i}") for i in range(13)]


def codec_address(file_offset):
    return file_offset - 0x33db34 + 0x40006008


class Decoder:
    def __init__(self, osos, elf):
        if hashlib.sha256(osos).hexdigest() != STOCK:
            raise ValueError("expected stock 4G monochrome 4.3.1.1 OSOS")
        self.symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
        code = elf.get_section_by_name(".text")
        cpu = self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address, size in ((0, 0x400000), (0x40000000, 0x20000), (0x14400000, 0x160000),
                              (0x10390000, 0x100000), (0x10000000, 0x100000), (0x60000000, 0x1000)):
            cpu.mem_map(address, size)
        cpu.mem_write(0, osos)
        cpu.mem_write(code["sh_addr"], code.data())
        for dest, source, length in (
                (0x40000000, 0x33bf14, 0xf1c), (0x400016e4, 0x33ce30, 0xd04),
                (0x40006008, 0x33db34, 0x49ac), (0x4000c000, 0x3424e0, 0x3888),
                (0x14466770, 0x33b770, 0x7a0), (0x10390004, 0x32de04, 0xd96c)):
            cpu.mem_write(dest, osos[source:source+length])
        self.write(0x4000f888, CONTEXT)
        # The original firmware schedules each granule on its other CPU. Run
        # the identical ARM synthesis routine synchronously on the subband
        # buffer already populated by the stock transpose at 0x40006008.
        self.output = self.granules = 0
        for offset in (0x33dfb4, 0x33e228, 0x33dfcc, 0x33e238, 0x33e1bc, 0x33e1c4,
                       0x33e2f4, 0x33e2fc, 0x33e1e8, 0x33e320):
            address = codec_address(offset)
            cpu.hook_add(UC_HOOK_CODE, self.schedule, offset, begin=address, end=address)
        assert self.call(codec_address(0x340e64), CONTEXT, 0, 0) == CONTEXT
        self.call(0x8c0bc, SYNTH)

    def write(self, address, value):
        self.cpu.mem_write(address, struct.pack("<I", value))

    def read(self, address):
        return struct.unpack("<I", self.cpu.mem_read(address, 4))[0]

    def call(self, pc, *args, until=STOP):
        self.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        self.cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
        for reg, value in zip(REG[:4], args):
            self.cpu.reg_write(reg, value)
        if len(args) > 4:
            self.write(STACK, args[4])
        self.cpu.emu_start(pc, until, count=1000000)
        actual = self.cpu.reg_read(arm.UC_ARM_REG_PC)
        assert actual == until, f"from {pc:#x}, stopped at {actual:#x}, expected {until:#x}"
        return self.cpu.reg_read(REG[0])

    def schedule(self, cpu, pc, size, offset):
        if offset in (0x33dfb4, 0x33e228):
            self.output = cpu.reg_read(REG[1])
            self.granules = 0
        elif offset in (0x33e1e8, 0x33e320):
            for reg, arg in zip(REG, (SYNTH, SUBBANDS, self.output + self.granules * 2304, 2)):
                cpu.reg_write(reg, arg)
            cpu.reg_write(arm.UC_ARM_REG_LR, pc + 4)
            cpu.reg_write(arm.UC_ARM_REG_PC, 0x331c8)
            self.granules += 1
        else:
            if offset in (0x33dfcc, 0x33e238, 0x33e1c4, 0x33e2fc):
                cpu.reg_write(REG[0], 0)  # First of the native two subband buffers.
            cpu.reg_write(arm.UC_ARM_REG_PC, pc + 4)

    def origin(self, data, frames_in_header, source_frames, *, database_timing=None):
        selected = self.symbols["gapless_engine_timing"]
        cookie = 1234
        self.write(selected + 32, cookie)
        if database_timing is not None:
            delay, count, padding = database_timing
            self.cpu.mem_write(selected, struct.pack("<4I", delay, count, 0, padding))
        elif not frames_in_header:
            # Simulate iTunesDB timing for a headerless file; its frame count
            # and end padding do not come from the MPEG payload.
            self.cpu.mem_write(selected, struct.pack("<4I", 576, source_frames, 0, 576))
        self.cpu.mem_write(COMPRESSED, data)
        self.cpu.reg_write(REG[7], PROBE_INFO)
        self.cpu.reg_write(REG[8], COMPRESSED)
        assert self.read(0xdb594) == 0xe1a01008
        delta = self.symbols["gapless_mp3_probe_hook"] - (0xdb594 + 8)
        self.write(0xdb594, 0xea000000 | ((delta >> 2) & 0xffffff))
        self.call(0xdb594, min(len(data), 2048), until=0xdb5a0)
        extra = self.call(self.symbols["gapless_mp3_extra_frames"], cookie)
        assert extra == frames_in_header
        assert not self.call(self.symbols["gapless_mp3_extra_frames"], cookie + 1)
        engine, queue = 0x144a91fc, 0x1446702c
        self.write(engine + 0x2e80, queue)
        self.write(queue + 0xd0, 1)
        self.cpu.mem_write(queue + 0x94, bytes((2, 16)))
        self.call(self.symbols["gapless_decoder_start"], engine)
        delay, count, _, padding = struct.unpack('<4I', self.cpu.mem_read(selected, 16))
        if extra:
            if database_timing is None:
                assert delay == 576
            assert count == source_frames, (count, source_frames)
        enabled = bool(delay and count > delay + 529 + padding)
        if database_timing is not None and database_timing[1] != count:
            enabled = True  # The verified LAME count supplies the window even for a short clip.
        assert bool(self.read(selected + 28)) == enabled
        if enabled:
            assert self.read(selected + 16) == delay + 529 + extra
        return bytes(self.cpu.mem_read(selected, 36))

    def decode(self, data):
        self.cpu.mem_write(COMPRESSED, data)
        blocks = []
        position = 0
        for _ in range(100):
            accepted = self.call(codec_address(0x33ef98), CONTEXT, COMPRESSED + position, len(data) - position)
            position += accepted
            self.granules = 0
            self.write(OUTPUT_BYTES, 0)
            status = self.call(codec_address(0x33deb8), CONTEXT, PCM, 4608, OUTPUT_BYTES, 0)
            if self.granules:
                length = self.read(OUTPUT_BYTES)
                assert status == 0 and length == self.granules * 2304
                blocks.append(bytes(self.cpu.mem_read(PCM, length)))
            elif position == len(data) and accepted == 0:
                return blocks
        raise AssertionError("stock MP3 decoder did not reach the end of the input")


def encode(directory, lame, rate, options, source_frames, impulse):
    wav_file, mp3_file = directory / "source.wav", directory / "encoded.mp3"
    with wave.open(str(wav_file), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<hh", peak, peak) for peak in
                                 (24000 if i == impulse else 0 for i in range(source_frames))))
    subprocess.run([lame, "--silent", "--resample", str(rate / 1000), *options,
                    str(wav_file), str(mp3_file)], check=True)
    return mp3_file.read_bytes()


def transmit(transport, selected, blocks):
    # Stock 4G MP3 synthesis above already produced the packed stereo16 bytes.
    # Place them in the corresponding physical slots. Run the linked producer,
    # worker state changes, stock output adapter and each real completion IRQ.
    m = transport
    wire_bytes = len(blocks[0])
    m.reset(2 * wire_bytes)
    m.cpu.mem_write(m.symbols["gapless_engine_timing"], selected)
    m.write(ENGINE + 0x2e80, QUEUE)
    m.write(ENGINE + 0x90cc, wire_bytes // 4)
    m.write(ENGINE + 0x90c8, 2 * wire_bytes)
    m.write(0x144b22ec, wire_bytes)
    playback = bytearray()
    for block in blocks:
        assert len(block) == wire_bytes
        assert m.call(0x40017a30, QUEUE, OUT) == 0
        address = m.read(OUT)
        m.cpu.mem_write(address, block)
        m.call("gapless_decoder_ready", ENGINE)
        assert m.call(0x400154f0, QUEUE) == 0
        assert m.call(0x40015528, QUEUE, OUT) == 0
        output_bytes = m.call(0x115b68, ENGINE, QUEUE + 0x88, address, wire_bytes)
        assert m.call(0x4001556c, QUEUE) == 0
        tail = m.read(TX + 0x14)
        assert m.call(0x1333d4, QUEUE, 0, output_bytes) == 0
        if tail != m.read(TX + 0x14):
            source, length = m.fifo(tail)
            playback.extend(m.cpu.mem_read(source, length))
            m.done(source, length)
    return bytes(playback)


def peak(stereo):
    left = struct.unpack(f"<{len(stereo) // 2}h", stereo)[::2]
    return max(range(len(left)), key=lambda i: abs(left[i]))


def verify(stock, elf_path, lame):
    with tempfile.TemporaryDirectory() as tmp:
        for name, rate, options, silent_header, source_frames in (
                ("no metadata frame", 44100, ("-t", "-b", "128"), 0, SOURCE_FRAMES),
                ("Info", 44100, ("-b", "128"), 1152, SOURCE_FRAMES),
                ("Xing", 44100, ("-V", "2"), 1152, SOURCE_FRAMES),
                ("protected Xing", 44100, ("-p", "-V", "2"), 1152, SOURCE_FRAMES),
                ("MPEG-2 Info", 22050, ("-b", "64"), 576, SOURCE_FRAMES),
                ("MPEG-2.5 Info", 11025, ("-b", "32"), 576, SOURCE_FRAMES),
                ("short Info", 44100, ("-b", "128"), 1152, 3000)):
            impulse = min(IMPULSE, source_frames // 2)
            raw = encode(Path(tmp), lame, rate, options, source_frames, impulse)
            with elf_path.open("rb") as file:
                decoder = Decoder(stock, ELFFile(file))
            selected = decoder.origin(raw, silent_header, source_frames)
            bias = struct.unpack_from("<I", selected, 16)[0]
            blocks = decoder.decode(raw)
            native = b"".join(blocks)
            assert peak(native) == impulse + bias
            for adapter in (0x21eb4, 0x21f3c):
                with elf_path.open("rb") as file:
                    playback = transmit(Machine(stock, file, adapter), selected, blocks)
                assert len(playback) == source_frames * 4 and peak(playback) == impulse
                assert playback == native[4*bias:4*(bias+source_frames)]
            print(f"{name}, native peak {peak(native)}, I2S peak {peak(playback)},"
                  f" both adapters emitted {len(playback) // 4} stereo frames")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_osos", type=Path)
    parser.add_argument("playback_elf", type=Path)
    parser.add_argument("--lame", default=shutil.which("lame") or "lame")
    args = parser.parse_args()
    verify(args.stock_osos.read_bytes(), args.playback_elf, args.lame)
