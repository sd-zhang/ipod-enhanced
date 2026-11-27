.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* 4a450: cache the accepted track's host group or already-read display name. */
.global letters_index_accept
letters_index_accept:
    push {r0-r3, r12, lr}
    add r0, sp, #24
    ldr r1, [r0, #8]
    ldr r0, [r0, #0x214]
    ldr r2, [r6, #0x20]
    mov r3, r11
    bl letters_track_group
    ldr r1, [r8, #0x24]
    ldrh r1, [r1, #0x2e]
    add r1, r7, r1, lsl #2
    strb r0, [r1, r4]
    pop {r0-r3, r12, lr}
    mov r0, r4
    ldr pc, =0x4a454

/* b6ec4: native dispatch has normalized the mhod header and skipped any
 * header extension. Preserve live registers and rejoin its status handling. */
.global letters_import_sort_hook
letters_import_sort_hook:
    push {r0-r3, r12, lr}
    add r12, sp, #24
    ldr r2, [r12, #0x418]
    ldr r3, [r12, #0x414]
    ldr r0, [r12, #0x410]
    sub r3, r3, r0
    mov r0, r6
    mov r1, r4
    bl letters_import_sort
    str r0, [sp]
    pop {r0-r3, r12, lr}
    ldr pc, =0xb6ed8

.global letters_index_finish
letters_index_finish:
    push {r0-r3, r12, lr}
    mov r0, r7
    ldr r1, [r8, #0x24]
    ldrh r1, [r1, #0x2e]
    mov r2, r4
    bl letters_index_compact
    pop {r0-r3, r12, lr}
    ldr r0, [r5, #0xa70]
    ldr pc, =0x4a470
.ltorg

.global letters_alloc_hook
letters_alloc_hook:
    bl letters_accel_create
    cmp r0, #0
    ldreq pc, =0x8676c /* stock wheel lacks an allocation-failure check */
    ldr pc, =0x866a8

.global letters_wheel_hook
letters_wheel_hook:
    mov r0, r4
    mov r1, r5
    bl letters_wheel
    cmp r0, #0
    ldrne pc, =0x8676c
    cmp r5, #0 /* displaced condition used by the original normal wheel path */
    ldr pc, =0x86704
.ltorg
