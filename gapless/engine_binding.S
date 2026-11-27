/* iPod 4G 4.3.1.1 RetailOS. Call sites and original opcodes are checked in
 * patch_engine_binding.py. All pointers below are ordinary ARM data pointers.
 * The linker supplies the writable, shared gapless_* state addresses.
 */
#include "diag.h"
.syntax unified
.arch armv4t
.arm
.text
.balign 4

.global gapless_store_request_timing
.type gapless_store_request_timing,%function
gapless_store_request_timing:
    stm     r12, {r0-r3}            /* displaced request snapshot */
    mov     r0, #0
    str     r0, [r12, #16]         /* generation before request submit */
    bx      lr

.global gapless_file_append
.type gapless_file_append,%function
gapless_file_append:
    /* 0xdc2f8 frame is 344 bytes. 0xdc43c saved its creator's LR at
     * its own SP+52. Require the expanded music constructor before taking
     * a pointer past the legacy 'tply', including on indirect callers.
     * 0xa8cf4 uses this same constructor on automatic advance.
     */
    ldr     ip, [sp, #396]
    ldr     r3, =0x000a7e24
    cmp     ip, r3
    addeq   r3, r5, #0x2a8
    movne   r3, #0
    mov     ip, r3
    mov     r3, #0                /* stock FILE flag */
.Lappend_node:
    push    {ip, lr}              /* private fifth argument */
    ldr     ip, =0x00106c98
    mov     lr, pc
    bx      ip
.Lappend_return:
    pop     {ip, pc}

.global gapless_cached_file_append
.type gapless_cached_file_append,%function
gapless_cached_file_append:
    /* At 0xdc3d8 r4 is the source record and r5 the tply. RetailOS
     * 0x43c94 retains a 'trch' for a local database track and stores it
     * in both source+0x110 and UI+0x29d0. Other MFIL producers use the
     * same append service; their requests do not own our extension.
     * 0xdc43c's saved r5/r7 are the constructor's track and UI. */
    push    {r0-r2}
    ldr     r0, [sp, #(396+12)]
    ldr     r1, =0xa7e24
    cmp     r0, r1
    bne     .Luntimed_cache
    ldr     r0, [sp, #(368+12)]
    ldr     r1, [r0, #0xc]
    ldr     r2, =0x46494c45       /* database FILE, effective MFIL */
    cmp     r1, r2
    bne     .Luntimed_cache
    ldr     r2, [sp, #(376+12)]
    add     r2, r2, #0x2000
    ldr     r2, [r2, #0x9d0]
    ldr     r1, [r4, #0x110]      /* stock already uses this live object */
    cmp     r1, #0
    beq     .Luntimed_cache
    cmp     r1, r2
    bne     .Luntimed_cache
    ldr     r2, [r1, #4]
    ldr     ip, =0x74726368
    cmp     r2, ip
    ldreq   r0, [r0, #0x14]
    ldreq   r2, [r1, #0x18]
    cmpeq   r0, r2
    addeq   ip, r5, #0x2a8
    beq     .Lcache_append
.Luntimed_cache:
    mov     ip, #0
.Lcache_append:
    pop     {r0-r2}
    b       .Lappend_node         /* r3=1, native MFIL notification */

.global gapless_make_file_node
.type gapless_make_file_node,%function
gapless_make_file_node:
    /* 0x104c30, 0x106cd4 and 0x106c98 have 104-, 24-, 16-byte
     * stack frames. The saved caller LR at SP+140 authenticates the
     * optional fifth argument at +144. All other appends get a zero tail.
     */
    ldr     ip, [sp, #140]
    adr     r1, .Lappend_return
    cmp     ip, r1
    ldreq   r3, [sp, #144]
    movne   r3, #0
    push    {r3, r4, r5, lr}
    mov     r0, #84               /* stock file node (64) plus 5 words */
    ldr     ip, =0x001045b8
    mov     lr, pc
    bx      ip
    cmp     r0, #0
    beq     .Lnode_return
    ldr     ip, =0x000f1104       /* original initializer on allocated node */
    mov     lr, pc
    bx      ip
    ldr     r3, [sp]
    cmp     r3, #0
    adr     ip, .Lempty_timing
    movne   ip, r3
    ldm     ip, {r1-r5}
    add     ip, r0, #64
    stm     ip, {r1-r5}
.Lnode_return:
    pop     {r3, r4, r5, pc}

.global gapless_note_append
.type gapless_note_append,%function
gapless_note_append:
    /* Both successful list insertion paths join here, under the list lock. */
    DIAG_TIME DG_T_NOTE
    DIAG_EV 0x06
    ldr     r1, [r4, #80]
    cmp     r1, #0
    ldrne   ip, =gapless_handoff_state
    strne   r1, [ip, #4]
#ifdef GAPLESS_DIAG
    ldr     ip, =gapless_diag + 4 * DG_NOTE_CK
    str     r1, [ip]
#endif
    mov     r0, #1                /* displaced instruction */
    bx      lr

.global gapless_bind_selection
.type gapless_bind_selection,%function
gapless_bind_selection:
    /* r7 = destination, r6 = selected list node. Other users request a
     * 48-byte file-info snapshot in a temporary buffer. Only bind the
     * actual decoder's destination at engine 0x144a91fc + 0x2e3c.
     */
    ldr     ip, =0x144ac038
    cmp     r7, ip
    bne     .Lcopy_info
    ldrsb   ip, [r6, #24]
    cmp     ip, #0
    blt     .Lcopy_info
    push    {r0-r5, r12, lr}
    DIAG_INC DG_BIND
    DIAG_TIME DG_T_BIND
    DIAG_EV 0x07
    ldr     ip, =gapless_engine_timing
    ldr     r4, [r6, #80]
    cmp     r4, #0
    beq     .Lbind                /* untagged selects always expire cursor */
    ldr     r5, [ip, #32]
    cmp     r4, r5
    beq     .Lbound               /* same valid cookie may be a codec retry */
.Lbind:
    mov     r5, #0
    str     r5, [ip, #28]         /* cursor disabled until decoder origin */
    str     r5, [ip, #16]
    str     r5, [ip, #20]
    str     r5, [ip, #24]
    add     r5, r6, #64
    ldm     r5, {r0-r3}
    stm     ip, {r0-r3}
    str     r4, [ip, #32]
.Lbound:
    pop     {r0-r5, r12, lr}
.Lcopy_info:
    ldr     ip, =0x000000ec        /* tail-call displaced 48-byte memcpy */
    bx      ip
.balign 4
.Lempty_timing:
    .word   0, 0, 0, 0, 0
.ltorg

.section .bss.gapless_engine_timing,"aw",%nobits
.balign 4
.global gapless_engine_timing
.type gapless_engine_timing,%object
gapless_engine_timing:
    .space 36                     /* timing, cursor, selected request cookie */
.size gapless_engine_timing, . - gapless_engine_timing
