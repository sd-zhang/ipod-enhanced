"""Append only optimized wheel/codec code to the exact r15 firmware."""
import hashlib
import importlib.util
import io
import json
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from letters_patch import grouping_table, reference_image, REPO

SOURCE = Path(__file__).resolve().parent
SOURCE_HASH = '8801763928f4f931b5a979029f571b39ee286cce202cf5ac1ce9dcea02fdf021'
HOOKS = {0x86700: (3525360, 'letters_wheel_hook'),
         0x14fae0: (3601816, 'volume_codec')}


def branch(source, target):
    delta = target - source - 8
    if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError('hotpath hook outside ARM branch range')
    return 0xea000000 | ((delta >> 2) & 0xffffff)


def compile_code(directory, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository environment')
    linker = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    table = grouping_table(reference_image(REPO / 'iPod_25.1.3.ipsw'))
    (directory / 'letter_map.h').write_text(
        'static const u8 letter_map[512] = {' + ','.join(map(str, table)) + '};\n')
    objects = []
    for name in ('letters.c', 'volume_output.c', 'hotpath_hooks.S', 'volume_hooks.S'):
        obj = directory / (name + '.o')
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-ffunction-sections', '-fdata-sections',
                        '-Wall', '-Wextra', '-Werror', '-I', str(directory),
                        '-c', str(SOURCE / name), '-o', str(obj)], check=True)
        objects.append(str(obj))
    script, output = directory / 'hotpaths.ld', directory / 'hotpaths.elf'
    script.write_text(f'SECTIONS {{ . = 0x{base:x}; .text : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }')
    subprocess.run([str(linker), 'ld.lld', '-m', 'armelf', '--gc-sections', '-T', str(script),
                    '-e', 'letters_wheel_hook', '-u', 'volume_codec', '-o', str(output), *objects], check=True)
    raw = output.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    if any(s['sh_size'] for s in elf.iter_sections() if s.name in ('.data', '.bss')):
        raise ValueError('hotpath optimization must not use writable globals')
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] == 'STT_FUNC' and s['st_shndx'] != 'SHN_UNDEF'}
    section = elf.get_section_by_name('.text')
    if section['sh_addr'] != base or len(section.data()) > 4096:
        raise ValueError('unexpected hotpath code layout')
    if not {'letters_wheel_hook', 'letters_wheel', 'volume_codec'} <= symbols.keys():
        raise ValueError('missing hotpath entry points')
    return section.data(), symbols, raw


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != SOURCE_HASH:
        raise ValueError('expected pinned r15 navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new path')
    if image[-4:] != bytes.fromhex('55aa55aa') or len(image) % 4:
        raise ValueError('unexpected image terminator')
    with TemporaryDirectory() as temporary:
        code, symbols, elf = compile_code(Path(temporary), len(image) - 4)
    output = bytearray(image[:-4] + code + image[-4:])
    for at, (previous, symbol) in HOOKS.items():
        if struct.unpack_from('<I', image, at)[0] != branch(at, previous):
            raise ValueError(f'unexpected r15 hook at {at:#x}')
        struct.pack_into('<I', output, at, branch(at, symbols[symbol]))
    struct.pack_into('<I', output, 0xe8, len(output))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', output, at, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError('hotpath optimization overlaps initialized RAM')
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
        (artifacts / 'hotpaths.elf').write_bytes(elf)
        (artifacts / 'hotpaths-symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
    return bytes(output)
