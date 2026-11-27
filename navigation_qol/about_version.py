"""Display the Enhanced revision in the stock monochrome 4G About field.

This only replaces the dedicated version formatter's argument setup. The
existing C/Pascal/UTF-16 conversion, stack frame and return path stay native.
No resource, updater identity, persistent state or image extent changes.
"""
import struct

REVISION = 50
APPLE_VERSION = '3.1.1'
DISPLAY_VERSION = f'{APPLE_VERSION} / r{REVISION}'
ENTRY, END = 0x7669c, 0x766e8
EDITS = (*range(0x766a4, 0x766c4, 4), 0x766cc)
STOCK_CODE = struct.pack('<19I',
    0xe92d4010, 0xe24ddf5a, 0xe28d3004, 0xe58d3000, 0xe1a04000,
    0xe3a00003, 0xe3a03000, 0xe3a02080, 0xe3a01011, 0xeb02c921,
    0xe3a020ff, 0xe28d1068, 0xe28d0004, 0xebff3fbe, 0xe1a01004,
    0xe28d0068, 0xebff45d5, 0xe28ddf5a, 0xe8bd8010)


def install(image):
    """Patch a mutable OSOS image only when its native builder is exact."""
    if image[ENTRY:END] != STOCK_CODE:
        raise ValueError('unexpected native 4G About version builder')
    literal = DISPLAY_VERSION.encode('ascii') + b'\0'
    if len(literal) > 20:
        raise ValueError('About version exceeds its in-place literal slot')
    # MOV r4,r0; ADR r0,0x766b0; B 0x766c4. The skipped instruction
    # space owns the literal; the existing converter receives its pointer.
    image[0x766a4:0x766c4] = (
        struct.pack('<3I', 0xe1a04000, 0xe28f0000, 0xea000004)
        + literal.ljust(20, b'\0'))
    struct.pack_into('<I', image, 0x766cc, 0xe1a00000)
    return dict(revision=REVISION, apple_version=APPLE_VERSION,
                display_version=DISPLAY_VERSION, edits=list(EDITS),
                added_code_bytes=0, added_state_bytes=0)
