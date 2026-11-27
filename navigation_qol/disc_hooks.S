.syntax unified
.arch armv4t
.arm
.text
.balign 4
/* Share named-list top reveal for Songs; disc mode only invalidates, since
 * its heading-aware reveal already ran. ARM tail branches retain the caller
 * LR and avoid literal loads/indirect calls in the size-constrained overlay. */
.global songs_reveal_top
.type songs_reveal_top,%function
.global native_list_invalidate
.type native_list_invalidate,%function
.global discs_jump_redraw
.type discs_jump_redraw,%function
discs_jump_redraw:
    cmp r1,#0
    bne songs_reveal_top
    b native_list_invalidate
.size discs_jump_redraw,.-discs_jump_redraw

.global songs_title_hook
.type songs_title_hook,%function
songs_title_hook:
    /* Apple's classifier returns via LDM pc and cannot return to Thumb on
     * ARMv4T. Cache in our interworking-safe helper, then tail-call Apple
     * from ARM with the original ARM return address and arguments. */
    push {r0-r3,ip,lr}
    mov r2,r4
    bl songs_cache_title
    pop {r0-r3,ip,lr}
    ldr pc,7f
7:  .word 0x92e58
.size songs_title_hook,.-songs_title_hook

.global songs_import_sort_hook
.type songs_import_sort_hook,%function
songs_import_sort_hook:
    /* Unknown metadata can tail-call Apple's ARM-only stream skipper.
     * Preserve its ARM LR; only the checked title-sort path enters Thumb. */
    cmp r2,#27
    ldrne pc,8f
    b songs_import_sort
8:  .word 0x35c5bc
.size songs_import_sort_hook,.-songs_import_sort_hook

.global discs_publish_hook
.type discs_publish_hook,%function
discs_publish_hook:
    push {r0-r3,ip,lr}
    mov r0,r4
    mov r1,r9
    mov r2,r8
    bl discs_index_finish
    pop {r0-r3,ip,lr}
    str r9,[r4,#0xaac]!
    ldr pc,1f
1:  .word 0x4bd2c
.size discs_publish_hook,.-discs_publish_hook

/* 7492c retains the property ID in lr until this shared count branch.
 * Preserve it across the new call; caller return address is already stacked. */
.global discs_count_hook
.type discs_count_hook,%function
discs_count_hook:
    mov r0,r5
    mov r1,lr
    bl discs_visible_count
    ldr pc,2f
2:  .word 0x75060
.size discs_count_hook,.-discs_count_hook

/* Replace only the native minimum-row load. r0 is the old selection and r1
 * the proposed row; preserve both and every other live scratch register.
 * Native9ca68 sets flags before the boundary decision consumes them. */
.global discs_previous_min_hook
.type discs_previous_min_hook,%function
discs_previous_min_hook:
    push {r0-r3,ip,lr}
    mov r0,r4
    bl discs_previous_min
    str r0,[sp,#8]
    pop {r0-r3,ip,lr}
    ldr pc,3f
3:  .word 0x9ca64
.size discs_previous_min_hook,.-discs_previous_min_hook

/* bfdd4 has offset the selected row rectangle at original sp+4. Expand it
 * before native dirty publication and viewport reveal. Preserve the live
 * registers, then replay the displaced add r1,sp,#4. */
.global discs_reveal_hook
.type discs_reveal_hook,%function
discs_reveal_hook:
    push {r0-r3,ip,lr}
    mov r0,r4
    add r1,sp,#28
    bl discs_reveal_rect
    pop {r0-r3,ip,lr}
    add r1,sp,#4
    ldr pc,4f
4:  .word 0xbfe68
.size discs_reveal_hook,.-discs_reveal_hook

/* Replace the native wheel entry branch rather than changing a carried ELF.
 * Its original register/continuation contract stays the same. */
.global discs_fast_wheel_hook
.type discs_fast_wheel_hook,%function
discs_fast_wheel_hook:
    mov r0,r4
    mov r1,r5
    bl discs_fast_wheel
    cmp r0,#0
    ldrne pc,5f
    cmp r5,#0
    ldr pc,6f
5:  .word 0x8676c
6:  .word 0x86704
.size discs_fast_wheel_hook,.-discs_fast_wheel_hook
