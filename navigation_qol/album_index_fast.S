.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* Preserve the pinned letters_index_accept frame and its existing cache write.
 * Albums with a host hint or an immediate ASCII letter need no UTF-16 prefix
 * parser. All other names run the unchanged r32 implementation. No I/O, new
 * index state, locale policy or sort-order change is introduced.
 */
.global album_index_accept
.type album_index_accept, %function
album_index_accept:
    cmp r11, #6
    ldrne pc, 1f
    push {r0-r3, r12, lr}
    ldr r0, [r6, #0x20]
    ldrb r0, [r0, #0xfd]
    cmp r0, #0
    ldrne pc, 2f
    ldr r1, [sp, #32]       /* original sp+8: name length */
    cmp r1, #0
    beq 3f
    ldr r0, [sp, #0x22c]    /* original sp+0x214: UTF-16 name */
    ldrh r0, [r0]
    orr r1, r0, #32
    sub r1, r1, #97
    cmp r1, #25
    bhi 3f
    bic r0, r0, #32
    ldr pc, 2f
3:
    pop {r0-r3, r12, lr}
    ldr pc, 1f
1:  .word 0x35ca48          /* original complete acceptance helper */
2:  .word 0x35ca64          /* its cache write, register restore and return */
.size album_index_accept, . - album_index_accept
