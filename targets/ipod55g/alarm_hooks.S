.syntax unified
.arm
.text
.align 2
.global ipod55g_alarm_original_power
.type ipod55g_alarm_original_power,%function
ipod55g_alarm_original_power:
    push {r4, r5, r6, r7, r8, lr}
    ldr pc, =0x26628c

.global ipod55g_alarm_original_brightness
.type ipod55g_alarm_original_brightness,%function
ipod55g_alarm_original_brightness:
    push {r4, lr}
    ldr pc, =0x266180

.global ipod55g_alarm_begin_hook
.type ipod55g_alarm_begin_hook,%function
ipod55g_alarm_begin_hook:
    push {r0-r12, lr}
    mov r0, r5
    ldr r1, =0x10882314
    ldr r1, [r1, r6, lsl #2]
    bl ipod55g_alarm_tone_begin
    str r0, [sp, #20]
    pop {r0-r12, lr}
    add r0, r0, r5
    bx lr

.global ipod55g_alarm_timer_hook
.type ipod55g_alarm_timer_hook,%function
ipod55g_alarm_timer_hook:
    push {r0-r12, lr}
    bl ipod55g_alarm_timer_start
    pop {r0-r12, lr}
    bx lr

.global ipod55g_alarm_cancel_hook
.type ipod55g_alarm_cancel_hook,%function
ipod55g_alarm_cancel_hook:
    push {r0-r12, lr}
    bl ipod55g_alarm_cancel
    pop {r0-r12, lr}
    ldr pc, =0x28516c
