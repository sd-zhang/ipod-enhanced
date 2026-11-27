"""Add wheel-delivery recovery and an Albums index fast path to pinned r32."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'clock_pomodoro'))
from pattern_build import MEMBER, read_archive, repack, word, branch

SOURCE = ROOT / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r32.ipsw'
SOURCE_SHA = 'cffbdada260b58c842a61eca9c4ff30c57f2718000712ebdc677537512bc9f10'
HOOKS = ((0xb13d8, 0xebffe9fb, 'wheel_post'),
         (0x8f668, 0xeb008841, 'wheel_reject'),
         (0x4a450, 0xea0c497c, 'album_index_accept'))


def compile_code(directory, base):
    zig = Path(next(iter(importlib.util.find_spec('ziglang').submodule_search_locations))) / 'zig'
    obj, script, elf_path = (directory / name for name in ('wheel.o', 'wheel.ld', 'wheel_recovery.elf'))
    subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                    '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                    '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                    '-c', str(ROOT / 'navigation_qol/wheel_recovery.c'), '-o', str(obj)], check=True)
    album_obj = directory / 'album.o'
    subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                    '-c', str(ROOT / 'navigation_qol/album_index_fast.S'),
                    '-o', str(album_obj)], check=True)
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      '.bss (NOLOAD) : { *(.bss*) *(COMMON) } .data : { *(.data*) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } } '
                      'ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no added state")')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(script),
                    '-e', 'wheel_post', '-o', str(elf_path), str(obj), str(album_obj)], check=True)
    raw = elf_path.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    code = elf.get_section_by_name('.text')
    if code['sh_addr'] != base or not 0 < code['sh_size'] <= 512:
        raise ValueError('unexpected wheel overlay layout')
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] == 'STT_FUNC' and s['st_shndx'] != 'SHN_UNDEF'}
    return code.data(), raw, symbols


def build(output):
    from verify_lcd_package import validated_parent
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('r32 source pin')
    validated_parent(SOURCE)
    artifacts = output.with_suffix('.artifacts')
    if any(p.exists() or p.is_symlink() for p in (output, artifacts)):
        raise ValueError('output exists; preserve previous evidence')
    infos, members, comment = read_archive(SOURCE)
    stock = read_archive(ROOT / 'iPod_4.3.1.1.ipsw')[1][MEMBER]
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

    for at, expected, name in HOOKS:
        replace(at, expected, branch(at, symbols[name], expected >> 24))
    replace(0xe8, len(image), len(candidate))
    for at in (0x2e8, 0x4a8):
        replace(at, 0x04000000 + len(image) - 4, 0x04000000 + len(candidate) - 4)
    if len(candidate) > 0x390000:
        raise ValueError('wheel overlay overlaps RAM')
    members[MEMBER] = bytes(repack(stock, candidate))
    carried = {p.name: p.read_bytes()
               for pattern in ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json')
               for p in SOURCE.with_suffix('.artifacts').glob(pattern)}
    carried.update({'wheel_recovery.elf': raw,
                    'wheel_recovery-symbols.json': (json.dumps(symbols, indent=2)+'\n').encode(),
                    'wheel_recovery-edits.json': (json.dumps(edits)+'\n').encode()})
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
        osos_bytes=len(candidate), headroom=0x390000-len(candidate),
        overlay_code_bytes=len(code),
        wheel_code_bytes=symbols['album_index_accept']-symbols['wheel_post'],
        album_code_bytes=len(code)-(symbols['album_index_accept']-symbols['wheel_post']),
        added_state_bytes=0, flashed=False), indent=2)+'\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build(args.output))
