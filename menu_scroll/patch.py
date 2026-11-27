"""Enable native selected-row marquees in missing 4G music-list resources.

The list loader at 0x2f458 installs its second child at object +0x94. The
renderer at 0x5cdc4 chooses it only for the selected index. Stock lists 3306,
330a and 330b already use this arrangement with text control class 3180.
No executable instruction is changed here.
"""
import hashlib
import struct

ROOT = 0x1a507c
END_MARKER = b"\x55\xaa\x55\xaa"
SUPPORTED = {
    "e6068744c2922845029591b54834b5447425701441daee1a7f11cfc5ba4bbd2a": "stock 4.3.1.1",
    "c927010a9a93d957022e35d3b8c328f45b549bb201c255215ab233c9e14dcf73": "gapless-complete-drain",
}
# List ID, row resource ID (signed short), string property ID.
TARGETS = (
    (0x3301, 0xffffff30, 0x3367), (0x3302, 0xffffff2e, 0x336a),
    (0x3303, 0xffffff24, 0x336b), (0x3304, 0xffffff26, 0x336c),
    (0x3305, 0xffffff22, 0x336d), (0x3307, 0xffffff32, 0x336f),
    (0x330f, 0xffffff38, 0x3374),
)


def word(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def install(image):
    if hashlib.sha256(image).hexdigest() not in SUPPORTED:
        raise ValueError("unsupported OSOS: expected stock 4.3.1.1 or the exact gapless-complete-drain image (not already patched)")
    if image[-4:] != END_MARKER or word(image, 0xe8) != len(image):
        raise ValueError("invalid image endpoint")
    base = ROOT + word(image, ROOT + 4)
    header = next(ROOT + 12 + 16 * i for i in range(word(image, ROOT + 8))
                  if image[ROOT + 12 + 16 * i:ROOT + 16 + 16 * i] == b"weiV")
    directory = ROOT + word(image, header + 12)
    entries = dict((key, (base + offset, size)) for key, offset, size in
                   struct.iter_unpack("<III", image[directory:directory + 12 * word(image, header + 4)]))
    result = bytearray(image[:-4])

    def contents(key):
        offset, size = entries[key]
        return image[offset:offset + size]

    def append(data):
        result.extend(bytes(-len(result) % 4))
        at = len(result)
        result.extend(data)
        return at, len(data)

    for index, (list_id, row_id, label_id) in enumerate(TARGETS):
        new_id = 0x6000 + index
        if new_id in entries:
            raise ValueError(f"selected-row resource {new_id:#x} already exists")
        parent, row = contents(list_id), contents(row_id)
        if (len(parent), word(parent, 0), word(parent, 8), word(parent, 16)) != (
                84, 1, 0x1780, row_id & 0xffff):
            raise ValueError(f"unexpected list {list_id:#x}")
        selected = bytearray(row[:4])
        cursor, promoted = 4, 0
        for _ in range(word(row, 0)):
            size = word(row, cursor) + 4
            child = bytearray(row[cursor:cursor + size])
            if word(child, 12) == label_id:
                if (size, word(child, 4), word(child, 8)) != (0x58, 0x1580, 0x53747220):
                    raise ValueError(f"unexpected label {label_id:#x}")
                struct.pack_into("<II", child, 0, 0x64, 0x3180)
                child.extend(struct.pack("<4I", 140, 1250, 8, 25))
                promoted += 1
            selected.extend(child)
            cursor += size
        if cursor != len(row) or promoted != 1:
            raise ValueError(f"unexpected row {row_id:#x}")
        entries[new_id] = append(selected)
        clone = bytearray(parent[4:])
        struct.pack_into("<I", clone, 12, new_id)
        entries[list_id] = append(struct.pack("<I", 2) + parent[4:] + clone)

    table = b"".join(struct.pack("<III", key, offset - base, size)
                     for key, (offset, size) in sorted(entries.items()))
    at, _ = append(table)
    struct.pack_into("<I", result, header + 4, len(entries))
    struct.pack_into("<I", result, header + 12, at - ROOT)
    result.extend(END_MARKER)
    if len(result) > 0x390000 or len(result) % 4:
        raise ValueError("extended resources exceed the OSOS load region")
    struct.pack_into("<I", result, 0xe8, len(result))
    for offset in (0x2e8, 0x4a8):
        if word(image, offset) != 0x04000000 + len(image) - 4:
            raise ValueError("unexpected startup end-marker address")
        struct.pack_into("<I", result, offset, 0x04000000 + len(result) - 4)
    return bytes(result)
