/* Sample-accurate AAC, MP3 and ALAC output for the 4G 4.3.1.1 playback pipeline.
 *
 * Producer, the decode pump (COP). When the pump publishes a completed
 * slot, note which of its frames are audible according to the iTunesDB
 * timing of the selected track. The slot itself is not modified.
 *
 * Consumer, the output worker (CPU). After the stock DSP has packed a slot,
 * pass its audible frames through a FIFO that holds back HOLD frames and
 * return the byte count to submit, a multiple of ALIGN frames. The stock worker submits whatever
 * length its DSP returns (the resampler already produces varying lengths),
 * so every hardware submission has a nonzero length. A block with no audible frames borrows
 * MIN_OUT held frames, which the next audible block repays. At a gapless
 * track change the old track's held frames thus fill the next track's
 * fully trimmed first block. If the reserve is too short, keep its samples
 * and retire that empty descriptor in completion order without sending it
 * to I2S. Cold start still sends a full first descriptor like the native one.
 *
 * Nothing here runs on more than one core: notes are written by the COP
 * before the stock publish and read by the CPU after the stock acquire.
 */
#include <stddef.h>
#include <stdint.h>
#include "clip_pcm.h"
#include "diag.h"
#include "ring_geometry.h"

#define ENGINE_RUNNING 0x1940
#define ENGINE_CODEC 0x2e54
#define ENGINE_QUEUE 0x2e80
#define ENGINE_FRAMES 0x90cc
#define WIRE_BYTES 0x144b22ec   /* packed bytes the DSP consumes per slot */

enum {
  MAX_SLOTS = 32,
  AAC_FRAMES = 1024,
  MP3_FRAMES = 1152,              /* MPEG-1 Layer III; MPEG-2/2.5 use 576 */
  ALAC_FRAMES = 4096,
  MAX_BLOCK = MP3_FRAMES,
  /* The I2S driver keeps one descriptor programmed ahead of the playing
   * one (0x156950). A descriptor much shorter than the native 23 ms leaves
   * the driver only a few ms to queue the next while the next track opens,
   * so every descriptor here is at least 11.6 ms and a multiple of 512
   * bytes, which also divides both native lengths (4096 AAC, 4608 MP3). */
  ALIGN = 128,                    /* frames: 512-byte multiples */
  HOLD = AAC_FRAMES + ALIGN,      /* frames held back across descriptors */
  MIN_OUT = 512,                  /* smallest descriptor while playing */
  /* An AAC track's padding after its last audible frame, held back as
   * well, so that removing it at the join (or never letting it in) still
   * leaves HOLD frames for the next track's fully trimmed first block. */
  MAX_TAIL = 2 * AAC_FRAMES,
  FIFO_FRAMES = HOLD + MAX_TAIL + MAX_BLOCK + ALIGN + MIN_OUT,
  APPLE_AAC_DELAY = 2112,         /* used when the database has no timing */
  NOTE_VALID = 1, NOTE_RESET = 2, NOTE_TRIM = 4, NOTE_DROP = 8,
  NOTE_RECOVER = 16,              /* untrimmed AAC block; preserve the reserve */
  NOTE_END = 32,                  /* drain the carry; no decoded input in this slot */
  NOTE_ALAC = 64,                 /* ALAC geometry; NOTE_TRIM also requires a valid count */
};

struct note {
  volatile uint32_t tag;          /* slot base, written last; 0 once consumed */
  volatile uint32_t flags;
  volatile uint32_t lo, hi;       /* audible frame range within the slot */
  volatile uint32_t frames;       /* decoded frames in the slot */
  volatile uint32_t hold;         /* frames the FIFO keeps back after it */
  volatile uint32_t drop;         /* NOTE_DROP: newest held frames to discard first */
};

#ifdef GAPLESS_ALAC_OVERLAY
/* The appended fix shares the released playback state. The builder checks
 * every address and field spacing against the pinned playback ELF. */
#define TRIM_STATE extern
#else
#define TRIM_STATE static
#endif

TRIM_STATE struct note notes[MAX_SLOTS];

TRIM_STATE struct {
  uint32_t enabled, armed, reset_pending, started_cookie, codec;
  uint32_t front, end;            /* decoded-frame coordinates of this track */
  int32_t position;               /* of the next published block */
  /* Anything moved the decoder (seek, scan, mid-track start or restart, a
   * block that failed a check, or the decoder disagreeing with the count):
   * an AAC track's end is then removed by its padding count at the join. */
  uint32_t uncertain;
  uint32_t tail, tail_known;      /* AAC frames decoded after the last audible one */
  uint32_t pending_drop;          /* for the next track's first note */
  uint32_t first;                 /* trace budget; retain field for released state ABI */
  uint32_t tail_removed;          /* padding already excluded before recovery */
} producer;

TRIM_STATE uint32_t fifo[FIFO_FRAMES];  /* stereo16 frames, consumer only */
TRIM_STATE uint32_t fifo_len;
TRIM_STATE uint32_t output_started;     /* audible frames left the FIFO since reset */
/* CPU output worker and CPU completion interrupt only. An empty descriptor
 * reaches native status 9, but has no hardware completion of its own. It
 * must wait behind older, still-playing descriptors before native release. */
TRIM_STATE volatile uint32_t empty_ring, empty_mask;
TRIM_STATE uint32_t empty_ready, empty_queue;
TRIM_STATE uint32_t synthetic_output;  /* CPU worker: exclude drain slots from the clock */

/* COP drain only. Carry samples stay CPU-owned and cross the existing
 * producer/consumer boundary solely through ordinary published notes. */
TRIM_STATE uint8_t *finish_engine;
TRIM_STATE uint32_t finish_blocks, finish_frames, finish_drop;
TRIM_STATE uint32_t finish_ring, finish_capacity;

static uint32_t irq_save(void) {
  uint32_t saved, masked;
  __asm__ volatile("mrs %0, cpsr\n\torr %1, %0, #0xc0\n\tmsr cpsr_c, %1"
                   : "=&r"(saved), "=&r"(masked) : : "memory", "cc");
  return saved;
}
static void irq_restore(uint32_t saved) {
  __asm__ volatile("msr cpsr_c, %0" : : "r"(saved) : "memory", "cc");
}

static uint32_t rd32(const void *base, uint32_t offset) {
  return *(volatile const uint32_t *)((uintptr_t)base + offset);
}
static uint32_t rd8(const void *base, uint32_t offset) {
  return *(volatile const uint8_t *)((uintptr_t)base + offset);
}
static int sdram(uint32_t address) {
  uint32_t window = address & 0xfc000000u;
  return address && !(address & 3) && (address & 0x03ffffffu) < 0x02000000u &&
         (window == 0 || window == 0x04000000u || window == 0x10000000u ||
          window == 0x14000000u);
}

static int readable_word(uint32_t address) {
  return sdram(address) || (address >= 0x40000000u && address < 0x40018000u && !(address & 3));
}

/* Packets in the selected M4A, from the native parser: decoder +0x14 is its
 * file handle, handle +8 the MP4 parser, parser +0x234 the stsz count. */
static uint32_t aac_packets(uint32_t decoder) {
  if (!readable_word(decoder + 0x14))
    return 0;
  uint32_t handle = rd32((void *)decoder, 0x14);
  if (!sdram(handle))
    return 0;
  uint32_t parser = rd32((void *)handle, 8);
  if (!sdram(parser))
    return 0;
  return rd32((void *)parser, 0x234);
}

/* Native AAC decoder (object 0x4000a68c, decode 0x400065a4, ready
 * 0x40006578). The open at 0xdbdd8 zeroes all four words. */
#define AAC_PACKET    0x2cd4  /* packet index, advanced before each read */
#define AAC_EOF       0x2cf4  /* set when a read fails or the stream ends */
#define AAC_SUBMITTED 0x2d10  /* decode jobs submitted */
#define AAC_DONE      0x2d14  /* decode jobs completed, counted by ready */

/* Decoded-frame position of the block the pump is about to publish. The
 * pump decodes one packet per pass into the next slot, then publishes the
 * oldest completed job, so the jobs still in flight hold the packets after
 * this one. Seeks (0x40006d2c) and scanning (0x400065ec) move the index
 * directly, so this stays exact across both; only a block already in
 * flight at a seek is misplaced, and that one is mid-track. Packet one is
 * the stream origin (the stock open starts at index 0 and reads packet one
 * first). Fails once the EOF flag is set, because the failed read has
 * already advanced the index: the caller then continues its own count. */
static int aac_block_position(uint32_t decoder, int32_t *position) {
  if (!readable_word(decoder) || !readable_word(decoder + AAC_DONE))
    return 0;
  uint32_t done = rd32((void *)decoder, AAC_DONE);
  uint32_t submitted = rd32((void *)decoder, AAC_SUBMITTED);
  if (!done || submitted < done || submitted - done > 4 || rd32((void *)decoder, AAC_EOF))
    return 0;
  int32_t packet = (int32_t)(rd32((void *)decoder, AAC_PACKET) - (submitted - done));
  if (packet < 0 || packet > 0x1ffffe)
    return 0;
  *position = (packet - 1) * AAC_FRAMES;
  return 1;
}

/* The primary ring and the descriptor at one of its cursors, or zero. */
static uint32_t slot_at(const void *queue, unsigned cursor, uint32_t *position_out) {
  if (!sdram((uintptr_t)queue))
    return 0;
  uint32_t ring = rd32(queue, 0x60);
  if (!sdram(ring))
    return 0;
  uint32_t position = rd32((void *)ring, 4 * cursor), slots = rd32((void *)ring, 0x1c);
  uint32_t table = rd32((void *)ring, 0x30);
  if (!slots || slots > MAX_SLOTS || position >= slots || !sdram(table))
    return 0;
  *position_out = position;
  return table + 16 * position;
}

extern uint32_t gapless_output_tx(const void *queue);

typedef uint32_t (*release_fn)(uint32_t ring, uint32_t owners, uint32_t stage);

/* Caller masks CPU interrupts. Release only skipped descriptors at the
 * native completion cursor, never an older descriptor still owned by I2S. */
static void reap_empty(uint32_t ring) {
  if (ring != empty_ring || !empty_mask)
    return;
  uint32_t slots = rd32((void *)ring, 0x1c), table = rd32((void *)ring, 0x30);
  if (!slots || slots > MAX_SLOTS || !sdram(table))
    return;
  for (uint32_t n = 0; n < slots; ++n) {
    uint32_t position = rd32((void *)ring, 4);
    if (position >= slots || !(empty_mask & (1u << position)))
      break;
    uint32_t slot = table + 16 * position;
    if (rd8((void *)slot, 8) != 9 || rd32((void *)slot, 12) != 1)
      break;
    /* Clear before native release makes this slot available to the COP. */
    empty_mask &= ~(1u << position);
    if (((release_fn)0x208d8)(ring, 1, 1)) {
      empty_mask |= 1u << position;
      break;
    }
  }
}

/* 0x133548, after stock has acquired this descriptor for the primary TX.
 * Return 1 to omit only the submission the trim worker marked empty. */
uint32_t gapless_trim_skip_submit(const uint8_t *queue, uint32_t bytes) {
  if (bytes || !empty_ready || empty_queue != (uintptr_t)queue)
    return 0;
  uint32_t saved = irq_save();
  uint32_t position = empty_ready - 1;
  empty_ring = rd32(queue, 0x60);
  empty_mask |= 1u << position;
  empty_ready = 0;
  reap_empty(empty_ring);
  irq_restore(saved);
  return 1;
}

/* 0x133be8, primary listener completion. Keep the stock release first,
 * then consume empty successors before the next real completion arrives. */
uint32_t gapless_trim_release(uint32_t ring, uint32_t owners, uint32_t stage) {
  if (!empty_mask || ring != empty_ring || owners != 1 || stage != 1)
    return ((release_fn)0x208d8)(ring, owners, stage);
  uint32_t saved = irq_save();
  uint32_t result = ((release_fn)0x208d8)(ring, owners, stage);
  if (!result)
    reap_empty(ring);
  irq_restore(saved);
  return result;
}

/* Count submits that find the hardware queue empty while the output is
 * running: the previous descriptor already finished, so the listener
 * heard silence before this one. */
static void note_underrun(const void *queue) {
#ifdef GAPLESS_DIAG
  uint32_t engine = rd32(queue, 0x5c), tx = gapless_output_tx(queue);
  if (sdram(engine) && rd32((void *)engine, ENGINE_RUNNING) && sdram(tx) &&
      rd32((void *)tx, 0x14) == rd32((void *)tx, 0x18))
    DIAG_INC(DG_UNDERRUN);
#else
  (void)queue;
#endif
}

static int stereo16(const void *queue) {
  return rd8(queue, 0x94) == 2 && rd8(queue, 0x95) == 16 && !rd8(queue, 0x69);
}

/* 0x4f540, after a successful native ALAC decode and before its zero fill.
 * Keep that zero fill and the fixed DSP geometry intact. Bind the actual
 * count to the reserved descriptor, not engine+0x1948: the pump can reserve
 * its next buffer before it publishes this one. Only the COP owns status 7.
 * Publish converts this provisional note into the ordinary CPU handoff. */
void gapless_alac_decoded(uint32_t buffer, uint32_t actual, uint32_t capacity,
                          const uint8_t *engine) {
  if (!sdram((uintptr_t)engine) || rd8(engine, ENGINE_CODEC) != 11 ||
      capacity != ALAC_FRAMES || !actual || actual > capacity || !sdram(buffer))
    return;
  const void *queue = (const void *)(uintptr_t)rd32(engine, ENGINE_QUEUE);
  if (!sdram((uintptr_t)queue) || !stereo16(queue))
    return;
  uint32_t ring = rd32(queue, 0x60);
  if (!sdram(ring))
    return;
  uint32_t slots = rd32((void *)ring, 0x1c), table = rd32((void *)ring, 0x30);
  if (!slots || slots > MAX_SLOTS || !sdram(table))
    return;
  for (uint32_t i = 0; i < slots; ++i) {
    const void *slot = (const void *)(uintptr_t)(table + 16 * i);
    if (rd32(slot, 0) != buffer || rd8(slot, 8) != 7 || rd32(slot, 4) < capacity * 4)
      continue;
    struct note *note = &notes[i];
    note->flags = NOTE_ALAC;
    note->hi = actual;
    note->frames = capacity;
    note->tag = buffer;
    return;
  }
}

static uint32_t clamp(int64_t value, uint32_t limit) {
  return value <= 0 ? 0 : value >= limit ? limit : (uint32_t)value;
}

/* 0x14a964 via gapless_limit_primary_slots, only for the primary ring set up
 * by the decoder's output configuration (the caller chain is checked there).
 * count is the stock quotient min(arena, limit) / stride, already above the
 * stock cap of 8. AAC and MP3 blocks last only 23 to 26 ms, so eight of them
 * cannot cover the next track's open. Apple Lossless blocks are four times
 * longer and keep the stock cap. */
uint32_t gapless_trim_slot_count(uint32_t count, const uint8_t *queue, const uint8_t *ring) {
  /* (Apple Lossless, codec 11, keeps eight 4096-frame slots, ~0.74 s.) */
  uint32_t result = 8;
  unsigned codec = rd8(queue, 0xa0);
  if ((codec == 10 || codec == 0) && stereo16(queue) && !rd8(ring, 0x25) && !rd8(ring, 0x26)) {
    uint32_t descriptors = rd32(ring, 0x34), tx = gapless_output_tx(queue);
    uint32_t fifo = sdram(tx) ? rd32((void *)tx, 0x0c) : 0;
    result = count;
    if (result > descriptors)
      result = descriptors;
    if (result > fifo)
      result = fifo;
    if (result > GAPLESS_MAX_SLOTS)
      result = GAPLESS_MAX_SLOTS;
    if (result < 8)
      result = 8 < count ? 8 : count;
  }
  return result;
}

/* Handoff state words (handoff.S). */
extern volatile uint32_t gapless_handoff_state[];
enum { HANDOFF_RING = 5, HANDOFF_PHASE = 8 };


/* Descriptors of the held ring that still hold audio to play. */
uint32_t gapless_trim_queued(void) {
  uint32_t ring = gapless_handoff_state[HANDOFF_RING];
  if (!sdram(ring))
    return 0;
  uint32_t slots = rd32((void *)ring, 0x1c), table = rd32((void *)ring, 0x30), queued = 0;
  if (!slots || slots > MAX_SLOTS || !sdram(table))
    return 0;
  for (uint32_t i = 0; i < slots; ++i) {
    uint32_t status = rd8((void *)table, 16 * i + 8);
    queued += status == 5 || status == 6 || status == 8 || status == 9;
  }
  return queued;
}

/* handoff.S (B's decoder match) and the soft select: keep the held output
 * for B while any of A's audio is still queued. The output plays that audio
 * while the UI chooses B (0x123f98), so the queue drains in real time; B's
 * first block follows the play command by ~55 ms. Abandoning means the
 * stock drain, stop and cold start, a longer gap than any underrun here.
 * The held ring still has A's geometry, so WIRE_BYTES is its slot length. */
uint32_t gapless_trim_enough_queued(void) {
  uint32_t frames = rd32((void *)WIRE_BYTES, 0) / 4;
  return frames && frames <= 0x10000 && gapless_trim_queued() != 0;
}

/* Stock 0x116e0c, reached from the play-file commands through 0x116da0,
 * fades out and stops the output before the next file opens. While the
 * output is held at an EOF with audio still queued, that stop would discard
 * the outgoing track's last ~0.6 s. Defer it: do the same file selection
 * and state changes, keep the output running, and stop it later only if the
 * handoff is abandoned (gapless_trim_drain). */
static uint32_t deferred_stop;

typedef void (*select_fn)(uint32_t source, uint32_t file);
typedef void (*state_fn)(const uint8_t *engine, uint32_t state);
typedef void (*engine_fn)(const uint8_t *engine);
typedef void (*queue_fn)(uint32_t queue);

uint32_t gapless_trim_soft_select(const uint8_t *engine, uint32_t file) {
  if (gapless_handoff_state[HANDOFF_PHASE] != 1 || !rd32(engine, ENGINE_RUNNING) ||
      !gapless_trim_enough_queued())
    return 0;
  ((select_fn)0x14f584)(rd32(engine, 0x2e7c), file);   /* as 0x116db0 */
  deferred_stop = 1;
  DIAG_EV(0x0c);
  ((state_fn)0x14e944)(engine, 0);                    /* as 0x116e80 */
  ((engine_fn)0x1447ec)(engine);                      /* as 0x116e8c */
  return 1;
}

/* BL at 0x123f98, in the output owner's play mode (0x123f24, reached from
 * the TX endpoint's buffer request at 0x133054): "is the engine in state
 * 2?" through 0x12ce68. In states 1 (paused) and 2 (ended) the owner
 * reports mode 0 and the endpoint feeds its silence buffer instead of the
 * queued PCM (0x133038). Every gapless track change passes through state 2
 * at A's EOF (0x116de4) until the UI's next play command, so stock would
 * silence A's queued last ~0.6 s for as long as the UI takes. While a
 * handoff holds the output (PHASE 1, the only phase seen in state 2) and
 * A's audio is still queued, that audio is A's to play: answer no. Once
 * the held ring is empty, stock silence and its idle timeout apply. */
uint32_t gapless_trim_state_is(const uint8_t *engine, uint32_t state) {
  if (rd8(engine, 0x2994) != state)
    return 0;
  if (state == 2 && gapless_handoff_state[HANDOFF_PHASE] == 1 && gapless_trim_queued()) {
    DIAG_INC(DG_UNSILENCED);
    return 0;
  }
  return 1;
}

/* handoff.S, BL at 0xa7f20: the UI disposes of a retired music request (its
 * 600 ms deadline, 0xa9f10, passed; 0x89d6c -> 0xa7f04). The stock dispose
 * 0xdcd48 marks its 'tply' object disposed and then always posts command 3
 * (stop) to the player, 0x158294, unless a stop is already pending. After a
 * gapless change the player is already playing the next song, so that stop
 * cut the next song short. When the request is not the one being played
 * (its cookie differs from the decoder's selection), do the stock
 * bookkeeping and release without the stop. Returns 1 when handled. */
uint32_t gapless_trim_dispose(uint8_t *tply, const uint8_t *request) {
  uint32_t cookie = rd32(request, 0x2cc), playing = gapless_engine_timing.cookie;
  if (!cookie || !playing || cookie == playing || rd32(tply, 0) != 0x74706c79u)  /* 'tply' */
    return 0;
  tply[0x22a] = 1;                                                 /* as 0xdcd70 */
  *(volatile uint32_t *)(tply + 0x224) =
      ((uint32_t (*)(uint32_t))0x123318)(rd32((void *)0x103c99a8u, 4));  /* 0xdcd74 */
  ((void (*)(void *, uint32_t))0x9b640)(tply, 600);                /* 0xdcdc8 */
  DIAG_INC(DG_QUIET_DISPOSE);
  TRACE(0x190, cookie, playing, 0);
  return 1;
}

/* Perform a stop deferred by gapless_trim_soft_select, exactly as stock. */
static void deferred_stop_now(uint8_t *engine) {
  if (!deferred_stop)
    return;
  deferred_stop = 0;
  DIAG_EV(0x0d);
  ((queue_fn)0x157df4)(rd32(engine, ENGINE_QUEUE));
  *(volatile uint32_t *)(engine + ENGINE_RUNNING) = 0;
}

/* handoff.S .Ldrain for phases 1 and 2: the stock EOF drain, then any
 * deferred stop. */
void gapless_trim_drain(uint8_t *engine) {
  ((engine_fn)0x116e94)(engine);
  deferred_stop_now(engine);
}

/* The ring was reused: the deferred stop is cancelled for good. */
void gapless_trim_reused(void) {
  deferred_stop = 0;
}

/* 0x1164a0, before the first pump of every decode, r0 = engine. A same-file
 * seek does not come here: the pump seeks in place and keeps decoding
 * (0x40015704, then 0x116d40). Positions are counted from the origin; after
 * a seek, scan or mid-track start, AAC positions come from the decoder and
 * an MP3 is left untrimmed. */
void gapless_trim_start(const uint8_t *engine, uint32_t decoder) {
  struct engine_timing *selected = &gapless_engine_timing;
  DIAG_INC(DG_START);
  DIAG_CORE(DGC_START);
  DIAG_EV(0x09);
  if (!rd32(engine, ENGINE_RUNNING)) {
    /* Output was stopped: nothing older can still be queued behind us. */
    producer.reset_pending = 1;
    producer.pending_drop = 0;
    DIAG_INC(DG_COLD);
  } else {
    DIAG_INC(DG_CONT);
  }
  uint32_t start_ms = rd32(engine, 0x29c0);
  uint32_t cookie = selected->cookie, codec = rd8(engine, ENGINE_CODEC);
  /* Unlike AAC, ALAC has no priming packet. Its native open sets index 0,
   * but decode increments before reading. Correct a fresh, forward start
   * once here, never in the decode loop (a failed packet must not retry
   * forever). Resume, explicit seek and scan retain their native positions. */
  if (codec == 11 && readable_word(decoder + 0x8088) &&
      rd32((void *)decoder, 0) == 0x2419c && !start_ms &&
      !rd32(engine, 0x29c4) && rd8(engine, 0x2994) != 3 && rd8(engine, 0x2994) != 4 &&
      (int32_t)rd32(engine, 0x29b0) >= 0 && !rd32((void *)decoder, 0x8050) &&
      !rd32((void *)decoder, 0x8054) && !rd32((void *)decoder, 0x8070) &&
      !rd32((void *)decoder, 0x8084) && !rd32((void *)decoder, 0x8088))
    *(volatile uint32_t *)(uintptr_t)(decoder + 0x8050) = UINT32_MAX;
  int same = cookie && cookie == producer.started_cookie && codec == producer.codec;
  TRACE(0x110, start_ms, cookie, codec | rd32(engine, ENGINE_RUNNING) << 8 | (uint32_t)same << 9 |
        producer.armed << 10);
#ifdef GAPLESS_DIAG
  producer.first = 12;
#endif
  producer.started_cookie = cookie;
  producer.position = 0;
  producer.tail_removed = 0;
  producer.uncertain = start_ms != 0;
  const void *queue = (const void *)(uintptr_t)rd32(engine, ENGINE_QUEUE);
  if (same && producer.armed) {
    /* The same selection decoding again: a restart, wherever it starts. */
    producer.uncertain = 1;
    if (codec == 0) {
      producer.front = 0;
      producer.end = UINT32_MAX;
    }
    producer.enabled = 1;
    return;
  }
  producer.codec = codec;
  producer.enabled = producer.armed = 0;
  producer.tail = producer.tail_known = 0;
  if (!cookie || same || !sdram((uintptr_t)queue) || !stereo16(queue))
    return;

  if (codec == 11) {
    producer.front = 0;
    producer.end = UINT32_MAX;
    producer.enabled = producer.armed = 1;
    return;
  }
  if (codec == 0) {
    /* MPEG audio: the verified frame-origin rule (mp3_origin.c, lame_timing.c). The
     * native decoder adds 529 frames of synthesis delay and decodes the
     * LAME/Xing header frame; the probe at 0xdb594 checks the database
     * timing against the LAME tag for this selection. */
    gapless_start_mp3_clip(selected);
    if (!selected->cursor.enabled || start_ms)
      return;
    producer.front = selected->cursor.skip_remaining;
    producer.end = selected->cursor.keep_high ? UINT32_MAX
                 : producer.front > UINT32_MAX - selected->cursor.keep_low ? UINT32_MAX
                 : producer.front + selected->cursor.keep_low;
    producer.enabled = producer.armed = 1;
    DIAG_SET(DG_T_DELAY, selected->timing.delay);
    DIAG_SET(DG_T_SAMPLES, selected->timing.samples_low);
    return;
  }
  if (codec != 10)
    return;
  uint32_t delay = selected->timing.delay, end = UINT32_MAX;
  if (!delay) {
    delay = APPLE_AAC_DELAY;    /* front only; the end padding is unknown */
  } else if (!selected->timing.samples_high && selected->timing.samples_low) {
    end = delay - 1024 + selected->timing.samples_low;
  }
  if (delay < 1024 || delay > 16 * AAC_FRAMES || (end != UINT32_MAX && end < delay - 1024))
    return;
  /* The native M4A reader starts at packet one, 1024 frames into the
   * encoder's output, so packet zero's priming never reaches the slot.
   * It decodes every later packet into one block (0x40006840), so the
   * decoded stream ends (packets - 1) * 1024 frames after its origin. */
  producer.front = start_ms ? 0 : delay - 1024;
  producer.end = end;
  uint32_t packets = aac_packets(decoder);
  if (end != UINT32_MAX && packets > 1 && packets < UINT32_MAX / AAC_FRAMES) {
    uint32_t total = (packets - 1) * AAC_FRAMES;
    if (total >= end && total - end <= MAX_TAIL) {
      producer.tail = total - end;
      producer.tail_known = 1;
    }
  }
  producer.enabled = producer.armed = 1;
  DIAG_SET(DG_T_DELAY, selected->timing.delay);
  DIAG_SET(DG_T_SAMPLES, selected->timing.samples_low);
  DIAG_SET(DG_TAIL, producer.tail_known ? producer.tail : 0xfff);
  TRACE(0x111, producer.front, producer.end, producer.tail_known ? producer.tail : 0xffffffffu);
}

/* handoff.S, at a natural EOF, before choosing handoff or drain. If this track's
 * position became uncertain, padding not already excluded by normal trim
 * remains in the FIFO. Have the consumer drop only that remainder before
 * the next track's first frames or the final carry drain. */
#ifdef GAPLESS_DIAG
/* Output passes still to trace after a held EOF. */
static volatile uint32_t join_watch;
#endif

void gapless_trim_eof(void) {
#ifdef GAPLESS_DIAG
  join_watch = 40;
#endif
  TRACE(0x140, producer.tail, producer.uncertain | producer.tail_known << 1 | producer.enabled << 2,
        producer.position);
  if (producer.enabled && producer.codec == 10 && producer.uncertain &&
      producer.tail_known && producer.tail)
    producer.pending_drop = producer.tail - producer.tail_removed;
}

/* 0x116e94, the stock blocking EOF drain. It cannot see the CPU's private
 * carry, so append empty input slots that ask that CPU worker to emit it.
 * A retained gapless handoff does not enter this routine. */
void gapless_trim_finish_begin(uint8_t *engine) {
  finish_engine = 0;
  finish_blocks = 0;
  const uint8_t *queue = (const uint8_t *)(uintptr_t)rd32(engine, ENGINE_QUEUE);
  uint32_t frames = rd32((void *)WIRE_BYTES, 0) / 4;
  uint32_t max_block = producer.codec == 11 ? ALAC_FRAMES : MAX_BLOCK;
  if (!producer.armed || !rd32(engine, ENGINE_RUNNING) ||
      !sdram((uintptr_t)queue) || !rd32(queue, 0x18) ||
      !gapless_output_tx(queue) || frames < MIN_OUT || frames > max_block)
    return;
  uint32_t position, slot = slot_at(queue, 5, &position);
  if (!slot || rd32((void *)slot, 4) < 4 * frames ||
      rd32((void *)slot, 4) > 8 * max_block)
    return;
  finish_engine = engine;
  finish_ring = rd32(queue, 0x60);
  finish_capacity = rd32((void *)slot, 4);
  finish_frames = frames;
  finish_drop = producer.pending_drop;
  uint32_t chunk = frames & ~(uint32_t)(ALIGN - 1);
  for (uint32_t left = FIFO_FRAMES; left; left = left > chunk ? left - chunk : 0)
    ++finish_blocks;
}

/* 0x116ea8. Try one native producer reservation per stock drain poll.
 * When the ring is full, return to the original wait loop; do not add a
 * second wait loop or touch any descriptor still owned by another stage. */
uint32_t gapless_trim_finish_poll(uint8_t *queue) {
  if (finish_engine && rd32(finish_engine, ENGINE_QUEUE) == (uintptr_t)queue) {
    uint32_t position, slot = slot_at(queue, 5, &position);
    if (!rd32(finish_engine, ENGINE_RUNNING) || !rd32(queue, 0x18) ||
        !gapless_output_tx(queue) || rd32(queue, 0x60) != finish_ring || !slot ||
        rd32((void *)slot, 4) != finish_capacity)
      goto changed;
    if (finish_blocks) {
      volatile uint32_t *reserved = (volatile uint32_t *)(finish_engine + 0x1948);
      if (!*reserved)
        ((uint32_t (*)(void *, volatile uint32_t *))0x40017a30)(queue, reserved);
      if (*reserved) {
        if (rd8((void *)slot, 8) != 7 || rd32((void *)slot, 0) != *reserved)
          goto changed;
        uint32_t *buffer = (uint32_t *)(uintptr_t)*reserved;
        for (uint32_t i = 0; i < finish_capacity / 4; ++i)
          buffer[i] = 0;
        struct note *note = &notes[position];
        note->flags = NOTE_VALID | NOTE_END | (finish_drop ? NOTE_DROP : 0);
        note->lo = note->hi = note->hold = 0;
        note->frames = finish_frames;
        note->drop = finish_drop;
        note->tag = *reserved;
        if (((uint32_t (*)(void *))0x400154f0)(queue)) {
          note->tag = 0;
          goto changed;
        }
        *reserved = 0;
        --finish_blocks;
        finish_drop = producer.pending_drop = 0;
        ((void (*)(void *))0x400154bc)(queue);  /* stock output mailbox */
      }
    }
    if (finish_blocks || rd32(queue, 0xe0))
      return 0;
    /* The mailbox acknowledges before the worker handles its slot. The
     * stock check sees only hardware owners; also wait for queued DSP work. */
    uint32_t ring = rd32(queue, 0x60), slots = rd32((void *)ring, 0x1c);
    uint32_t table = rd32((void *)ring, 0x30);
    if (slots > MAX_SLOTS || !sdram(table))
      return 0;
    for (uint32_t i = 0; i < slots; ++i) {
      uint32_t status = rd8((void *)(table + 16 * i), 8);
      if (status != 3 && status != 7)
        return 0;
    }
  }
  return ((uint32_t (*)(void *))0x12c970)(queue);
changed:
  /* A rejected/reconfigured route must retain the stock drain behaviour,
   * not wait forever for a reservation that this geometry cannot supply. */
  TRACE(0x155, finish_blocks, finish_ring, rd32(queue, 0x60));
  finish_blocks = 0;
  finish_engine = 0;
  return ((uint32_t (*)(void *))0x12c970)(queue);
}

uint32_t gapless_trim_account(uint32_t frames) {
  return synthetic_output ? 0 : frames;
}

/* Overlay 0x40015a78, immediately before the stock publish of a slot, after
 * the decoder's ready returned 1. r8 there is the decoder object. */
void gapless_trim_publish(const uint8_t *engine, uint32_t decoder) {
  DIAG_INC(DG_READY);
  DIAG_CORE(DGC_READY);
  const void *queue = (const void *)(uintptr_t)rd32(engine, ENGINE_QUEUE);
  uint32_t position, slot = slot_at(queue, 5, &position);
  if (!slot) {
    producer.enabled = 0;
    return;
  }
  uint32_t base = rd32((void *)slot, 0), frames = rd32(engine, ENGINE_FRAMES);
  unsigned codec = rd8(engine, ENGINE_CODEC);
  struct note *note = &notes[position];
  int alac = codec == 11 && frames == ALAC_FRAMES && note->tag == base &&
             note->flags == NOTE_ALAC && note->frames == frames &&
             note->hi && note->hi <= frames;
  uint32_t alac_frames = alac ? note->hi : frames;
  int block_ok = alac || (codec == 10 ? frames == AAC_FRAMES
               : codec == 0 && (frames == MP3_FRAMES || frames == MP3_FRAMES / 2));
  /* Geometry survives a missing count. The untrimmed fallback must still
   * carry older samples through a 4096-frame block, not attempt a direct
   * flush that cannot fit both into the native 4096-frame output buffer. */
  uint32_t flags = NOTE_VALID | (codec == 11 && frames == ALAC_FRAMES ? NOTE_ALAC : 0);
  uint32_t lo = 0, hi = frames;
  if (producer.reset_pending) {
    flags |= NOTE_RESET;
    producer.reset_pending = 0;
  }
  uint32_t drop = producer.pending_drop, hold = HOLD;
  if (drop) {
    flags |= NOTE_DROP;
    producer.pending_drop = 0;
  }
  unsigned state = rd8(engine, 0x2994);
  if (producer.enabled && (state == 3 || state == 4)) {
    /* Fast-forward or rewind scanning (0x14e934, pump commands 8 to 10). */
    if (!producer.uncertain)
      TRACE(0x132, state, producer.position, 0);
    producer.uncertain = 1;
    if (codec == 0) {
      producer.front = 0;
      producer.end = UINT32_MAX;
    }
    DIAG_INC(DG_SCANS);
  }
  if (producer.enabled && codec == 10) {
    /* The decoder's own count of this block (equal to the running count
     * in straight play) is the position whenever the two differ. */
    int32_t at;
    if (aac_block_position(decoder, &at)) {
      DIAG_INC(DG_DERIVED);
      if (at != producer.position) {
        TRACE(0x130, at, producer.position, producer.uncertain);
        DIAG_INC(DG_RESYNC);
        DIAG_SET(DG_RESYNC_D, (at - producer.position) / AAC_FRAMES);
        producer.uncertain = 1;
        producer.tail_removed = 0;
        producer.position = at;
      }
    }
  }
  if (producer.enabled) {
    uint32_t why = (block_ok ? 0 : 1) | (rd32((void *)WIRE_BYTES, 0) == 4 * frames ? 0 : 2) |
                   (rd8((void *)slot, 8) == 7 ? 0 : 4) | (stereo16(queue) ? 0 : 8);
    int tail_mode = codec == 10 && producer.uncertain && producer.tail_known;
    if (codec == 10 && producer.tail_known)
      hold = HOLD + producer.tail;
    else if (codec == 0)
      /* A LAME track's front trim (header block, 576 + 529 frames) spans
       * two blocks after a third that may also borrow: hold one more. */
      hold = HOLD + MP3_FRAMES;
    else if (codec == 11)
      hold = MIN_OUT;             /* 4096 + at most 639 fits the existing FIFO */
    if (!why) {
      int64_t at = producer.position;
      lo = clamp((int64_t)producer.front - at, frames);
      hi = producer.end == UINT32_MAX || tail_mode ? frames
         : clamp((int64_t)producer.end - at, frames);
      if (hi < lo)
        hi = lo;
      if (alac) {
        lo = 0;
        hi = alac_frames;
        flags |= NOTE_ALAC;
      }
      if (codec == 10 && producer.tail_known && !tail_mode && hi < frames) {
        uint32_t removed = frames - hi;
        uint32_t left = producer.tail - producer.tail_removed;
        producer.tail_removed += removed < left ? removed : left;
      }
#ifdef GAPLESS_DIAG
      /* Loudness of this track's first four decoded blocks, as the
       * decoder published them (before any trimming). */
      uint32_t block = (uint32_t)producer.position / AAC_FRAMES;
      if (codec == 10 && producer.position >= 0 && block < 4 && sdram(base)) {
        const volatile int16_t *pcm = (const volatile int16_t *)(uintptr_t)base;
        uint32_t peak = 0;
        for (uint32_t i = 0; i < 2 * AAC_FRAMES; ++i) {
          int32_t v = pcm[i];
          uint32_t a = (uint32_t)(v < 0 ? -v : v);
          if (a > peak)
            peak = a;
        }
        gapless_diag[DG_PEAK + block] = peak >> 8;
        gapless_diag[DG_PEAK_POS + block] = producer.position;
        gapless_diag[DG_PEAK_LO + block] = lo;
      }
#endif
#ifdef GAPLESS_DIAG
      if (producer.first) {
        TRACE(producer.first == 12 ? 0x112 : 0x113, producer.position, lo, hi | hold << 16);
        producer.first--;
      }
#endif
      producer.position = (int32_t)((uint32_t)producer.position + frames);
      flags |= NOTE_TRIM;
      DIAG_INC(DG_READY_OK);
    } else {
      /* Pass this block through untrimmed. AAC can recover from decoder
       * positions and padding; ALAC's next successful block supplies its
       * own count. An MP3 track has no other position source and stops. */
      DIAG_SET(DG_WHY, why);
      DIAG_INC(DG_SKIPPED);
      TRACE(0x131, why, rd8((void *)slot, 8), frames);
      if (codec == 10 || codec == 11) {
        producer.uncertain = 1;
        producer.position = (int32_t)((uint32_t)producer.position + frames);
        flags |= NOTE_RECOVER;
      } else {
        producer.enabled = 0;
      }
    }
  }
  note->flags = flags;
  note->lo = lo;
  note->hi = hi;
  note->frames = frames;
  note->hold = hold;
  note->drop = drop;
  note->tag = base;
  /* AAC packet zero is the only negative origin (-1024); short recovery
   * blocks can leave -1024..-1. Anything smaller has overflowed the signed
   * counter. Preserve playback through the existing untrimmed FIFO path.
   * Keep armed set so the final held audio still drains at EOF. */
  if (producer.position < -AAC_FRAMES)
    producer.enabled = 0;
}

#ifdef GAPLESS_DIAG
/* 0x133070, in the TX endpoint's buffer request (event 1, 0x133038): the
 * owner's play mode for the next hardware buffer. Mode 3 plays the next
 * queued buffer; the other modes feed the endpoint's silence buffer. Trace
 * changes for the music endpoint only. */
void gapless_txmode(uint32_t mode, uint32_t tx) {
  static uint32_t last_mode_plus_1, last_owner;   /* zero-initialised RAM only */
  const void *queue = (const void *)(uintptr_t)rd32((void *)0x144a91fc, ENGINE_QUEUE);
  if (!sdram((uintptr_t)queue) || tx != gapless_output_tx(queue))
    return;
  uint32_t owner = rd32((void *)tx, 0x30);
  if (mode + 1 != last_mode_plus_1)
    TRACE(0x170, mode, tx, rd8((void *)0x144a91fc, 0x2994));
  if (owner != last_owner && sdram(owner))
    TRACE(0x172, owner, rd32((void *)owner, 0), 0);
  last_mode_plus_1 = mode + 1;
  last_owner = owner;
}

/* 0x116de4, the decoder's cleanup after a decode ends (EOF, error or a
 * command): why it ended. For AAC: packet index, the file's packet count,
 * the EOF flag and jobs in flight; with the caller and engine state. */
void gapless_cleanup_note(const uint8_t *engine, uint32_t caller) {
  uint32_t decoder = 0x4000a68cu;
  if (rd8(engine, ENGINE_CODEC) == 10 && rd32((void *)decoder, 0) == 0x2362cu) {
    uint32_t done = rd32((void *)decoder, AAC_DONE), submitted = rd32((void *)decoder, AAC_SUBMITTED);
    TRACE(0x180, caller, rd32((void *)decoder, AAC_PACKET), aac_packets(decoder));
    TRACE(0x181, rd32((void *)decoder, AAC_EOF), submitted - done,
          rd8(engine, 0x2994) | rd32(engine, ENGINE_RUNNING) << 8 |
          gapless_handoff_state[HANDOFF_PHASE] << 16);
  } else {
    TRACE(0x180, caller, 0xffffffffu, rd8(engine, ENGINE_CODEC));
  }
}

/* 0x1572fc, the engine's state broadcast (it reads the state after 5 ms). */
void gapless_statecast(const uint8_t *engine, uint32_t caller) {
  TRACE(0x171, rd8(engine, 0x2994), caller, 0);
}
#endif

/* Overlay seek handlers: the decoder is about to jump within the track to
 * target (ms) and keep decoding from there. AAC positions come from the
 * decoder from now on; the rest of an MP3 track is left untrimmed. */
void gapless_trim_seek(uint32_t target, uint32_t source) {
  DIAG_INC(DG_SEEKS);
  DIAG_SET(DG_SEEK_T, target);
  DIAG_SET(DG_SEEK_POS, producer.position);
  DIAG_SET(DG_SEEK_SRC, source);
  TRACE(0x120, target, source, producer.position);
  (void)target;
  (void)source;
  if (producer.armed && producer.codec == 0) {
    producer.front = 0;
    producer.end = UINT32_MAX;
  }
  producer.uncertain = 1;
  producer.tail_removed = 0;
}

/* Word-aligned PCM, disjoint buffers or forward overlap (to <= from).
 * The ARM implementation retains the same four-frame-before-store ordering. */
extern void copy_frames(uint32_t *to, const uint32_t *from, uint32_t count);

/* Prepend whatever the FIFO still holds to an untrimmed block. */
static uint32_t flush_into(uint32_t *buffer, uint32_t frames, uint32_t capacity_frames) {
  uint32_t held = fifo_len;
  fifo_len = 0;
  if (!held || held + frames > capacity_frames)
    return frames;
  for (uint32_t i = frames; i--;)
    buffer[held + i] = buffer[i];
  copy_frames(buffer, fifo, held);
  return held + frames;
}

/* 0x15a8cc, replacing "mov r5, r0" after the stock DSP call. bytes is the
 * packed stereo16 length the DSP produced in the queue's current buffer. */
uint32_t gapless_trim_output(uint32_t bytes, const uint8_t *queue) {
  empty_ready = 0;
  synthetic_output = 0;
  DIAG_INC(DG_GEOM);
  DIAG_CORE(DGC_GEOM);
  note_underrun(queue);
#ifdef GAPLESS_DIAG
  if (join_watch) {
    /* Output pass near a join: the I2S queue's read and write indices and
     * capacity, the DSP's frames, and the frames held. */
    join_watch--;
    uint32_t tx = gapless_output_tx(queue);
    TRACE(0x160, sdram(tx) ? (rd32((void *)tx, 0x14) & 0xff) | (rd32((void *)tx, 0x18) & 0xff) << 8 |
                             (rd32((void *)tx, 0x0c) & 0xff) << 16 : 0xffffffffu,
          bytes / 4, fifo_len);
  }
#endif
  uint32_t position, slot = slot_at(queue, 4, &position);
  if (!slot || (bytes & 3) || !stereo16(queue))
    return bytes;
  uint32_t base = rd32((void *)slot, 0), capacity = rd32((void *)slot, 4);
  if (rd32(queue, 0xdc) != base || !sdram(base) || bytes > capacity)
    return bytes;
  uint32_t *buffer = (uint32_t *)(uintptr_t)base;
  uint32_t frames = bytes / 4, capacity_frames = capacity / 4;
  struct note *note = &notes[position];
  uint32_t flags = note->tag == base ? note->flags : 0;
  uint32_t lo = note->lo, hi = note->hi, note_frames = note->frames;
  uint32_t hold = note->hold, drop = note->drop;
  note->tag = 0;
  if (flags & NOTE_RESET) {
    TRACE(0x152, fifo_len, output_started, 0);
    fifo_len = output_started = 0;
    uint32_t saved = irq_save();
    empty_mask = empty_ring = 0;
    irq_restore(saved);
  }
  if (flags & NOTE_DROP) {
    TRACE(0x150, drop, fifo_len, hold);
    /* The previous track's padding, still held: discard it. */
    fifo_len -= drop < fifo_len ? drop : fifo_len;
    DIAG_SET(DG_DROPPED, drop);
  }
  if (flags & NOTE_END) {
    synthetic_output = 1;
    /* The carry has already passed through DSP. An empty resampler result
     * must not prevent it from draining; use the reserved slot's bounds. */
    uint32_t real = note_frames < capacity_frames ? note_frames : capacity_frames;
    real &= ~(uint32_t)(ALIGN - 1);
    if (real > fifo_len)
      real = fifo_len;
    if (!real) {
      empty_queue = (uintptr_t)queue;
      empty_ready = position + 1;
      return 0;
    }
    uint32_t out = (real + ALIGN - 1) & ~(uint32_t)(ALIGN - 1);
    if (out < MIN_OUT)
      out = MIN_OUT;
    copy_frames(buffer, fifo, real);
    for (uint32_t i = real; i < out; ++i)
      buffer[i] = 0;
    fifo_len -= real;
    copy_frames(fifo, fifo + real, fifo_len);
    output_started = 1;
    DIAG_SET(DG_OUT_LAST, out);
    DIAG_SET(DG_FIFO, fifo_len);
    TRACE(0x154, real, out, fifo_len);
    return 4 * out;
  }
  uint32_t max_block = flags & NOTE_ALAC ? ALAC_FRAMES : MAX_BLOCK;
  if (flags & NOTE_ALAC)
    hold = MIN_OUT;
  else if (hold < HOLD || hold > HOLD + MAX_TAIL)
    hold = HOLD;
  if (!(flags & NOTE_TRIM) || frames != note_frames || frames > max_block ||
      hi > frames || lo > hi) {
    DIAG_INC(DG_BYPASS);
    /* Even the first ALAC fallback must establish its reserve and mark
     * output_started. Otherwise a later short valid block would insert
     * cold-start silence after music has already reached the hardware. */
    if ((!fifo_len && !(flags & NOTE_ALAC)) || frames > max_block ||
        fifo_len + frames > FIFO_FRAMES) {
      if (fifo_len)
        TRACE(0x153, fifo_len, frames, capacity_frames);
      uint32_t out = flush_into(buffer, frames, capacity_frames);
      DIAG_SET(DG_OUT_LAST, out);
      return 4 * out;
    }
    /* An AAC check failure passes the block untrimmed but must keep the
     * padding and priming reserve for recovery at EOF. Other bypasses
     * pass all frames through and let the FIFO drain. */
    lo = 0;
    hi = frames;
    if (!(flags & NOTE_RECOVER))
      hold = 0;
  }
  DIAG_INC(DG_GEOM_HIT);
  uint32_t audible = hi - lo;
  if (fifo_len + audible > FIFO_FRAMES)
    fifo_len = 0;                  /* cannot happen with HOLD-sized carries */
  copy_frames(fifo + fifo_len, buffer + lo, audible);
  fifo_len += audible;
  uint32_t out, real, pad = 0;
  if (fifo_len >= hold + MIN_OUT) {
    out = real = (fifo_len - hold) & ~(uint32_t)(ALIGN - 1);
    if (out > capacity_frames)
      out = real = capacity_frames & ~(uint32_t)(ALIGN - 1);
  }
  else if (fifo_len >= MIN_OUT)
    out = real = MIN_OUT;          /* borrow from the held frames */
  else if (!output_started) {
    /* Cold start: a full block of leading silence keeps the first
     * descriptor as long as the native one. Keep any audible frames for
     * the next descriptor so the music itself stays contiguous. */
    out = pad = frames;
    real = 0;
    DIAG_INC(DG_FILL);
  } else {
    /* The CPU owns both this worker and the primary TX interrupt. Keep a
     * short reserve intact instead of inserting silence into the music.
     * The submit hook and listener retire this block in native FIFO order. */
    uint32_t ring = rd32(queue, 0x60);
    if (rd8((void *)0x60000000u, 0) == 0x55 && gapless_output_tx(queue) &&
        (!empty_mask || empty_ring == ring)) {
      empty_queue = (uintptr_t)queue;
      empty_ready = position + 1;
      DIAG_SET(DG_OUT_LAST, 0);
      DIAG_SET(DG_FIFO, fifo_len);
      return 0;
    }
    /* Mid-stream, fewer than MIN_OUT frames held: a run of fully trimmed
     * blocks longer than HOLD, normally padding after a track's last
     * audible frames. Play those frames, then silence. */
    out = MIN_OUT;
    real = fifo_len;
    for (uint32_t i = real; i < out; ++i)
      buffer[i] = 0;
    DIAG_INC(DG_FILL);
    TRACE(0x151, fifo_len, out, frames);
  }
  for (uint32_t i = 0; i < pad; ++i)
    buffer[i] = 0;
  copy_frames(buffer + pad, fifo, real);
  if (real)
    output_started = 1;
  fifo_len -= real;
  copy_frames(fifo, fifo + real, fifo_len);
  DIAG_SET(DG_OUT_LAST, out);
  DIAG_SET(DG_FIFO, fifo_len);
  return 4 * out;
}
