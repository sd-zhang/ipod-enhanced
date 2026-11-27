"""Run the linked ARM tag reader on LAME-encoded, known-length audio."""
import argparse
import math
import struct
import subprocess
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_ARM, Uc
from unicorn import arm_const as arm

MEMORY = 0x900000
END = MEMORY + 0x10000
STOP = MEMORY + 0x100
TIMING = MEMORY + 0x200
WORDS = struct.Struct("<4I")


def source(directory, rate, channels, count, options, encoder):
    wav = directory / "source.wav"
    with wave.open(str(wav), "wb") as out:
        out.setparams((channels, 2, rate, 0, "NONE", "NONE"))
        left = (int(14000 * math.sin(i * 0.03)) for i in range(count))
        out.writeframes(b"".join(struct.pack("<h", value) * channels for value in left))
    mp3 = directory / "source.mp3"
    subprocess.run([encoder, "--silent", *options, str(wav), str(mp3)], check=True)
    return mp3.read_bytes()


def checksum(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value.to_bytes(2, "big")


def check(file, encoder, profile_probe=False):
    elf = ELFFile(file)
    symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
    text = elf.get_section_by_name(".text")
    cpu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    cpu.mem_map(0, 0x400000)
    cpu.mem_write(text["sh_addr"], text.data())
    cpu.mem_map(MEMORY, END - MEMORY)
    empty = (0,) * 4
    selected = symbols["gapless_engine_timing"]
    bss = elf.get_section_by_name(".bss")     # all static RAM (diagnostic counters too)
    first, last = bss["sh_addr"] & ~4095, (bss["sh_addr"] + bss["sh_size"] + 4095) & ~4095
    cpu.mem_map(first, last - first)

    def call(name, *args):
        regs = (arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1, arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3)
        for reg, value in zip(regs, args):
            cpu.reg_write(reg, value)
        cpu.reg_write(arm.UC_ARM_REG_SP, MEMORY + 0x8000)
        cpu.reg_write(arm.UC_ARM_REG_LR, STOP)
        cpu.emu_start(symbols[name], STOP, count=40000)
        assert cpu.reg_read(arm.UC_ARM_REG_PC) == STOP
        return cpu.reg_read(arm.UC_ARM_REG_R0)

    def parse(data, timing=empty, expected_leading=None):
        # An over-read crosses an unmapped guard page, even for a partial tag.
        buffer = END - len(data)
        if data:
            cpu.mem_write(buffer, bytes(data))
        cpu.mem_write(TIMING, WORDS.pack(*timing))
        leading = TIMING + WORDS.size
        cpu.mem_write(leading, b"\xff" * 4)
        result = call("gapless_lame_timing", TIMING, buffer, len(data), leading)
        if expected_leading is not None:
            assert int.from_bytes(cpu.mem_read(leading, 4), "little") == expected_leading
        return result, WORDS.unpack(cpu.mem_read(TIMING, 16))

    def measure_probe(label, data, timing=empty):
        instructions = 0

        def count(*_):
            nonlocal instructions
            instructions += 1

        buffer = END - len(data)
        cpu.mem_write(buffer, bytes(data))
        cpu.mem_write(selected, struct.pack("<9I", *timing, *empty, 1))
        hook = cpu.hook_add(UC_HOOK_CODE, count)
        call("gapless_mp3_probe", buffer, len(data))
        cpu.hook_del(hook)
        print(f"{label}: {instructions} ARM instructions")

    with TemporaryDirectory() as temp:
        directory = Path(temp)
        if profile_probe:
            for label, options in (("CBR Info", ["-b128"]), ("VBR Xing", ["-V2"]),
                                   ("no metadata frame", ["-t", "-b128"])):
                first = source(directory, 44100, 2, 44191, options, encoder)[:2048]
                measure_probe(label, first)
                if label in ("CBR Info", "VBR Xing"):
                    lame = first.index(b"LAME")
                    cut = int.from_bytes(first[lame + 21:lame + 24], "big")
                    delay, padding = cut >> 12, cut & 4095
                    if label == "CBR Info":
                        database = (delay, 44191 + 1152, 0, padding)
                    else:
                        database = (delay + 1152, 44191, 0, padding)
                    measure_probe(f"{label} with database timing", first, database)
                if label == "CBR Info":
                    bad_crc = bytearray(first)
                    bad_crc[lame + 35] ^= 1
                    measure_probe("CRC mismatch", bad_crc)
                    measure_probe("database mismatch", first, (1, 0, 0, 0))
                    measure_probe("truncated LAME tag", first[:lame + 35])
            return
        cases = ((44100, 2, ["-V2"]), (44100, 2, ["-b128"]),
                 (48000, 2, ["-p", "-b128"]), (44100, 1, ["-V3"]),
                 (22050, 2, ["-b64"]), (11025, 1, ["-b32"]))
        for rate, channels, options in cases:
            count = rate + 91
            raw = source(directory, rate, channels, count, options, encoder)
            first = raw[:2048]
            at = first.find(b"LAME")
            assert 0 < at < 200
            delay_padding = int.from_bytes(first[at + 21:at + 24], "big")
            wanted = (delay_padding >> 12, count, 0, delay_padding & 4095)
            assert parse(first) == (1, wanted), (rate, channels, options, parse(first), wanted)
            assert parse(first[:at + 36]) == (1, wanted)
            header_frames = 1152 if rate >= 32000 else 576
            assert parse(first[:at], expected_leading=header_frames) == (0, empty)
            assert parse(first[:3], expected_leading=0) == (0, empty)
            adjusted = (wanted[0] + header_frames, *wanted[1:])
            assert parse(first, adjusted) == (1, wanted), (rate, adjusted, parse(first, adjusted))
            assert parse(first, wanted) == (0, wanted)  # A second probe is idempotent.
            counted = (wanted[0], count + header_frames, 0, wanted[3])
            if b"Info" in first[:at]:
                assert parse(first, counted) == (1, wanted), (rate, counted, parse(first, counted))
            else:
                assert parse(first, counted) == (0, counted)
            for field in range(4):
                mismatch = list(adjusted)
                mismatch[field] += 1
                assert parse(first, tuple(mismatch)) == (0, tuple(mismatch))
                mismatch = list(counted)
                mismatch[field] += 1
                assert parse(first, tuple(mismatch)) == (0, tuple(mismatch))
            for n in (0, 1, 3, 4, 8, 12, at, at + 23, at + 34, at + 35):
                assert parse(first[:n]) == (0, empty)
            for existing in ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)):
                assert parse(first, existing) == (0, existing)
            for index in (0, at, at + 21, at + 34, at + 35):
                broken = bytearray(first)
                broken[index] ^= 1
                assert parse(broken) == (0, empty)
                assert parse(broken, adjusted) == (0, adjusted)
                assert parse(broken, counted) == (0, counted)
        no_tag = source(directory, 44100, 2, 44100, ["-t", "-b128"], encoder)
        assert parse(no_tag[:2048]) == (0, empty)
        first = bytearray(source(directory, 44100, 2, 44100, ["-b128"], encoder)[:2048])
        at = first.index(b"LAME")
        mark = first.index(b"Info")
        _, (delay, _, _, padding) = parse(first)
        for index, value in ((1, 0xeb), (2, 0), (2, 0xf0), (2, first[2] | 12)):
            broken_header = bytearray(first)
            broken_header[index] = value
            if index == 1:  # A reserved MPEG version would otherwise probe at byte 21.
                broken_header[21:25] = b"Info"
            assert parse(broken_header, expected_leading=0) == (0, empty)
        # Some CRC-protected frames put the marker after the two CRC bytes.
        protected = first[:mark] + b"\0\0" + first[mark:]
        protected[1] &= 0xfe
        protected[at + 36:at + 38] = checksum(protected[:at + 36])
        assert parse(protected[:2048], expected_leading=1152) == (1, (delay, 44100, 0, padding))
        for packets in (1 << 24, ((1 << 32) + delay + padding - 1) // 1152):
            first[mark + 8:mark + 12] = packets.to_bytes(4, "big")
            first[at + 34:at + 36] = checksum(first[:at + 34])
            count = packets * 1152 - delay - padding
            wanted = (delay, count & 0xffffffff, count >> 32, padding)
            assert parse(first) == (1, wanted)
            inflated = count + 1152
            assert parse(first, (delay, inflated & 0xffffffff, inflated >> 32, padding)) == (1, wanted)

        # Audible length is independent of the encoded stream's cuts.
        for count in (1, 24, 900):
            cpu.mem_write(TIMING, WORDS.pack(576, count, 0, 1704))
            call('gapless_start_clip', selected + 16, TIMING, 529)
            assert WORDS.unpack(cpu.mem_read(selected + 16, 16)) == (1105, count, 0, 1)

        # A retry can read the same tag again, see a short read, or give way
        # to another selected track before the decoder starts.
        frame = source(directory, 44100, 2, 24, ["-b128"], encoder)[:2048]
        buffer = END - len(frame)
        cpu.mem_write(buffer, frame)
        for retry_size, start_cookie, expected in (
                (len(frame), 1, (2257, 24, 0, 1)),
                (3, 1, empty),
                (len(frame), 2, empty)):
            cpu.mem_write(selected, struct.pack("<9I", 576, 1176, 0, 1704, *empty, 1))
            call("gapless_mp3_probe", buffer, len(frame))
            call("gapless_mp3_probe", buffer, retry_size)
            cpu.mem_write(selected + 32, struct.pack("<I", start_cookie))
            call("gapless_start_mp3_clip", selected)
            assert WORDS.unpack(cpu.mem_read(selected + 16, 16)) == expected
    print("Six real CBR/VBR LAME encodes matched original PCM lengths in ARM; protected frames, "
          "three MPEG versions, ID-free starts, missing tags, corrupt/truncated data, existing "
          "database fields and a 64-bit count passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("linked_elf", type=Path)
    parser.add_argument("--lame", default="lame")
    parser.add_argument("--profile-probe", action="store_true",
                        help="count ARM instructions from gapless_mp3_probe entry through return")
    args = parser.parse_args()
    with args.linked_elf.open("rb") as file:
        check(file, args.lame, args.profile_probe)
