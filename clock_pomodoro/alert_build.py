"""Add shared alarm backlight and its native Settings toggle to pinned r26."""
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
sys.path[:0] = [str(REPO / 'navigation_qol'), str(REPO / 'menu_scroll'), str(REPO / 'gapless')]
from optimize_build import archive
from image_layout import repack, word
from verify_resources import directory, records, ROOT

SOURCE = REPO / 'build/navigation-qol-complete/iPod_4.3.1.1-gapless-navigation-complete-r26.ipsw'
SOURCE_SHA = '1e113058612a0b66dfc32ce436ea5388d725e0db0b757083b969ae0bb717246a'
MEMBER = 'Firmware-4.3.1.1'
HOOKS = ((0x159a0c, 0x1bfd003a, 'alarm_wake', True),
         (0x159a84, 0x1bfd001c, 'alarm_wake', True),
         (0x73e20, 0xe3a00000, 'alarm_get_hook', False),
         (0xccddc, 0xe3a08000, 'alarm_set_hook', False),
         (0xbeb08, 0xe5900058, 'alarm_home_reset', False))


def compile_code(directory, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository virtualenv')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    objects = []
    for name in ('alarm_light.c', 'alarm_light_hooks.S'):
        output = directory / (name + '.o')
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                        '-c', str(REPO / 'clock_pomodoro' / name), '-o', str(output)], check=True)
        objects.append(str(output))
    script, output = directory / 'alarm_light.ld', directory / 'alarm_light.elf'
    script.write_text(f'SECTIONS {{ .text {base} : {{ *(.text*) *(.rodata*) . = ALIGN(4); }} '
                      '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } } '
                      'ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "no added state")')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(script), '-e', 'alarm_wake',
                    '-o', str(output), *objects], check=True)
    raw = output.read_bytes()
    elf = ELFFile(io.BytesIO(raw))
    section = elf.get_section_by_name('.text')
    if section['sh_addr'] != base or section['sh_size'] > 2048:
        raise ValueError('unexpected alarm code layout')
    symbols = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()
               if s['st_info']['type'] == 'STT_FUNC' and s['st_shndx'] != 'SHN_UNDEF'}
    return section.data(), raw, symbols


def install_resources(image, output):
    groups = directory(image)
    if any(0x6150 in entries for _, entries in groups.values()):
        raise ValueError('alarm property/caption key already allocated')
    rows = records(groups['Menu'][1][0x278d])
    if len(rows) != 16 or word(rows[5], 24) != 0x27a3:
        raise ValueError('unexpected Settings order')
    row = bytearray(rows[3])  # native immediate setting cycle, command2155
    for at, value in ((16, 0), (20, 0x6150), (24, 0x6150), (32, 0x6150)):
        struct.pack_into('<I', row, at, value)
    changed = {'Menu': {0x278d: struct.pack('<I', 17) + b''.join(rows[:6] + [row] + rows[6:])},
               'Str ': {0x6150: b'Backlight Alerts\0'}}
    base = ROOT + word(image, ROOT + 4)
    edited = []
    for tag, resources in changed.items():
        header, original = groups[tag]
        table = ROOT + word(image, header + 12)
        entries = {key: (offset, size) for key, offset, size in struct.iter_unpack(
            '<III', image[table:table + 12 * len(original)])}
        for key, data in resources.items():
            output.extend(bytes(-len(output) % 4))
            entries[key] = (len(output) - base, len(data))
            output.extend(data)
        output.extend(bytes(-len(output) % 4))
        at = len(output)
        for key, (offset, size) in sorted(entries.items()):
            output.extend(struct.pack('<III', key, offset, size))
        struct.pack_into('<I', output, header + 4, len(entries))
        struct.pack_into('<I', output, header + 12, at - ROOT)
        edited += [header + 4, header + 12]
    return edited


def build_candidate(source, stock, output):
    artifacts = output.with_suffix('.artifacts')
    if output.exists() or output.is_symlink():
        raise ValueError('output already exists; preserve previous candidates')
    if artifacts.exists() or artifacts.is_symlink():
        raise ValueError('artifacts already exist; preserve previous evidence')
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('expected pinned r26 archive')
    infos, members = archive(source)
    _, original = archive(stock)
    if members.keys() != original.keys() or any(members[k] != original[k] for k in members if k != MEMBER):
        raise ValueError('archive metadata differs from stock')
    firmware = members[MEMBER]
    start, size = 512 + word(firmware, 0x420c), word(firmware, 0x4210)
    image = firmware[start:start + size]
    if firmware != repack(original[MEMBER], image) or image[-4:] != bytes.fromhex('55aa55aa'):
        raise ValueError('invalid source repack')
    source_art = source.with_suffix('.artifacts')
    carried = {}
    for name in ('letters', 'actions', 'volume', 'hotpaths', 'otg', 'pomodoro', 'playback', 'refinements'):
        raw = (source_art / (name + '.elf')).read_bytes()
        elf = ELFFile(io.BytesIO(raw))
        for section in elf.iter_sections():
            if section['sh_flags'] & 2 and section['sh_type'] != 'SHT_NOBITS':
                at, count = section['sh_addr'], section['sh_size']
                if image[at:at + count] != section.data():
                    raise ValueError(f'{name} ELF differs from source image')
        carried[name + '.elf'] = raw
        if (source_art / (name + '-symbols.json')).exists():
            carried[name + '-symbols.json'] = (source_art / (name + '-symbols.json')).read_bytes()
    for name in ('pomodoro-layout.json', 'pomodoro-edits.json'):
        carried[name] = (source_art / name).read_bytes()
    with TemporaryDirectory() as temporary:
        code, elf, symbols = compile_code(Path(temporary), len(image) - 4)
    candidate = bytearray(image[:-4] + code)
    edited = []
    for at, expected, symbol, link in HOOKS:
        if word(image, at) != expected:
            raise ValueError(f'unexpected original alarm hook at {at:#x}')
        delta = symbols[symbol] - at - 8
        if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
            raise ValueError('alarm branch outside ARM range')
        # Preserve BLNE: wake=false alarm records must never enter the wrapper.
        branch = (expected & 0xf0000000) | (0x0b000000 if link else 0x0a000000)
        struct.pack_into('<I', candidate, at, branch | ((delta >> 2) & 0xffffff))
        edited.append(at)
    edited += install_resources(image, candidate)
    candidate.extend(bytes.fromhex('55aa55aa'))
    struct.pack_into('<I', candidate, 0xe8, len(candidate))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', candidate, at, 0x04000000 + len(candidate) - 4)
    edited += [0xe8, 0x2e8, 0x4a8]
    if len(candidate) > 0x390000:
        raise ValueError('alarm resources overlap initialized RAM')
    carried['alarm_light.elf'] = elf
    carried['alarm_light-symbols.json'] = (json.dumps(symbols, indent=2) + '\n').encode()
    carried['alarm-light-edits.json'] = (json.dumps(edited) + '\n').encode()
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
        alarm_code_bytes=len(code), flashed=False), indent=2) + '\n')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--stock', type=Path, default=REPO / 'iPod_4.3.1.1.ipsw')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(args.output)
    print('SHA-256', build_candidate(args.source, args.stock, args.output))
