"""Install the 4.3.1.1 file-node and decoder-selection hooks into a staged image."""

from struct import pack_into, unpack_from


BINDING_HOOKS = (
    (0xa7dc4, "gapless_store_request_timing", "bl"),
    (0xdc408, "gapless_file_append", "bl"),
    (0xdc3d8, "gapless_cached_file_append", "bl"),
    (0x104c70, "gapless_make_file_node", "bl"),
    (0x104cf8, "gapless_note_append", "bl"),
    (0x114178, "gapless_bind_selection", "bl"),
)
BINDING_WORDS = ((0xa7da4, 0xe3a00fb3, 0xe3a00fb4),)
HOOK_ORIGINALS = {
    0xa7dc4: 0xe88c000f, 0xdc408: 0xeb00aa22, 0xdc3d8: 0xeb00aa2e, 0x104c70: 0xebffb123,
    0x104cf8: 0xe3a00001, 0x114178: 0xebfbafdb,
}


def install_binding(image, symbols):
    """Patch checked sites in a bytearray after adding the linked ARM payload."""
    edits = list(BINDING_WORDS)
    for position, name, _ in BINDING_HOOKS:
        displacement = symbols[name] - position - 8
        if displacement & 3 or not -(1 << 25) <= displacement < 1 << 25:
            raise ValueError(f"unreachable binding hook {name} at {symbols[name]:#x}")
        opcode = 0xeb000000 | ((displacement >> 2) & 0xffffff)
        edits.append((position, HOOK_ORIGINALS[position], opcode))
    for offset, original, _ in edits:
        if unpack_from("<I", image, offset)[0] != original:
            raise ValueError(f"unexpected instruction at {offset:#x} staging engine timing")
    for offset, _, replacement in edits:
        pack_into("<I", image, offset, replacement)
