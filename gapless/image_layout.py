"""Install linked playback code and retain both images in the 4G firmware container."""
import hashlib
import struct

from elftools.elf.elffile import ELFFile

from patch_track_metadata import STOCK_SHA256

OSOS_ENTRY, AUPD_ENTRY = 0x4200, 0x4228
SECTOR = 0x200
TEXT_BASE, BSS_BASE = 0x356f10, 0x1452f014
END_MARKER = b"\x55\xaa\x55\xaa"
STOCK_FIRMWARE = "5b5ce4e560ce03859a2ec30ca7f36ada01dd68e15ce0ca62b3bf847158378d58"


def word(image, offset):
    return struct.unpack_from("<I", image, offset)[0]


def replace_word(image, offset, expected, value):
    if word(image, offset) != expected:
        raise ValueError(f"unexpected word at OSOS offset {offset:#x}")
    struct.pack_into("<I", image, offset, value)


def branch(image, offset, expected, destination, link=False, condition=14):
    displacement = destination - (offset + 8)
    if displacement % 4 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError(f"ARM target {destination:#x} unreachable at {offset:#x}")
    opcode = (condition << 28) | (0xb if link else 0xa) << 24
    replace_word(image, offset, expected, opcode | ((displacement >> 2) & 0xffffff))


def extend_osos(image, elf_file):
    elf = ELFFile(elf_file)
    code = elf.get_section_by_name(".text")
    state = elf.get_section_by_name(".bss")
    if (elf["e_machine"] != "EM_ARM" or code["sh_addr"] != TEXT_BASE or
            state["sh_addr"] != BSS_BASE or state["sh_type"] != "SHT_NOBITS"):
        raise ValueError("unexpected ARM link layout")
    if len(image) != TEXT_BASE + 4 or image[-4:] != END_MARKER:
        raise ValueError("unexpected OSOS endpoint")
    symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
    result = bytearray(image[:TEXT_BASE]) + code.data() + END_MARKER
    if len(result) > 0x390000 or len(result) % 4 or state["sh_size"] % 4:
        raise ValueError("invalid segment bounds")
    replace_word(result, 0xe8, len(image), len(result))
    for offset in (0x2e8, 0x4a8):
        replace_word(result, offset, 0x04000000 + TEXT_BASE, 0x04000000 + len(result) - 4)
    replace_word(result, 0x20428, 0xc8104, 0xc8104 + state["sh_size"])
    replace_word(result, 0x4b0, BSS_BASE, BSS_BASE + state["sh_size"])
    return result, symbols


def unpack_stock(firmware):
    if hashlib.sha256(firmware).hexdigest() != STOCK_FIRMWARE:
        raise ValueError("expected the original Firmware-4.3.1.1 archive member")
    start = SECTOR + word(firmware, OSOS_ENTRY + 0xc)
    end = start + word(firmware, OSOS_ENTRY + 0x10)
    osos = firmware[start:end]
    if hashlib.sha256(osos).hexdigest() != STOCK_SHA256:
        raise ValueError("unexpected OSOS payload")
    return osos


def repack(firmware, osos):
    original = unpack_stock(firmware)
    if len(osos) < len(original):
        raise ValueError("expected an extended OSOS")
    start = SECTOR + word(firmware, OSOS_ENTRY + 0xc)
    extra = len(osos) - len(original)
    shift = (extra + SECTOR - 1) // SECTOR * SECTOR
    # Preserve every original byte past OSOS, including the original alignment
    # and opaque updater payload. Advance the updater's disk offset in sectors.
    result = bytearray(firmware[:start]) + osos + bytes(shift - extra) + firmware[start + len(original):]
    struct.pack_into("<I", result, OSOS_ENTRY + 0x10, len(osos))
    struct.pack_into("<I", result, OSOS_ENTRY + 0x1c, sum(osos) & 0xffffffff)
    struct.pack_into("<I", result, AUPD_ENTRY + 0xc, word(firmware, AUPD_ENTRY + 0xc) + shift)
    return result
