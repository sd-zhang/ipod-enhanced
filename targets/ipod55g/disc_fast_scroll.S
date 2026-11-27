.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* Native +fa is zero and the stock wheel event carries the fast flag. */
.global discs_fast_begin_hook
.type discs_fast_begin_hook,%function
discs_fast_begin_hook:
    push {r0-r3, ip, lr}
    mov r0, r4
    bl discs_fast_begin
    cmp r0, #0
    pop {r0-r3, ip, lr}
    moveq r0, #0x134
    ldreq pc, 1f
    ldr pc, 2f
1:  .word 0x239dbc
2:  .word 0x239e4c
.size discs_fast_begin_hook, .-discs_fast_begin_hook

/* Stock enabled, previous row, magnitude and velocity checks all passed.
 * Ordinary lists continue through Apple's initial-letter comparison. */
.global discs_fast_gate_hook
.type discs_fast_gate_hook,%function
discs_fast_gate_hook:
    push {r0-r3, ip, lr}
    mov r0, r4
    /* 23a514 saved the outer wheel handler's r5 (signed effective step)
     * at original sp+12 before taking abs(r1). Our save adds 24 bytes. */
    ldr r1, [sp, #36]
    bl discs_fast_gate
    cmp r0, #0
    strge r0, [sp]
    pop {r0-r3, ip, lr}
    movlt r0, #0x134
    ldrlt pc, 3f
    ldr pc, 4f
3:  .word 0x23a570
4:  .word 0x23a5c0
.size discs_fast_gate_hook, .-discs_fast_gate_hook

/* Covers both direct destruction and the stock deleting wrapper. */
.global discs_fast_destroy_hook
.type discs_fast_destroy_hook,%function
discs_fast_destroy_hook:
    push {r0-r3, ip, lr}
    bl discs_fast_cancel
    pop {r0-r3, ip, lr}
    push {r4, lr}
    ldr pc, 5f
5:  .word 0x23bab8
.size discs_fast_destroy_hook, .-discs_fast_destroy_hook
