/* Alarm-only visual pulses on the native AsyncPiezo worker. The existing
 * hardware timer wakes the worker once during each pair's second tone;
 * its IRQ only acknowledges the timer, so the buzzer continues uninterrupted.
 * No UI pointer is retained, no work runs in an IRQ, and no new timer is owned.
 */
typedef unsigned int u32;
typedef unsigned char u8;
#define FN(a, t) ((t)(a))
#define WORD(p, n) (*(u32 *)((u8 *)(p) + (n)))
#define PTR(p, n) (*(void **)((u8 *)(p) + (n)))
#define TAG 0x80000000u
#define DISABLED 0x01000000u
#define SECOND_LIT 150000u
#define PIEZO ((void *)0x10419a2c)
#define TIMER (*(volatile u32 *)0x60005008)

/* Owned startup-zeroed RAM. Only the piezo worker accesses this word.
 * 0: no cue; 1: lit, unsplit tone; 2: dark tail.
 * Values >2 mean a lit split tone, encoding (dark-tail microseconds + 2).
 * This keeps the first tone lit through its deadline without another timer
 * or state word. The second tone supplies the pair's single dark interval. */
u32 alarm_flash_active;

int alarm_tone_request(u32 period, u32 duration) {
    return FN(0xa6e6c, int (*)(u32, u32))(period, duration | TAG);
}

void pomo_alarm_pattern(void) {
    /* Exact native Clock event 0x8a sequence. Like stock, full-queue failures
     * neither retry nor block for tone completion. RTC locks are already free. */
    for (u32 pair = 0; pair != 4; ++pair) {
        alarm_tone_request(180, 200000);
        alarm_tone_request(144, 400000);
    }
}

static void *application(void) {
    return FN(0x70290, void *(*)(void))();
}

static int enabled(void *app) {
    if (!app) return 0;
    void *ui = PTR(app, 0x18), *prefs = PTR(app, 0x124);
    return ui && prefs && !*((u8 *)ui + 0x189f) &&
           !(WORD(prefs, 0xb34) & DISABLED);
}

/* Share this cold cleanup body instead of cloning it into every hook. */
__attribute__((noinline)) void alarm_pattern_cancel(void) {
    if (!alarm_flash_active) return;
    alarm_flash_active = 0;
    void *app = application();
    if (app && PTR(app, 0x18))
        FN(0x3408c, void (*)(void *, void *))((u8 *)app + 0x11c, app);
}

void alarm_tone_begin(u32 duration, u32 period) {
    /* Ordinary clicker tones pay only this tag check and, normally, a no-op
     * restore. They do not look up application/preferences or touch the UI. */
    if (!(duration & TAG) || (duration & ~TAG) <= 50000u) {
        alarm_pattern_cancel();
        return;
    }
    void *app = application();
    if (!enabled(app)) {
        alarm_pattern_cancel();
        return;
    }
    /* The second tone normally inherits the first tone's lit screen. Check
     * actual native state as well: another UI action may have turned it off. */
    if (alarm_flash_active != 1 || !FN(0x69904, u32 (*)(void *))(app))
        FN(0x3408c, void (*)(void *, void *))((u8 *)app + 0x11c, app);
    duration &= ~TAG;
    /* Clock/Pomodoro: 200 ms + 150 ms lit, then 250 ms dark.
     * Calendar's shorter second tone instead leaves a 50 ms dark tail.
     * Identify the second tone by pitch so a partial queue needs no history. */
    alarm_flash_active = period == 144 && duration > SECOND_LIT
                       ? duration - SECOND_LIT + 2 : 1;
}

void alarm_timer_start(u32 control) {
    u32 duration = control & 0x1fffffffu;
    if (alarm_flash_active) {
        /* Date the sound deadline at its actual hardware start, after light
         * service overhead. Never change the pitch or split its waveform. */
        WORD(PIEZO, 12) = FN(0x154144, u32 (*)(void))() + duration;
        if (alarm_flash_active > 2)
            control = (control & ~0x1fffffffu) | SECOND_LIT;
    }
    TIMER = control;
}

void alarm_pattern_advance(void) {
    if (alarm_flash_active > 2) {
        u32 now = FN(0x154144, u32 (*)(void))();
        int remaining = (int)(WORD(PIEZO, 12) - now);
        if (remaining > 0 && remaining <= (int)(alarm_flash_active - 2)) {
            void *app = application();
            if (enabled(app)) {
                FN(0xc9734, void (*)(void *, u32))(app, 0);
                *((u8 *)app + 0x11f) = FN(0x69904, u32 (*)(void *))(app);
                alarm_flash_active = 2;
            } else {
                alarm_pattern_cancel();
            }
            /* Account for native backlight work and delayed scheduling; never
             * extend a tone to manufacture a missed visual gap. */
            now = FN(0x154144, u32 (*)(void))();
            remaining = (int)(WORD(PIEZO, 12) - now);
            if (remaining > 0) TIMER = TAG | (u32)remaining;
        }
    }
    FN(0xaf72c, void (*)(void))();
}

void alarm_pattern_finish(void) {
    FN(0x60704, void (*)(void))();
    alarm_pattern_cancel();
}
