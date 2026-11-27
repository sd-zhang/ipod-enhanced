/* Veneers for trim.c. Each runs with the machine state at its firmware
 * offset in the 4.3.1.1 OSOS; build.py checks every original word. */
#include "diag.h"
.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* BL at 0x1164a0 (originally the 0x208a8 veneer to the SRAM pump start).
 * r0 = engine, r1 = decoder, lr = 0x1164a4. */
.global gapless_trim_start_hook
gapless_trim_start_hook:
    push {r0-r3, r12, lr}
    bl gapless_trim_start
    pop {r0-r3, r12, lr}
    ldr pc, =0x40015914

/* BL at 0x123f98, replacing "bl 0x12ce68" (r0 = engine, r1 = state; it
 * changes only r0). */
.global gapless_trim_state_hook
gapless_trim_state_hook:
    push {r1-r3, r12, lr}
    bl gapless_trim_state_is
    pop {r1-r3, r12, lr}
    bx lr

/* Literal jump from SRAM 0x40015a78, which held "ldr r0, [r4, #0xe80]"
 * and "bl 0x400154f0". r4 = engine + 0x2000, r6 = engine. */
.global gapless_trim_publish_hook
gapless_trim_publish_hook:
    mov r0, r6
    mov r1, r8                       /* the decoder object */
    bl gapless_trim_publish
    ldr r0, [r4, #0xe80]
    ldr lr, =0x40015a80
    ldr pc, =0x400154f0

/* Literal jumps from the SRAM seek message (two sites) and seek timer
 * handlers. Each replaced "ldr r1, [...]" and "mov pc, r2". */
.global gapless_trim_seek_message_hook, gapless_trim_seek_timer_hook
gapless_trim_seek_message_hook:
    ldr r1, [r5, #0x31c]
    push {r0-r3, r12, lr}
    mov r0, r1                   /* seek target */
    mov r1, #1
    b 1f
gapless_trim_seek_timer_hook:
    ldr r1, [r4, #0x9c0]
    push {r0-r3, r12, lr}
    mov r0, r1
    mov r1, #2
1:  bl gapless_trim_seek
    pop {r0-r3, r12, lr}
    mov pc, r2

/* BL at 0x15a8cc in the output worker, replacing "mov r5, r0". r0 is the
 * DSP's packed byte count and r4 the output queue. lr = 0x15a8d0. */
.global gapless_trim_worker_hook
gapless_trim_worker_hook:
    push {r1-r3, r12, lr}
    mov r1, r4
    bl gapless_trim_output
    pop {r1-r3, r12, lr}
    mov r5, r0
    bx lr

/* B at 0x133548, replacing the primary adapter's "mov r2, r7".
 * lr already points to 0x133554, the continuation after the driver call. */
.global gapless_trim_submit_hook
gapless_trim_submit_hook:
    cmp r7, #0
    bne 1f
    push {r0-r3, r12, lr}
    mov r0, r4
    mov r1, r7
    bl gapless_trim_skip_submit
    cmp r0, #0
    pop {r0-r3, r12, lr}
    bxne lr
1:  mov r2, r7
    ldr pc, =0x13354c

/* BL at 0x133be8. Same arguments and result as the stock ring release. */
.global gapless_trim_release_hook
gapless_trim_release_hook:
    b gapless_trim_release

/* B at 0x116e94, before the stock drain prologue. */
.global gapless_trim_finish_hook
gapless_trim_finish_hook:
    push {r0-r3, r12, lr}
    bl gapless_trim_finish_begin
    pop {r0-r3, r12, lr}
    push {r4, lr}
    ldr pc, =0x116e98

/* BL at 0x116ea8, replacing the native queue-empty check. */
.global gapless_trim_finish_poll_hook
gapless_trim_finish_poll_hook:
    b gapless_trim_finish_poll

/* BL at 0x15a9d0. Drain slots have no source samples to account for. */
.global gapless_trim_account_hook
gapless_trim_account_hook:
    push {r0-r3, r12, lr}
    mov r0, r1
    bl gapless_trim_account
    str r0, [sp, #4]
    pop {r0-r3, r12, lr}
    ldr pc, =0x129598

/* B at 0x116da0 (select file, then stock stop 0x116e0c), replacing
 * "push {r4, lr}". r0 = engine, r1 = file argument, lr = command case. */
.global gapless_trim_select_hook
gapless_trim_select_hook:
    push {r0-r3, r12, lr}
    bl gapless_trim_soft_select
    cmp r0, #0
    pop {r0-r3, r12, lr}
    bxne lr
    push {r4, lr}
    ldr pc, =0x116da4

#ifdef GAPLESS_DIAG
/* Event-log taps, diagnostic builds only. Each replaces the first word of
 * a stock function with B (lr intact) and re-executes it. */
.global gapless_ev_stop_hook, gapless_ev_cleanup_hook, gapless_ev_command_hook
gapless_ev_stop_hook:               /* 0x157df4, output stop */
    DIAG_EV 0x01
    push {r4, r5, r6, lr}
    ldr pc, =0x157df8
gapless_ev_cleanup_hook:            /* 0x116de4, decoder idle/cleanup, r0 = engine */
    push {r0-r3, r12, lr}
    mov r1, lr                      /* who ended the decode */
    bl gapless_cleanup_note
    pop {r0-r3, r12, lr}
    DIAG_EV 0x02
    push {r4, lr}
    ldr pc, =0x116de8
.global gapless_ev_txmode_hook, gapless_ev_statecast_hook
gapless_ev_txmode_hook:             /* 0x133070, "ldrb r0, [sp]": the TX owner's play mode */
    push {r0-r3, r12, lr}
    ldrb r0, [sp, #24]
    mov r1, r4                      /* the TX endpoint */
    bl gapless_txmode
    pop {r0-r3, r12, lr}
    ldrb r0, [sp]
    ldr pc, =0x133074
gapless_ev_statecast_hook:          /* 0x1572fc, engine state broadcast, r0 = engine */
    push {r0-r3, r12, lr}
    mov r1, lr
    bl gapless_statecast
    pop {r0-r3, r12, lr}
    push {r4, r5, lr}
    ldr pc, =0x157300
gapless_ev_command_hook:            /* BL at 0x116774, command byte read */
    push {r1-r3, r12, lr}
    ldrb r0, [r4, #0x314]
    orr r0, r0, #0x40
    bl gapless_ev
    pop {r1-r3, r12, lr}
    ldrb r0, [r4, #0x314]
    bx lr
#endif
.ltorg
