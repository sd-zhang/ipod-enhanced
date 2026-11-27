"""Contextual Now Playing OTG action over pinned r18; no playback-code edits."""
import hashlib
import importlib.util
import json
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from actions_patch import ROOT, SOURCE, call, method, integer, op, functions
from verify_resources import directory, records, word

SOURCE_HASH = 'd8e07e277d6a739d134ffeceb7579311a9ff7c7f88d41488d8d9601feb43f368'
SCRIPT_COMMANDS = {2537, 2541, 2542, 2543, 2544}


def rewrite_script(blob):
    head, count, explicit, _, entries = functions(blob)
    if (word(blob, 0), count, explicit) != (2553, 633, 19):
        raise ValueError('expected r18 command table')
    finish = b'\x43\x05'
    control = lambda arg, keep=False: method(0xea, arg, owner=0x9ad, keep=keep)
    success = call(1860, b'\x42') + finish
    codes = {
        2537: (call(1892) + call(1950) + method(0x8a, integer(0x261d), owner=0x99e)
               + call(2544) + finish),
        2541: (control(op(0x54, 0), keep=True) + op(0x07, 3 + len(success)) + success
               + call(1899, integer(0x80), integer(0x6132)) + finish),
        2542: call(2541, integer(-6)) + finish,
        2543: call(2541, integer(-7)) + finish,
        2544: call(1866, control(integer(-5), keep=True), keep=True) + b'\x05',
    }
    table = (head + 4 + explicit * 2 + 3) & ~3
    offsets = struct.unpack_from(f'<{count + 1}I', blob, table)
    body = table + 4 * (count + 1)
    result = bytearray(blob)
    for start, (atom, _, _, _, old) in zip(offsets, entries):
        if atom not in codes:
            continue
        code = codes[atom]
        if len(code) > len(old):
            raise ValueError(f'command {atom} needs {len(code)} bytes; slot has {len(old)}')
        # Keep declared length/alignment and extension count unchanged. Padding
        # is unreachable after return. Reusing dead slots saves a 48-KB copy.
        at = body + start + 10
        result[at:at + len(old)] = code + bytes(len(old) - len(code))
    return bytes(result)


def compile_code(path, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository environment')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    obj, elf, script = (path / name for name in ('otg.o', 'otg.elf', 'otg.ld'))
    subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                    '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                    '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                    '-c', str(SOURCE / 'otg.c'), '-o', str(obj)], check=True)
    script.write_text(f'SECTIONS {{ . = 0x{base:x}; .text : {{ *(.text*) *(.rodata*) }} '
                      '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(script), '-e',
                    'otg_album', '-o', str(elf), str(obj)], check=True)
    with elf.open('rb') as stream:
        file = ELFFile(stream)
        if any(s['sh_size'] for s in file.iter_sections() if s.name in ('.data', '.bss')):
            raise ValueError('OTG helper must not have writable static state')
        symbols = {s.name: s['st_value'] for s in file.get_section_by_name('.symtab').iter_symbols()
                   if s.name.startswith('otg_')}
        return file.get_section_by_name('.text').data(), symbols, elf.read_bytes()


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != SOURCE_HASH:
        raise ValueError('expected pinned r18 navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new path')
    if image[-4:] != bytes.fromhex('55aa55aa') or len(image) % 4:
        raise ValueError('unexpected image terminator')
    groups = directory(image)
    output, changes = bytearray(image[:-4]), {}
    def place(data):
        output.extend(bytes(-len(output) % 4))
        at = len(output)
        output.extend(data)
        return at, len(data)
    def put(tag, key, data):
        changes.setdefault(tag, {})[key] = place(data)
    with TemporaryDirectory() as temporary:
        code, symbols, elf = compile_code(Path(temporary), len(output))
    place(code)
    if word(image, 0x1e0068) != 0x35e604:
        raise ValueError('unexpected r18 album selector')
    struct.pack_into('<I', output, 0x1e0068, symbols['otg_album'])
    rows = records(groups['Menu'][1][0x6102])
    if len(rows) != 5:
        raise ValueError('unexpected r18 actions menu')
    for key, command, label in ((0x6102, 2542, 0x6130), (0x6109, 2543, 0x6131)):
        row = bytearray(rows[0])
        for at, value in ((12, command * 4 + 2), (20, label), (32, label)):
            struct.pack_into('<I', row, at, value)
        put('Menu', key, struct.pack('<I', 6) + b''.join(rows) + row)
    for key, text in ((0x6130, 'Add to On-The-Go'), (0x6131, 'Remove from On-The-Go'),
                      (0x6132, 'On-The-Go Unavailable')):
        if key in groups['Str '][1]:
            raise ValueError('OTG string key already used')
        put('Str ', key, text.encode() + b'\0')
    base = ROOT + word(image, ROOT + 4)
    header, resources = groups['TCmd']
    table = ROOT + word(image, header + 12)
    offset, size = next((offset, size) for key, offset, size in struct.iter_unpack(
        '<III', image[table:table + 12 * len(resources)]) if key == 0x2380)
    script = rewrite_script(resources[0x2380])
    if len(script) != size:
        raise ValueError('script rewrite changed allocation size')
    output[base + offset:base + offset + size] = script
    for tag, additions in changes.items():
        header, old = groups[tag]
        table = ROOT + word(image, header + 12)
        positions = {key: (base + at, size) for key, at, size in struct.iter_unpack(
            '<III', image[table:table + len(old) * 12])}
        positions.update(additions)
        at, _ = place(b''.join(struct.pack('<III', key, at - base, size)
                              for key, (at, size) in sorted(positions.items())))
        struct.pack_into('<I', output, header + 4, len(positions))
        struct.pack_into('<I', output, header + 12, at - ROOT)
    output.extend(bytes(-len(output) % 4) + bytes.fromhex('55aa55aa'))
    struct.pack_into('<I', output, 0xe8, len(output))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', output, at, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError('OTG resources overlap initialized RAM')
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
        (artifacts / 'otg.elf').write_bytes(elf)
        (artifacts / 'otg-symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
    return bytes(output)
