"""Install stock 5.5G 1.3 album disc separator hooks."""
import struct

from image import word
from layout import TEXT
from pomodoro_patch import branch

# Every overwritten word is checked before any edit is made. The native
# helpers and fallthroughs use the stock 1.3 addresses shown beside each site.
# Kinds are ARM branch, ARM linked branch, conditional EQ branch and pointer.
HOOKS = (
    (0x0cc810, 0xebff6e58, 'discs_index_alloc', 'bl'),
    (0x0cc960, 0xebffb56e, 'discs_finish_index_hook', 'bl'),
    (0x19eccc, 0xeb02e163, 'discs_selected_song', 'bl'),
    (0x1e8508, 0xeb01b1af, 'discs_visible_count', 'bl'),
    (0x23a164, 0xe59420b4, 'discs_previous_hook', 'b'),
    (0x23a364, 0xe0801006, 'discs_next_hook', 'b'),
    (0x23a37c, 0x059410b4, 'discs_forward_wrap_hook', 'beq'),
    (0x23a660, 0xe0020190, 'discs_reveal_hook', 'b'),
    (0x66b684, 0x0017f49c, 'discs_model_property', 'pointer'),
    (0x66fbe0, 0x0019d148, 'discs_flash_song', 'pointer'),
    (0x6773dc, 0x0023a064, 'discs_select', 'pointer'),
    (0x67741c, 0x002397a8, 'discs_draw_row', 'pointer'),
)

# Extra stock words consumed by the inline trampolines.
FALLTHROUGHS = (
    (0x0cc964, 0xe59d0018),
    (0x0cc968, 0xe580bc88),
    (0x23a168, 0xe0401005),
    (0x23a16c, 0xe1520001),
    (0x23a368, 0xe1520001),
    (0x23a380, 0x12421001),
    (0x23a664, 0xe3a01000),
)


def apply(image, symbols):
    edits = []
    for address, expected in FALLTHROUGHS:
        if word(image, address) != expected:
            raise ValueError(f'changed native disc continuation at {address:#x}')
    for address, expected, symbol, kind in HOOKS:
        if word(image, address) != expected:
            raise ValueError(f'changed native disc hook at {address:#x}')
        target = symbols[symbol]
        if target < TEXT or target & 3 or target + 4 > len(image):
            raise ValueError(f'invalid native disc helper placement for {symbol}')
        if kind == 'pointer':
            replacement = target
        else:
            replacement = branch(address, target, kind == 'bl')
            if kind == 'beq':
                replacement &= 0x0fffffff
        edits.append((address, replacement))
    for address, value in edits:
        struct.pack_into('<I', image, address, value)
    return [address for address, _ in edits]
