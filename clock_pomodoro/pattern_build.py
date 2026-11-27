"""Append stock Pomodoro sound and synchronized alarm light to pinned r28."""
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

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'gapless'))
from image_layout import repack, word
from label_build import read_archive

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r28.ipsw'
SOURCE_SHA = '92cca44cef2e0912030fbdfc246fcea8026e024b189f2b146dc81ec697b5750b'
MEMBER = 'Firmware-4.3.1.1'
BSS = 0x14534654
CALLS = (0x158c74, 0x158cb8, 0x158cc4, 0x158cd0, 0x158cdc,
         0x158ce8, 0x158cf4, 0x158d00, 0x158d0c)
HOOKS = ((0xaf77c, 0xe7915106, 'alarm_duration_hook', 0xeb),
         (0xaf7b0, 0xe5810008, 'alarm_timer_hook', 0xeb),
         (0x1596d8, 0xebfd5813, 'alarm_advance_hook', 0xeb),
         (0xaf764, 0xebfec3e6, 'alarm_finish_hook', 0xeb),
         (0x159658, 0x05857008, 'alarm_cancel_hook', 0x0b))


def branch(at, target, opcode=0xeb):
    delta = target - at - 8
    if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError('alarm branch outside ARM range')
    return (opcode << 24) | ((delta >> 2) & 0xffffff)


def compile_code(directory, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository virtualenv')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    objects = []
    for source in ('alarm_pattern.c', 'alarm_pattern_hooks.S'):
        output = directory / (source + '.o')
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                        '-c', str(REPO / 'clock_pomodoro' / source), '-o', str(output)], check=True)
        objects.append(str(output))
    script, output = directory / 'alarm_pattern.ld', directory / 'alarm_pattern.elf'
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      f'.bss {BSS} (NOLOAD) : {{ *(.bss*) *(COMMON) }} '
                      '.data : { *(.data*) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } } '
                      'ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 4, "one owned state word")')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(script),
                    '-e', 'pomo_alarm_pattern', '-o', str(output), *objects], check=True)
    raw = output.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    code, state = elf.get_section_by_name('.text'), elf.get_section_by_name('.bss')
    if code['sh_addr'] != base or code['sh_size'] > 4096 or state['sh_addr'] != BSS:
        raise ValueError('unexpected alarm pattern layout')
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT') and s['st_shndx'] != 'SHN_UNDEF'}
    return code.data(), raw, symbols


def build_candidate(source, stock, output):
    artifacts = output.with_suffix('.artifacts')
    for path in (output, artifacts):
        if path.exists() or path.is_symlink():
            raise ValueError(f'output already exists; preserve previous evidence: {path}')
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('expected pinned hardware-tested r28 archive')
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
                'playback', 'refinements', 'alarm_light', 'alac'}
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
    with TemporaryDirectory() as temporary:
        code, raw, symbols = compile_code(Path(temporary), len(image) - 4)
    candidate = bytearray(image[:-4] + code + image[-4:])
    edits = []

    def replace(at, expected, value):
        if word(image, at) != expected:
            raise ValueError(f'unexpected source instruction/value at {at:#x}')
        struct.pack_into('<I', candidate, at, value)
        edits.append(at)

    for at in CALLS:
        replace(at, branch(at, 0xa6e6c), branch(at, symbols['alarm_tone_request']))
    for at, expected, name, opcode in HOOKS:
        replace(at, expected, branch(at, symbols[name], opcode))
    # r28 Pomodoro loads its beep callee from this private literal. Only the
    # callee changes; phase/rearm/lock/runtime/UI code remains byte-identical.
    replace(0x384338, 0xa6e6c, symbols['pomo_alarm_pattern'])
    replace(0x4b0, BSS, BSS + 4)
    replace(0x20428, 0xcd744, 0xcd748)
    replace(0xe8, len(image), len(candidate))
    for at in (0x2e8, 0x4a8):
        replace(at, 0x04000000 + len(image) - 4, 0x04000000 + len(candidate) - 4)
    if len(candidate) > 0x390000:
        raise ValueError('alarm code overlaps initialized RAM')
    pomo = bytearray(carried['pomodoro.elf'])
    section = ELFFile(io.BytesIO(pomo)).get_section_by_name('.text')
    at = section['sh_offset'] + 0x384338 - section['sh_addr']
    pomo[at:at + 4] = candidate[0x384338:0x38433c]
    carried['pomodoro.elf'] = bytes(pomo)
    carried['alarm_pattern.elf'] = raw
    carried['alarm_pattern-symbols.json'] = (json.dumps(symbols, indent=2) + '\n').encode()
    carried['alarm_pattern-edits.json'] = (json.dumps(edits) + '\n').encode()
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
        alarm_code_bytes=len(code), added_state_bytes=4, flashed=False), indent=2) + '\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build_candidate(args.source, args.stock, args.output))
