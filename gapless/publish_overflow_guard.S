.syntax unified
.arch armv4t
.arm
.text
.balign 4
.global gapless_publish_overflow_guard
.type gapless_publish_overflow_guard, %function
/* The pinned-r25 release overlay implements trim.c's counter-range fallback
 * without relocating its publisher or private state. The builder verifies
 * these absolute bindings and the displaced prologue against the source ELF.
 * AAC's legitimate -1024..-1 pre-roll/recovery positions remain enabled.
 * Only enabled changes; armed must remain set for native EOF carry draining. */
gapless_publish_overflow_guard:
    push {r4, lr}
    adr lr, .Lafter_publish
    push {r4-r11, lr}
    ldr pc, =gapless_r25_publish_resume
.Lafter_publish:
    ldr r0, =gapless_r25_position
    ldr r0, [r0]
    cmn r0, #1024
    ldrlt r1, =gapless_r25_enabled
    movlt r0, #0
    strblt r0, [r1]
    pop {r4, lr}
    bx lr
.ltorg
.size gapless_publish_overflow_guard, .-gapless_publish_overflow_guard
