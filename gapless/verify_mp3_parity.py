"""Compare native stereo Layer III PCM from stock 5.5G and 4G RetailOS."""

import argparse
import hashlib
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn import arm_const as arm

from verify_clip import REFERENCE_HASH
from verify_mp3 import (COMPRESSED, IMPULSE, OUTPUT_BYTES, PCM, REG, SOURCE_FRAMES,
                        STACK, STOP, Decoder, encode, peak, transmit)
from verify_pcm_parity import Retail55Pump
from verify_tx import Machine

CONTEXT, SYNTH, SUBBANDS = 0x40013aec, 0x40003714, 0x40002514
FILE_INFO = 0x10052000
# OSOS offsets used by the 5.5G startup and MP3 overlay loaders at
# 0x83848 and 0x839c0. Each row is destination, source and initialized length.
REGIONS = (
    (0x40000000, 0x71619c, 0xfb8), (0x40001808, 0x719b00, 0xd0c),
    (0x40008008, 0x71a80c, 0x4678), (0x40010000, 0x71ee84, 0x3aa8),
    (0x10800004, 0x67e2a8, 0x4b7a8), (0x1493690c, 0x6c9a64, 0x4c734),
)


def codec_address(offset):
    return 0x40008008 + offset - 0x71a80c


class ReferenceMP3:
    """Run the stock 5.5G decoder and its original synthesis kernel on stereo MP3."""

    def __init__(self, firmware):
        if hashlib.sha256(firmware).hexdigest() != REFERENCE_HASH:
            raise ValueError("expected stock 5.5G OSOS from iPod_25.1.3.ipsw")
        cpu = self.cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for address, size in (
                (0, 0x740000), (0x40000000, 0x20000), (0x10000000, 0x100000),
                (0x10800000, 0x140000), (0x14930000, 0x100000)):
            cpu.mem_map(address, size)
        cpu.mem_write(0, firmware)
        for address, offset, size in REGIONS:
            cpu.mem_write(address, firmware[offset:offset+size])
        self.output = self.granules = 0
        self.write(0x40013a64, CONTEXT)  # Stock constructor 0x1a9068..70.
        for offset in (0x71cea4, 0x71d13c, 0x71ceb8, 0x71d080,
                       0x71d14c, 0x71d1fc, 0x71d0a4, 0x71d220):
            address = codec_address(offset)
            cpu.hook_add(UC_HOOK_CODE, self.schedule, offset, begin=address, end=address)
        assert self.call(codec_address(0x71e4a0), CONTEXT, 0, 0) == CONTEXT
        self.call(0x124990, SYNTH)

    def write(self, address, value):
        self.cpu.mem_write(address, struct.pack("<I", value))

    def read(self, address):
        return struct.unpack("<I", self.cpu.mem_read(address, 4))[0]

    def file_info(self, data, stack_fill=0xa5):
        """Run the retail MP3 file inspector and return mode and MPEG-start offset."""
        cpu = self.cpu
        file_object, table, methods = 0x10050000, 0x10050100, 0x10050200
        scratch = 0x10054000
        position = 0

        def service(cpu, address, size, unused):
            nonlocal position
            result = 0
            if address == 0x25a944:
                assert cpu.reg_read(REG[0]) == 2208
                result = scratch
            elif address == 0x12873c:
                self.write(self.read(cpu.reg_read(arm.UC_ARM_REG_SP)), file_object)
            elif address == methods + 8:
                count_ptr = cpu.reg_read(REG[2])
                found = data[position:position + self.read(count_ptr)]
                if found:
                    cpu.mem_write(cpu.reg_read(REG[1]), found)
                self.write(count_ptr, len(found))
                position += len(found)
            elif address == methods + 0x10:
                offset_ptr = cpu.reg_read(REG[1])
                offset, = struct.unpack("<q", cpu.mem_read(offset_ptr, 8))
                position = (0, position, len(data))[cpu.reg_read(REG[2])] + offset
                assert position >= 0
                cpu.mem_write(offset_ptr, struct.pack("<q", position))
            elif address == methods + 0x38:
                result = len(data)
                cpu.reg_write(REG[1], 0)
            cpu.reg_write(REG[0], result)
            cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

        self.write(0x14937290, 1)  # Startup's once-only object registration.
        self.write(file_object, table)
        slots = (0, 8, 0x10, 0x24, 0x38)
        for slot in slots:
            self.write(table + slot, methods + slot)
        services = (0x25a944, 0x25a8e4, 0x12873c, 0x1286e4)
        hooks = [cpu.hook_add(UC_HOOK_CODE, service, begin=address, end=address)
                 for address in (*services, *(methods + slot for slot in slots))]
        try:
            # A successful Xing probe bypasses the VBRI probe at 0x277c70.
            # 0x277d5c still reads that test's local. Both possible modes
            # request the same one-frame discard in the retail PCM pump.
            cpu.mem_write(STACK - 0x200, bytes((stack_fill,)) * 0x200)
            assert self.call(0x2778f0, 0, 0, FILE_INFO) == 0
            return cpu.mem_read(FILE_INFO + 0x20, 1)[0], self.read(FILE_INFO + 0x10)
        finally:
            for handle in hooks:
                cpu.hook_del(handle)

    def first_codec_input(self, data):
        """Return the file offset passed to the stock decoder's first refill."""
        cpu = self.cpu
        codec = CONTEXT - 0x44
        engine, stream, table, methods = 0x10060000, 0x10070000, 0x10070100, 0x10070200
        position = 0

        def stream_method(cpu, address, size, unused):
            nonlocal position
            method = address - methods
            result = 0
            if method == 0:
                position = 0
                result = 1
            elif method == 8:
                # Read into the 2048-byte scratch used by Xing and VBRI detection.
                found = data[position:position + cpu.reg_read(REG[2])]
                cpu.mem_write(cpu.reg_read(REG[1]), found)
                position += len(found)
                result = len(found)
            elif method == 0x18:
                assert self.read(cpu.reg_read(arm.UC_ARM_REG_SP)) == 0
                assert cpu.reg_read(REG[3]) == 0
                position = result = cpu.reg_read(REG[2])
                cpu.reg_write(REG[1], 0)
            elif method == 0xc:
                # Expose the current file bytes for the decoder's borrowed read.
                self.write(cpu.reg_read(REG[1]), COMPRESSED + position)
                result = min(len(data) - position, cpu.reg_read(REG[2]))
            cpu.reg_write(REG[0], result)
            cpu.reg_write(arm.UC_ARM_REG_PC, cpu.reg_read(arm.UC_ARM_REG_LR))

        self.write(codec + 4, engine)
        self.write(codec + 0x3b9c, stream)
        self.write(stream, table)
        cpu.mem_write(COMPRESSED, data)
        slots = (0, 8, 0xc, 0x18, 0x20)
        handles = []
        for slot in slots:
            self.write(table + slot, methods + slot)
            handles.append(cpu.hook_add(UC_HOOK_CODE, stream_method,
                                        begin=methods + slot, end=methods + slot))
        try:
            # The codec's configure method probes the header, rewinds the
            # stream and seeks to the selected MPEG offset on normal playback.
            self.call(0x127df4, codec, FILE_INFO, 0, 0, until=0x128100)
            # Only task/decoder initialization follows that point. The first
            # decode vtable call obtains bytes from the same stream and passes
            # the borrowed pointer directly to the decoder.
            self.call(codec_address(0x71d614), codec, PCM, 1000,
                      until=codec_address(0x71e490))
            assert cpu.reg_read(REG[0]) == CONTEXT
            assert cpu.reg_read(REG[2]) == min(2048, len(data) - position)
            assert cpu.reg_read(REG[1]) == COMPRESSED + position
            return position
        finally:
            for handle in handles:
                cpu.hook_del(handle)

    def call(self, address, *args, until=STOP):
        self.cpu.reg_write(arm.UC_ARM_REG_SP, STACK)
        self.cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
        for reg, arg in zip(REG[:4], args):
            self.cpu.reg_write(reg, arg)
        if len(args) > 4:
            self.write(STACK, args[4])
        self.cpu.emu_start(address, until, count=1000000)
        pc = self.cpu.reg_read(arm.UC_ARM_REG_PC)
        assert pc == until, f"5.5G call from {address:#x} stopped at {pc:#x}"
        return self.cpu.reg_read(REG[0])

    def schedule(self, cpu, pc, size, offset):
        if offset in (0x71cea4, 0x71d13c):
            self.output = cpu.reg_read(REG[1])
            self.granules = 0
        elif offset in (0x71d0a4, 0x71d220):
            # The original transpose at 0x71a80c prepared the subbands.
            # Run the native second-CPU task's 0x1b1a98 synthesis call inline.
            args = SYNTH, SUBBANDS, self.output + self.granules * 2304, 2
            for register, value in zip(REG, args):
                cpu.reg_write(register, value)
            cpu.reg_write(arm.UC_ARM_REG_LR, pc + 4)
            cpu.reg_write(arm.UC_ARM_REG_PC, 0x1249c4)
            self.granules += 1
        else:
            # A synchronous worker always has the first subband buffer free.
            cpu.reg_write(REG[0], 0)
            cpu.reg_write(arm.UC_ARM_REG_PC, pc + 4)

    def decode(self, data):
        """Return untrimmed stereo16 blocks from one stream at its MPEG sync."""
        if len(data) > PCM - COMPRESSED:
            raise ValueError("fixture exceeds the compressed input region")
        self.cpu.mem_write(COMPRESSED, data)
        position = 0
        blocks = []
        for _ in range(100):
            accepted = self.call(codec_address(0x71e490), CONTEXT, COMPRESSED + position,
                                 len(data) - position)
            position += accepted
            self.granules = 0
            self.write(OUTPUT_BYTES, 0)
            status = self.call(codec_address(0x71e2dc), CONTEXT, PCM, 4608, OUTPUT_BYTES, 0)
            if self.granules:
                size, = struct.unpack("<I", self.cpu.mem_read(OUTPUT_BYTES, 4))
                assert status == 0 and size == self.granules * 2304
                blocks.append(bytes(self.cpu.mem_read(PCM, size)))
            elif position == len(data) and accepted == 0:
                return blocks
        raise AssertionError("5.5G MP3 decoder did not reach the end of the input")


def verify(target, reference, elf_file, encoder):
    with TemporaryDirectory() as temp:
        for name, rate, options, metadata_frames, source_frames in (
                ("headerless", 44100, ("-t", "-b", "128"), 0, SOURCE_FRAMES),
                ("Info", 44100, ("-b", "128"), 1152, SOURCE_FRAMES),
                ("Xing", 44100, ("-V", "2"), 1152, SOURCE_FRAMES),
                ("protected Xing", 44100, ("-p", "-V", "2"), 1152, SOURCE_FRAMES),
                ("MPEG-2 Info", 22050, ("-b", "64"), 576, 5760),
                ("MPEG-2.5 Info", 11025, ("-b", "32"), 576, 5760),
                ("short Info", 44100, ("-b", "128"), 1152, 3000)):
            impulse = min(IMPULSE, source_frames // 2)
            compressed = encode(Path(temp), encoder, rate, options, source_frames, impulse)
            with elf_file.open("rb") as file:
                four = Decoder(target, ELFFile(file))
            selected = four.origin(compressed, metadata_frames, source_frames)
            blocks = four.decode(compressed)
            five = ReferenceMP3(reference)
            zero_mode, zero_offset = five.file_info(compressed, stack_fill=0)
            mode, offset = five.file_info(compressed)
            is_xing = "Xing" in name
            assert offset == zero_offset == 0, f"{name}: unexpected MPEG-start offset"
            assert (mode, zero_mode) == ((0, 1) if is_xing else (2, 2))
            start_byte = five.first_codec_input(compressed)
            assert start_byte == offset
            native_ref = ReferenceMP3(reference).decode(compressed[start_byte:])
            if name == "Info":
                # Positive control for an actual nonzero compressed read.
                id3 = b"ID3\x03\x00\x00\x00\x00\x00\x20" + bytes(32)
                prefixed = id3 + compressed
                assert five.file_info(prefixed) == (mode, len(id3))
                assert five.first_codec_input(prefixed) == len(id3)
            assert native_ref == blocks, f"{name}: native decoded PCM differs"
            observed = b"".join(native_ref)
            assert peak(observed) == impulse + 576 + 529 + metadata_frames
            with elf_file.open("rb") as file:
                output = transmit(Machine(target, file, 0x21f3c), selected, blocks)
            delay, count, high_count, padding = struct.unpack_from("<4I", selected)
            assert count == source_frames and high_count == 0
            stock = Retail55Pump(reference).track(native_ref, codec=0, delay=delay,
                                                  count=count, padding=padding, mp3_mode=mode)
            start = (peak(observed) - impulse) * 4
            assert output == observed[start:start + source_frames * 4]
            assert len(stock) == len(output) == source_frames * 4
            lag = metadata_frames if mode == 2 else 0
            stock_start = start - lag * 4
            assert stock == observed[stock_start:stock_start + source_frames * 4]
            assert peak(output) == impulse and peak(stock) == impulse + lag
            assert (stock == output) == (lag == 0)
            if "Info" in name:
                # With a checked tag, either library delay must reach the
                # same audible window, including the short track where the
                # extra frame would otherwise invalidate the timing.
                with elf_file.open("rb") as file:
                    adjusted = Decoder(target, ELFFile(file)).origin(
                        compressed, metadata_frames, source_frames,
                        database_timing=(delay + metadata_frames, count, padding))
                    file.seek(0)
                    adjusted_wire = transmit(Machine(target, file, 0x21eb4), adjusted, blocks)
                assert adjusted_wire == output, name
            print(f"{name}: {len(observed)//4} bit-identical decoded stereo frames, "
                  f"5.5G MPEG/read offsets {offset}/{start_byte}, mode {mode}, "
                  f"5.5G ring/4G I2S {len(stock)//4}/{len(output)//4} frames, "
                  f"peaks {peak(stock)}/{peak(output)}, byte match {stock == output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_4g", type=Path)
    parser.add_argument("stock_55g", type=Path)
    parser.add_argument("playback_elf", type=Path)
    parser.add_argument("--lame", default="lame")
    args = parser.parse_args()
    verify(args.stock_4g.read_bytes(), args.stock_55g.read_bytes(), args.playback_elf, args.lame)
