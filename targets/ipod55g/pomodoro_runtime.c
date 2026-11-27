/* 5.5G-only runtime. Calendar arithmetic and RTC scheduler execute native
 * 25.1.3 OSOS interfaces; UI and RTC task share recursive mutex 0x124.
 * This target owns its calendar/table and loaded extension state.
 */
/* Interval arithmetic uses native target calendar normalization below. */
#include "pomodoro_runtime.h"

_Static_assert(sizeof(Date) == 12, "native date layout");
_Static_assert(sizeof(Alarm) == 20, "native alarm record layout");
_Static_assert(sizeof(State) == 32, "5.5G interval state layout");

static u32 stamp(const Date *date) {
    /* Civil RTC seconds since 2000, with unsigned rollover. This avoids the
     * 71-minute microsecond counter. The native setters below preserve duration
     * across manual clock and timezone changes.
     */
    u32 y = date->year - 1;
    u32 leap = y / 4 - y / 100 + y / 400 - (1999 / 4 - 1999 / 100 + 1999 / 400);
    u32 days = (date->year - 2000u) * 365u + leap + date->year_day;
    return ((days * 24u + date->hour) * 60u + date->minute) * 60u + date->second;
}

static void add_seconds(Date *out, const Date *now, u32 seconds) {
    /* Normalize whole days/months/leap years using Apple's existing routine. */
    FN(0x88454, void (*)(Date *, const Date *, int, int, int, int))
        (out, now, 0, 0, seconds / 60u, seconds % 60u);
}

static int arm_alarm(State *state) {
    Alarm alarm = {0};
    alarm.date = state->deadline;
    alarm.wake = 1;
    /* Event=0: the background hook handles expiry after the native RTC task unlocks;
     * no unreserved kernel signal ID is consumed. */
    return FN(0xb3870, int (*)(int, const Alarm *))(5, &alarm) == 0;
}

u32 pomo_remaining(const State *state, const Date *now) {
    if (!state->running)
        return state->remaining;
    i32 left = (i32)(stamp(&state->deadline) - stamp(now));
    return left > 0 ? (u32)left : 0;
}

void pomo_reset(State *state) {
    FN(0x906e0, int (*)(int))(5);
    *state = (State){0};
    state->remaining = 25u * 60u;
}

int pomo_start(State *state, const Date *now) {
    if (state->running)
        return 1;
    add_seconds(&state->deadline, now, state->remaining);
    if (!arm_alarm(state))
        return 0;
    state->running = 1;
    return 1;
}

u32 pomo_expire(State *state, const Date *now) {
    if (!state->running || pomo_remaining(state, now))
        return 0;
    u32 elapsed = stamp(now) - stamp(&state->deadline);
    u32 changed = 0;
    /* One full four-focus cycle is 130 minutes / eight transitions. Bound
     * catch-up cost even when the UI/event consumer was absent for hours. */
    if (elapsed >= 7800u) {
        changed = (elapsed / 7800u) * 8u;
        elapsed %= 7800u;
    }
    u32 duration;
    for (;;) {
        if (state->phase == 0) {
            state->phase = state->round == 3 ? 2 : 1;
            duration = state->phase == 2 ? 900u : 300u;
        } else {
            state->phase = 0;
            state->round = (state->round + 1u) & 3u;
            duration = 1500u;
        }
        ++changed;
        if (elapsed < duration)
            break;
        elapsed -= duration;
    }
    state->remaining = duration - elapsed;
    add_seconds(&state->deadline, now, state->remaining);
    state->transitions += changed;
    if (!arm_alarm(state))
        state->running = 0;
    return changed;  /* Integration can emit ONE alert, not a catch-up burst. */
}

void pomo_pause(State *state, const Date *now) {
    if (!state->running)
        return;
    /* Reconcile an already due phase before freezing it. */
    pomo_expire(state, now);
    state->remaining = pomo_remaining(state, now);
    FN(0x906e0, int (*)(int))(5);
    state->running = 0;
}

/* Native scheduler derives records as epoch + 12. Keep this adjacency. */
struct { Date epoch; Alarm records[6]; } pomo_calendar;
_Static_assert(sizeof(pomo_calendar) == 132, "native calendar plus sixth record");
#define pomo_alarms pomo_calendar.records
extern void ipod55g_alarm_signal(void);
State pomo_state;
u32 pomo_ready;
u32 pomo_alert_pending;

extern int pomo_original_set_clock(const Date *date);
extern int pomo_original_set_zone(int zone, int daylight);

static void lock(void) { FN(0xa6540, int (*)(int))(0x124); }
static void unlock(void) { FN(0xa687c, int (*)(int))(0x124); }
static void read_time(Date *now) {
    FN(0xa6540, int (*)(int))(0x123);
    FN(0xb0124, void (*)(Date *))(now);
    FN(0xa687c, int (*)(int))(0x123);
}

/* Called during native RTC initialization, with 0x124 already held and all
 * six alarm records disabled. Reboot always starts stopped, without a file. */
void pomo_boot(void) {
    pomo_state = (State){0};
    pomo_state.remaining = 1500;
    pomo_alert_pending = 0;
    pomo_ready = 1;
}

/* 0 toggles Start/Pause; 1 is Reset (cancel and return to stopped Focus).
 * Explicit user actions take precedence over an alert waiting for dispatch. */
void pomo_control(u32 action) {
    Date now;
    if (!pomo_ready || action > 1) return;
    lock();
    pomo_alert_pending = 0;
    if (action == 1) {
        pomo_reset(&pomo_state);
    } else {
        read_time(&now);
        if (pomo_state.running) pomo_pause(&pomo_state, &now);
        else pomo_start(&pomo_state, &now);
    }
    unlock();
}

void pomo_snapshot(State *out, u32 with_remaining) {
    Date now;
    lock();
    *out = pomo_state;
    if (with_remaining && pomo_state.running) {
        read_time(&now);
        out->remaining = pomo_remaining(&pomo_state, &now);
    }
    unlock();
}

/* Runs after native RTC retirement/rescheduling and wake. No UI object is
 * retained here. Serialize the short AsyncPiezo publication with controls;
 * its receiver acknowledges under12c without acquiring124 or calendar123. */
void pomo_background(void) {
    Date now;
    u32 changed = 0;
    if (!pomo_ready) return;
    lock();
    if (pomo_state.running) {
        read_time(&now);
        changed = pomo_expire(&pomo_state, &now);
        changed |= pomo_alert_pending;
        /* Restore the actual phase deadline after a deferred clock-edit
         * alert, or repair a record retired by an early/stale interrupt. */
        if (pomo_state.running && (pomo_alert_pending || !pomo_alarms[5].enabled) &&
            !arm_alarm(&pomo_state))
            pomo_state.running = 0;
    }
    pomo_alert_pending = 0;
    /* Native RTC has already woken the system for Alarm.wake=1. This adapter
     * publishes tagged tones only: no wake/UI callbacks under the RTC lock.
     * Reset/Pause before publication cancel it; afterward the request belongs
     * to AsyncPiezo. Holding124 closes the previous unlock-to-dispatch race. */
    if (changed) ipod55g_alarm_signal();
    unlock();
}

static void before_clock_change(void) {
    Date now;
    if (!pomo_state.running) return;
    read_time(&now);
    if (pomo_expire(&pomo_state, &now)) pomo_alert_pending = 1;
    pomo_state.remaining = pomo_remaining(&pomo_state, &now);
    FN(0x906e0, int (*)(int))(5);
}

static void after_clock_change(void) {
    Date now;
    if (!pomo_state.running) return;
    read_time(&now);
    add_seconds(&pomo_state.deadline, &now, pomo_state.remaining);
    if (pomo_alert_pending) {
        /* Keep the phase deadline intact, but wake the RTC worker promptly
         * to deliver its alert outside the UI thread and calendar mutex. */
        State alert = pomo_state;
        add_seconds(&alert.deadline, &now, 1);
        if (!arm_alarm(&alert)) pomo_state.running = 0;
    } else if (!arm_alarm(&pomo_state)) pomo_state.running = 0;
}

int pomo_set_clock(const Date *date) {
    /* Both mutexes exist before any native setter call. Wrapping the entire
     * operation keeps the lock order RTC then calendar, including callbacks. */
    lock();
    before_clock_change();
    int result = pomo_original_set_clock(date);
    after_clock_change();
    unlock();
    return result;
}

int pomo_set_zone(int zone, int daylight) {
    lock();
    before_clock_change();
    int result = pomo_original_set_zone(zone, daylight);
    after_clock_change();
    unlock();
    return result;
}
