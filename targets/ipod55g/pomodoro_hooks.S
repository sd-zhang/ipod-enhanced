.syntax unified
.arm
.text
.balign 4
.global pomo_boot_hook
pomo_boot_hook:
    push {r0-r3, r12, lr}
    bl pomo_boot
    pop {r0-r3, r12, lr}
    mov r0, #0x124
    bx lr

.global pomo_background_hook
pomo_background_hook:
    push {r0-r3, r12, lr}
    bl pomo_background
    pop {r0-r3, r12, lr}
    ldr pc, =0x285470

.global pomo_original_set_clock
pomo_original_set_clock:
    push {r4, r5, lr}
    ldr pc, =0xb3768

.global pomo_original_set_zone
pomo_original_set_zone:
    push {r2-r8, lr}
    ldr pc, =0xbb11c
.ltorg
