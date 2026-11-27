.syntax unified
.arm
.text
.balign 4
.global alarm_original_defaults
alarm_original_defaults:
    push {r4-r8, lr}
    ldr pc, =0xf3dc4

.global alarm_preferences_loaded_hook
alarm_preferences_loaded_hook:
    push {r0-r3, r12, lr}
    mov r0, r4
    bl alarm_preferences_loaded
    pop {r0-r3, r12, lr}
    strb r7, [r4, #0x99]
    ldr pc, =0xaf890
.ltorg
