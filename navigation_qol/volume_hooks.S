.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* Exact displaced 14fae0 prologue, then continue the original codec routine. */
.global volume_codec_original
.type volume_codec_original, %function
volume_codec_original:
    push {r3-r9, lr}
    ldr pc, =0x14fae4
.size volume_codec_original, .-volume_codec_original
.ltorg

/* Mode2 setup equivalent to14fae0..14fb1c, then skip the left-only cache
 * comparison at14fc68. The original conversion, cache stores, two register
 * writes, error handling and epilogue all execute unchanged from14fc78.
 * No temporary mutation of the shared codec force flag is needed.
 */
.global volume_codec_refresh
.type volume_codec_refresh, %function
volume_codec_refresh:
    push {r3-r9, lr}
    mov r4, r0
    mov r5, r2
    mov r6, r3
    mov r7, #1
    ldr r2, =0x1039052e
    ldr r3, =0x10412f0c
    ldrsh r8, [r3]
    ldr pc, =0x14fc78
.size volume_codec_refresh, .-volume_codec_refresh
.ltorg
