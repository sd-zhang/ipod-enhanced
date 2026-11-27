.syntax unified
.arm
.text
.balign 4

.macro trampoline name, instruction, resume
.global \name
.type \name, %function
\name:
    \instruction
    ldr pc, 1f
1:  .word \resume
.size \name, . - \name
.endm

/* Keep c3f74/c3f78's two-instruction batched/no-I/O return entirely native. */
trampoline lcd_redraw_native, "push {r4, r5, r6, r7, r8, r9, r10, lr}", 0xc3f80
trampoline lcd_init_native, "push {r3, r4, r5, r6, r7, lr}", 0x1549b8
trampoline lcd_contrast_native, "push {r4, r5, r6, lr}", 0x154e24

/* Relocate PC-relative first instructions, retaining the native destination. */
.macro relocated name, reg, resume
.global \name
.type \name, %function
\name:
    ldr \reg, 1f
    ldr pc, 2f
1:  .word 0x10419990
2:  .word \resume
.size \name, . - \name
.endm
relocated lcd_display_mode_native, r0, 0x154bd8
relocated lcd_suspend_native, r0, 0x154c98
relocated lcd_power_native, r1, 0x154cc8
