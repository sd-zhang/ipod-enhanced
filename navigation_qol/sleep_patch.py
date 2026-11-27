"""Add the native Sleep Timer row to Hold Center actions over pinned r14."""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'menu_scroll'))
from verify_resources import directory, word

ROOT = 0x1a507c
SOURCE_HASH = '857a659fac2fba7a4608aea21bad31ccdc9c04ca737d9291778903835eb636d1'


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != SOURCE_HASH:
        raise ValueError('expected pinned r14 navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new path')
    if image[-4:] != bytes.fromhex('55aa55aa') or len(image) % 4:
        raise ValueError('unexpected image terminator')
    groups = directory(image)
    header, menus = groups['Menu']
    actions, clock = menus[0x6102], menus[0x4581]
    if len(actions) != 164 or word(actions, 0) != 4:
        raise ValueError('unexpected r14 actions menu')
    row = bytearray(clock[44:84])
    if struct.unpack('<10I', row) != (
            0x24, 0x26, 0x1d92, 0x21c2, 0xff55,
            0x0308ff56, 0x27d9, 0x2780, 0xff56, 0):
        raise ValueError('unexpected stock Sleep Timer row')
    # Keep the stock value provider, submenu key and command. Only omit the
    # main-menu customization bit and shortcut icon, as for the Shuffle row.
    struct.pack_into('<I', row, 4, 0x22)
    struct.pack_into('<H', row, 22, 0)
    output = bytearray(image[:-4])
    menu_at = len(output)
    output.extend(struct.pack('<I', 5) + actions[4:] + row)
    base = ROOT + word(image, ROOT + 4)
    old_table = ROOT + word(image, header + 12)
    entries = list(struct.iter_unpack('<III', image[old_table:old_table + len(menus) * 12]))
    table_at = len(output)
    for key, offset, size in entries:
        if key == 0x6102:
            offset, size = menu_at - base, 204
        output.extend(struct.pack('<III', key, offset, size))
    struct.pack_into('<I', output, header + 12, table_at - ROOT)
    output.extend(bytes.fromhex('55aa55aa'))
    struct.pack_into('<I', output, 0xe8, len(output))
    for at in (0x2e8, 0x4a8):
        struct.pack_into('<I', output, at, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError('Sleep Timer patch overlaps initialized RAM')
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
    return bytes(output)
