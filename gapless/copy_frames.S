.syntax unified
.arch armv4t
.arm
.text
.balign 4
.global copy_frames
.type copy_frames, %function
/* Packed stereo16 frames are word aligned. FIFO compaction always moves
 * toward lower addresses. Load all four frames before writing any of them.
 * Same eight-byte stack footprint as the previous compiled C helper. */
copy_frames:
    push {r4, lr}
    cmp r2, #4
    blo 2f
1:
    ldmia r1!, {r3, r4, r12, lr}
    stmia r0!, {r3, r4, r12, lr}
    subs r2, r2, #4
    cmp r2, #4
    bhs 1b
2:
    cmp r2, #0
    beq 4f
3:
    ldr r3, [r1], #4
    str r3, [r0], #4
    subs r2, r2, #1
    bne 3b
4:
    pop {r4, lr}
    bx lr
.size copy_frames, .-copy_frames
