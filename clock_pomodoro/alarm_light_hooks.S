.syntax unified
.arch armv4t
.arm
.text
.balign 4

/* Native Settings getter default: r5=Settings, r4=out, r6=scratch.
 * Reuse its own prID/Str result paths and localized On/Off resources. */
.global alarm_get_hook
.type alarm_get_hook, %function
alarm_get_hook:
    ldr r12, =0x6150
    cmp r2, r12
    bne .Lunknown_get
    ldr r12, [r5, #0x58]
    cmp r12, #0
    beq .Lunknown_get
    ldr r12, [r12]
    cmp r12, #0
    beq .Lunknown_get
    ldr r12, [r12, #0xb34]
    tst r12, #0x01000000
    ldr r11, =0x27a1
    addne r11, r11, #1
    ldr r12, =0x70724944
    cmp r1, r12
    ldreq pc, =0x73830
    ldr r12, =0x53747220
    cmp r1, r12
    ldreq pc, =0x73dcc
    ldr pc, =0x73e18
.Lunknown_get:
    mov r0, #0
    ldr pc, =0x73e24
.size alarm_get_hook, .-alarm_get_hook
.ltorg

/* Native Settings setter default: r4=Settings, r5=value, r6=key.
 * Retain all unrelated unhandled properties and the native return epilogue. */
.global alarm_set_hook
.type alarm_set_hook, %function
alarm_set_hook:
    ldr r12, =0x6150
    cmp r6, r12
    bne .Lunknown_set
    push {r0-r3, r12, lr}
    mov r0, r4
    mov r2, r5
    bl alarm_set
    mov r8, r0
    pop {r0-r3, r12, lr}
    ldr pc, =0xccde0
.Lunknown_set:
    mov r8, #0
    ldr pc, =0xccde0
.size alarm_set_hook, .-alarm_set_hook
.ltorg
