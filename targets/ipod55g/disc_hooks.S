.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* r0 is the database, r8 the filtered count, and fp the unpublished index. */
.global discs_finish_index_hook
.type discs_finish_index_hook,%function
discs_finish_index_hook:
    push {r0-r3, ip, lr}
    ldr r0, [sp, #48]
    mov r1, fp
    mov r2, r8
    bl discs_index_finish
    pop {r0-r3, ip, lr}
    ldr pc, 1f
1:  .word 0xb9f20
.size discs_finish_index_hook, .-discs_finish_index_hook

/* Both wheel directions keep r0 as the old selection. The previous path
 * also consumes the first selectable row in r2. */
.global discs_previous_hook
.type discs_previous_hook,%function
discs_previous_hook:
    push {r0-r3, ip, lr}
    mov r1, r0
    mov r0, r4
    rsb r2, r5, #0
    add r3, sp, #8
    bl discs_step
    str r0, [sp, #4]
    pop {r0-r3, ip, lr}
    ldr pc, 2f
2:  .word 0x23a16c
.size discs_previous_hook, .-discs_previous_hook

.global discs_next_hook
.type discs_next_hook,%function
discs_next_hook:
    push {r0-r3, ip, lr}
    mov r1, r0
    mov r0, r4
    mov r2, r6
    mov r3, #0
    bl discs_step
    str r0, [sp, #4]
    pop {r0-r3, ip, lr}
    ldr pc, 3f
3:  .word 0x23a368
.size discs_next_hook, .-discs_next_hook

/* Replaces ldreq in the wrap arm. The following movne/subne use Z. */
.global discs_forward_wrap_hook
.type discs_forward_wrap_hook,%function
discs_forward_wrap_hook:
    push {r0-r3, ip, lr}
    mov r0, r4
    bl discs_first_selectable
    str r0, [sp, #4]
    pop {r0-r3, ip, lr}
    cmp r0, r0
    ldr pc, 4f
4:  .word 0x23a380
.size discs_forward_wrap_hook, .-discs_forward_wrap_hook

/* The base row rectangle is on the native stack. Replay mul before the
 * standard translation, even when there was no heading above the song. */
.global discs_reveal_hook
.type discs_reveal_hook,%function
discs_reveal_hook:
    push {r0-r3, ip, lr}
    mov r3, r0
    mov r2, r1
    add r1, sp, #24
    mov r0, r4
    bl discs_reveal_heading
    pop {r0-r3, ip, lr}
    mul r2, r0, r1
    ldr pc, 5f
5:  .word 0x23a664
.size discs_reveal_hook, .-discs_reveal_hook
