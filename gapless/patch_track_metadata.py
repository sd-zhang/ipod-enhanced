"""Experimental gapless metadata import for the 4G monochrome 4.3.1.1 OSOS.

Input and output are extracted OSOS payloads. Offsets use the OSOS origin.
"""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from struct import pack, pack_into

STOCK_SHA256 = "e6068744c2922845029591b54834b5447425701441daee1a7f11cfc5ba4bbd2a"

# This block replaces the parser's precomputation of twelve child field
# addresses. The thirteen uses below now compute them directly from r4.
# ARMv4T in ARM state. r4 is the track. lr was saved by the parser's prologue.
# Its next live return address is written by a subsequent BL.
IMPORT_AT = 0xb6b34
IMPORT_WORDS = (
    0xe3a09000,  # mov r9, #0                   ; child index for stock loop
    0xe59d0428,  # ldr r0, [sp, #0x428]          ; normalized header length
    0xe35000cc,  # cmp r0, #0xcc
    0x3a000011,  # blo done                     ; zero-initialized track
    0xe28dcb01,  # add r12, sp, #0x400           ; table offsets index stack
    0xe28420ec,  # add r2, r4, #0xec             ; first new track field
    0xe28f303c,  # adr r3, order
    0xe5d7e083,  # ldrb lr, [r7, #0x83]          ; stock DB byte-order flag
    0xe35e0000,  # cmp lr, #0
    0x12833004,  # addne r3, r3, #4              ; BE sample count high first
    0xe4d30001,  # loop: ldrb r0, [r3], #1
    0xe79c0000,  # ldr r0, [r12, r0]
    0xe35e0000,  # cmp lr, #0
    0x10201860,  # eorne r1, r0, r0, ror #16     ; reverse BE word on ARMv4T
    0x13c118ff,  # bicne r1, r1, #0xff0000
    0x11a00460,  # rorne r0, r0, #8
    0x10200421,  # eorne r0, r0, r1, lsr #8
    0xe4820004,  # str r0, [r2], #4
    0xe2899001,  # add r9, r9, #1
    0xe3590004,  # cmp r9, #4
    0x1afffff4,  # bne loop
    0xe3a09000,  # mov r9, #0                   ; restore stock child index
    0xea0000d5,  # done: b 0xb6ee8               ; stock child-loop test
    0xece4e0dc,  # order: .byte 0xdc, 0xe0, 0xe4, 0xec
    0xece0e4dc,  #        .byte 0xdc, 0xe4, 0xe0, 0xec
)

# (OSOS offset, destination register, track field address).
CHILD_FIELDS = (
    (0xb6c84, 3, 0x24), (0xb6cb0, 3, 0x30), (0xb6cdc, 3, 0x34),
    (0xb6cf0, 3, 0x28), (0xb6d1c, 3, 0x38), (0xb6d30, 3, 0x3c),
    (0xb6d5c, 3, 0x40), (0xb6d70, 3, 0x44), (0xb6de4, 2, 0x44),
    (0xb6df8, 3, 0x48), (0xb6e0c, 3, 0x4c), (0xb6e20, 3, 0x50),
    (0xb6e34, 3, 0x54),
)


def patch_osos(stock: bytes) -> bytes:
    if sha256(stock).hexdigest() != STOCK_SHA256:
        raise ValueError("expected the unmodified 4.3.1.1 monochrome OSOS")
    image = bytearray(stock)
    pack_into("<I", image, 0x8bf18, 0xe3a000fc)  # track pool byte size
    pack_into("<I", image, 0xda208, 0xe3a020fc)  # whole-track clone byte count
    code = pack(f"<{len(IMPORT_WORDS)}I", *IMPORT_WORDS)
    image[IMPORT_AT:IMPORT_AT + len(code)] = code
    for offset, register, field in CHILD_FIELDS:
        # add Rd, r4, #field
        pack_into("<I", image, offset, 0xe2840000 | register << 12 | field)
    return bytes(image)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("stock_osos", type=Path)
    parser.add_argument("output_osos", type=Path)
    args = parser.parse_args()
    patched = patch_osos(args.stock_osos.read_bytes())
    with args.output_osos.open("xb") as output:
        output.write(patched)
    print(f"{args.output_osos}  SHA-256 {sha256(patched).hexdigest()}")
