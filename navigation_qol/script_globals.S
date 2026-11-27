/* 4G 4.3.1.1 resolver 0x94a9c: r0 is the signed-decoded atom,
 * ip points to the implicit-function endpoint, r4/r5 are output pointers.
 * The checked package profile reserves 2461..2535 for implicit globals.
 * Unsigned subtraction excludes negative/large atoms without aliasing.
 * Both stock continuations set flags before consuming them. The fallback
 * restores displaced r2, and stock 0x94aa0 immediately replaces scratch r1.
 */
.syntax unified
.arch armv4t
.arm
.text
.balign 4
.global ipod4g_script_globals
.type ipod4g_script_globals, %function
ipod4g_script_globals:
    ldr r1, 1f
    sub r1, r0, r1
    cmp r1, #75
    ldrlo pc, 2f
    ldr r2, 3f
    ldr pc, 4f
1:  .word 2461
2:  .word 0x94af4
3:  .word 0x1041fb1c
4:  .word 0x94aa0
.size ipod4g_script_globals, .-ipod4g_script_globals
