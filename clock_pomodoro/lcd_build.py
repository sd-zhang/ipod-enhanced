"""Add LCD transaction serialization to the pinned, hardware-tested r31."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
from pattern_build import REPO, MEMBER, read_archive, repack, word, branch

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r31.ipsw'
SOURCE_SHA = '5f016dad9fb588de755d6cbfeb94360d4d592a65e34d43f0a6e7073e24f02aa7'
BSS = 0x14534658
HOOKS = ((0x3973c, 0xeb025739, 'lcd_display_start', 0xeb),
         (0xb27e0, 0xebff5547, 'lcd_pool_reset', 0xeb),
         (0xc3f7c, 0xe92d47f0, 'lcd_redraw', 0xea),
         (0x1549b4, 0xe92d40f8, 'lcd_init', 0xea),
         (0x154bd4, 0xe59f0034, 'lcd_display_mode', 0xea),
         (0x154c94, 0xe59f0024, 'lcd_suspend', 0xea),
         (0x154cc4, 0xe59f10d8, 'lcd_power', 0xea),
         (0x154e20, 0xe92d4070, 'lcd_contrast', 0xea))


def compile_code(directory, base):
    zig = Path(next(iter(importlib.util.find_spec('ziglang').submodule_search_locations))) / 'zig'
    objects = []
    for source in ('lcd_serialization.c', 'lcd_serialization.S'):
        output = directory / (source + '.o')
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                        '-c', str(REPO / 'clock_pomodoro' / source), '-o', str(output)], check=True)
        objects.append(str(output))
    script, output = directory / 'lcd.ld', directory / 'lcd.elf'
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      f'.bss {BSS} (NOLOAD) : {{ *(.bss*) *(COMMON) }} '
                      '.data : { *(.data*) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } } '
                      'ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 4, "one owned mutex ID")')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(script),
                    '-e', 'lcd_display_start', '-o', str(output), *objects], check=True)
    raw = output.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    code, state = elf.get_section_by_name('.text'), elf.get_section_by_name('.bss')
    if code['sh_addr'] != base or code['sh_size'] > 2048 or state['sh_addr'] != BSS:
        raise ValueError('unexpected LCD overlay layout')
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT') and s['st_shndx'] != 'SHN_UNDEF'}
    return code.data(), raw, symbols


def build(output):
    from verify_pattern_package import validated_base
    validated_base(SOURCE)
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('r31 source pin')
    artifacts = output.with_suffix('.artifacts')
    if any(p.exists() or p.is_symlink() for p in (output, artifacts)):
        raise ValueError('output exists; preserve previous evidence')
    infos, members, comment = read_archive(SOURCE)
    stock = read_archive(REPO / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
    firmware = members[MEMBER]
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    image = firmware[start:start + size]
    with TemporaryDirectory() as temporary:
        code, raw, symbols = compile_code(Path(temporary), len(image) - 4)
    candidate = bytearray(image[:-4] + code + image[-4:])
    edits = []

    def replace(at, expected, value):
        if word(image, at) != expected:
            raise ValueError(f'unexpected source word at {at:#x}')
        struct.pack_into('<I', candidate, at, value)
        edits.append(at)

    for at, expected, name, opcode in HOOKS:
        replace(at, expected, branch(at, symbols[name], opcode))
    replace(0x4b0, BSS, BSS + 4)
    replace(0x20428, 0xcd748, 0xcd74c)
    replace(0xe8, len(image), len(candidate))
    for at in (0x2e8, 0x4a8):
        replace(at, 0x04000000 + len(image) - 4, 0x04000000 + len(candidate) - 4)
    if len(candidate) > 0x390000:
        raise ValueError('LCD overlay overlaps RAM')
    members[MEMBER] = bytes(repack(stock, candidate))
    carried = {p.name: p.read_bytes()
               for pattern in ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json')
               for p in SOURCE.with_suffix('.artifacts').glob(pattern)}
    carried.update({'lcd.elf': raw, 'lcd-symbols.json': (json.dumps(symbols, indent=2)+'\n').encode(),
                    'lcd-edits.json': (json.dumps(edits)+'\n').encode()})
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / 'candidate.ipsw'
        with ZipFile(staging, 'w') as archive:
            archive.comment = comment
            for info in infos:
                archive.writestr(info, members[info.filename])
        if read_archive(staging)[1:] != (members, comment):
            raise ValueError('archive readback differs')
        artifacts.mkdir()
        for name, value in carried.items():
            (artifacts / name).write_bytes(value)
        os.link(staging, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (artifacts / 'build.json').write_text(json.dumps(dict(
        source_sha256=SOURCE_SHA, sha256=digest, osos_sha256=hashlib.sha256(candidate).hexdigest(),
        osos_bytes=len(candidate), headroom=0x390000-len(candidate), lcd_code_bytes=len(code),
        added_state_bytes=4, total_alarm_state_bytes=8, flashed=False), indent=2)+'\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build(args.output))
