"""Append the ALAC boundary fix to the pinned r27 label candidate."""
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
from image_layout import repack, word

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'clock_pomodoro'))
from label_build import read_archive

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-label-review-01.ipsw'
SOURCE_SHA = '363d0d31508f17f1868572da76a8f3de3db4bbf56dad49ea2d39ba8ea618b627'
MEMBER = 'Firmware-4.3.1.1'
ENTRIES = {'gapless_trim_start': 0x358b20, 'gapless_trim_finish_begin': 0x358f4c,
           'gapless_trim_publish': 0x35944c, 'gapless_trim_output': 0x359920}
STATE = {'empty_ready': (0x1452f0a4, 4), 'empty_queue': (0x1452f0a8, 4),
         'empty_ring': (0x1452f0ac, 4), 'empty_mask': (0x1452f0b0, 4),
         'notes': (0x1452f108, 896), 'fifo': (0x1452f494, 19968),
         'fifo_len': (0x1452f490, 4), 'output_started': (0x1452f48c, 1),
         'synthetic_output': (0x1452f488, 1), 'finish_engine': (0x1452f0f0, 4),
         'finish_blocks': (0x1452f0f4, 4), 'finish_ring': (0x1452f0f8, 4),
         'finish_capacity': (0x1452f0fc, 4), 'finish_frames': (0x1452f100, 4),
         'finish_drop': (0x1452f104, 4), 'gapless_engine_timing': (0x1452f080, 36)}
FUNCTIONS = {'copy_frames': 0x359d90, 'gapless_output_tx': 0x357678,
             'gapless_start_mp3_clip': 0x3570bc}


def check_abi(raw):
    elf = ELFFile(io.BytesIO(raw))
    symbols = {s.name: (s['st_value'], s['st_size'])
               for s in elf.get_section_by_name('.symtab').iter_symbols()}
    expected = dict(STATE)
    for index in range(14):
        expected[f'producer.{index}'] = (0x1452f0b8 + index * 4,
                                       1 if index in (0, 1, 2, 10) else 4)
    for key, value in expected.items():
        if symbols.get(key) != value:
            raise ValueError(f'changed playback state ABI: {key}')
    for key, value in {**ENTRIES, **FUNCTIONS}.items():
        if symbols.get(key, (None,))[0] != value:
            raise ValueError(f'changed playback function ABI: {key}')
    # A widened access to the old one-byte boolean must touch padding only.
    widened = [value[0] for value in expected.values() if value[1] == 1]
    for at in widened:
        if any(at < value[0] < at + 4 for value in symbols.values()):
            raise ValueError('shared boolean has no three-byte padding')


def compile_code(directory, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository virtualenv')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    objects = []
    for source in ('trim.c', 'alac_hooks.S'):
        output = directory / (source + '.o')
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-ffunction-sections', '-fdata-sections',
                        '-Wall', '-Wextra', '-Werror', '-DGAPLESS_ALAC_OVERLAY',
                        '-c', str(REPO / 'gapless' / source), '-o', str(output)], check=True)
        objects.append(str(output))
    bindings = {key: value[0] for key, value in STATE.items()}
    bindings.update(FUNCTIONS)
    bindings['producer'] = 0x1452f0b8
    script, output = directory / 'alac.ld', directory / 'alac.elf'
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }\n' +
                      '\n'.join(f'{key} = {value};' for key, value in bindings.items()) +
                      '\nASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no added state")\n')
    keep = [part for key in ENTRIES for part in ('-u', key)]
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '--gc-sections', '-T', str(script),
                    '-e', 'gapless_alac_decoded_hook', *keep, '-o', str(output), *objects], check=True)
    raw = output.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    section = elf.get_section_by_name('.text')
    if section['sh_addr'] != base or section['sh_size'] > 12000:
        raise ValueError('unexpected ALAC code layout')
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] == 'STT_FUNC' and s['st_shndx'] != 'SHN_UNDEF'}
    return section.data(), raw, symbols


def branch(at, target, link=False):
    delta = target - at - 8
    if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError('ALAC branch outside ARM range')
    return (0xeb000000 if link else 0xea000000) | ((delta >> 2) & 0xffffff)


def build_candidate(source, stock, output):
    artifacts = output.with_suffix('.artifacts')
    for path in (output, artifacts):
        if path.exists() or path.is_symlink():
            raise ValueError(f'output already exists; preserve previous evidence: {path}')
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('expected pinned r27 label archive')
    infos, members, comment = read_archive(source)
    _, original, _ = read_archive(stock)
    if members.keys() != original.keys() or any(members[k] != original[k] for k in members if k != MEMBER):
        raise ValueError('unrelated archive members differ from stock')
    firmware = members[MEMBER]
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    image = firmware[start:start + size]
    if firmware != repack(original[MEMBER], image) or image[-4:] != bytes.fromhex('55aa55aa'):
        raise ValueError('invalid source repack')
    carried = {p.name: p.read_bytes()
               for pattern in ('*.elf', '*-symbols.json', '*-layout.json', '*-edits.json')
               for p in source.with_suffix('.artifacts').glob(pattern)}
    expected = {'letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro',
                'playback', 'refinements', 'alarm_light'}
    if {Path(name).stem for name in carried if name.endswith('.elf')} != expected:
        raise ValueError('missing or unexpected source ELF artifacts')
    for name, raw in carried.items():
        if not name.endswith('.elf'):
            continue
        elf = ELFFile(io.BytesIO(raw))
        for section in elf.iter_sections():
            if section['sh_flags'] & 2 and section['sh_type'] != 'SHT_NOBITS':
                at, count = section['sh_addr'], section['sh_size']
                if image[at:at + count] != section.data():
                    raise ValueError(f'{name} ELF differs from source')
    check_abi(carried['playback.elf'])
    if word(image, 0x4f540) != 0xe5942058:
        raise ValueError('unexpected ALAC successful-decode instruction')
    with TemporaryDirectory() as temporary:
        code, raw, symbols = compile_code(Path(temporary), len(image) - 4)
    candidate = bytearray(image[:-4] + code + image[-4:])
    edits = []
    for name, at in ENTRIES.items():
        struct.pack_into('<I', candidate, at, branch(at, symbols[name]))
        edits.append(at)
    struct.pack_into('<I', candidate, 0x4f540, branch(0x4f540, symbols['gapless_alac_decoded_hook'], True))
    edits.append(0x4f540)
    struct.pack_into('<I', candidate, 0xe8, len(candidate))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', candidate, at, 0x04000000 + len(candidate) - 4)
    edits += [0xe8, 0x2e8, 0x4a8]
    if len(candidate) > 0x390000:
        raise ValueError('ALAC code overlaps initialized RAM')
    playback = bytearray(carried['playback.elf'])
    elf = ELFFile(io.BytesIO(playback))
    section = elf.get_section_by_name('.text')
    at, count, offset = section['sh_addr'], section['sh_size'], section['sh_offset']
    playback[offset:offset + count] = candidate[at:at + count]
    carried['playback.elf'] = bytes(playback)
    carried['alac.elf'] = raw
    carried['alac-symbols.json'] = (json.dumps(symbols, indent=2) + '\n').encode()
    carried['alac-edits.json'] = (json.dumps(edits) + '\n').encode()
    members[MEMBER] = bytes(repack(original[MEMBER], candidate))
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
        osos_bytes=len(candidate), headroom=0x390000 - len(candidate),
        alac_code_bytes=len(code), added_state_bytes=0, flashed=False), indent=2) + '\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build_candidate(args.source, args.stock, args.output))
