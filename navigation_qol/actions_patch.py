"""Append Now Playing actions to the pinned three-feature navigation firmware."""
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE.parent / 'menu_scroll'))
from verify_resources import directory, records, word

ROOT = 0x1a507c
SOURCE_HASHES = {
    'a6502680a3f61ec4beb4e77f0cf13e56dbe5c67737a6047a6c7bf0b5ee5d6b5e',
    '8680368e5c05ac1524af2e7db4e0933901dd183a62be90f00974f2c5ee47919a',
    '9a94877874e76200fc607d2c408181b58b5496ac3136d828f6945cd72f924816',
    'ab3bc5e9543eac184397bba15f250abd69de18fbdba41863d16cfe41887271ac',
    'e07537e2ce7df8a89a2907e0499ea3dc57c340791c3c76c497d96dbcb6ed3e46',
    '4e518282e420ee3cf19108c91c08a82d3d31d7bca61860f65720a1710dfde26a',
    '7ccf5a13fa6c9290aebbc4cc280d9caf8ca786f6291cb9eb3fcacd5b2994e6ca',
    'a717a783c5650ba997ff6eef85ed278c6bc07a69042b9876b8f91025055b1d02',
}
OPEN, SCREEN, ALBUM, ARTIST, BROWSE_PARENTS = range(2536, 2541)
REPEAT_ALBUM, REPEAT_OPEN, REPEAT_SCREEN, REPEAT_OFF, REPEAT_ONE, REPEAT_ALL = range(2541, 2547)
VIEW, LIST, MENU = 0x6100, 0x6101, 0x6102
REPEAT_VIEW, REPEAT_LIST, REPEAT_MENU = 0x6107, 0x6108, 0x6109


def op(code, value):
    return bytes([code]) + struct.pack('>H', value)


def integer(value):
    return op(0x58, abs(value)) + (b'\x22' if value < 0 else b'')


def symbol(value):
    return op(0x3b, value)


def call(command, *args, owner=None, keep=False):
    target = symbol(command) if owner is None else symbol(owner) + op(0x35, command)
    return target + b'\x39' + b''.join(args) + op(0x3a, len(args)) + (b'' if keep else b'\x02')


def method(selector, *args, owner=0x99d, keep=False):
    return call(selector, *args, owner=owner, keep=keep)


def functions(blob):
    head = 8 + 4 * struct.unpack_from('<H', blob, 6)[0]
    count, explicit = struct.unpack_from('<HH', blob, head)
    table = (head + 4 + explicit * 2 + 3) & ~3
    offsets = struct.unpack_from(f'<{count + 1}I', blob, table)
    body = table + 4 * (count + 1)
    result = []
    for start in offsets[:-1]:
        atom, argc, local, stack, size = struct.unpack_from('<5H', blob, body + start)
        result.append((atom, argc, local, stack, blob[body + start + 10:body + start + 10 + size]))
    return head, count, explicit, body + offsets[-1], result


def extend_script(blob):
    head, count, explicit, footer, original = functions(blob)
    if (word(blob, 0), count, explicit) != (2536, 616, 2):
        raise ValueError('unexpected 4G command table')
    finish = b'\x43\x05'
    title = lambda key: call(1899, integer(0x80), integer(key))
    # Like stock Hold Center actions 2078/2094, consume the pending release by
    # restoring the normal Center binding (1888), without activating a row.
    # Reset temporary seek/rating/panel bindings before history snapshots them.
    # Suspension closes those modes, so restoring their keys on Back is wrong.
    # Stock 2030(false) restores normal controls without an animated transition;
    # native 2019 still commits/cleans up pending seeking during the push.
    opening = (call(2030, b'\x42') + title(VIEW) + method(0x76, symbol(SCREEN)) + call(1950)
               + method(0x6a, integer(3), integer(0x6f), symbol(1888)) + finish)
    # Stock row activation 1892 installs Menu/back and Center/dispatch (1896).
    # 1950 separately restores normal Play/Pause behavior for a generic menu.
    screen = (call(1892) + call(1950)
              + method(0x8a, integer(0x261d), owner=0x99e)
              + call(1866, integer(VIEW)) + finish)
    # A fresh browse must discard old screens which share the one library
    # query. Rebuild only non-browsing parents so Menu has real levels to pop.
    # 2012 constructs normal Now Playing without starting/restarting audio.
    # Push SCREEN directly: OPEN consumes a held Center release, but the
    # shortcut was selected with a normal release and has no pending one.
    parents = (call(1855) + call(2012) + title(VIEW)
               + method(0x76, symbol(SCREEN)) + finish)
    repeat_open = title(0x610b) + method(0x76, symbol(REPEAT_SCREEN)) + finish
    repeat_screen = (call(1892) + call(1950)
                     + method(0x8a, integer(0x261d), owner=0x99e)
                     + call(1866, integer(REPEAT_VIEW)) + finish)
    def repeat_setting(key):
        return method(0x140, integer(0x27b9), integer(0x2780), integer(key), owner=0x9ba)
    # Album is an explicit queue-changing action. Native Repeat All then loops
    # that queue; no fourth native repeat mode or persistent patch state exists.
    apply_album = method(0xea, integer(-4), owner=0x9ad, keep=True)
    set_all = repeat_setting(0x27bc)
    album_body = (call(1868, symbol(BROWSE_PARENTS)) + call(REPEAT_OPEN)
                  + apply_album + op(0x07, 3 + len(set_all)) + set_all)
    repeat_album = (method(0xea, integer(-2), owner=0x9ad, keep=True)
                    + op(0x07, 3 + len(album_body)) + album_body + finish)

    def browse(selector, heading, destination):
        # Validate before leaving the actions menu. Read again after native
        # history cleanup; a queued track change cannot retain a stale pointer.
        apply = method(selector, integer(-3), owner=0x9ad, keep=True)
        show = title(heading) + call(destination)
        body = call(1868, symbol(BROWSE_PARENTS)) + apply + op(0x07, 3 + len(show)) + show
        return (method(selector, integer(-2), owner=0x9ad, keep=True)
                + op(0x07, 3 + len(body)) + body + finish)

    changes = {
        2015: method(0x6a, integer(2), integer(0x6f), symbol(OPEN)),
        2019: method(0x6b, integer(2), integer(0x6f)),
    }
    updated = []
    for atom, argc, local, stack, code in original:
        if atom in changes:
            code = changes[atom] + code
            stack = max(stack, 5)
        updated.append((atom, argc, local, stack, code))
    updated += [(OPEN, 1, 0, 6, opening), (SCREEN, 1, 0, 6, screen),
                (ALBUM, 1, 0, 6, browse(0xea, 0x6105, 2074)),
                (ARTIST, 1, 0, 6, browse(0xe8, 0x6106, 2090)),
                (BROWSE_PARENTS, 0, 0, 6, parents),
                (REPEAT_ALBUM, 1, 0, 6, repeat_album),
                (REPEAT_OPEN, 1, 0, 6, repeat_open),
                (REPEAT_SCREEN, 1, 0, 6, repeat_screen),
                (REPEAT_OFF, 1, 0, 6, repeat_setting(0x27ba) + finish),
                (REPEAT_ONE, 1, 0, 6, repeat_setting(0x27bb) + finish),
                (REPEAT_ALL, 1, 0, 6, set_all + finish)]
    data, offsets = bytearray(), [0]
    for atom, argc, local, stack, code in updated:
        # 68efc/68f00 reads the u16 extension count at code + declared size.
        # Padding must be part of that size, as in every stock function;
        # merely aligning the next record leaves an odd ARM7 LDRH address.
        code += bytes(len(code) % 2)
        data.extend(struct.pack('<5H', atom, argc, local, stack, len(code)) + code)
        data.extend(b'\0\0')
        offsets.append(len(data))
    header = bytearray(blob[:head])
    struct.pack_into('<H', header, 0, REPEAT_ALL + 1)
    header.extend(struct.pack('<HH', count + 11, explicit + 11))
    header.extend(blob[head + 4:head + 4 + explicit * 2])
    header.extend(struct.pack('<11H', *range(OPEN, REPEAT_ALL + 1)))
    header.extend(bytes(-len(header) % 4))
    return bytes(header + struct.pack(f'<{len(offsets)}I', *offsets) + data + blob[footer:])


def compile_code(path, base):
    spec = importlib.util.find_spec('ziglang')
    if not spec or not spec.submodule_search_locations:
        raise ValueError('ziglang is required in the repository environment')
    zig = Path(next(iter(spec.submodule_search_locations))) / 'zig'
    obj, elf, script = (path / name for name in ('actions.o', 'actions.elf', 'actions.ld'))
    subprocess.run(['clang', '--target=armv4t-none-eabi', '-mcpu=arm7tdmi', '-marm',
                    '-Os', '-ffreestanding', '-fno-builtin', '-fno-unwind-tables',
                    '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra', '-Werror',
                    '-c', str(SOURCE / 'actions.c'), '-o', str(obj)], check=True)
    script.write_text(f'SECTIONS {{ . = 0x{base:x}; .text : {{ *(.text*) *(.rodata*) }} '
                      '.data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } '
                      '/DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) } }')
    subprocess.run([str(zig), 'ld.lld', '-m', 'armelf', '-T', str(script), '-e',
                    'actions_artist', '-o', str(elf), str(obj)], check=True)
    with elf.open('rb') as stream:
        file = ELFFile(stream)
        if any(s['sh_size'] for s in file.iter_sections() if s.name in ('.data', '.bss')):
            raise ValueError('actions must not have writable static state')
        symbols = {s.name: s['st_value'] for s in file.get_section_by_name('.symtab').iter_symbols()
                   if s.name.startswith('actions_')}
        return file.get_section_by_name('.text').data(), symbols, elf.read_bytes()


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() not in SOURCE_HASHES:
        raise ValueError('expected pinned three-feature navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new output path')
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
    groups = directory(image)
    changes = {}

    def put(tag, key, data):
        changes.setdefault(tag, {})[key] = place(data)

    root_rows = records(groups['View'][1][0x250d])
    clones = []
    for view, listing, menu in ((VIEW, LIST, MENU), (REPEAT_VIEW, REPEAT_LIST, REPEAT_MENU)):
        clone = bytearray(next(row for row in root_rows if word(row, 12) == 0x2790))
        struct.pack_into('<I', clone, 12, view)
        clones.append(clone)
        container = bytearray(groups['View'][1][0x2790])
        # Container's scrollbar target, list resource key and list object ID.
        for at in (4 + 92, 104 + 12, 104 + 20):
            expected = 0xffffff07 if at != 116 else 0xff07
            if word(container, at) != expected:
                raise ValueError(f'unexpected stock list layout at {at}')
            struct.pack_into('<I', container, at, listing)
        put('View', view, container)
        list_view = bytearray(groups['View'][1][0xffffff09])
        struct.pack_into('<I', list_view, 4 + 12, menu)
        struct.pack_into('<I', list_view, 4 + 16, 0xffffffff)
        put('View', listing, list_view)
        put('View', menu, groups['View'][1][0x278d])
    put('View', 0x250d, struct.pack('<I', len(root_rows) + 2) + b''.join(root_rows + clones))
    music = groups['Menu'][1][0x25ee]
    rows = []
    def action_row(command, label):
        row = bytearray(music[4:44])
        struct.pack_into('<I', row, 4, 0x20)  # ordinary visible action, no preference shortcut
        struct.pack_into('<I', row, 12, command * 4 + 2)
        struct.pack_into('<I', row, 20, label)
        struct.pack_into('<I', row, 32, label)
        return row
    rows.extend(action_row(command, label) for command, label in ((ALBUM, 0x6103), (ARTIST, 0x6104)))
    shuffle = bytearray(groups['Menu'][1][0x278d][84:124])
    struct.pack_into('<I', shuffle, 4, 0x22)  # preserve the value flag, remove main-menu shortcut
    struct.pack_into('<H', shuffle, 22, 0)
    rows.append(shuffle)
    repeat = bytearray(groups['Menu'][1][0x278d][124:164])
    struct.pack_into('<I', repeat, 4, 0x22)
    struct.pack_into('<I', repeat, 12, REPEAT_OPEN * 4 + 2)
    struct.pack_into('<H', repeat, 22, 0)
    rows.append(repeat)
    put('Menu', MENU, struct.pack('<I', 4) + b''.join(rows))
    put('Menu', REPEAT_MENU, struct.pack('<I', 4) + b''.join(
        action_row(command, label) for command, label in
        ((REPEAT_OFF, 0x27ba), (REPEAT_ONE, 0x27bb), (REPEAT_ALBUM, 0x610a), (REPEAT_ALL, 0x27bc))))
    for key, text in ((VIEW, 'Now Playing'), (0x6103, 'Browse Album'),
                      (0x6104, 'Browse Artist'), (0x6105, 'Album Songs'),
                      (0x6106, 'Artist Albums'), (0x610a, 'Album'), (0x610b, 'Repeat')):
        put('Str ', key, text.encode() + b'\0')
    put('TCmd', 0x2380, extend_script(groups['TCmd'][1][0x2380]))
    base = ROOT + word(image, ROOT + 4)
    for tag, additions in changes.items():
        header, old = groups[tag]
        original_table = ROOT + word(image, header + 12)
        positions = {key: (base + at, size) for key, at, size in struct.iter_unpack(
            '<III', image[original_table:original_table + len(old) * 12])}
        for key in additions:
            if key >= 0x6100 and key < 0x6110 and key in positions:
                raise ValueError('new resource ID already exists')
        positions.update(additions)
        table = b''.join(struct.pack('<III', key, at - base, size)
                         for key, (at, size) in sorted(positions.items()))
        at, _ = place(table)
        struct.pack_into('<I', output, header + 4, len(positions))
        struct.pack_into('<I', output, header + 12, at - ROOT)
    for at, expected, name in ((0x1e0060, 0xc560c, 'actions_artist'), (0x1e0068, 0xc5430, 'actions_album')):
        if word(output, at) != expected:
            raise ValueError('unexpected native selector')
        struct.pack_into('<I', output, at, symbols[name])
    # Native scheduler calls divide-by-ten after multiplying dwell by three
    # only for intermediate segments. Initial/wrap pauses bypass this call.
    at = 0xd2ad8
    if word(output, at) != 0xebfd07cb:
        raise ValueError('unexpected marquee dwell call')
    delta = symbols['actions_scroll_pause'] - at - 8
    if delta % 4 or not -(1 << 25) <= delta < (1 << 25):
        raise ValueError('marquee dwell target out of ARM branch range')
    struct.pack_into('<I', output, at, 0xeb000000 | ((delta >> 2) & 0xffffff))
    output.extend(bytes(-len(output) % 4) + bytes.fromhex('55aa55aa'))
    struct.pack_into('<I', output, 0xe8, len(output))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', output, at, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError('patch overlaps initialized RAM')
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
        (artifacts / 'actions.elf').write_bytes(elf)
        (artifacts / 'actions-symbols.json').write_text(json.dumps(symbols, indent=2) + '\n')
    return bytes(output)
