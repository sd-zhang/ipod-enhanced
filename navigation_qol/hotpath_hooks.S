.syntax unified
.arch armv4t
.arm
.section .text.letters_wheel_hook,"ax",%progbits
.balign 4

/* Same ABI and native continuations as letters_hooks.S. Keeping this one
 * entry separate lets the linker discard all unrelated letter code/data. */
.global letters_wheel_hook
.type letters_wheel_hook, %function
letters_wheel_hook:
    mov r0, r4
    mov r1, r5
    bl letters_wheel
    cmp r0, #0
    ldrne pc, =0x8676c
    cmp r5, #0
    ldr pc, =0x86704
.size letters_wheel_hook, .-letters_wheel_hook
.ltorg
