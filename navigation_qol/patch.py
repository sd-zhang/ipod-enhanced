"""Two bounded navigation changes over the hardware-tested gapless/marquee image."""
import hashlib
import struct

SOURCE_SHA256 = "e0b9634a6aff0b3cfb466ec1a07308fa8bfe724f172511864f0cf52cf8254466"
HOLD_AT = 0x1bb16a
OLD_HOLD = bytes.fromhex("3b 09 ac 35 00 cd 39 54 00 00 3a 00 01 02 43 05")
# withAnimationsDisabled(goHome); return true. Existing event registration,
# special-screen unregistration, Backlight item and short Menu stay unchanged.
NEW_HOLD = bytes.fromhex("3b 07 4c 39 3b 07 3f 3a 00 01 02 43 05 00 00 00")
INDEX_AT = 0x4a350
ALL_GUARD_AT = 0xc5640


def branch(source, target, condition=0xe):
    displacement = target - source - 8
    if displacement % 4 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError("ARM branch out of range or unaligned")
    return (condition << 28) | 0x0a000000 | ((displacement >> 2) & 0xffffff)


def install(image):
    if hashlib.sha256(image).hexdigest() != SOURCE_SHA256:
        raise ValueError("expected the pinned gapless-menu-scroll OSOS; refusing an unknown/already patched image")
    if (image[HOLD_AT:HOLD_AT + 16] != OLD_HOLD
            or struct.unpack_from("<I", image, INDEX_AT)[0] != 0xe28d2008
            or struct.unpack_from("<I", image, ALL_GUARD_AT)[0] != 0x0a00000d):
        raise ValueError("unexpected navigation hook bytes")
    if image[-4:] != bytes.fromhex("55 aa 55 aa") or len(image) % 4:
        raise ValueError("unexpected OSOS end marker/alignment")
    hook = len(image) - 4
    # Run only after the original query predicate accepts a track. Do not
    # modify its cached predicate result: compilation tracks remain available
    # when browsing albums/songs. Reject before the dedupe name is committed.
    words = [
        0xe59d0218,                   # ldr r0, [sp, #0x218]  ; index field mask
        0xe3500004,                   # cmp r0, #4           ; Artists only
        branch(hook + 8, hook + 28, 1),  # bne resume
        0xe5960020,                   # ldr r0, [r6, #0x20]  ; native track
        0xe5d00011,                   # ldrb r0, [r0, #0x11]
        0xe3100002,                   # tst r0, #2           ; mhit compilation
        branch(hook + 24, 0x4a45c, 1),   # bne next_track
        0xe28d2008,                   # resume: add r2, sp, #8 (displaced)
        branch(hook + 32, INDEX_AT + 4),
    ]
    all_guard = hook + len(words) * 4
    # The stock UI rejects every row when the artist count is zero. A library
    # containing only compilations now has zero artists but still needs All.
    # A non-null cache proves the builder passed its nonempty/allocation checks.
    # Empty libraries and failed allocations keep the original rejection.
    words += [
        0xe3560000,                        # cmp r6, #0 ; All row only
        branch(all_guard + 4, 0xc567c, 1), # bne reject
        0xe595001c,                        # ldr r0, [r5, #0x1c] ; query
        0xe5900a8c,                        # ldr r0, [r0, #0xa8c] ; cache
        0xe3500000,                        # cmp r0, #0
        branch(all_guard + 20, 0xc567c, 0), # beq reject
        branch(all_guard + 24, 0xc5644),    # resume original All selection
    ]
    output = bytearray(image[:-4] + struct.pack(f"<{len(words)}I", *words) + image[-4:])
    output[HOLD_AT:HOLD_AT + 16] = NEW_HOLD
    struct.pack_into("<I", output, INDEX_AT, branch(INDEX_AT, hook))
    struct.pack_into("<I", output, ALL_GUARD_AT, branch(ALL_GUARD_AT, all_guard, 0))
    struct.pack_into("<I", output, 0xe8, len(output))
    for offset in (0x2e8, 0x4a8):
        struct.pack_into("<I", output, offset, 0x04000000 + len(output) - 4)
    if len(output) > 0x390000:
        raise ValueError("OSOS overlaps initialized RAM")
    return bytes(output)
