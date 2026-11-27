"""Native Volume Limit appearance and stock Repeat toggle over pinned r16.

Only resources change. Executable code, scripts and existing resource payloads
stay in place; replacement resource records and directories are appended.
"""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'menu_scroll'))
from verify_resources import directory, records, word

ROOT = 0x1a507c
SOURCE_HASH = 'a8153864583ca312cfceb43c57cb4b0b869d38e55f99f6634d0ff14fe22dc0ce'


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != SOURCE_HASH:
        raise ValueError('expected pinned r16 navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new path')
    if image[-4:] != bytes.fromhex('55aa55aa') or len(image) % 4:
        raise ValueError('unexpected image terminator')
    groups = directory(image)
    output = bytearray(image[:-4])
    changes = {}

    def place(data):
        output.extend(bytes(-len(output) % 4))
        at = len(output)
        output.extend(data)
        return at, len(data)

    def put(tag, key, data):
        changes.setdefault(tag, {})[key] = place(data)

    # Use the actual 4G volume symbols and spacing. The Contrast-derived
    # slider keeps its editable/focus flags and private ID, with no provider
    # binding to live playback volume. Center/Menu still save/cancel its draft.
    small, _, large = map(bytearray, records(groups['View'][1][0x2637]))
    slider = bytearray(next(r for r in records(groups['View'][1][0x6120])
                            if word(r, 12) == 0x6121))
    for icon in (small, large):
        struct.pack_into('<3I', icon, 0x1c, 1, 2, 0xfffffffa)  # center minus 6
    struct.pack_into('<3I', slider, 0x1c, 0, 0x100, 2)  # previous icon top + 2
    struct.pack_into('<3I', slider, 0x28, 0, 0x400, 4)  # previous icon right + 4
    struct.pack_into('<3I', slider, 0x40, 0, 0x80000002, 0xffffffe8)
    put('View', 0x6120, struct.pack('<I', 3) + small + slider + large)

    settings = bytearray(groups['Menu'][1][0x278d])
    contrast = bytearray(settings[364:404])
    if (word(settings, 0), word(contrast, 12), word(contrast, 24)) != (16, 2190 * 4 + 2, 0x27b3):
        raise ValueError('unexpected native Contrast entry')
    # The native Contrast property 27b3 supplies BMap25ae (the > arrow) and
    # no text value. Copy that read-only provider; change only label/command
    # and remove its main-menu customization decoration, like other shortcuts.
    for at, value in ((4, 0x22), (12, 2547 * 4 + 2), (20, 0x6120), (32, 0x6120)):
        struct.pack_into('<I', contrast, at, value)
    settings[324:364] = contrast
    put('Menu', 0x278d, settings)

    actions = bytearray(groups['Menu'][1][0x6102])
    repeat = bytearray(settings[124:164])
    if (word(actions, 0), word(repeat, 12), word(repeat, 24)) != (5, 2155 * 4 + 2, 0x27b9):
        raise ValueError('unexpected native Repeat entry')
    struct.pack_into('<I', repeat, 4, 0x22)
    struct.pack_into('<H', repeat, 22, 0)
    actions[124:164] = repeat
    put('Menu', 0x6102, actions)

    base = ROOT + word(image, ROOT + 4)
    for tag, replacements in changes.items():
        header, resources = groups[tag]
        if not replacements.keys() <= resources.keys():
            raise ValueError('replacement resource missing from source')
        table = ROOT + word(image, header + 12)
        entries = []
        for key, offset, size in struct.iter_unpack('<III', image[table:table + len(resources) * 12]):
            if key in replacements:
                at, size = replacements[key]
                offset = at - base
            entries.append(struct.pack('<III', key, offset, size))
        at, _ = place(b''.join(entries))
        struct.pack_into('<I', output, header + 12, at - ROOT)
    output.extend(bytes(-len(output) % 4) + bytes.fromhex('55aa55aa'))
    struct.pack_into('<I', output, 0xe8, len(output))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', output, at, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError('native control resources overlap initialized RAM')
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
    return bytes(output)
