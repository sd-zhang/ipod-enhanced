/* Monochrome 4G letter navigation. All addresses are pinned to 4.3.1.1. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef signed int i32;
#define WORD(p, o) (*(u32 *)((u8 *)(p) + (o)))
#define HALF(p, o) (*(u16 *)((u8 *)(p) + (o)))
#define PTR(p, o) (*(void **)((u8 *)(p) + (o)))
#define FN(a, t) ((t)(a))
#include "letter_map.h"

/* The 5G display-name fallback, applied to the UTF-16 already in the builder.
 * No new disk reads, allocations or changes to native name IDs/order. */
u32 letters_group(const u16 *name, u32 length) {
    while (length && *name == ' ') { ++name; --length; }
    if (!length) return '#';
    u32 c = *name;
    if (c < 512 && letter_map[c]) {
        c = letter_map[c];
        return c >= '0' && c <= '9' ? '#' : c;
    }
    if (c >= 0xd800 && c <= 0xdfff) return '#'; /* supplementary UTF-16 */
    return c >= 0x2e80 ? '!' : '#';
}

void *letters_index_alloc(u32 original_bytes) {
    return FN(0x9b8e0, void *(*)(u32))(original_bytes + (original_bytes >> 2));
}

void letters_index_compact(u8 *index, u32 capacity, u32 accepted) {
    /* An unfiltered, already unique index needs no relocation. */
    if (capacity == accepted) return;
    /* Destination never follows source; forward copy handles overlap. */
    u8 *source = index + 4 * capacity;
    u8 *dest = index + 4 * accepted;
    for (u32 i = 0; i < accepted; ++i) dest[i] = source[i];
}

/* The native list remains 232 bytes. This extends its separately owned wheel
 * accelerator, whose original 44-byte prefix is untouched. */
typedef struct {
    u32 native[11];
    u32 magic;
    u32 timer[10];
    u32 ready, active, letter, last_input, last_step;
    i32 direction;
    u32 stroke_start, stroke_last;
    i32 stroke_distance;
    u32 previous_native_tick;
} LetterState;
#define MAGIC 0x4c545234u
#define TIMEOUT 1000000u
#define STEP_TIME 150000u
#define STROKE_WINDOW 500000u
#define STROKE_PAUSE 200000u
#define STROKE_DISTANCE 64

/* Subtract before converting units: 6c218 divides a wrapping microsecond
 * counter by 1000, so its result is not a modular 32-bit millisecond clock. */
static u32 now(void) { return FN(0x87cec, u32 (*)(void))(); }
static void invalidate(void *list) { FN(0x9107c, void (*)(void *))(list); }
static void stop(LetterState *s) { FN(0xd3aa0, void (*)(void *))(s->timer); }

static LetterState *state(void *list) {
    LetterState *s = PTR(list, 0xe4);
    return s && s->magic == MAGIC ? s : (LetterState *)0;
}

void *letters_accel_create(void) {
    LetterState *s = FN(0xd9a60, void *(*)(u32))(sizeof(LetterState));
    if (!s) return s;
    FN(0xf20d8, void *(*)(void *, u32, u32))(s, 5, 1);
    s->magic = MAGIC;
    s->ready = s->active = s->letter = s->last_input = s->last_step = 0;
    s->direction = 0;
    s->stroke_start = s->stroke_last = 0;
    s->stroke_distance = 0;
    s->previous_native_tick = s->native[0];
    return s;
}

static void reset_acceleration(LetterState *s) {
    u32 time = FN(0x6c218, u32 (*)(void))(); /* native accelerator uses ms */
    s->native[0] = time;
    s->native[1] = time + 350;
    s->native[2] = time + 200;
    s->native[3] = 1;
    s->native[4] = s->native[5] = 0;
    s->previous_native_tick = time;
}

static void close_letters(void *list, LetterState *s, u32 repaint) {
    if (!s) return;
    s->stroke_distance = 0; /* Keys/hide also cancel an unfinished fast stroke. */
    if (!s->active) return;
    s->active = 0;
    stop(s); /* Native stop also removes an already queued event for this timer. */
    reset_acceleration(s);
    if (repaint) invalidate(list);
}

void letters_accel_destroy(LetterState *s, u32 release) {
    if (s && s->magic == MAGIC) {
        if (s->ready) stop(s);
        s->magic = 0;
    }
    FN(0xff778, void (*)(void *, u32))(s, release);
}

/* Only the four verified name-index views. Track order, playlists, settings
 * and subclass objects retain ordinary scrolling. Resolve the query anew:
 * never retain an index pointer across native invalidation or a menu change. */
static const u8 *groups(void *list, u32 *count) {
    if (WORD(list, 0) != 0x29030) return 0;
    u32 offset;
    switch (HALF(list, 0x1c)) {
    case 0x3302: offset = 0xa9c; break;
    case 0x3303: offset = 0xa94; break;
    case 0x3304: offset = 0xa8c; break;
    case 0x3305: offset = 0xa84; break;
    default: return 0;
    }
    void *model = PTR(list, 0x8c);
    if (!model || WORD(model, 0) != 0x22fd4) return 0;
    void *ui = PTR(model, 0x18);
    if (!ui || WORD(ui, 0) != 0x26294) return 0;
    void *query = PTR(ui, 0x1c);
    if (!query) return 0;
    u32 n = WORD(query, offset + 4);
    u8 *index = PTR(query, offset);
    if (!index || !n || n > 65535 || WORD(list, 0x9c) != 0 ||
        WORD(list, 0xa0) != n + 1) return 0;
    *count = n;
    return index + n * 4;
}

static u32 row_group(const u8 *g, u32 row) { return g[row ? row - 1 : 0]; }

static u32 row_method(void *row, u32 slot) {
    u32 table = WORD(row, 0);
    return FN(table + WORD((void *)table, slot), u32 (*)(void *))(row);
}

/* Exact run search, entirely in RAM. Handles repeated/nonmonotonic letters
 * in host sort order without sparse probes accidentally skipping a group. */
u32 letters_target(const u8 *g, u32 count, u32 row, i32 direction) {
    if (!count || row > count || !direction) return row;
    u32 at = row, letter = row_group(g, row);
    if (direction > 0) {
        while (at < count && row_group(g, at + 1) == letter) ++at;
        return at < count ? at + 1 : row;
    }
    if (at && row_group(g, at - 1) != letter) letter = row_group(g, --at);
    while (at && row_group(g, at - 1) == letter) --at;
    return at;
}

static void arm_timeout(void *list, LetterState *s) {
    if (!s->ready) {
        FN(0x8bddc, void (*)(void *, void *, u32))(list, s->timer, 0);
        s->ready = 1;
    }
    /* Native interval lives at +4. Start already stops/purges the timer;
     * avoid another stop when its interval is unchanged. A stale delivery
     * may have shortened it, so restore 1000 before the next wheel restart. */
    if (s->timer[1] != 1000)
        FN(0xcdd84, void (*)(void *, u32))(s->timer, 1000);
    FN(0xd2bd0, void (*)(void *))(s->timer);
}

/* Native 3c258 retains this event's displacement before the row divisor and
 * 866f0's +/-1 clamp. Undo the native power-of-two multiplier when enabled,
 * so entry measures wheel travel rather than packet frequency or acceleration.
 * No new driver hook, event read, allocation, or storage access is needed. */
static i32 wheel_distance(LetterState *s) {
    i32 distance = (i32)s->native[4];
    if ((s->native[8] & 0xffffu) == 1) {
        for (u32 factor = s->native[3]; factor > 1; factor >>= 1)
            distance >>= 1;
    }
    return distance;
}

static u32 fast_stroke(LetterState *s, i32 distance, u32 time) {
    if (!distance) return 0;
    if (!s->stroke_distance || time - s->stroke_start >= STROKE_WINDOW ||
        time - s->stroke_last >= STROKE_PAUSE ||
        (distance < 0) != (s->stroke_distance < 0)) {
        s->stroke_start = time;
        s->stroke_distance = 0;
    }
    s->stroke_last = time;
    s->stroke_distance += distance;
    return s->stroke_distance >= STROKE_DISTANCE ||
           s->stroke_distance <= -STROKE_DISTANCE;
}

u32 letters_wheel(void *list, i32 delta) {
    LetterState *s = state(list);
    if (!s) return 0;
    u32 time = now(), count;
    /* 86394 skips its update when the previous native millisecond tick was
     * zero (also possible at clock wrap). Its retained displacement is then
     * stale, so discard the unfinished stroke instead of counting it twice. */
    i32 distance = 0;
    if (s->previous_native_tick) distance = wheel_distance(s);
    else s->stroke_distance = 0;
    s->previous_native_tick = s->native[0];
    if (s->active && time - s->last_input >= TIMEOUT) close_letters(list, s, 1);
    const u8 *g = groups(list, &count);
    void *normal = PTR(list, 0x90);
    if (!g || count < 32 || !normal || !row_method(normal, 0x120)) {
        close_letters(list, s, 1);
        return 0;
    }
    u32 current = row_method(normal, 0x11c);
    if (current > count) { close_letters(list, s, 1); return 0; }
    if (!s->active) {
        u32 fast = fast_stroke(s, distance, time);
        if (!fast && delta < 16 && delta > -16) return 0;
    }
    if (!delta) return s->active;
    i32 direction = delta > 0 ? 1 : -1;
    u32 entering = !s->active;
    s->last_input = time;
    /* Keep the real scheduled deadline current: the native publisher can
     * silently drop events, so relying on early expiry delivery is unsafe. */
    arm_timeout(list, s);
    if (!entering && direction == s->direction && time - s->last_step < STEP_TIME) return 1;
    s->active = 1;
    s->direction = direction;
    s->last_step = time;
    u32 target = letters_target(g, count, current, direction);
    u32 previous_letter = s->letter;
    s->letter = row_group(g, target);
    if (target != current) {
        WORD(list, 0xa4) = (u32)direction;
        /* No temporary writes to the shared row model. Native selection owns
         * the layout, notification and selected-label marquee updates. */
        FN(0xd10e8, void (*)(void *, u32))(list, target);
        FN(0x625f4, void (*)(u32))(1);
    }
    /* A boundary stroke still refreshes the timeout, but unchanged content
     * needs no redraw. Entry and a refreshed index's new marker still do. */
    if (entering || target != current || previous_letter != s->letter)
        invalidate(list);
    return 1;
}

u32 letters_event(void *list, void *event) {
    LetterState *s = state(list);
    if (s && s->ready && WORD(event, 4) == 0x74696d65 && PTR(event, 8) == s->timer) {
        if (s->active) {
            u32 elapsed = now() - s->last_input;
            if (elapsed >= TIMEOUT || !FN(0xe3ddc, u32 (*)(void *))(list)) {
                close_letters(list, s, 1);
            } else {
                /* A stale delivery must not dismiss a newly refreshed mode. */
                u32 milliseconds = FN(0x1494c, u32 (*)(u32, u32))(1000, TIMEOUT - elapsed + 999);
                FN(0xcdd84, void (*)(void *, u32))(s->timer, milliseconds);
                FN(0xd2bd0, void (*)(void *))(s->timer);
            }
        }
        return 1;
    }
    return FN(0x8150c, u32 (*)(void *, void *))(list, event);
}

void letters_visibility(void *list, u32 show) {
    if (!show) close_letters(list, state(list), 0);
    FN(0xa4d80, void (*)(void *, u32))(list, show);
}

u32 letters_key(void *list, void *event) {
    close_letters(list, state(list), 1);
    return FN(0x7f104, u32 (*)(void *, void *))(list, event);
}

/* Small 5x7 bitmap, enlarged to 20x28 with native clipped rectangle fills.
 * No offscreen allocations or repeated font/disk lookup during scrolling. */
static const u8 glyphs[28][7] = {
    {14,17,17,31,17,17,17}, {30,17,17,30,17,17,30},
    {14,17,16,16,16,17,14}, {30,17,17,17,17,17,30},
    {31,16,16,30,16,16,31}, {31,16,16,30,16,16,16},
    {14,17,16,23,17,17,15}, {17,17,17,31,17,17,17},
    {14,4,4,4,4,4,14}, {7,2,2,2,18,18,12},
    {17,18,20,24,20,18,17}, {16,16,16,16,16,16,31},
    {17,27,21,21,17,17,17}, {17,25,25,21,19,19,17},
    {14,17,17,17,17,17,14}, {30,17,17,30,16,16,16},
    {14,17,17,17,21,18,13}, {30,17,17,30,20,18,17},
    {15,16,16,14,1,1,30}, {31,4,4,4,4,4,4},
    {17,17,17,17,17,17,14}, {17,17,17,17,17,10,4},
    {17,17,17,21,21,27,17}, {17,17,10,4,10,17,17},
    {17,17,10,4,4,4,4}, {31,1,2,4,8,16,31},
    {10,10,31,10,31,10,10}, {4,4,4,4,4,0,4}
};

static void fill(void *context, i32 top, i32 left, i32 bottom, i32 right) {
    i32 rect[4] = {top, left, bottom, right};
    FN(0xa4a38, void (*)(void *, void *))(context, rect);
}

void letters_draw(void *list, void *model, void *clip) {
    LetterState *s = state(list);
    /* The native publisher can drop the final timeout when its event pool is
     * full. Recover on the next paint; invalidate the full list because this
     * paint's clip may miss the old marker. No new background timer is needed. */
    if (s && s->active && now() - s->last_input >= TIMEOUT)
        close_letters(list, s, 1);
    FN(0x5cdc4, void (*)(void *, void *, void *))(list, model, clip);
    if (!s || !s->active || !FN(0xe3ddc, u32 (*)(void *))(list)) return;
    /* Native 3bd34 expands the list to the height of every row. Its cached
     * visible bounds (+78), maintained by 3c62c, already intersect all parent
     * viewports. Center there, independently of the partial repaint clip.
     */
    const i32 *visible = (const i32 *)((u8 *)list + 0x78);
    i32 height = visible[2] - visible[0];
    i32 width = visible[3] - visible[1];
    if (height < 44 || width < 44) return;
    u32 glyph = s->letter >= 'A' && s->letter <= 'Z' ? s->letter - 'A' :
                s->letter == '!' ? 27 : 26;
    u32 context[17];
    FN(0xc9c2c, void (*)(void *, void *))(list, clip);
    FN(0xf745c, void *(*)(void *))(context);
    FN(0x66efc, void (*)(void *, void *))(list, context);
    /* 66efc sets the drawing origin to the list's absolute content origin. */
    i32 top = visible[0] - (i32)WORD(list, 0x58) + (height - 44) / 2;
    i32 left = visible[1] - (i32)WORD(list, 0x5c) + (width - 44) / 2;
    u32 black = 0, white = 0x00ffffff;
    FN(0xcac3c, void (*)(void *, void *))(context, &black);
    fill(context, top, left, top + 44, left + 44);
    FN(0xcac3c, void (*)(void *, void *))(context, &white);
    fill(context, top + 2, left + 2, top + 42, left + 42);
    FN(0xcac3c, void (*)(void *, void *))(context, &black);
    for (u32 y = 0; y < 7; ++y) {
        for (u32 x = 0; x < 5; ++x) {
            if (!(glyphs[glyph][y] & (16u >> x))) continue;
            u32 start = x;
            while (x + 1 < 5 && (glyphs[glyph][y] & (16u >> (x + 1)))) ++x;
            /* Adjacent cells share a solid span; native clipping and the
             * resulting pixels are identical with one fill for the run. */
            fill(context, top + 8 + 4 * y, left + 12 + 4 * start,
                 top + 12 + 4 * y, left + 16 + 4 * x);
        }
    }
    FN(0x102318, void (*)(void *, u32))(context, 0);
}
