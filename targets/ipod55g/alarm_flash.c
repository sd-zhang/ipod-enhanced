/* Native 5.5G 1.3 alarm cue. Sound remains on AsyncPiezo; physical light work
 * runs on BacklightTask, whose fade mutex must never block the sound worker.
 * See CLOCK_NATIVE.md for independently traced addresses and evidence limits. */
typedef unsigned int u32;
typedef unsigned char u8;
#define FN(a, t) ((t)(a))
#define WORD(a) (*(volatile u32 *)(a))
#define SIGNAL 0x188u
#define TAG 0x80000000u
#define TIMER WORD(0x60005008)
#define DEADLINE WORD(0x1081dfbc)
#define LIGHT ((volatile u32 *)0x1081ff40)
extern int ipod55g_alarm_enabled(void);
extern void ipod55g_alarm_original_power(u32);
extern void ipod55g_alarm_original_brightness(u32);

/* The full native table is relocated, retaining indices 0..391. Startup's
 * original initialization loop initializes our additional entry 392 too. */
u32 ipod55g_alarm_signal_table[393];
const u32 ipod55g_alarm_light_events[] = {0xa6, 0xa7, 0xa8, 0xa9, 0xaa, SIGNAL, 0};

/* Producer writes a latest-state mailbox, then signals. Native signals coalesce;
 * consequently delayed handlers apply the latest phase, never replay old edges.
 * 0 restores current stock policy; 1 lights; 2 darkens. */
volatile u32 ipod55g_alarm_light_phase;
u32 ipod55g_alarm_tone_state;
static volatile u32 stock_power, stock_brightness, power_seen, brightness_seen;
static volatile u32 projected, projection_epoch, output_repair_pending;
static volatile u32 stock_generation;
static u32 fallback_power, fallback_brightness;

static int lcd_enabled(void) { return *(volatile u8 *)0x1081e028 != 0; }
static u32 micros(void) { return FN(0x282860, u32 (*)(void))(); }
static void request_phase(u32 phase) {
    ipod55g_alarm_light_phase = phase;
    __asm__ volatile("" ::: "memory");
    FN(0xa67b0, u32 (*)(u32))(SIGNAL);
}

static void restore_request(void) {
    if (ipod55g_alarm_tone_state || ipod55g_alarm_light_phase) {
        ipod55g_alarm_tone_state = 0;
        request_phase(0);
    }
}

void ipod55g_alarm_setting_changed(void) {
    if (!ipod55g_alarm_enabled()) request_phase(0);
}

/* Only the two native Clock sites target this adapter; WatchdogTask retains
 * its original shared pattern. Each request retains native rejection behavior. */
void ipod55g_clock_alarm_signal(void) {
    for (u32 n = 0; n != 4; ++n) {
        FN(0xcd430, int (*)(u32,u32))(180, 200000 | TAG);
        FN(0xcd430, int (*)(u32,u32))(144, 400000 | TAG);
    }
}

void ipod55g_alarm_signal(void) {
    /* The RTC task already wakes for the Pomodoro record before dispatch.
     * Keep this adapter free of UI/wake services: runtime holds mutex124
     * through these short AsyncPiezo publications to serialize Reset. */
    ipod55g_clock_alarm_signal();
}

u32 ipod55g_alarm_tone_begin(u32 encoded, u32 period) {
    u32 duration = encoded & ~TAG;
    if (!(encoded & TAG) || duration <= 50000 ||
        !ipod55g_alarm_enabled() || !lcd_enabled()) {
        restore_request();
        return duration;
    }
    if (ipod55g_alarm_light_phase != 1) request_phase(1);
    ipod55g_alarm_tone_state = period == 144 && duration > 150000
                           ? duration - 150000 + 2 : 1;
    return duration;
}

void ipod55g_alarm_timer_start(u32 control) {
    if (ipod55g_alarm_tone_state) {
        u32 duration = control & 0x1fffffffu;
        DEADLINE = micros() + duration;
        if (ipod55g_alarm_tone_state > 2)
            control = (control & ~0x1fffffffu) | 150000;
    }
    TIMER = control;
}

void ipod55g_alarm_worker(void) {
    if (ipod55g_alarm_tone_state && (!ipod55g_alarm_enabled() || !lcd_enabled())) {
        u32 split = ipod55g_alarm_tone_state > 2;
        int remaining = (int)(DEADLINE - micros());
        restore_request();
        /* A disabled cue must still retire a tone whose timer was split. */
        if (split && remaining > 0) TIMER = TAG | (u32)remaining;
    }
    if (ipod55g_alarm_tone_state > 2) {
        int remaining = (int)(DEADLINE - micros());
        if (remaining > 0 && remaining <= (int)(ipod55g_alarm_tone_state - 2)) {
            request_phase(2);
            ipod55g_alarm_tone_state = 2;
            remaining = (int)(DEADLINE - micros());
            if (remaining > 0) TIMER = TAG | (u32)remaining;
        }
    }
    FN(0xc719c, void (*)(void))();
}

void ipod55g_alarm_drain(void) {
    FN(0x11c750, void (*)(void))();
    restore_request();
}

void ipod55g_alarm_cancel(void) { restore_request(); }

/* These hooks retain the latest stock output requests even during an override.
 * Calls made by our own projection bypass these hooks through trampolines. */
static void output_finished(u32 was_projected, u32 epoch) {
    /* A native output can finish after the owner restored and cleared projected.
     * Only BacklightTask advances epoch, also detecting a complete intervening
     * cue. Producers only store 1: no shared read/modify/write counter is needed.
     * Never rewrite the phase here; a newer sound/setting request must win. */
    if (was_projected || projected || projection_epoch != epoch) {
        output_repair_pending = 1;
        FN(0xa67b0, u32 (*)(u32))(SIGNAL);
    }
}

void ipod55g_alarm_power(u32 wanted) {
    u32 epoch = projection_epoch, was_projected = projected;
    stock_power = wanted;
    power_seen = 1;
    ++stock_generation;
    if (projected && ipod55g_alarm_light_phase &&
        ipod55g_alarm_enabled() && lcd_enabled())
        wanted = ipod55g_alarm_light_phase == 1;
    ipod55g_alarm_original_power(wanted);
    output_finished(was_projected, epoch);
}

void ipod55g_alarm_brightness(u32 wanted) {
    u32 epoch = projection_epoch, was_projected = projected;
    stock_brightness = wanted;
    brightness_seen = 1;
    ++stock_generation;
    if (projected && ipod55g_alarm_light_phase &&
        ipod55g_alarm_enabled() && lcd_enabled())
        wanted = ipod55g_alarm_light_phase == 1 ? WORD(0x1081d8c8) : 0;
    ipod55g_alarm_original_brightness(wanted);
    output_finished(was_projected, epoch);
}

static void apply_light(void) {
    u32 owner = FN(0xa63e0, u32 (*)(u32))(0x136);
    u32 task = FN(0xa6420, u32 (*)(void))();
    int took_lock = owner != task;
    if (took_lock) FN(0xa6540, void (*)(u32))(0x136);
    u32 repair = output_repair_pending;
    /* Clear only an observed request, before reading policy or writing output.
     * A completion before this clear is covered by this repair; one afterward
     * retains its flag/event. Reading zero must not erase a racing completion. */
    if (repair) output_repair_pending = 0;
    u32 phase = ipod55g_alarm_light_phase;
    if (!ipod55g_alarm_enabled() || !lcd_enabled()) phase = 0;
    if ((phase || repair) && !projected) {
        /* Repair-only work also owns an interval: an in-flight native writer
         * must detect it, and exhausted restoration attempts must retry.
         * Publish ownership before epoch; wrappers read epoch before ownership,
         * so a writer starting between the stores cannot miss the interval. */
        projected = 1;
        ++projection_epoch;
        fallback_power = (*(volatile u8 *)0x1081ff40 != 0);
        fallback_brightness = LIGHT[2];
    }
    if (phase) {
        if (phase == 1) {
            ipod55g_alarm_original_power(1);
            ipod55g_alarm_original_brightness(WORD(0x1081d8c8));
        } else ipod55g_alarm_original_power(0);
    } else if (projected) {
        /* A concurrent native brightness/power request must win restoration.
         * Three attempts bound owner-task work; another owned event retries
         * if native requests arrive during every attempt. Completion repair
         * above also covers in-flight output and lost generation increments. */
        for (u32 attempt = 0; attempt != 3; ++attempt) {
            u32 generation = stock_generation;
            /* Only native producers write their shadows. Capturing fallback
             * must not overwrite a request that arrives during that capture. */
            u32 power = power_seen ? stock_power : fallback_power;
            u32 brightness = brightness_seen ? stock_brightness : fallback_brightness;
            if (power && lcd_enabled()) {
                ipod55g_alarm_original_power(power);
                ipod55g_alarm_original_brightness(brightness);
            } else {
                ipod55g_alarm_original_brightness(brightness);
                ipod55g_alarm_original_power(0);
            }
            if (stock_generation == generation) { projected = 0; break; }
        }
        if (projected) FN(0xa67b0, u32 (*)(u32))(SIGNAL);
    }
    if (took_lock) FN(0xa687c, void (*)(u32))(0x136);
}

u32 ipod55g_alarm_light_wait(const u32 *unused) {
    (void)unused;
    for (;;) {
        u32 event = FN(0xa6958, u32 (*)(const u32 *))(ipod55g_alarm_light_events);
        if (event != SIGNAL) return event;
        apply_light();
    }
}
