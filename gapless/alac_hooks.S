.syntax unified
.arm
.text
.global gapless_alac_decoded_hook
.type gapless_alac_decoded_hook,%function
gapless_alac_decoded_hook:
    /* Native successful decode, before zero fill. r4 = decoder + 0x8000.
     * Original sp+8 is the true count; sp+44 is the decoded buffer. */
    stmdb sp!, {r0-r3, r12, lr}
    ldr r0, [sp, #68]
    ldr r1, [sp, #32]
    ldr r2, [r4, #0x58]
    sub r3, r4, #0x8000
    ldr r3, [r3, #4]
    bl gapless_alac_decoded
    ldmia sp!, {r0-r3, r12, lr}
    ldr r2, [r4, #0x58]
    bx lr
.size gapless_alac_decoded_hook, .-gapless_alac_decoded_hook
