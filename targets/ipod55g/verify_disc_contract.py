"""Independent 5.5G native disc-hook and ownership contracts.

This module deliberately does not import either patch installer.
"""
import hashlib
import struct
from image import word

TEXT = 0x7358c0
BRANCHES = (
    (0xcc810, 0xebff6e58, 'discs_index_alloc', 0xeb),
    (0xcc960, 0xebffb56e, 'discs_finish_index_hook', 0xeb),
    (0x19eccc, 0xeb02e163, 'discs_selected_song', 0xeb),
    (0x1e8508, 0xeb01b1af, 'discs_visible_count', 0xeb),
    (0x23a164, 0xe59420b4, 'discs_previous_hook', 0xea),
    (0x23a364, 0xe0801006, 'discs_next_hook', 0xea),
    (0x23a37c, 0x059410b4, 'discs_forward_wrap_hook', 0x0a),
    (0x23a660, 0xe0020190, 'discs_reveal_hook', 0xea),
    (0x239db8, 0xe3a00f4d, 'discs_fast_begin_hook', 0xea),
    (0x23a56c, 0xe3a00f4d, 'discs_fast_gate_hook', 0xea),
    (0x23bab4, 0xe92d4010, 'discs_fast_destroy_hook', 0xea),
)
POINTERS = (
    (0x66b684, 0x17f49c, 'discs_model_property'),
    (0x66fbe0, 0x19d148, 'discs_flash_song'),
    (0x6773dc, 0x23a064, 'discs_select'),
    (0x67741c, 0x2397a8, 'discs_draw_row'),
    (0x6772c0, 0x23b704, 'discs_fast_event'),
    (0x677334, 0x23b694, 'discs_fast_visibility'),
    (0x677438, 0x23a7e4, 'discs_fast_advance'),
    (0x67744c, 0x23adcc, 'discs_fast_publish'),
)
CONTINUATIONS = (
    (0xcc964, 0xe59d0018), (0xcc968, 0xe580bc88),
    (0x23a168, 0xe0401005), (0x23a16c, 0xe1520001),
    (0x23a368, 0xe1520001), (0x23a380, 0x12421001),
    (0x23a664, 0xe3a01000),
    (0x239da4, 0xe3100101), (0x239dbc, 0xe19020f4),
    (0x239e4c, 0xe5940000),
    (0x239ea0, 0xe1a05000), (0x239ee0, 0xe1a01005),
    (0x23a514, 0xe92d407c),
    (0x23a548, 0xebfe201e), (0x23a570, 0xe19020f4),
    (0x23a5c0, 0xe8bd807c), (0x23bab8, 0xe1a04000),
)
REGIONS = (
    (0xcc7a8, 0xcc998, '533fccaf869b4d22253f82eba5dbafaedad9ec75f235337129397cf9c85b3a26'),
    (0xd7a60, 0xd7aa0, 'bcf3e5936d66e5d422d9eb8fb56bf3e50ea8ead3aa41c878c1afb86b0a3dac74'),
    (0xa376c, 0xa37b8, '711abed895a46f89e3f2834c641bd7c62b0ff8880abcc318f947e67d58156658'),
    (0x23a120, 0x23a3cc, '746b482dc951ac7bd27b4897707e3f425b581562f0098d33ccaa28c820e1b3a3'),
    (0x23a5ec, 0x23a6d0, 'fc76b337529a2e398b46ce1ca4b34c9f7640167c107fa561611277c63a74c9e0'),
    (0x239d50, 0x239e58, '3fcbba81c59b9ee371340821abeaed5cbf4d1450938e310b23dcded20bc23002'),
    (0x23a514, 0x23a5c4, '3cc9eaea34690bd70dc8f91d29ee248036e57052b158160dfb904c000efcc594'),
    (0x23b694, 0x23b704, '492b07cbf7ca303518ef67f63628b701eccb543515a9ab2ea3a04c494a792df5'),
    (0x23b704, 0x23b97c, 'b24f1a0d0059503f5a40bd328098bd4b3d161f924b4dbeb236441bc9b3977afa'),
    (0x23bab4, 0x23bb60, '1806bc92078b42d815998d671f13f6da8e4503c14b1245a312d0fa3c308f9b32'),
    (0x17f58c, 0x17f73c, '6dc926902ef46bc2af0676d90e486ea43b04e8b28bc1d27b03feb71ba4a181cc'),
    (0x1c2418, 0x1c26e4, 'e3da1c0ae26af01f828096700c02fc5092001a27475f98c5bdf1b52cd7d29779'),
    (0x239e4c, 0x239f54, 'fdc34c08f773f1cb5b932b6903552f38e775fc54a40180407bafc78cfb58bbba'),
)


def audit(image, stock, symbols, expected):
    if 'discs_index_alloc' not in symbols:
        return None  # The unchanged r9 package still has its original contract.
    originals = {}
    for at, original, name, opcode in BRANCHES:
        assert word(stock, at) == original, f'disc stock branch {at:#x}'
        target = symbols[name]
        assert TEXT <= target < symbols['__extension_end'] and target % 4 == 0, 'disc helper placement'
        value = word(image, at)
        delta = value & 0xffffff
        if delta & 0x800000: delta -= 0x1000000
        assert value >> 24 == opcode and at+8+delta*4 == target, f'disc branch {at:#x}'
        expected[at], originals[at] = value, original
    for at, original, name in POINTERS:
        assert word(stock, at) == original, f'disc stock pointer {at:#x}'
        target = symbols[name]
        assert TEXT <= target < symbols['__extension_end'] and target % 4 == 0, 'disc helper placement'
        assert word(image, at) == target, f'disc pointer {at:#x}'
        expected[at], originals[at] = target, original
    for at, value in CONTINUATIONS:
        assert word(image, at) == value, f'disc native continuation {at:#x}'
    # Independent of the installer: only the shared entry magnitude changes.
    assert word(stock, 0x23a534) == 0xe3510010, 'Quick Scroll stock entry comparison'
    assert word(image, 0x23a534) == 0xe351000d, 'Quick Scroll entry comparison'
    expected[0x23a534], originals[0x23a534] = 0xe351000d, 0xe3510010
    for start, stop, digest in REGIONS:
        region = bytearray(image[start:stop])
        for at, value in originals.items():
            if start <= at < stop: struct.pack_into('<I', region, at-start, value)
        assert hashlib.sha256(region).hexdigest() == digest, f'disc native contract {start:#x}'
    return {'index_footer_bytes': 1032, 'max_disc_runs': 256,
            'native_song_indexes_preserved': True, 'native_contract_regions': len(REGIONS),
            'numeric_disc_quick_scroll': True, 'quick_scroll_entry_steps': 13,
            'stock_cadence_and_acceleration_preserved': True}
