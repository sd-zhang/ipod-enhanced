/* Native ScriptVM globals cannot name an explicit function in this profile.
 * r0 is the decoded atom, ip the native implicit-function endpoint pointer.
 * Only scratch r1/r2 and flags change; stock continuations overwrite flags.
 */
.syntax unified
.arm
.text
.balign 4
.global ipod55g_script_globals
.type ipod55g_script_globals, %function
ipod55g_script_globals:
    ldr r1, 1f
    sub r1, r0, r1
    cmp r1, #83
    ldrlo pc, 2f
    ldr r2, 3f
    ldr pc, 4f
1:  .word 5010
2:  .word 0xd6348
3:  .word 0x108317b0
4:  .word 0xd62f4
.size ipod55g_script_globals, .-ipod55g_script_globals
