/* Hardware diagnostics for the 4G playback patch.
 *
 * Built only with -DGAPLESS_DIAG. Counters live in the extension's zeroed,
 * uncached BSS and are shown on Settings > About > Version (see diag.c).
 * Increments are not atomic. A lost update between cores only miscounts.
 */
#ifndef IPOD_4G_GAPLESS_DIAG_H
#define IPOD_4G_GAPLESS_DIAG_H

#define DG_START      0   /* gapless_decoder_start, first pump of each decode */
#define DG_READY      1   /* gapless_decoder_ready, one per completed slot */
#define DG_READY_OK   2   /* span armed or untrimmed record accepted */
#define DG_READY_BAIL 3   /* last early-exit reason in gapless_decoder_ready */
#define DG_GEOM       4   /* output worker iterations (size hook) */
#define DG_GEOM_HIT   5   /* worker found one of our span records */
#define DG_SUBMIT     6   /* I2S submit hook entries */
#define DG_SUB_NATIVE 7   /* submits passed through unchanged */
#define DG_SUB_DROP   8   /* submits swallowed while a reset was committing */
#define DG_SUB_SPAN   9   /* submits of a trimmed span */
#define DG_DRV_FAIL  10   /* driver submit calls that returned nonzero */
#define DG_DRV_LAST  11   /* last nonzero driver result */
#define DG_RELEASE   12   /* ring release hook entries */
#define DG_REL11     13   /* releases with owner 1, action 1 */
#define DG_RESET     14   /* gapless_reset_ring entries */
#define DG_RESET_NO  15   /* resets refused (stock returns busy) */
#define DG_RETRY     16   /* retries taken by the output worker */
#define DG_FLUSH     17   /* driver flush hook */
#define DG_CANCEL    18   /* driver cancel hook */
#define DG_BIND      19   /* decoder file selection bound */
#define DG_AAC_OPEN  20   /* AAC open hook */
#define DG_MP3_PROBE 21   /* MP3 metadata probe hook */
#define DG_CONFIG    22   /* gapless_configure_ring entries */
#define DG_SLOTS     23   /* last slot count chosen at 0x14a964 */
#define DG_LOCK_MAX  24   /* longest Peterson lock wait, in loop passes */
#define DG_MATCH     25   /* gapless_match_decoder entries */
#define DG_EOF       26   /* gapless_drain_at_eof entries */
#define DG_OUTPTR    27   /* gapless_reset_output_pointers entries */
#define DG_CORES     28   /* two bits per event: bit0 CPU, bit1 COP */
#define DG_POLL_SLOW 29   /* worker poll took the locked path */
#define DG_MONITOR   30   /* gapless_monitor_request entries */
#define DG_STAGE     31   /* gapless_stage_successor entries */
#define DG_COLD      32   /* decoder starts with the output stopped */
#define DG_CONT      33   /* decoder starts with the output still running */
#define DG_BYPASS    34   /* worker passes without a trim note */
#define DG_FILL      35   /* silent fills of a fully trimmed first block */
#define DG_OUT_LAST  36   /* frames handed to the last submit */
#define DG_FIFO      37   /* frames held after the last trimmed block */
#define DG_T_DELAY   38   /* database delay of the last trimmed track */
#define DG_T_SAMPLES 39   /* database sample count (low word) */
#define DG_HELD      40   /* EOF decisions that kept the output running */
#define DG_REUSE     41   /* next track reused the running ring */
#define DG_DRAINED   42   /* handoffs abandoned to the stock drain */
#define DG_DRAIN_PH  43   /* handoff phase when last abandoned */
#define DG_UNDERRUN  44   /* worker found the I2S FIFO empty while running */
#define DG_TRAIL     45   /* last handoff check passed (see handoff.S) */
#define DG_SITE      46   /* handoff function about to drain */
#define DG_D_TRAIL   47   /* DG_TRAIL when a handoff was abandoned */
#define DG_D_SITE    48   /* DG_SITE when a handoff was abandoned */
#define DG_D_GEN     49   /* request generation at the abandon */
#define DG_D_PUB     50   /* published file-list cookie at the abandon */
#define DG_D_EXP     51   /* expected successor cookie at the abandon */
#define DG_D_SEL     52   /* decoder's selected cookie at the abandon */
#define DG_STAGE_CK  53   /* cookie given to the last music request */
#define DG_T_EOF     54   /* timer at the last EOF that kept the output running */
#define DG_EOF_WAKE  55   /* 0: successor already queued at that EOF */
#define DG_EOF_QUEUED 56  /* descriptors holding audio at that EOF */
#define DG_T_STAGE   57   /* timer at the last music request construction */
#define DG_T_NOTE    58   /* timer at the last file-list insertion */
#define DG_T_BIND    59   /* timer at the last decoder file selection */
#define DG_T_MATCH   60   /* timer at the last decoder match check */
#define DG_T_DRAIN   61   /* timer at the last abandoned handoff */
#define DG_NOTE_CK   62   /* cookie of the last file-list insertion */
#define DG_WAITS     63   /* decoder yields waiting for the queued successor */
#define DG_FRESH     64   /* first About render after a pause */
#define DG_EV_COUNT  65   /* events logged since the last held EOF */
#define DG_EV        66   /* 8 words: code << 24 | timer low 24 bits / 16 */
#define DG_PEAK      74   /* 4 words: peak level (0-127) of a track's first blocks */
#define DG_PEAK_POS  78   /* 4 words: decoded-frame position of those blocks */
#define DG_PEAK_LO   82   /* 4 words: first audible frame noted for those blocks */
#define DG_SEEKS     86   /* decoder seeks seen by the SRAM seek hooks */
#define DG_SEEK_T    87   /* target of the last seek */
#define DG_SEEK_POS  88   /* decoded frames of the track before that seek */
#define DG_SEEK_SRC  89   /* 1 message handler, 2 start-position timer */
#define DG_RESYNC_D  90   /* last AAC resync, in blocks (signed) */
#define DG_DERIVED   91   /* AAC blocks positioned from the decoder's counters */
#define DG_RESYNC    92   /* of those, blocks where the running count was off */
#define DG_SCANS     93   /* MP3 blocks published while scanning (FF/RW) */
#define DG_WHY       94   /* checks the last untrimmed block failed: 1 size, 2 wire, 4 status, 8 format */
#define DG_SKIPPED   95   /* blocks passed through untrimmed after failing a check */
#define DG_TAIL      96   /* padding frames after the last AAC track's audio (fff: unknown) */
#define DG_DROPPED   97   /* padding frames last removed at a join */
#define DG_LOGS      98   /* log files written */
#define DG_LOG_ERR   99   /* last log write failure (open or write result) */
#define DG_UNSILENCED 100  /* output buffer requests kept playing in state 2 */
#define DG_QUIET_DISPOSE 101 /* retired requests released without the stock stop */
#define DG_PAGE      102
#define DG_LAST_SHOW 103
#define DG_WORDS     104

/* Event numbers for the DG_CORES bitmask. */
#define DGC_START   0
#define DGC_READY   1
#define DGC_GEOM    2
#define DGC_SUBMIT  3
#define DGC_RELEASE 4
#define DGC_RESET   5
#define DGC_CONFIG  6
#define DGC_BIND    7

#ifdef __ASSEMBLER__

/* Increment a counter. Preserves every register and the condition flags,
 * and leaves sp unchanged, so it can precede sp-relative frame reads. */
.macro DIAG_INC index
#ifdef GAPLESS_DIAG
    push {r0, r1}
    mrs r0, cpsr
    push {r0}
    ldr r0, =gapless_diag + 4 * (\index)
    ldr r1, [r0]
    add r1, r1, #1
    str r1, [r0]
    pop {r0}
    msr cpsr_f, r0
    pop {r0, r1}
#endif
.endm

/* Store a small constant in a counter word. Same guarantees as DIAG_INC. */
.macro DIAG_STORE index, value
#ifdef GAPLESS_DIAG
    push {r0, r1}
    mrs r0, cpsr
    push {r0}
    ldr r0, =gapless_diag + 4 * (\index)
    mov r1, #(\value)
    str r1, [r0]
    pop {r0}
    msr cpsr_f, r0
    pop {r0, r1}
#endif
.endm
/* Store the microsecond timer in a counter word. Preserves registers,
 * flags and sp. */
.macro DIAG_TIME index
#ifdef GAPLESS_DIAG
    push {r0, r1}
    ldr r0, =0x60005010
    ldr r1, [r0]
    ldr r0, =gapless_diag + 4 * (\index)
    str r1, [r0]
    pop {r0, r1}
#endif
.endm
/* Log a playback event after a held EOF (diag.c gapless_ev). Preserves
 * registers, flags and sp. */
.macro DIAG_EV code
#ifdef GAPLESS_DIAG
    push {r0-r3, r12, lr}
    mrs r0, cpsr
    push {r0}
    mov r0, #(\code)
    bl gapless_ev
    pop {r0}
    msr cpsr_f, r0
    pop {r0-r3, r12, lr}
#endif
.endm
.macro TRAIL value
    DIAG_STORE DG_TRAIL, \value
.endm
.macro SITE value
    DIAG_STORE DG_SITE, \value
.endm

#else

#include <stdint.h>

#ifdef GAPLESS_DIAG
extern volatile uint32_t gapless_diag[DG_WORDS];
#define DIAG_INC(i) ((void)(gapless_diag[(i)]++))
#define DIAG_SET(i, v) ((void)(gapless_diag[(i)] = (uint32_t)(v)))
#define DIAG_MAX(i, v) do { uint32_t v_ = (uint32_t)(v); \
    if (v_ > gapless_diag[(i)]) gapless_diag[(i)] = v_; } while (0)
#define DIAG_CORE(e) ((void)(gapless_diag[DG_CORES] |= \
    1u << ((e) * 2 + (*(volatile const uint8_t *)0x60000000 >> 7))))
void gapless_ev(uint32_t code);
#define DIAG_EV(c) gapless_ev(c)
void gapless_trace(uint32_t code, uint32_t a, uint32_t b, uint32_t c);
#define TRACE(code, a, b, c) gapless_trace((code), (uint32_t)(a), (uint32_t)(b), (uint32_t)(c))
#else
#define TRACE(code, a, b, c) ((void)0)
#define DIAG_EV(c) ((void)0)
#define DIAG_INC(i) ((void)0)
#define DIAG_SET(i, v) ((void)0)
#define DIAG_MAX(i, v) ((void)0)
#define DIAG_CORE(e) ((void)0)
#endif

#endif
#endif
