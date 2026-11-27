"""Append a persistent Volume Limit to the pinned r13 navigation image."""
import hashlib
import importlib.util
import io
import json
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile
from actions_patch import functions, integer, symbol, call, method, op
from verify_resources import directory, records, word

SOURCE = Path(__file__).resolve().parent
ROOT = 0x1a507c
SOURCE_HASH = '39406f8a90c65621a04d5aa569aee65fe861b21d97c537423b000c7cf38989b4'
VIEW, SLIDER, ERROR, HELP = 0x6120, 0x6121, 0x6122, 0x6123
OPEN, SCREEN, WHEEL, UP, DOWN, SAVE = range(2547, 2553)


def branch(source, target):
    delta = target - source - 8
    if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError('volume hook is outside ARM branch range')
    return 0xea000000 | ((delta >> 2) & 0xffffff)


def extend_script(blob):
    head, count, explicit, footer, original = functions(blob)
    if (word(blob, 0), count, explicit) != (2547, 627, 13):
        raise ValueError('expected r13 command table')
    finish = b'\x43\x05'
    title = lambda key: call(1899, integer(0x80), integer(key))
    control = lambda operation, keep=False: method(0xa7, integer(operation), owner=0x99e, keep=keep)
    opening = title(VIEW) + method(0x76, symbol(SCREEN)) + finish
    # Follow the native Contrast screen's wheel and leave bindings. Its LCD
    # contrast setters are never called. Slider value is owned by its widget.
    screen = (method(0x8a, integer(0x261d), owner=0x99e)
              + call(1866, integer(VIEW)) + control(-1)
              + call(1889, symbol(WHEEL)) + call(1887, symbol(SAVE))
              + method(0x7a, symbol(2137)) + method(0x79, symbol(2137)) + finish)
    wheel = next(code for atom, _, _, _, code in original if atom == 2194)
    if wheel.count(symbol(2192)) != 1 or wheel.count(symbol(2195)) != 1:
        raise ValueError('unexpected native Contrast wheel dispatch')
    wheel = wheel.replace(symbol(2192), symbol(UP)).replace(symbol(2195), symbol(DOWN))
    # The stock wheel dispatcher supplies a positive magnitude to each branch.
    # Encode it in the signed getter argument: even -16-2*n increases; odd
    # -17-2*n decreases. The helper clamps magnitude and treats zero as a no-op.
    def step(base):
        encoded = integer(base) + op(0x54, 0) + integer(2) + b'\x1d\x1c'
        return title(VIEW) + method(0xa7, encoded, owner=0x99e) + finish
    success = call(1860, op(0x54, 0)) + finish
    save = control(-4, keep=True) + op(0x07, 3 + len(success)) + success + title(ERROR) + finish
    added = [(OPEN, 1, 0, 6, opening), (SCREEN, 1, 0, 6, screen),
             (WHEEL, 1, 1, 6, wheel),
             (UP, 2, 0, 8, step(-16)),
             (DOWN, 2, 0, 8, step(-17)),
             (SAVE, 1, 0, 6, save)]
    body, offsets = bytearray(), [0]
    for atom, argc, local, stack, code in original + added:
        code += bytes(len(code) % 2)
        body.extend(struct.pack('<5H', atom, argc, local, stack, len(code)) + code + b'\0\0')
        offsets.append(len(body))
    header = bytearray(blob[:head])
    struct.pack_into('<H', header, 0, SAVE + 1)
    header.extend(struct.pack('<HH', count + len(added), explicit + len(added)))
    header.extend(blob[head + 4:head + 4 + explicit * 2])
    header.extend(struct.pack('<6H', *range(OPEN, SAVE + 1)))
    header.extend(bytes(-len(header) % 4))
    return bytes(header + struct.pack(f'<{len(offsets)}I', *offsets) + body + blob[footer:])


def compile_code(path, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository environment')
    linker = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    objects = []
    for name in ('volume.c', 'volume_output.c', 'volume_hooks.S'):
        obj = path / (name + '.o')
        subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                        '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                        '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                        '-c', str(SOURCE / name), '-o', str(obj)], check=True)
        objects.append(str(obj))
    script, elf = path / 'volume.ld', path / 'volume.elf'
    script.write_text(f'SECTIONS {{ . = 0x{base:x}; .text : {{ *(.text*) *(.rodata*) }} '
                      '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }')
    subprocess.run([str(linker), 'ld.lld', '-m', 'armelf', '-T', str(script), '-e',
                    'volume_control', '-o', str(elf), *objects], check=True)
    data = ELFFile(io.BytesIO(elf.read_bytes()))
    if any(s['sh_size'] for s in data.iter_sections() if s.name in ('.data', '.bss')):
        raise ValueError('volume patch must not use writable globals')
    symbols = {s.name: s['st_value'] for s in data.get_section_by_name('.symtab').iter_symbols()
               if s.name.startswith('volume_')}
    return data.get_section_by_name('.text').data(), symbols, elf.read_bytes()


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != SOURCE_HASH:
        raise ValueError('expected pinned r13 navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new path')
    if image[-4:] != bytes.fromhex('55aa55aa') or len(image) % 4:
        raise ValueError('unexpected image terminator')
    with TemporaryDirectory() as temporary:
        code, symbols, elf = compile_code(Path(temporary), len(image) - 4)
    output = bytearray(image[:-4])

    def place(data):
        output.extend(bytes(-len(output) % 4))
        at = len(output)
        output.extend(data)
        return at, len(data)

    place(code)
    groups, changes = directory(image), {}

    def put(tag, key, data):
        changes.setdefault(tag, {})[key] = place(data)

    root_rows = records(groups['View'][1][0x250d])
    root = bytearray(next(row for row in root_rows if word(row, 12) == 0x27b4))
    struct.pack_into('<I', root, 12, VIEW)
    put('View', 0x250d, struct.pack('<I', len(root_rows) + 1) + b''.join(root_rows) + root)
    slider = bytearray(next(row for row in records(groups['View'][1][0x27b4])
                            if word(row, 4) == 0x3080))
    if len(slider) != 96 or word(slider, 12) != 0x333f:
        raise ValueError('unexpected native slider layout')
    struct.pack_into('<II', slider, 12, SLIDER, 0xffffffff)
    # Contrast's bar follows its icon. Our independent bar instead anchors to
    # its parent; native geometry resolves it to (50,25,59,135) in 160x108.
    struct.pack_into('<3I', slider, 0x1c, 0, 0x80000002, 50)
    struct.pack_into('<3I', slider, 0x28, 0, 0x80000002, 25)
    # Native scalar-control resource trailer: current, minimum, maximum,
    # optional dynamic-range provider keys. This control owns a static range.
    struct.pack_into('<4I', slider, 80, 256, 0, 256, 0)
    caption = bytearray(records(groups['View'][1][0x3308])[0])
    if len(caption) != 88 or word(caption, 4) != 0x1580:
        raise ValueError('unexpected native static text layout')
    struct.pack_into('<II', caption, 12, HELP, 0xffffffff)
    # Low halfword is the literal Str key when no dynamic provider is used.
    struct.pack_into('<I', caption, 0x4c, 0x25540000 | HELP)
    for offset, triple in ((0x1c, (0, 0x80000002, 68)),
                           (0x28, (0, 0x80000002, 5)),
                           (0x34, (0, 0x80000008, 12)),
                           (0x40, (0, 0x80000002, 0xfffffffb))):
        struct.pack_into('<3I', caption, offset, *triple)
    put('View', VIEW, struct.pack('<I', 2) + slider + caption)
    menu = groups['Menu'][1][0x278d]
    if word(menu, 0) != 15 or (word(menu, 4 + 8 * 40 + 12) - 2) // 4 != 2190:
        raise ValueError('unexpected Settings menu layout')
    row = bytearray(groups['Menu'][1][0x25ee][4:44])
    for offset, value in ((4, 0x20), (12, OPEN * 4 + 2), (20, VIEW), (32, VIEW)):
        struct.pack_into('<I', row, offset, value)
    put('Menu', 0x278d, struct.pack('<I', 16) + menu[4:324] + row + menu[324:])
    put('Str ', VIEW, b'Volume Limit\0')
    put('Str ', ERROR, b"Couldn't Set Limit\0")
    put('Str ', HELP, b'Center saves; Menu cancels.\0')
    put('TCmd', 0x2380, extend_script(groups['TCmd'][1][0x2380]))
    base = ROOT + word(image, ROOT + 4)
    for tag, additions in changes.items():
        header, old = groups[tag]
        original_table = ROOT + word(image, header + 12)
        positions = {key: (base + at, size) for key, at, size in struct.iter_unpack(
            '<III', image[original_table:original_table + len(old) * 12])}
        if any(0x6120 <= key <= 0x6123 and key in positions for key in additions):
            raise ValueError('new volume resource ID already exists')
        positions.update(additions)
        table = b''.join(struct.pack('<III', key, at - base, size)
                         for key, (at, size) in sorted(positions.items()))
        at, _ = place(table)
        struct.pack_into('<I', output, header + 4, len(positions))
        struct.pack_into('<I', output, header + 12, at - ROOT)
    for at, expected, symbol, link in (
            (0x8db08, branch(0x8db08, 0xb5408) | 0x01000000, 'volume_load', True),
            (0xf3368, 0xebff1e42, 'volume_owner_register', True),
            (0x14fae0, 0xe92d43f8, 'volume_codec', False)):
        if word(image, at) != expected:
            raise ValueError(f'unexpected volume hook at {at:#x}')
        struct.pack_into('<I', output, at, branch(at, symbols[symbol]) | (0x01000000 if link else 0))
    if word(image, 0x1dff6c) != 0x1b6f8:
        raise ValueError('unexpected Renderer a7 binding')
    struct.pack_into('<I', output, 0x1dff6c, symbols['volume_control'])
    output.extend(bytes(-len(output) % 4) + bytes.fromhex('55aa55aa'))
    struct.pack_into('<I', output, 0xe8, len(output))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', output, at, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError('volume patch overlaps initialized RAM')
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
        (artifacts / 'volume.elf').write_bytes(elf)
        (artifacts / 'volume-symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
    return bytes(output)
