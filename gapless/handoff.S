/* 4.3.1.1 OSOS ARM playback service. All absolute stock addresses are
 * image-relative. The linker supplies gapless_fw_base and the two BSS symbols.
 * The ABI for each veneer is the machine state at its named firmware offset.
 */
#include "ring_geometry.h"
#include "diag.h"
.syntax unified
.arm
.text
.p2align 2

/* First two words have independent publishers. The music request constructor
 * writes GENERATION. File-list insertion writes PUBLISHED after enqueue. The
 * decode worker owns the remaining words. Generation zero means ineligible.
 */
.equ GENERATION, 0
.equ PUBLISHED, 4
.equ CLEAN, 8
.equ EXPECTED, 12
.equ OUTPUT, 16
.equ RING, 20
.equ RATE, 24
.equ FORMAT, 28
.equ PHASE, 32                  /* 0 idle, 1 held, 2 parsed, 3 reused, 4 deferred setup, 5 reloading */
.equ ENGINE, 36
.equ LAST_REQUEST, 40
.equ PROSPECT, 44                /* outgoing request cookie; published last by UI */
.equ NEXT_ITEM, 48
.equ NEXT_ID, 52
.equ WAKE_COOKIE, 56             /* nonzero only for the decoder-before-UI case */
.equ DISPATCHING, 60
.equ EOF_TICK, 64
.equ RETIRED_HEAD, 68            /* UI task exclusively owns this linked list */
.equ HARDWARE_TX, 72             /* physical FIFO owned by the outgoing track */
.equ DRIVER, 76                  /* A's primary output adapter */
.equ HANDOFF_BYTES, 80

/* BL at 0xa7e20, in the extended music request constructor. The saved
 * frame offsets are fixed by the three stock ARM function prologues. */
.global gapless_stage_successor
gapless_stage_successor:
    DIAG_INC DG_STAGE
    DIAG_TIME DG_T_STAGE
    DIAG_EV 0x05
    push {r0-r3}
    ldr r0, =gapless_handoff_state
    ldr r1, [r0, #GENERATION]
    add r1, r1, #1
    bics r1, r1, #0x80000000
    moveq r1, #1
    str r1, [r0, #GENERATION]
    orr r2, r1, #0x80000000     /* manual play/retry retains a unique cursor */
    ldr r0, [sp, #(0x26c+16)]
    ldr r3, =gapless_fw_base+0xa8cf8
    cmp r0, r3
    bne 1f
    ldr r0, [sp, #(0x2a4+16)]
    ldr r3, =gapless_fw_base+0xa89ec
    cmp r0, r3
    bne 1f
    ldr r0, [sp, #(0x2c4+16)]
    ldr r3, =gapless_fw_base+0xa8004
    cmp r0, r3
    bne 1f
    ldr r0, =gapless_handoff_state
    ldr r3, [r0, #WAKE_COOKIE]
    cmp r3, #0
    beq 3f
    ldr r3, [r0, #EXPECTED]
    cmp r3, r1
    bne 1f
    ldr r3, [r0, #NEXT_ITEM]
    cmp r3, r9
    bne 1f
    ldr r3, [r9, #0x10]
    ldr r0, [r0, #NEXT_ID]
    cmp r3, r0
    bne 1f
3:  mov r2, r1
1:  str r2, [r4, #0x2cc]
#ifdef GAPLESS_DIAG
    push {r0}
    ldr r0, =gapless_diag + 4 * DG_STAGE_CK
    str r2, [r0]
    pop {r0}
#endif
    ldr r0, =gapless_handoff_state
    str r4, [r0, #LAST_REQUEST]
    pop {r0-r3}
    ldr pc, =gapless_fw_base+0xdc43c

/* BL at 0x11656c. The original next instruction ORs the decoder EOF and
 * the independent forced-completion local. Only the former qualifies. */
.global gapless_classify_eof
gapless_classify_eof:
    ldr r1, [sp, #0x68]
    ldr r12, =gapless_handoff_state
    mov r2, #0
    cmp r0, #0
    beq 1f
    cmp r1, #0
    bne 1f
    ldrb r3, [r7, #0xc]
    cmp r3, #0
    ldrbeq r3, [r4, #0x994]
    cmpeq r3, #0
    moveq r2, #1
1:  str r2, [r12, #CLEAN]
    bx lr

/* BL at 0x1165bc. Postpone the stock status-9 wait for a queued successor
 * or a live ordinary playlist prospect with outgoing PCM still in flight. */
.global gapless_drain_at_eof
gapless_drain_at_eof:
    DIAG_INC DG_EOF
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #CLEAN]
    mov r2, #0
    str r2, [r12, #CLEAN]
    str r2, [r12, #PHASE]
    str r2, [r12, #WAKE_COOKIE]
    str r2, [r12, #DISPATCHING]
    cmp r1, #1
    bne 2f
    push {r0-r3, r12, lr}
    bl gapless_trim_eof
    pop {r0-r3, r12, lr}
    ldrb r3, [r4, #0xe54]        /* validated outgoing MP3 and AAC-LC paths */
    cmp r3, #0
    cmpne r3, #10
    cmpne r3, #11                /* Apple Lossless: no padding, 8 long slots */
    bne 2f
    ldr r2, =gapless_engine_timing
    ldr r2, [r2, #0x20]          /* outgoing file's selected cookie */
    ldr r1, [r12, #PUBLISHED]
    cmp r1, #0
    beq .Ltry_prospect
    ldr r3, [r12, #GENERATION]
    cmp r1, r3
    bne .Ltry_prospect
    cmp r1, r2
    bne .Lhold
.Ltry_prospect:
    cmp r2, #0
    beq 2f
    ldr r1, [r12, #PROSPECT]
    cmp r1, r2
    bne 2f
    bic r1, r2, #0x80000000
    ldr r3, [r12, #GENERATION]
    cmp r1, r3
    bne 2f
    add r1, r1, #1
    bics r1, r1, #0x80000000
    moveq r1, #1
    str r2, [r12, #WAKE_COOKIE]
    ldr r3, =0x60005010           /* stock PortalPlayer microsecond counter */
    ldr r3, [r3]
    str r3, [r12, #EOF_TICK]
.Lhold:
    ldr r2, [r8, #0x940]
    cmp r2, #0
    beq 2f
    ldr r2, [r4, #0xe80]
    cmp r2, #0
    beq 2f
    ldr r3, [r2, #0x60]
    cmp r3, #0
    beq 2f
    push {r0-r3, r12, lr}
    mov r0, r2
    bl gapless_output_tx
    ldr r12, =gapless_handoff_state
    str r0, [r12, #HARDWARE_TX]
    ldr r1, [sp, #8]              /* saved r2 is A's output queue */
    ldr r1, [r1, #0x70]
    str r1, [r12, #DRIVER]
    cmp r0, #0
    pop {r0-r3, r12, lr}
    beq 2f
    str r0, [r12, #ENGINE]
    str r1, [r12, #EXPECTED]
    str r2, [r12, #OUTPUT]
    str r3, [r12, #RING]
    ldr r2, [r12, #WAKE_COOKIE]
    cmp r2, #0
    beq 6f
    push {r0-r3, r12, lr}
    mov r0, r3
    bl .Lhas_pcm
    cmp r0, #0
    pop {r0-r3, r12, lr}
    beq 2f
6:  ldr r1, [r4, #0xe40]
    str r1, [r12, #RATE]
    ldrb r1, [r4, #0xe48]
    ldrb r2, [r4, #0xe49]
    orr r1, r1, r2, lsl #8
    ldrb r2, [r4, #0xe54]
    orr r1, r1, r2, lsl #16
    str r1, [r12, #FORMAT]
    mov r1, #1
    str r1, [r12, #PHASE]
    DIAG_INC DG_HELD
#ifdef GAPLESS_DIAG
    push {r0-r3, r12, lr}
    ldr r0, [r12, #RING]
    ldr r1, [r12, #WAKE_COOKIE]
    bl gapless_diag_eof
    pop {r0-r3, r12, lr}
#endif
    bx lr
2:  mov r1, #0
    str r1, [r12, #WAKE_COOKIE]
    ldr pc, =gapless_fw_base+0x116e94

/* Checks at the start of the B decoder, before the stock stop/rate change.
 * Preserve r0/r1 and their original CMP result for the conditional branch. */
.global gapless_match_decoder
gapless_match_decoder:
    DIAG_INC DG_MATCH
    DIAG_TIME DG_T_MATCH
    DIAG_EV 0x08
    push {r0-r3, r12, lr}
    ldr r12, =gapless_handoff_state
    ldr r2, [r12, #PHASE]
    cmp r2, #1
    cmpne r2, #5
    bne 2f
    TRAIL 0x11
    ldr r0, [r12, #WAKE_COOKIE]
    cmp r0, #0
    /* A successor queued before EOF skips the UI deadline, but still
     * requires A's audio to be playing: never resume an empty ring. */
    beq .Lmatch_buffered
    TRAIL 0x12
    /* The UI enforces its 500 ms deadline before claiming this cookie.
     * A validated dispatch may take longer to open B on a slow disk.
     * Still require live PCM and B's exact selected/published generation. */
    ldr r1, [r12, #DISPATCHING]
    cmp r1, r0
    beq .Lmatch_buffered
    ldr r0, =0x60005010
    ldr r0, [r0]
    ldr r1, [r12, #EOF_TICK]
    sub r0, r0, r1
    ldr r1, =500000
    cmp r0, r1
    bhi 1f
.Lmatch_buffered:
    TRAIL 0x13
    bl gapless_trim_enough_queued
    ldr r12, =gapless_handoff_state
    cmp r0, #0
    beq 1f
3:
    TRAIL 0x15
    ldr r0, [r12, #ENGINE]
    cmp r0, r5
    bne 1f
    TRAIL 0x16
    ldr r0, [r12, #EXPECTED]
    ldr r1, [r12, #GENERATION]
    cmp r1, r0
    ldreq r1, [r12, #PUBLISHED]
    cmpeq r1, r0
    ldreq r1, =gapless_engine_timing
    ldreq r1, [r1, #0x20]
    cmpeq r1, r0
    bne 1f
    TRAIL 0x17
    ldr r0, [r12, #RATE]
    ldr r1, [r4, #0xe40]
    cmp r0, r1
    bne 1f
    TRAIL 0x18
    ldrb r0, [r4, #0xe48]
    ldrb r1, [r4, #0xe49]
    orr r0, r0, r1, lsl #8
    ldr r2, [r12, #FORMAT]
    bic r1, r2, #0xff0000
    cmp r0, r1
    bne 1f
    TRAIL 0x19
    ldrb r1, [r4, #0xe54]
    mov r2, r2, lsr #16
    cmp r2, r1
    bne .Lmixed_source
    TRAIL 0x1a
    ldr r3, [r12, #PHASE]
    cmp r3, #1
    bne 1f
    TRAIL 0x1b
    ldm sp, {r0,r1}
    cmp r0, r1
    bne 1f
    TRAIL 0x1c
.Ldecoder_ready:
    mov r0, #2
    str r0, [r12, #PHASE]
    b 2f
/* A and B use different codecs. A's decoded PCM belongs to the persistent
 * output queue. Only A's decoder object and codec overlay must be replaced.
 * The original worker has freshly parsed B and resolved its registry index. */
.Lmixed_source:
    TRAIL 0x1d
    cmp r1, #0
    cmpne r1, #10
    bne 1f
    ldr r1, =0x1002              /* validated 16-bit, stereo output */
    cmp r0, r1
    bne 1f
    ldr r0, [r12, #RATE]
    cmp r0, #0
    beq 1f
    /* A still owns this converter at both file probes. B configures its
     * own settings later, before the final physical ring geometry check. */
    ldr r0, [r12, #OUTPUT]
    bl gapless_mixed_dsp_eligible
    cmp r0, #0
    ldr r12, =gapless_handoff_state
    beq 1f
    ldr r3, [r12, #PHASE]
    cmp r3, #5
    beq .Lreopened
    ldm sp, {r0,r1}
    cmn r0, #1
    cmpne r0, r1
    beq 1f
    ldr r1, [r12, #FORMAT]       /* resolve held A type using the live registry */
    mov r1, r1, lsr #16
    add r0, r5, #12
    ldr r3, =gapless_fw_base+0x208a0
    mov lr, pc
    bx r3
    ldr r1, [sp, #4]             /* native A object ID, [r7+8] */
    cmp r0, r1
    ldr r12, =gapless_handoff_state
    bne 1f
    mov r0, #5
    str r0, [r12, #PHASE]
    pop {r0-r3, r12, lr}
    /* Keep A's rate/running state and output. Close A through its real
     * vtable, then let the outer loop load B and re-enter the worker. */
    ldr pc, =gapless_fw_base+0x115f70
.Lreopened:
    ldm sp, {r0,r1}
    cmn r0, #1
    beq 1f
    cmp r0, r1                   /* fresh B's registry ID vs the new B object */
    beq .Ldecoder_ready
1:  ldr r2, [r12, #PHASE]
    cmp r2, #5
    bne .Lreject_decoder
    ldm sp, {r0,r1}
    cmn r0, #1
    beq .Lreject_decoder
    cmp r0, r1
    /* For a different, known next decoder, the native 0x115f60 switch
     * stops the output after we drain it. Avoid sending that stop twice. */
    movne r2, #1
    strne r2, [r12, #PHASE]
.Lreject_decoder:
    SITE 1
    bl .Ldrain
2:  pop {r0-r3, r12, lr}
    cmp r0, r1
    bx lr

/* Mode 2 re-entered during the overlay swap. Complete A's outstanding
 * audio teardown before dispatching the stop/manual playback command. */
.global gapless_decoder_command
gapless_decoder_command:
    ldr r12, =gapless_handoff_state
    ldr r12, [r12, #PHASE]
    cmp r12, #5
    bne 1f
    push {r0-r3, r12, lr}
    SITE 2
    bl .Ldrain
    pop {r0-r3, r12, lr}
1:  ldr pc, =gapless_fw_base+0x116758

/* BL at 0x115e64. A selected file may fail before codec negotiation. */
.global gapless_check_file_result
gapless_check_file_result:
    cmp r0, #0
    bxne lr
    push {r0-r3, r12, lr}
    SITE 3
    bl .Ldrain
    pop {r0-r3, r12, lr}
    cmp r0, #0
    bx lr

/* BL at 0x115f58. Handles the stock no-source/error cleanup route. */
.global gapless_cleanup_source
gapless_cleanup_source:
    push {r0-r3, r12, lr}
    SITE 4
    bl .Ldrain
    pop {r0-r3, r12, lr}
    ldr pc, =gapless_fw_base+0x116de4

/* BL at 0x113128. The earlier output-mode initializer zeros the two IRQ
 * pointers before slot geometry is known. At this point stock has computed
 * the requested output role/layout. Reject mismatches before that reset. */
.global gapless_reset_output_pointers
gapless_reset_output_pointers:
    DIAG_INC DG_OUTPTR
    push {r0-r3, r12, lr}
    ldr r12, =gapless_handoff_state
    ldr r0, [r12, #PHASE]
    cmp r0, #0
    beq 3f
    ldr r1, [r12, #OUTPUT]
    cmp r1, r4
    bne 3f
    cmp r0, #2
    bne 2f
    TRAIL 0x21
    ldr r0, [sp, #(24+28)]
    ldr r1, =gapless_fw_base+0x116008
    cmp r0, r1
    bne 2f
    TRAIL 0x22
    ldr r0, [r12, #EXPECTED]
    ldr r1, [r12, #GENERATION]
    cmp r0, r1
    ldreq r1, [r12, #PUBLISHED]
    cmpeq r0, r1
    bne 2f
    TRAIL 0x23
    ldr r0, [r4, #0x60]
    ldr r1, [r12, #RING]
    cmp r0, r1
    bne 2f
    TRAIL 0x24
    bl .Lcheck_route
    bne 2f
    TRAIL 0x25
    ldrb r1, [r4, #0x69]
    ldrb r2, [r0, #0x25]
    cmp r1, r2
    ldrbeq r1, [r4, #0x68]
    ldrbeq r2, [r0, #0x26]
    cmpeq r1, r2
    bne 2f
    /* The next message at 0x11315c reprograms the same driver and clears
     * its live I2S FIFO (TX vtable+12 -> 0x1449d4). Do not post it twice. */
    mov r1, #4
    str r1, [r12, #PHASE]
    pop {r0-r3, r12, lr}
    ldr pc, =gapless_fw_base+0x113160
2:
    SITE 5
    bl .Ldrain
3:  pop {r0-r3, r12, lr}
    ldr pc, =gapless_fw_base+0x14472c

/* BL at 0x14cef0. Directly before stock clears the seven cursors, rewrites
 * all descriptors and resets the direct IRQ pointers, verify actual geometry.
 * Returning 8 follows the stock outer routine's "skip remaining setup" exit.
 * The outer routine's arguments and its caller's PC are still on its stack. */
.global gapless_configure_ring
gapless_configure_ring:
    /* For ordinary stereo playback, give both codecs the MP3 physical
     * stride on cold initialization as well as on a handoff. Stock has
     * already published this codec's logical scratch and packed lengths.
     * r5 is the caller's stride for the division after 0x14cef4. */
    DIAG_INC DG_CONFIG
.Lslot_chosen:
    mov r3, r5
    push {r0-r3, r12, lr}
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #PHASE]
    cmp r1, #0
    beq 3f
    ldr r1, [r12, #OUTPUT]
    cmp r1, r4
    bne 3f                       /* another output object has no ownership */
    ldr r1, [r12, #PHASE]
    cmp r1, #4
    bne 2f
    TRAIL 0x31
    ldr r1, [sp, #(24+28)]
    ldr r2, =gapless_fw_base+0x11640c
    cmp r1, r2
    bne 2f
    TRAIL 0x32
    bl .Lcheck_route
    bne 2f
    TRAIL 0x33
    /* B's rate converter and time-stretch setup have now run. A pending
     * block must not enter a processor sized for a different codec. */
    ldr r1, [r12, #FORMAT]
    ldrb r2, [r4, #0xa0]
    cmp r2, r1, lsr #16
    beq .Lring_format_ready
    mov r0, r4
    bl gapless_mixed_dsp_eligible
    cmp r0, #0
    beq 2f
    ldr r0, [sp]
    ldr r3, [sp, #12]
    ldr r12, =gapless_handoff_state
.Lring_format_ready:
    TRAIL 0x34
    ldr r1, [r12, #EXPECTED]
    ldr r2, [r12, #GENERATION]
    cmp r1, r2
    ldreq r2, [r12, #PUBLISHED]
    cmpeq r1, r2
    bne 2f
    TRAIL 0x35
    ldr r1, [r12, #RING]
    cmp r0, r1
    bne 2f
    TRAIL 0x36
    ldr r1, [r12, #ENGINE]
    add r1, r1, #0x1000
    ldr r1, [r1, #0x940]
    cmp r1, #0
    beq 2f
    TRAIL 0x37
    ldrb r1, [r0, #0x24]
    cmp r1, #3
    ldreq r1, [r0, #0x2c]
    cmpeq r1, r3                 /* bytes per slot */
    ldrbeq r1, [r0, #0x25]
    ldreq r2, [sp, #4]
    cmpeq r1, r2                 /* ring role */
    ldrbeq r1, [r0, #0x26]
    ldreq r2, [sp, #8]
    cmpeq r1, r2                 /* PCM layout */
    bne 2f
    TRAIL 0x38
    ldr r1, [r0, #0x1c]
    cmp r1, #8
    bls .Lring_capacity_ready
    /* trim.c sized this ring from the same stride and arena B requests. */
    cmp r1, #GAPLESS_MAX_SLOTS
    bhi 2f
.Lring_capacity_ready:
    TRAIL 0x39
    /* In the primary PCM layout, cursor 5 names the next descriptor to
     * publish. A decoder may already own it for its next output callback. */
    ldrb r1, [r0, #0x25]
    cmp r1, #0
    bne 2f
    ldr r1, [r0, #0x14]
    ldr r2, [r0, #0x1c]
    cmp r1, r2
    bhs 2f
    TRAIL 0x3a
    ldr r2, [r0, #0x30]
    add r2, r2, r1, lsl #4
    ldr r1, [r12, #ENGINE]
    add r1, r1, #0x1000
    ldr r1, [r1, #0x948]
    ldrb r3, [r2, #8]
    cmp r1, #0
    bne 4f
    cmp r3, #7                   /* a reservation without its data pointer */
    beq 2f
    b 5f
4:  cmp r3, #7
    ldreq r3, [r2]
    cmpeq r3, r1
    bne 2f
    ldr r3, [r2, #0xc]
    tst r3, #0x80000000          /* owner bit in the stock PCM producer */
    beq 2f
5:  mov r1, #3
    str r1, [r12, #PHASE]
    push {r0-r3, r12, lr}
    bl gapless_trim_reused
    pop {r0-r3, r12, lr}
    DIAG_INC DG_REUSE
    DIAG_EV 0x0a
    pop {r0-r3, r12, lr}
    mov r0, #8
    bx lr
2:
    SITE 6
    bl .Ldrain
3:  pop {r0-r3, r12, lr}
    ldr pc, =gapless_fw_base+0x14dcd4

/* BL at 0x116484. The stock pump may reserve B's first PCM slot at
 * the end of its last A callback. Its verified status-7 descriptor and
 * the writing pointer must remain paired. Reset all per-track counters. */
.global gapless_reset_decoder_output
gapless_reset_decoder_output:
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #PHASE]
    cmp r1, #3
    ldrne pc, =gapless_fw_base+0x129874
    mov r1, #0
    add r0, r0, #0x1000
    ldr pc, =gapless_fw_base+0x129880

/* BL at 0x11648c. Preserve the live output flag only after reuse actually
 * committed. The incoming r0 is zero, the original value to store. */
.global gapless_clear_running
gapless_clear_running:
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #PHASE]
    cmp r1, #3
    strne r0, [r8, #0x940]
    streq r0, [r12, #PHASE]
    streq r0, [r12, #WAKE_COOKIE]
    bx lr

/* r4 holds the queue at both setup sites. Return equality in the flags,
 * with the caller's arguments intact for the native setup on rejection. */
.Lcheck_route:
    push {r0-r3, r12, lr}
    mov r0, r4
    bl gapless_output_tx
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #HARDWARE_TX]
    cmp r0, r1
    ldreq r0, [r4, #0x70]
    ldreq r1, [r12, #DRIVER]
    cmpeq r0, r1
    pop {r0-r3, r12, pc}

.Lhas_pcm:
    ldr r1, [r0, #0x1c]
    cmp r1, #GAPLESS_MAX_SLOTS
    bhi 2f
    ldr r2, [r0, #0x30]
1:  cmp r1, #0
    beq 2f
    ldrb r3, [r2, #8]
    cmp r3, #5
    cmpne r3, #6
    cmpne r3, #8
    cmpne r3, #9
    moveq r0, #1
    bxeq lr
    add r2, r2, #16
    sub r1, r1, #1
    b 1b
2:  mov r0, #0
    bx lr

/* BHI at 0x14a964. r0 is the stock arena/stride quotient, already >8.
 * Verify each native caller before reading its frame. Other ring setups
 * keep the original cap. 0x14a8d8 saves 16 bytes, 0x14dcd4 saves 24,
 * and 0x14ce68 saves 32 including its caller's return at offset 28. */
.global gapless_limit_primary_slots
gapless_limit_primary_slots:
    ldr r1, [sp, #12]
    ldr r2, =gapless_fw_base+0x14dd3c
    cmp r1, r2
    ldreq r1, [sp, #(16+20)]
    ldreq r2, =gapless_fw_base+0x14cef4
    cmpeq r1, r2
    ldreq r1, [sp, #(16+24+28)]
    ldreq r2, =gapless_fw_base+0x11640c
    cmpeq r1, r2
    movne r0, #8
    bne 1f
    ldr r1, [sp, #16]            /* queue, saved r4 of 0x14dcd4 */
    mov r2, r4
    bl gapless_trim_slot_count
1:
#ifdef GAPLESS_DIAG
    ldr r1, =gapless_diag + 4 * DG_SLOTS
    str r0, [r1]
#endif
    str r0, [r4, #0x1c]
    ldr pc, =gapless_fw_base+0x14a96c

.Ldrain:
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #PHASE]
    cmp r1, #0
    bxeq lr
    DIAG_INC DG_DRAINED
    DIAG_TIME DG_T_DRAIN
#ifdef GAPLESS_DIAG
    ldr r0, =gapless_diag
    str r1, [r0, #4 * DG_DRAIN_PH]
    ldr r2, [r0, #4 * DG_TRAIL]
    str r2, [r0, #4 * DG_D_TRAIL]
    ldr r2, [r0, #4 * DG_SITE]
    str r2, [r0, #4 * DG_D_SITE]
    ldr r2, [r12, #GENERATION]
    str r2, [r0, #4 * DG_D_GEN]
    ldr r2, [r12, #PUBLISHED]
    str r2, [r0, #4 * DG_D_PUB]
    ldr r2, [r12, #EXPECTED]
    str r2, [r0, #4 * DG_D_EXP]
    ldr r3, =gapless_engine_timing
    ldr r2, [r3, #0x20]
    str r2, [r0, #4 * DG_D_SEL]
    push {r0-r3, r12, lr}
    ldr r0, [r0, #4 * DG_SITE]
    add r0, r0, #0x10
    bl gapless_ev
    pop {r0-r3, r12, lr}
#endif
    mov r0, #0
    str r0, [r12, #PHASE]
    str r0, [r12, #WAKE_COOKIE]
    ldr r0, [r12, #ENGINE]
    cmp r1, #5
    beq .Lcancel_reload
    cmp r1, #4
    ldrne pc, =gapless_trim_drain
    /* Geometry rejected after the early mode check. Restore the complete
     * deferred setup, including its original FIFO event, before ring reset. */
    push {r4, lr}
    ldr r4, [r12, #OUTPUT]
    ldr r3, =gapless_trim_drain
    mov lr, pc
    bx r3
    mov r0, r4
    mov r1, #0
    mov r2, #2
    pop {r4, lr}
    ldr pc, =gapless_fw_base+0x113090
.Lcancel_reload:
    push {r4-r6, lr}
    mov r4, r0
    ldr r5, [r12, #OUTPUT]
    ldr r3, =gapless_fw_base+0x116e94
    mov lr, pc
    bx r3
    mov r0, r5
    ldr r3, =gapless_fw_base+0x157df4
    mov lr, pc
    bx r3
    add r4, r4, #0x1000
    mov r0, #0
    str r0, [r4, #0x940]
    bl gapless_trim_reused           /* the output is stopped: nothing deferred */
    pop {r4-r6, pc}
.ltorg

/* BL at 0x89d80. This runs in the application task, while r4 is a live
 * request in its owned list. The decoder never dereferences these UI pointers.
 * Only ordinary, forward-ordered local files are eligible for an early wake.
 * Query the same order and rank as 0xa8344/0x70e98; wrap, repeat and shuffle
 * remain entirely under the stock next-track selector. */
.global gapless_monitor_request
gapless_monitor_request:
    DIAG_INC DG_MONITOR
    push {r0-r3, r7-r11, lr}
    ldr r7, =gapless_handoff_state
    ldr r0, [r7, #LAST_REQUEST]
    cmp r0, r4
    bne .Lmonitor_return
    add r11, r5, #0x2000
    ldr r0, [r11, #0x768]
    cmp r0, #1                   /* stock playing state */
    ldrbeq r0, [r11, #0x9b0]
    cmpeq r0, #0
    ldrbeq r0, [r11, #0x9d5]
    cmpeq r0, #0
    bne .Lmonitor_invalidate
    ldrb r0, [r4, #0x2b9]
    cmp r0, #0
    bne .Lmonitor_invalidate
    ldrb r0, [r4, #0x2b6]
    cmp r0, #1
    bne .Lmonitor_invalidate
    ldr r0, [r4, #0x24]
    ldr r1, =0x46494c45
    cmp r0, r1
    ldrne r1, =0x4d46494c         /* local FILE converted by the track cache */
    cmpne r0, r1                 /* authenticated by the selected cookie below */
    bne .Lmonitor_invalidate
    ldr r8, [r4, #0x2cc]
    cmp r8, #0
    beq .Lmonitor_invalidate
    ldr r0, =gapless_engine_timing
    ldr r0, [r0, #0x20]
    cmp r0, r8
    bne .Lmonitor_invalidate
    ldr r0, [r7, #GENERATION]
    bic r1, r8, #0x80000000
    cmp r0, r1
    bne .Lmonitor_invalidate
    ldrb r0, [r5, #0x233]
    cmp r0, #0
    bne .Lmonitor_invalidate
    ldr r9, [r5, #0x2a4]         /* playlist */
    ldr r10, [r5, #0x2a8]        /* active item */
    cmp r9, #0
    cmpne r10, #0
    beq .Lmonitor_invalidate
    ldr r0, [r9, #4]
    ldr r1, =0x706c7374
    cmp r0, r1
    bne .Lmonitor_invalidate
    ldr r0, [r10]                /* item's parent playlist */
    cmp r0, r9
    bne .Lmonitor_invalidate
    ldrh r0, [r9, #0xdc]
    cmp r0, #0
    bne .Lmonitor_invalidate
    ldrb r0, [r9, #0xe4]
    tst r0, #1
    ldrbeq r0, [r9, #0xe5]
    tsteq r0, #2
    bne .Lmonitor_invalidate
    ldr r0, [r9, #0x24]
    cmp r0, #0
    beq .Lmonitor_invalidate
    ldrh r11, [r0, #0x2e]        /* playlist count */
    cmp r11, #2
    blo .Lmonitor_invalidate
    mov r0, r10
    mov r1, #39
    mov r2, #0
    ldr r3, =gapless_fw_base+0x7205c
    mov lr, pc
    bx r3
    cmp r0, #0
    blt .Lmonitor_invalidate
    add r3, r0, #1
    cmp r3, r11
    bhs .Lmonitor_invalidate
    mov r0, r9
    mov r1, #39
    mov r2, #0
    ldr r12, =gapless_fw_base+0x6e400
    mov lr, pc
    bx r12
    cmp r0, #0
    beq .Lmonitor_invalidate
    cmp r0, r10
    beq .Lmonitor_invalidate
    ldr r1, [r0, #0x20]
    cmp r1, #0
    beq .Lmonitor_invalidate
    ldr r2, [r1, #0xc]
    ldr r3, =0x46494c45
    cmp r2, r3
    bne .Lmonitor_invalidate
    mov r11, r0
    ldr r3, =gapless_fw_base+0xaaa50
    mov lr, pc
    bx r3
    cmp r0, #0
    beq .Lmonitor_invalidate
    mov r0, r11
    ldr r1, [r0, #0x10]
    cmp r1, #0
    beq .Lmonitor_invalidate
    ldr r2, [r7, #WAKE_COOKIE]
    cmp r2, r8
    bne .Lmonitor_publish
    ldr r2, [r7, #NEXT_ITEM]
    cmp r2, r0
    ldreq r2, [r7, #NEXT_ID]
    cmpeq r2, r1
    bne .Lmonitor_invalidate
    ldr r2, [r7, #PHASE]
    cmp r2, #1
    bne .Lmonitor_return
    ldr r2, =0x60005010
    ldr r2, [r2]
    ldr r3, [r7, #EOF_TICK]
    sub r2, r2, r3
    ldr r3, =500000
    cmp r2, r3
    bhi .Lmonitor_invalidate
    ldr r2, [r7, #DISPATCHING]
    cmp r2, #0
    bne .Lmonitor_return
    str r8, [r7, #DISPATCHING]
    pop {r0-r3, r7-r11, lr}
    ldr pc, =gapless_fw_base+0x89ee8
.Lmonitor_publish:
    str r0, [r7, #NEXT_ITEM]
    str r1, [r7, #NEXT_ID]
    str r8, [r7, #PROSPECT]
    b .Lmonitor_return
.Lmonitor_invalidate:
    mov r0, #0
    str r0, [r7, #PROSPECT]
    str r0, [r7, #NEXT_ITEM]
.Lmonitor_return:
    pop {r0-r3, r7-r11, lr}
    mov r0, r6
    bx lr

/* BLNE at 0xa7fe4; original condition guarantees r0 is a live request.
 * Use the existing deferred-release flag solely for the requested early
 * transition. Final and manual operations retain their old flag. */
.global gapless_defer_old_request
gapless_defer_old_request:
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #LAST_REQUEST]
    cmp r1, r0
    bne 1f
    ldr r1, [r12, #DISPATCHING]
    cmp r1, #0
    beq 1f
    ldr r2, [r0, #0x2cc]
    cmp r1, r2
    moveq r0, #1
    beq 2f
1:  ldrb r0, [r0, #0x2b8]
2:  cmp r12, #0                  /* preserve NE for the stock CMPNE at a7fe8 */
    bx lr
.ltorg

/* BL at 0xa9f04, after the application selected the deferred-release
 * branch. The old file's timing words were copied to the decoder sidecar.
 * Its first extension word can now link any number of short retired tracks. */
.global gapless_retire_request
gapless_retire_request:
    strb r8, [r6, #0x2b6]        /* displaced stock activity flag */
    ldr r12, =gapless_handoff_state
    ldr r1, [r12, #LAST_REQUEST]
    cmp r1, r6
    bxne lr
    ldr r1, [r12, #DISPATCHING]
    cmp r1, #0
    bxeq lr
    ldr r2, [r6, #0x2cc]
    cmp r1, r2
    bxne lr
    ldr r1, [r12, #RETIRED_HEAD]
    str r1, [r6, #0x2bc]
    str r6, [r12, #RETIRED_HEAD]
    bx lr

/* BL at 0xa7f20. Only requests claimed by gapless_retire_request own
 * extension links. Their old 'tply' is disposed by the normal clear/free
 * path, without sending global command 3 to the currently playing file. */
.global gapless_dispose_request
gapless_dispose_request:
    ldr r12, =gapless_handoff_state+RETIRED_HEAD
1:  ldr r1, [r12]
    cmp r1, #0
    beq .Ldispose_unclaimed
    cmp r1, r4
    addne r12, r1, #0x2bc
    bne 1b
    ldr r1, [r4, #0x2bc]
    str r1, [r12]
    mov r1, #600                 /* same clear as 0xdcdc8..dcdcc */
    ldr pc, =gapless_fw_base+0x9b640
/* Not claimed. The stock dispose sends command 3 (0x158294) to whatever is
 * playing. trim.c disposes a request that is not the one playing (its file
 * was handed over gaplessly) without that stop. */
.Ldispose_unclaimed:
    push {r0, lr}
    mov r1, r4                   /* the request; r0 = its 'tply' at +20 */
    bl gapless_trim_dispose
    cmp r0, #0
    pop {r0, lr}
    ldreq pc, =gapless_fw_base+0xdcd48
    mov r0, #0
    bx lr
.ltorg

.bss
.p2align 2
.global gapless_handoff_state
.type gapless_handoff_state,%object
gapless_handoff_state:
    .space HANDOFF_BYTES
.size gapless_handoff_state, .-gapless_handoff_state
