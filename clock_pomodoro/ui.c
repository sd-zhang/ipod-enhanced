/* One native screen; this file is called only by the UI task. */
#include "interval.h"
#define WORD(p, n) (*(u32 *)((u8 *)(p) + (n)))
#define PTR(p, n) (*(void **)((u8 *)(p) + (n)))
#define STR 0x53747220u
#define BMAP 0x424d6170u
#define MENU 0x4d656e75u
#define TIME 0x74696d65u
#define SCREEN (2554u * 4u + 2u)
#define LEAVE (2555u * 4u + 2u)
#define TOGGLE (2556u * 4u + 2u)
#define RESET (2557u * 4u + 2u)

u32 pomo_ui_timer[10];
u32 pomo_ui_visible;
u32 pomo_ui_ready;
u32 pomo_ui_phase;
/* Native BMap resource: four Clock digits plus its colon, without scaling.
 * One owned buffer keeps all digits from the same RTC snapshot. */
struct {
    u32 top, left, height, width;
    unsigned short stride, depth;
    u32 flags, bytes;
    u8 pixels[30 * 24];
} pomo_ui_bitmap;

static void *renderer(void) { return FN(0x64a48, void *(*)(u32))(0x2500); }
static void stop(void) { FN(0xd3aa0, void (*)(void *))(pomo_ui_timer); }
static int active(void *owner) {
    void *menu = owner ? PTR(owner, 0x24) : (void *)0;
    return menu && (WORD(menu, 0x1c) & 0xffffu) == 0x6141;
}

int pomo_property(void *owner, u32 tag, u32 key, void **out) {
    if (tag == BMAP && key == 0x6143) {
        State state;
        pomo_snapshot(&state, 1);
        u32 remaining = state.remaining > 1500 ? 1500 : state.remaining;
        u32 minutes = remaining / 60, seconds = remaining % 60;
        u32 keys[5] = {0x25ba + minutes / 10, 0x25ba + minutes % 10,
                       0x25b9, 0x25ba + seconds / 10, 0x25ba + seconds % 10};
        const u8 *glyphs[5];
        for (u32 i = 0; i < 5; ++i) {
            const u8 *src = FN(0x72a88, const u8 *(*)(u32, u32))(BMAP, keys[i]);
            if (!src) { *out = (void *)0; return 0; }
            glyphs[i] = src + 28;
        }
        pomo_ui_bitmap.top = pomo_ui_bitmap.left = 0;
        pomo_ui_bitmap.height = 30;
        pomo_ui_bitmap.width = 90;
        pomo_ui_bitmap.stride = 24;
        pomo_ui_bitmap.depth = 2;
        pomo_ui_bitmap.flags = 0;
        pomo_ui_bitmap.bytes = sizeof(pomo_ui_bitmap.pixels);
        /* Fixed native layout: digits at x=0,20,50,70; colon at x=43.
         * Write every packed byte once, including gaps and row padding. */
        for (u32 y = 0; y < 30; ++y) {
            const u8 *a = glyphs[0] + y * 8, *b = glyphs[1] + y * 8;
            const u8 *c = glyphs[3] + y * 8, *d = glyphs[4] + y * 8;
            u8 colon = glyphs[2][y * 4];
            u8 *dst = pomo_ui_bitmap.pixels + y * 24;
            for (u32 i = 0; i < 5; ++i) {
                dst[i] = a[i];
                dst[5 + i] = b[i];
            }
            dst[10] = colon >> 6;
            dst[11] = colon << 2;
            dst[12] = c[0] >> 4;
            for (u32 i = 0; i < 4; ++i) {
                dst[13 + i] = (c[i] << 4) | (c[i + 1] >> 4);
                dst[18 + i] = (d[i] << 4) | (d[i + 1] >> 4);
            }
            dst[17] = (c[4] << 4) | (d[0] >> 4);
            dst[22] = d[4] << 4;
            dst[23] = 0;
        }
        *out = &pomo_ui_bitmap;
        return 1;
    }
    if (tag == STR && (key == 0x6142 || key == 0x6144 || key == 0x6145)) {
        if (key == 0x6145) {
            *out = "Reset";
            return 1;
        }
        State state;
        pomo_snapshot(&state, 0);
        if (key == 0x6142) *out = state.phase ? "Break" : "Focus";
        else *out = state.running ? "Pause" : "Start";
        return 1;
    }
    if (tag == MENU && key == 0x6141) {
        *out = FN(0x72a88, void *(*)(u32, u32))(tag, key);
        return *out != (void *)0;
    }
    return FN(0x747b4, int (*)(void *, u32, u32, void **))(owner, tag, key, out);
}

static void refresh(void *owner, u32 changed) {
    if (!pomo_ui_visible || !active(owner)) return;
    State state;
    pomo_snapshot(&state, 0);
    if (changed || state.phase != pomo_ui_phase) {
        pomo_ui_phase = state.phase;
        FN(0x39b2c, void (*)(void *, u32, u32))(owner, STR, 0x6142);
    }
    FN(0x39b2c, void (*)(void *, u32, u32))(owner, BMAP, 0x6143);
    if (changed) FN(0x82f88, void (*)(void *))(PTR(owner, 0x24));

    if (state.running) {
        FN(0xd2bd0, void (*)(void *))(pomo_ui_timer);
    } else stop();
}

int pomo_dispatch(u32 command, void *event) {
    if (command == LEAVE) {
        pomo_ui_visible = 0;
        if (pomo_ui_ready) stop();
        return 1;
    }
    if (command == TOGGLE || command == RESET) {
        void *owner = renderer();
        if (pomo_ui_visible && active(owner)) {
            pomo_control(command == RESET);
            refresh(owner, 1);
        }
        return 1;
    }
    int result = FN(0x9cf04, int (*)(u32, void *))(command, event);
    if (command == SCREEN && result) {
        void *owner = renderer();
        if (active(owner)) {
            if (!pomo_ui_ready || pomo_ui_timer[8] != (u32)owner) {
                if (pomo_ui_ready) stop();
                FN(0x8bddc, void (*)(void *, void *, u32))(owner, pomo_ui_timer, 0);
                FN(0xcdd84, void (*)(void *, u32))(pomo_ui_timer, 1000);
                pomo_ui_ready = 1;
            }
            pomo_ui_visible = 1;
            refresh(owner, 1);
        }
    }
    return result;
}

int pomo_ui_event(void *owner, void *event) {
    if (event && WORD(event, 4) == TIME && PTR(event, 8) == pomo_ui_timer) {
        if (pomo_ui_visible && active(owner)) {
            refresh(owner, 0);
        } else {
            pomo_ui_visible = 0;
            if (pomo_ui_ready) stop();
        }
        return 1;
    }
    return FN(0x80870, int (*)(void *, void *))(owner, event);
}
