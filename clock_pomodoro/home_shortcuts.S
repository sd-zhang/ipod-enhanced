.syntax unified
.arm
.text
.balign 4

/* These three formerly vacant Main Menu slots use independent persisted bits.
 * The native table supplies its index in r0; each leaf returns only its mask.
 */
.global pomo_home_alarm_mask
.type pomo_home_alarm_mask, %function
pomo_home_alarm_mask:
    mov r0, #0x100000
    mov pc, lr

.global pomo_home_sleep_mask
.type pomo_home_sleep_mask, %function
pomo_home_sleep_mask:
    mov r0, #0x200000
    mov pc, lr

.global pomo_home_date_mask
.type pomo_home_date_mask, %function
pomo_home_date_mask:
    mov r0, #0x800000
    mov pc, lr

/* Entered only when the stock 733d0 BNE would reject this property key.
 * Preserve all arguments/locals and route just 2607..2609 to the stock boolean
 * getter. Every other key keeps the original unhandled-property return.
 */
.global pomo_home_getter
.type pomo_home_getter, %function
pomo_home_getter:
    sub r12, r2, #0x2600
    sub r12, r12, #7
    cmp r12, #2
    ldrls pc, =0x73d8c
    ldr pc, =0x73e20
.ltorg
