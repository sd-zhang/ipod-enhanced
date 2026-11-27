"""Compile and install ARMv4T letter navigation over the pinned two-feature image."""
import hashlib
import importlib.util
import json
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from patch import branch
from research_letters import CANDIDATE_SHA, reference_image

SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parent
HOOKS = [
    (0x4a2c8, 0xeb014584, "letters_index_alloc", True),
    (0x4a450, 0xe1a00004, "letters_index_accept", False),
    (0x4a46c, 0xe5950a70, "letters_index_finish", False),
    (0xb6ec4, 0xe59d0414, "letters_import_sort_hook", False),
    (0x866a4, 0xeb01ae8b, "letters_alloc_hook", False),
    (0x86700, 0xe3550000, "letters_wheel_hook", False),
    (0x103180, 0x1bfff17c, "letters_accel_destroy", True),
]
# The native pool rounds both logical sizes to the same 256-byte slot. Own
# its last word explicitly and include it in native whole-track clones.
TRACK_SIZES = [(0x8bf18, 0xe3a000fc, 0xe3a00c01),
               (0xda208, 0xe3a020fc, 0xe3a02c01)]
VTABLE = {
    0x24: (0x8150c, "letters_event"),
    0x60: (0x5cdc4, "letters_draw"),
    0x94: (0xa4d80, "letters_visibility"),
    0x160: (0x7f104, "letters_key"),
}


def grouping_table(reference):
    """Read the pinned 5G's boot-copy data and A–Z/digit variant strings."""
    regions = []
    for entry in (0x84058, 0x8406c, 0x84080, 0x84094, 0x840a8, 0x840bc):
        source, dest, size = struct.unpack_from("<3I", reference, entry)
        regions.append((dest, reference[source & 0x3ffffff:(source & 0x3ffffff) + size]))

    def read(at, size):
        if at < len(reference):
            return reference[at:at + size]
        for dest, data in regions:
            if dest <= at and at + size <= dest + len(data):
                return data[at - dest:at - dest + size]
        raise ValueError(f"unmapped reference data {at:#x}")

    result = bytearray(512)
    for array, first in ((0x1083184c, ord("A")), (0x108318b8, ord("0"))):
        for i in range(27):
            pointer, = struct.unpack("<I", read(array + 4 * i, 4))
            if not pointer:
                break
            data = bytearray()
            while (byte := read(pointer + len(data), 1)) != b"\0":
                data.extend(byte)
                if len(data) > 2048:
                    raise ValueError("unterminated variant string")
            for char in data.decode("utf-8"):
                if ord(char) >= 512:
                    raise ValueError("unexpected 5G grouping-table extent")
                result[ord(char)] = first + i
    return result


def compile_code(directory, base, clang="clang", linker=None):
    if linker is None:
        spec = importlib.util.find_spec("ziglang")
        if spec is None or not spec.submodule_search_locations:
            raise ValueError("install ziglang in the repository's uv environment")
        linker = [str(Path(next(iter(spec.submodule_search_locations))) / "zig"), "ld.lld"]
    table = grouping_table(reference_image(REPO / "iPod_25.1.3.ipsw"))
    (directory / "letter_map.h").write_text(
        "static const u8 letter_map[512] = {" + ",".join(map(str, table)) + "};\n")
    objects = []
    for name in ("letters.c", "sort_letters.c", "letters_hooks.S"):
        obj = directory / (name + ".o")
        subprocess.run([clang, "--target=armv4t-none-eabi", "-mcpu=arm7tdmi", "-marm",
                        "-Os", "-ffreestanding", "-fno-builtin", "-fno-unwind-tables",
                        "-fno-asynchronous-unwind-tables", "-Wall", "-Wextra", "-Werror",
                        "-I", str(directory), "-c", str(SOURCE / name), "-o", str(obj)], check=True)
        objects.append(str(obj))
    script = directory / "letters.ld"
    script.write_text(f"""SECTIONS {{
      . = 0x{base:x};
      .text : {{ *(.text*) *(.rodata*) }}
      .data : {{ *(.data*) }}
      .bss : {{ *(.bss*) *(COMMON) }}
      /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) }}
    }}""")
    elf = directory / "letters.elf"
    subprocess.run([*linker, "-m", "armelf", "-T", str(script), "-e", "letters_group",
                    "-o", str(elf), *objects], check=True)
    with elf.open("rb") as file:
        data = ELFFile(file)
        if any(s["sh_size"] for s in data.iter_sections() if s.name in (".data", ".bss")):
            raise ValueError("letter patch must have no writable static state")
        code = data.get_section_by_name(".text").data()
        symbols = {s.name: s["st_value"] for s in data.get_section_by_name(".symtab").iter_symbols()
                   if s.name.startswith("letters_")}
    return code, symbols


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != CANDIDATE_SHA:
        raise ValueError("expected pinned two-feature navigation OSOS")
    if image[-4:] != bytes.fromhex("55aa55aa") or len(image) % 4:
        raise ValueError("unexpected OSOS terminator")
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError("review artifact directory already exists; choose a new output path")
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        code, symbols = compile_code(directory, len(image) - 4)
        if artifacts is not None:
            artifacts.mkdir(parents=True, exist_ok=False)
            with (artifacts / "letters.elf").open("xb") as file:
                file.write((directory / "letters.elf").read_bytes())
            with (artifacts / "letters-symbols.json").open("x") as file:
                file.write(json.dumps(symbols, indent=2) + "\n")
    code += bytes(-len(code) % 4)
    output = bytearray(image[:-4] + code + image[-4:])
    for at, expected, replacement in TRACK_SIZES:
        if struct.unpack_from("<I", output, at)[0] != expected:
            raise ValueError(f"unexpected track size at {at:#x}")
        struct.pack_into("<I", output, at, replacement)
    for at, expected, symbol, link in HOOKS:
        if struct.unpack_from("<I", output, at)[0] != expected:
            raise ValueError(f"unexpected hook at {at:#x}")
        condition = expected >> 28 if link else 0xe
        struct.pack_into("<I", output, at, branch(at, symbols[symbol], condition) | (0x01000000 if link else 0))
    for slot, (original, symbol) in VTABLE.items():
        at = 0x29030 + slot
        if (0x29030 + struct.unpack_from("<I", output, at)[0]) & 0xffffffff != original:
            raise ValueError(f"unexpected list virtual method at {at:#x}")
        struct.pack_into("<I", output, at, (symbols[symbol] - 0x29030) & 0xffffffff)
    struct.pack_into("<I", output, 0xe8, len(output))
    for at in (0x2e8, 0x4a8):
        struct.pack_into("<I", output, at, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError("patch overlaps initialized RAM")
    return bytes(output)
