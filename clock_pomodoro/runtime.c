/* Native integration for the 4G Clock interval timer. All state is in an
 * explicit extension of the startup-zeroed BSS, below the new heap boundary.
 * The UI and RTC task share the existing recursive RTC mutex (0x124).
 */
#include "interval.c"

Alarm pomo_alarms[6];
State pomo_state;
u32 pomo_ready;
u32 pomo_alert_pending;

extern int pomo_original_set_clock(const Date *date);
extern int pomo_original_set_zone(int zone, int daylight);

static void lock(void) { FN(0x96710, int (*)(int))(0x124); }
static void unlock(void) { FN(0x96a70, int (*)(int))(0x124); }
static void read_time(Date *now) {
    FN(0x96710, int (*)(int))(0x123);
    FN(0xb7e10, void (*)(Date *))(now);
    FN(0x96a70, int (*)(int))(0x123);
}

/* Called during native RTC initialization, with 0x124 already held and all
 * six alarm records disabled. Reboot always starts stopped, without a file. */
void pomo_boot(void) {
    FN(0x14794, void (*)(void *, u32))(&pomo_state, sizeof(pomo_state));
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

/* Runs after native RTC retirement/rescheduling and its final unlock. No UI
 * object is retained or invoked here. The native beep service runs on its
 * own worker; this task waits for one short request after releasing locks. */
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
    unlock();
    /* Historical r28 callee. pattern_build.py redirects only its private
     * literal to pomo_alarm_pattern in the r29 overlay, retaining this runtime's
     * tested phase/RTC code and exact addresses. Rebuild releases with that
     * pinned overlay, not this historical r19-to-Pomodoro installer alone. */
    if (changed) FN(0xa6e6c, int (*)(int, int))(180, 200000);
}

static void before_clock_change(void) {
    Date now;
    if (!pomo_state.running) return;
    read_time(&now);
    if (pomo_expire(&pomo_state, &now)) pomo_alert_pending = 1;
    pomo_state.remaining = pomo_remaining(&pomo_state, &now);
    FN(0x3caa4, int (*)(int))(5);
}

static void after_clock_change(void) {
    Date now;
    if (!pomo_state.running) return;
    read_time(&now);
    add_seconds(&pomo_state.deadline, &now, pomo_state.remaining);
    if (pomo_alert_pending) {
        /* Keep the phase deadline intact, but wake the RTC worker promptly
         * to deliver its alert outside the UI thread and both mutexes. */
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
