.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* Native af77c: r0=current time, r1=duration table, r6=read index.
 * r12 and flags are dead until the next native call. Skip all helper/stack
 * work for untagged tones when there is no previous light cue to restore. */
.global alarm_duration_hook
.type alarm_duration_hook, %function
alarm_duration_hook:
    ldr r5, [r1, r6, lsl #2]
    tst r5, #0x80000000
    bne .Lduration_slow
    ldr r12, =alarm_flash_active
    ldr r12, [r12]
    cmp r12, #0
    bxeq lr
.Lduration_slow:
    push {r0-r3, r12, lr}
    mov r0, r5
    ldr r1, =0x10419a3c
    ldr r1, [r1, r6, lsl #2]
    bl alarm_tone_begin
    pop {r0-r3, r12, lr}
    bic r5, r5, #0x80000000
    bx lr
.size alarm_duration_hook, .-alarm_duration_hook
.ltorg

/* Native af7b0: replaces only the timer-control store. r0-r3 may be live
 * in the native continuation; r4-r11 and stack follow AAPCS. */
.global alarm_timer_hook
.type alarm_timer_hook, %function
alarm_timer_hook:
    ldr r12, =alarm_flash_active
    ldr r12, [r12]
    cmp r12, #0
    streq r0, [r1, #8]
    bxeq lr
    push {r0-r3, r12, lr}
    bl alarm_timer_start
    pop {r0-r3, r12, lr}
    bx lr
.size alarm_timer_hook, .-alarm_timer_hook
.ltorg

/* Whole-call replacements: a tail branch retains the native return address
 * and needs no saved registers when there is no lit interval to service. */
.global alarm_advance_hook
.type alarm_advance_hook, %function
alarm_advance_hook:
    ldr r12, =alarm_flash_active
    ldr r12, [r12]
    cmp r12, #2
    ldrls pc, =0xaf72c
    b alarm_pattern_advance
.size alarm_advance_hook, .-alarm_advance_hook
.ltorg

.global alarm_finish_hook
.type alarm_finish_hook, %function
alarm_finish_hook:
    ldr r12, =alarm_flash_active
    ldr r12, [r12]
    cmp r12, #0
    ldreq pc, =0x60704
    b alarm_pattern_finish
.size alarm_finish_hook, .-alarm_finish_hook
.ltorg

/* Native 159658 is STREQ; BL EQ enters only on a real cancellation. Its
 * following LDREQ/STREQ require the original flags after cleanup. */
.global alarm_cancel_hook
.type alarm_cancel_hook, %function
alarm_cancel_hook:
    push {r0-r3, r12, lr}
    mrs r0, cpsr
    push {r0, r1}
    bl alarm_pattern_cancel
    pop {r0, r1}
    msr cpsr_f, r0
    pop {r0-r3, r12, lr}
    str r7, [r5, #8]
    bx lr
.size alarm_cancel_hook, .-alarm_cancel_hook
