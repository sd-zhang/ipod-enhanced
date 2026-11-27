.syntax unified
.arch armv4t
.arm
.text
.balign 4

.global gapless_mp3_probe_hook
.type gapless_mp3_probe_hook,%function
gapless_mp3_probe_hook:
    /* At 0xdb594, r8 is the first 2048-byte read buffer at the selected
       audio offset. r0 is the count actually copied by FSbuf method +8.
       0x141b88 returns short reads and 0x141bd8 returns complete reads. */
    push {r0-r3, r12, lr}
    mov r1, r0
    mov r0, r8
    bl gapless_mp3_probe
    pop {r0-r3, r12, lr}
    mov r1, r8
    mov r0, r7
    ldr lr, =0x000db5a0
    ldr pc, =0x00079d64             @ original Xing probe and return value
.size gapless_mp3_probe_hook, . - gapless_mp3_probe_hook
