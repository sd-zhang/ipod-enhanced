"""Install the stock 5.5G 1.3 disc Quick Scroll hooks."""
import struct

from image import word
from layout import TEXT
from pomodoro_patch import branch

HOOKS = (
    (0x239db8, 0xe3a00f4d, 'discs_fast_begin_hook', 'b'),
    (0x23a56c, 0xe3a00f4d, 'discs_fast_gate_hook', 'b'),
    (0x23bab4, 0xe92d4010, 'discs_fast_destroy_hook', 'b'),
    (0x6772c0, 0x0023b704, 'discs_fast_event', 'pointer'),
    (0x677334, 0x0023b694, 'discs_fast_visibility', 'pointer'),
    (0x677438, 0x0023a7e4, 'discs_fast_advance', 'pointer'),
    (0x67744c, 0x0023adcc, 'discs_fast_publish', 'pointer'),
)

FALLTHROUGHS = (
    (0x239da4, 0xe3100101),
    (0x239dbc, 0xe19020f4),
    (0x239e4c, 0xe5940000),
    (0x239ea0, 0xe1a05000),
    (0x239ee0, 0xe1a01005),
    (0x23a514, 0xe92d407c),
    (0x23a534, 0xe3510010),
    (0x23a548, 0xebfe201e),
    (0x23a570, 0xe19020f4),
    (0x23a5c0, 0xe8bd807c),
    (0x23bab8, 0xe1a04000),
)


def apply(image, symbols):
    edits = []
    for address, expected in FALLTHROUGHS:
        if word(image, address) != expected:
            raise ValueError(f'changed native Quick Scroll continuation at {address:#x}')
    for address, expected, symbol, kind in HOOKS:
        if word(image, address) != expected:
            raise ValueError(f'changed native Quick Scroll hook at {address:#x}')
        target = symbols[symbol]
        if target < TEXT or target & 3 or target + 4 > len(image):
            raise ValueError(f'invalid Quick Scroll helper placement for {symbol}')
        edits.append((address, target if kind == 'pointer' else branch(address, target)))
    for address, value in edits:
        struct.pack_into('<I', image, address, value)
    return [address for address, _ in edits]
