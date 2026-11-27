"""Refine pinned r25 without relocating working playback, UI or resources."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile
from letters_patch import grouping_table, reference_image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'gapless'))
from image_layout import repack, word

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r25.ipsw'
SOURCE_SHA = 'ed00b7b0dea5cc36d3fdb9f285addc901d85bb16c156918f076d0984f94d0c65'
MEMBER = 'Firmware-4.3.1.1'
PLAYBACK = REPO / 'build/gapless-complete-drain/playback.elf'
ENTRIES = {'letters_index_compact': 0x35ba78, 'letters_draw': 0x35c304}
COPY_AT, COPY_SIZE = 0x359d90, 64
PUBLISH_AT = 0x35944c


def archive(path):
    with ZipFile(path) as z:
        infos = z.infolist()
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError('duplicate archive member')
        return infos, {i.filename: z.read(i) for i in infos}


def compile_code(directory, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository virtualenv')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    table = grouping_table(reference_image(REPO / 'iPod_25.1.3.ipsw'))
    (directory / 'letter_map.h').write_text(
        'static const u8 letter_map[512] = {' + ','.join(map(str, table)) + '};\n')
    for source, name in ((REPO / 'navigation_qol/letters.c', 'letters'),
                         (REPO / 'gapless/publish_overflow_guard.S', 'guard'),
                         (REPO / 'gapless/copy_frames.S', 'copy')):
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-ffunction-sections', '-fdata-sections',
                        '-Wall', '-Wextra', '-Werror', '-I', str(directory), '-c', str(source),
                        '-o', str(directory / (name + '.o'))], check=True)
    script = directory / 'refinements.ld'
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } } '
                      'gapless_r25_publish_resume = 0x359450; '
                      'gapless_r25_position = 0x1452f0d4; gapless_r25_enabled = 0x1452f0b8; '
                      'ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no added state")')
    elf_path = directory / 'refinements.elf'
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '--gc-sections', '-T', str(script),
                    '-e', 'letters_index_compact', '-u', 'letters_draw',
                    '-u', 'gapless_publish_overflow_guard', '-o', str(elf_path),
                    str(directory / 'letters.o'), str(directory / 'guard.o')], check=True)
    raw = elf_path.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    section = elf.get_section_by_name('.text')
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] == 'STT_FUNC' and s['st_shndx'] != 'SHN_UNDEF'}
    if section['sh_addr'] != base or section['sh_size'] > 4096:
        raise ValueError('unexpected refinement code layout')
    copy_elf = ELFFile(io.BytesIO((directory / 'copy.o').read_bytes()))
    copy_code = copy_elf.get_section_by_name('.text').data()
    if len(copy_code) != COPY_SIZE or any(s['sh_type'] in ('SHT_REL', 'SHT_RELA') and s['sh_size']
                                         for s in copy_elf.iter_sections()):
        raise ValueError('copy helper must fit its reviewed slot without relocations')
    return section.data(), symbols, raw, copy_code


def patched_elf(raw, image):
    elf = ELFFile(io.BytesIO(raw))
    section = elf.get_section_by_name('.text')
    offset, at, size = section['sh_offset'], section['sh_addr'], section['sh_size']
    result = bytearray(raw)
    result[offset:offset + size] = image[at:at + size]
    return bytes(result)


def build_candidate(source, stock, output, playback=PLAYBACK):
    artifacts = output.with_suffix('.artifacts')
    if output.exists() or output.is_symlink():
        raise ValueError('output already exists; preserve previous candidates')
    if artifacts.exists() or artifacts.is_symlink():
        raise ValueError('artifacts already exist; preserve previous evidence')
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('expected pinned r25 archive')
    infos, members = archive(source)
    _, original = archive(stock)
    if members.keys() != original.keys() or any(members[k] != original[k] for k in members if k != MEMBER):
        raise ValueError('archive metadata differs from stock')
    firmware = members[MEMBER]
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    image = firmware[start:start + size]
    if firmware != repack(original[MEMBER], image) or image[-4:] != bytes.fromhex('55aa55aa'):
        raise ValueError('invalid source repack')
    carried = {}
    for name in ('letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro', 'playback'):
        path = playback if name == 'playback' else source.with_suffix('.artifacts') / (name + '.elf')
        raw = path.read_bytes()
        elf = ELFFile(io.BytesIO(raw))
        for section in elf.iter_sections():
            if section['sh_flags'] & 2 and section['sh_type'] != 'SHT_NOBITS':
                at, count = section['sh_addr'], section['sh_size']
                if image[at:at + count] != section.data():
                    raise ValueError(f'{name} ELF differs from source image')
        carried[name + '.elf'] = raw
        if name == 'playback':
            symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
            if any(symbols.get(key) != value for key, value in (
                    ('copy_frames', COPY_AT), ('gapless_trim_publish', PUBLISH_AT),
                    ('producer.7', 0x1452f0d4), ('producer.0', 0x1452f0b8))):
                raise ValueError('unexpected released playback ABI')
            if word(image, PUBLISH_AT) != 0xe92d4ff0:
                raise ValueError('unexpected publisher prologue')
        if name != 'playback':
            carried[name + '-symbols.json'] = (source.with_suffix('.artifacts') /
                                              (name + '-symbols.json')).read_bytes()
    for name in ('pomodoro-layout.json', 'pomodoro-edits.json'):
        carried[name] = (source.with_suffix('.artifacts') / name).read_bytes()
    with TemporaryDirectory() as temporary:
        code, symbols, elf, copy = compile_code(Path(temporary), len(image) - 4)
    candidate = bytearray(image[:-4] + code + image[-4:])
    for name, at in {**ENTRIES, 'gapless_publish_overflow_guard': PUBLISH_AT}.items():
        if name in ENTRIES and json.loads(carried['letters-symbols.json'])[name] != at:
            raise ValueError('unexpected original helper address')
        delta = symbols[name] - at - 8
        if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
            raise ValueError('refinement branch outside ARM range')
        struct.pack_into('<I', candidate, at, 0xea000000 | ((delta >> 2) & 0xffffff))
    candidate[COPY_AT:COPY_AT + len(copy)] = copy
    struct.pack_into('<I', candidate, 0xe8, len(candidate))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', candidate, at, 0x04000000 + len(candidate) - 4)
    if len(candidate) > 0x390000:
        raise ValueError('refinements overlap initialized RAM')
    for name in ('letters.elf', 'playback.elf'):
        carried[name] = patched_elf(carried[name], candidate)
    carried['refinements.elf'] = elf
    carried['refinements-symbols.json'] = (json.dumps(symbols, indent=2) + '\n').encode()
    members[MEMBER] = bytes(repack(original[MEMBER], candidate))
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir()
    for name, raw in carried.items():
        (artifacts / name).write_bytes(raw)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / 'candidate.ipsw'
        with ZipFile(staging, 'w') as z:
            for info in infos:
                z.writestr(info, members[info.filename])
        if archive(staging)[1] != members:
            raise ValueError('archive readback differs')
        os.link(staging, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (artifacts / 'build.json').write_text(json.dumps(dict(
        source_sha256=SOURCE_SHA, sha256=digest, osos_sha256=hashlib.sha256(candidate).hexdigest(),
        osos_bytes=len(candidate), headroom=0x390000 - len(candidate),
        refinement_code_bytes=len(code), copy_code_bytes=len(copy), flashed=False), indent=2) + '\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--playback-elf', type=Path, default=PLAYBACK)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build_candidate(args.source, args.stock, args.output, args.playback_elf))
