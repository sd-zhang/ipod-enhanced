/* Settings > About > Version shows one page of playback diagnostics.
 *
 * 0x7669c builds the About screen's version field by calling the stock
 * formatter 0x128b4c(3, 0x11, 128, 0, buffer) and converting the C string
 * at the buffer into the field's Pascal string. The hook at 0x766c0 writes
 * a diagnostic page there instead. Each visit to About at least 0.6 s after
 * the previous one advances to the next page, A through G, then wraps.
 *
 * Every pointer read from a stock object is range-checked before use.
 */
#include <stddef.h>
#include <stdint.h>
#include "diag.h"

volatile uint32_t gapless_diag[DG_WORDS];


enum { PAGES = 23 };
#define ENGINE 0x144a91fcu     /* static playback engine, see engine_binding.S */
#define USEC_TIMER 0x60005010u

static uint32_t rd32(uint32_t address) {
  return *(volatile const uint32_t *)address;
}
static uint32_t rd8(uint32_t address) {
  return *(volatile const uint8_t *)address;
}

/* SDRAM in any of its four aliases (32 MiB) or the 96 KiB of SRAM. */
static int readable(uint32_t address, uint32_t size) {
  if (!address || (address & 3))
    return 0;
  uint32_t window = address & 0xfc000000u, offset = address & 0x03ffffffu;
  if (window == 0 || window == 0x04000000u || window == 0x10000000u ||
      window == 0x14000000u)
    return offset < 0x02000000u && size <= 0x02000000u - offset;
  return address >= 0x40000000u && address < 0x40018000u &&
         size <= 0x40018000u - address;
}

static char *put_hex(char *p, uint32_t value) {
  char digits[8];
  unsigned n = 0;
  do {
    digits[n++] = "0123456789abcdef"[value & 15];
    value >>= 4;
  } while (value && n < 8);
  while (n)
    *p++ = digits[--n];
  return p;
}

static char *put_signed(char *p, uint32_t value) {
  *p++ = ' ';
  if (value & 0x80000000u) {
    *p++ = '-';
    value = 0u - value;
  }
  return put_hex(p, value);
}

static char *put_values(char *p, const uint32_t *values, unsigned count) {
  for (unsigned i = 0; i < count; ++i) {
    *p++ = ' ';
    p = put_hex(p, values[i]);
  }
  return p;
}

/* Output object, ring and slot count, or zero if any link is implausible. */
static uint32_t live_queue(void) {
  uint32_t queue = rd32(ENGINE + 0x2e80);
  return readable(queue, 0x100) ? queue : 0;
}
static uint32_t live_ring(uint32_t queue) {
  if (!queue)
    return 0;
  uint32_t ring = rd32(queue + 0x60);
  return readable(ring, 0x50) ? ring : 0;
}
static uint32_t live_slots(uint32_t ring) {
  if (!ring)
    return 0;
  uint32_t slots = rd32(ring + 0x1c), table = rd32(ring + 0x30);
  if (slots > 64 || !readable(table, slots * 16))
    return 0;
  return slots;
}

/* Event log: the first eight events after the last held EOF. */
/* A ring of the last TRACE_N events, for the log file. Entries are written
 * from both cores without a lock; a collision only garbles one entry. */
enum { TRACE_N = 160 };
static volatile uint32_t trace[TRACE_N][5];
static volatile uint32_t trace_count;

void gapless_trace(uint32_t code, uint32_t a, uint32_t b, uint32_t c) {
  uint32_t n = trace_count++;
  volatile uint32_t *e = trace[n % TRACE_N];
  e[0] = rd32(USEC_TIMER);
  e[1] = code;
  e[2] = a;
  e[3] = b;
  e[4] = c;
}

void gapless_ev(uint32_t code) {
  gapless_trace(code, 0, 0, 0);
  uint32_t n = gapless_diag[DG_EV_COUNT];
  if (!gapless_diag[DG_T_EOF] || n >= 8)
    return;
  gapless_diag[DG_EV + n] = code << 24 | (rd32(USEC_TIMER) >> 4 & 0xffffff);
  gapless_diag[DG_EV_COUNT] = n + 1;
}

/* Called at the EOF that kept the output running (handoff.S). */
void gapless_diag_eof(uint32_t ring, uint32_t wake) {
  uint32_t now = rd32(USEC_TIMER);
  gapless_diag[DG_EV_COUNT] = 0;
  gapless_diag[DG_T_EOF] = now ? now : 1;
  gapless_diag[DG_EOF_WAKE] = wake;
  gapless_diag[DG_T_STAGE] = gapless_diag[DG_T_NOTE] = 0;
  gapless_diag[DG_T_BIND] = gapless_diag[DG_T_MATCH] = 0;
  uint32_t slots = live_slots(readable(ring, 0x50) ? ring : 0), queued = 0;
  if (slots) {
    uint32_t table = rd32(ring + 0x30);
    for (unsigned i = 0; i < slots; ++i) {
      uint32_t status = rd8(table + 16 * i + 8);
      queued += status == 5 || status == 6 || status == 8 || status == 9;
    }
  }
  gapless_diag[DG_EOF_QUEUED] = queued;
  gapless_ev(0xe0);
}

/* Event codes, two hex digits each, slots first..first+3. */
static char *put_codes(char *p, unsigned first) {
  for (unsigned i = first; i < first + 4; ++i) {
    *p++ = ' ';
    if (i < gapless_diag[DG_EV_COUNT]) {
      uint32_t code = gapless_diag[DG_EV + i] >> 24;
      *p++ = "0123456789abcdef"[code >> 4];
      *p++ = "0123456789abcdef"[code & 15];
    } else {
      *p++ = '-';
    }
  }
  return p;
}

/* Centiseconds of each event after the first logged one (the EOF). */
static char *put_times(char *p, unsigned first) {
  uint32_t n = gapless_diag[DG_EV_COUNT];
  for (unsigned i = first; i < first + 4 && i < n; ++i) {
    *p++ = ' ';
    uint32_t delta = ((gapless_diag[DG_EV + i] - gapless_diag[DG_EV]) & 0xffffff) << 4;
    uint32_t cs = 0;
    while (delta >= 10000 && cs < 999) {
      delta -= 10000;
      ++cs;
    }
    char digits[4];
    unsigned k = 0;
    do {
      digits[k++] = (char)('0' + cs % 10);
      cs /= 10;
    } while (cs && k < 4);
    while (k)
      *p++ = digits[--k];
  }
  return p;
}

/* Signed centiseconds from the last held EOF, in decimal. 'x' if unset. */
static char *put_delta(char *p, uint32_t when) {
  *p++ = ' ';
  uint32_t eof = gapless_diag[DG_T_EOF];
  if (!when || !eof) {
    *p++ = 'x';
    return p;
  }
  uint32_t delta = when - eof;
  if ((int32_t)delta < 0) {
    *p++ = '-';
    delta = -delta;
  }
  uint32_t cs = 0;
  while (delta >= 10000 && cs < 9999) {
    delta -= 10000;
    ++cs;
  }
  char digits[5];
  unsigned n = 0;
  do {
    digits[n++] = (char)('0' + cs % 10);
    cs /= 10;
  } while (cs && n < 5);
  while (n)
    *p++ = digits[--n];
  return p;
}

static uint32_t cookie(uint32_t value) {
  return (value & 0xff) | (value >> 31) << 8;
}

/* The first three pages hold at most three short numbers each: the screen
 * cuts longer lines off. */
static const unsigned order[] = {19, 20, 21, 22, 0, 1, 18, 7, 9, 12, 13, 14, 15, 16, 17, 10,
                                 11, 8, 2, 3, 4, 5, 6};

static char *page(char *p, unsigned slot) {
  unsigned index = order[slot];
  const volatile uint32_t *d = gapless_diag;
  uint32_t queue = live_queue(), ring = live_ring(queue);
  uint32_t slots = live_slots(ring);
  uint32_t v[8];
  unsigned n = 0;
  *p++ = (char)('A' + slot);
  switch (index) {
  case 19: /* track change held, abandoned, hardware ran dry */
    v[n++] = d[DG_REUSE]; v[n++] = d[DG_DRAINED]; v[n++] = d[DG_UNDERRUN];
    break;
  case 20: /* silent fills, untrimmed passes, failed checks of the last one */
    v[n++] = d[DG_FILL]; v[n++] = d[DG_BYPASS]; v[n++] = d[DG_WHY];
    break;
  case 21: /* seeks, padding of the last AAC track, padding removed at a join */
    v[n++] = d[DG_SEEKS]; v[n++] = d[DG_TAIL]; v[n++] = d[DG_DROPPED];
    break;
  case 22: /* resyncs to the decoder, last resync (blocks, signed) */
    v[n++] = d[DG_RESYNC];
    p = put_values(p, v, n);
    n = 0;
    p = put_signed(p, d[DG_RESYNC_D]);
    break;
  case 0: /* track change: ring reused, abandoned, where, last check passed,
             1 if the last music request was not an automatic advance */
    v[n++] = d[DG_REUSE]; v[n++] = d[DG_DRAINED]; v[n++] = d[DG_D_SITE];
    v[n++] = d[DG_D_TRAIL]; v[n++] = d[DG_STAGE_CK] >> 31;
    break;
  case 8: /* request cookies at the abandon: generation, published, expected,
             selected (low byte, +100 for a manual request) */
    v[n++] = cookie(d[DG_D_GEN]); v[n++] = cookie(d[DG_D_PUB]);
    v[n++] = cookie(d[DG_D_EXP]); v[n++] = cookie(d[DG_D_SEL]);
    break;
  case 18: /* seeks, last target, AAC blocks placed by the decoder,
             * resyncs, last resync (blocks), MP3 scan blocks */
    v[n++] = d[DG_SEEKS]; v[n++] = d[DG_SEEK_T]; v[n++] = d[DG_DERIVED];
    v[n++] = d[DG_RESYNC]; v[n++] = d[DG_RESYNC_D]; v[n++] = d[DG_SCANS];
    break;
  case 16: /* peak (0-127) of the last trimmed track's first four blocks */
    v[n++] = d[DG_PEAK]; v[n++] = d[DG_PEAK + 1]; v[n++] = d[DG_PEAK + 2];
    v[n++] = d[DG_PEAK + 3];
    break;
  case 17: /* first audible frame noted in each of those blocks */
    v[n++] = d[DG_PEAK_LO]; v[n++] = d[DG_PEAK_LO + 1]; v[n++] = d[DG_PEAK_LO + 2];
    v[n++] = d[DG_PEAK_LO + 3];
    break;
  case 12: /* events 1-4 after the last held EOF */
    p = put_codes(p, 0);
    break;
  case 13: /* events 5-8 */
    p = put_codes(p, 4);
    break;
  case 14: /* centiseconds of events 1-4 after the EOF */
    p = put_times(p, 0);
    break;
  case 15: /* centiseconds of events 5-8 */
    p = put_times(p, 4);
    break;
  case 10: /* last held EOF: 1 successor queued / 2 waited for the UI,
              descriptors with audio, centiseconds until B's decoder check,
              handoffs abandoned */
    v[n++] = d[DG_EOF_WAKE] ? 2 : 1; v[n++] = d[DG_EOF_QUEUED];
    p = put_values(p, v, n);
    n = 0;
    p = put_delta(p, d[DG_T_MATCH]);
    v[n++] = d[DG_DRAINED];
    break;
  case 11: /* centiseconds from that EOF to B's request, file-list
              insertion and decoder selection (negative: before it) */
    p = put_delta(p, d[DG_T_STAGE]);
    p = put_delta(p, d[DG_T_NOTE]);
    p = put_delta(p, d[DG_T_BIND]);
    v[n++] = d[DG_WAITS];
    break;
  case 9: /* kept running, hardware ran dry, database delay */
    v[n++] = d[DG_HELD]; v[n++] = d[DG_UNDERRUN]; v[n++] = d[DG_T_DELAY];
    break;
  case 7: /* decode starts, cold starts, continued starts, publishes, worker passes */
    v[n++] = d[DG_START]; v[n++] = d[DG_COLD]; v[n++] = d[DG_CONT];
    v[n++] = d[DG_READY]; v[n++] = d[DG_GEOM];
    break;
  case 1: /* trim notes, trimmed worker passes, silent fills, bypasses, held frames */
    v[n++] = d[DG_READY_OK]; v[n++] = d[DG_GEOM_HIT]; v[n++] = d[DG_FILL];
    v[n++] = d[DG_BYPASS]; v[n++] = d[DG_FIFO];
    break;
  case 2: /* EOF decisions, output kept running, ring reused, drained, phase */
    v[n++] = d[DG_EOF]; v[n++] = d[DG_HELD]; v[n++] = d[DG_REUSE];
    v[n++] = d[DG_DRAINED]; v[n++] = d[DG_DRAIN_PH];
    break;
  case 3: /* codec, engine state, worker pending flag, slots, ring state */
    v[n++] = rd8(ENGINE + 0x2e54); v[n++] = rd8(ENGINE + 0x2994);
    v[n++] = queue ? rd32(queue + 0xe0) : 0xee;
    v[n++] = ring ? rd32(ring + 0x1c) : 0xee;
    v[n++] = ring ? rd8(ring + 0x24) : 0xee;
    break;
  case 4: /* database delay and sample count of the last trimmed track */
    v[n++] = d[DG_T_DELAY]; v[n++] = d[DG_T_SAMPLES];
    break;
  case 5: { /* descriptor status digits, slot 0 first */
    *p++ = ' ';
    if (!slots) {
      *p++ = '-';
      break;
    }
    uint32_t table = rd32(ring + 0x30);
    for (unsigned i = 0; i < slots && i < 12; ++i) {
      uint32_t status = rd8(table + 16 * i + 8);
      *p++ = status < 10 ? (char)('0' + status) : (char)('a' + (status - 10) % 26);
    }
    break;
  }
  case 6: /* frames in the last submit, core bitmask */
    v[n++] = d[DG_OUT_LAST]; v[n++] = d[DG_CORES];
    break;
  default:
    break;
  }
  return put_values(p, v, n);
}

/* The log file on the iPod's own disk, written with the firmware's file
 * routines exactly as its Preferences writer at 0xe52cc does: the data
 * volume from 0xa2b60, a backslash path relative to the volume root, open
 * for writing (0x93974, 'data', mode 2, which creates a missing file), one
 * flushed write (0x93c50, flags 1, the count pointer on the stack) and
 * close (0x938d4). The file is always LOG_BYTES long, so after the first
 * write it is only rewritten in place. Called from the About screen in
 * the application task, at most once every two seconds. */
enum { LOG_BYTES = 8192 };
static const char log_path[] = "iPod_Control\\gapless.log";
static struct { int32_t vol; char path[260]; } log_spec;
static char log_text[LOG_BYTES];
static int32_t log_fd, log_written;
static uint32_t log_last;

typedef int32_t (*volume_fn)(void);
typedef int32_t (*open_fn)(void *spec, uint32_t type, int32_t perm, int32_t *fd);
typedef int32_t (*write_fn)(int32_t fd, const void *buffer, uint32_t bytes, int32_t flags,
                            int32_t *written);
typedef int32_t (*close_fn)(int32_t fd);

static char *put_word(char *p, uint32_t value, unsigned digits) {
  for (unsigned i = digits; i--;)
    *p++ = "0123456789abcdef"[value >> (4 * i) & 15];
  return p;
}

static void format_log(uint32_t now) {
  char *p = log_text, *limit = log_text + LOG_BYTES - 48;
  for (const char *h = "gapless log 1 t "; *h; ++h)
    *p++ = *h;
  p = put_word(p, now, 8);
  *p++ = '\n';
  for (unsigned i = 0; i < DG_WORDS; ++i) {
    if (i % 8 == 0) {
      *p++ = 'w';
      p = put_word(p, i, 2);
    }
    *p++ = ' ';
    p = put_word(p, gapless_diag[i], 8);
    if (i % 8 == 7 || i == DG_WORDS - 1)
      *p++ = '\n';
  }
  uint32_t count = trace_count, first = count > TRACE_N ? count - TRACE_N : 0;
  *p++ = 'e';
  *p++ = 'v';
  *p++ = ' ';
  p = put_word(p, count, 8);
  *p++ = '\n';
  for (uint32_t n = first; n < count && p < limit; ++n) {
    const volatile uint32_t *e = trace[n % TRACE_N];
    p = put_word(p, e[0], 8);
    *p++ = ' ';
    p = put_word(p, e[1], 3);
    for (unsigned k = 2; k < 5; ++k) {
      *p++ = ' ';
      p = put_word(p, e[k], 8);
    }
    *p++ = '\n';
  }
  while (p < log_text + LOG_BYTES - 1)
    *p++ = ' ';
  *p = '\n';
}

static void write_log(uint32_t now) {
  if (log_last && now - log_last < 2000000u)
    return;
  log_last = now ? now : 1;
  format_log(now);
  char *spec = (char *)&log_spec;
  for (unsigned i = 0; i < sizeof log_spec; ++i)
    spec[i] = 0;
  for (unsigned i = 0; log_path[i]; ++i)
    log_spec.path[i] = log_path[i];
  log_spec.vol = ((volume_fn)0xa2b60)();
  if (log_spec.vol < 0) {
    gapless_diag[DG_LOG_ERR] = 0xee;
    return;
  }
  log_fd = -1;
  int32_t e = ((open_fn)0x93974)(&log_spec, 0x64617461u, 2, &log_fd);
  if (e || log_fd < 0) {
    gapless_diag[DG_LOG_ERR] = (uint32_t)e;
    return;
  }
  log_written = 0;
  e = ((write_fn)0x93c50)(log_fd, log_text, LOG_BYTES, 1, &log_written);
  ((close_fn)0x938d4)(log_fd);
  if (e)
    gapless_diag[DG_LOG_ERR] = (uint32_t)e;
  else
    gapless_diag[DG_LOGS]++;
}

void gapless_diag_version(int major, int minor_bugfix, int release, int stage,
                          char *out) {
  (void)major; (void)minor_bugfix; (void)release; (void)stage;
  uint32_t now = rd32(USEC_TIMER);
  uint32_t index = gapless_diag[DG_PAGE], last = gapless_diag[DG_LAST_SHOW];
  /* Opening Settings already renders this field once. After a minute
   * without a visit, show page A for that render and the next one
   * (entering About). Backlight timeouts are shorter than that. */
  if (!last || now - last >= 60000000u) {
    index = 0;
    gapless_diag[DG_FRESH] = 1;
  } else if (gapless_diag[DG_FRESH]) {
    gapless_diag[DG_FRESH] = 0;
  } else if (now - last >= 600000u) {
    index = (index + 1) % PAGES;
  }
  gapless_diag[DG_PAGE] = index;
  gapless_diag[DG_LAST_SHOW] = now ? now : 1;
  char *end = page(out, index);
  *end = 0;
  write_log(now);
}
