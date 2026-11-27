"""Move Volume Limit below Repeat in the pinned r17 Settings resource."""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'menu_scroll'))
from verify_resources import directory, records, word

ROOT = 0x1a507c
SOURCE_HASH = '29fe823f71dfe794346eef436121c9fe841932fd940b66ca87d66e6890a9f3ed'


def install(image, artifacts=None):
    if hashlib.sha256(image).hexdigest() != SOURCE_HASH:
        raise ValueError('expected pinned r17 navigation OSOS')
    if artifacts is not None and (artifacts.exists() or artifacts.is_symlink()):
        raise ValueError('review artifacts already exist; choose a new path')
    header, menus = directory(image)['Menu']
    rows = records(menus[0x278d])
    if len(rows) != 16 or word(rows[8], 20) != 0x6120:
        raise ValueError('unexpected r17 Settings order')
    table = ROOT + word(image, header + 12)
    base = ROOT + word(image, ROOT + 4)
    offset, size = next((offset, size) for key, offset, size in struct.iter_unpack(
        '<III', image[table:table + 12 * len(menus)]) if key == 0x278d)
    reordered = struct.pack('<I', len(rows)) + b''.join(rows[:4] + [rows[8]] + rows[4:8] + rows[9:])
    if size != len(reordered) or image[base + offset:base + offset + size] != menus[0x278d]:
        raise ValueError('unexpected Settings resource location/size')
    # The record lengths do not change. Rewrite only the active Menu payload;
    # no code, pointers, image-size words or extra image space are needed.
    output = bytearray(image)
    output[base + offset:base + offset + size] = reordered
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=False)
    return bytes(output)
