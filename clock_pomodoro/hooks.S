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
    ldr pc, =0x1599c0

.global pomo_original_set_clock
pomo_original_set_clock:
    push {r4, r5, lr}
    ldr pc, =0xcd11c

.global pomo_original_set_zone
pomo_original_set_zone:
    push {r1-r7, lr}
    ldr pc, =0xe21bc
.ltorg
.global pomo_home_mask
.type pomo_home_mask, %function
pomo_home_mask:
    mov r0, #0x4000
    mov pc, lr
