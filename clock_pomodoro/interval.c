/* Shared interval logic. runtime.c supplies owned state, synchronization,
 * background expiry and clock-change handling; the host spike supplies its
 * own state and explicit expiry boundary.
 */
#include "interval.h"

_Static_assert(sizeof(Date) == 12, "native date layout");
_Static_assert(sizeof(Alarm) == 20, "native alarm record layout");
_Static_assert(sizeof(State) == 32, "prototype state layout");

static u32 stamp(const Date *date) {
    /* Civil RTC seconds since 2000, with unsigned rollover. This avoids the
     * 71-minute microsecond counter. Manual clock changes need an integration
     * hook in runtime.c; the isolated spike supplies none.
     */
    u32 y = date->year - 1;
    u32 leap = y / 4 - y / 100 + y / 400 - (1999 / 4 - 1999 / 100 + 1999 / 400);
    u32 days = (date->year - 2000u) * 365u + leap + date->year_day;
    return ((days * 24u + date->hour) * 60u + date->minute) * 60u + date->second;
}

static void add_seconds(Date *out, const Date *now, u32 seconds) {
    /* Normalize whole days/months/leap years using Apple's existing routine. */
    FN(0x3034c, void (*)(Date *, const Date *, int, int, int, int))
        (out, now, 0, 0, seconds / 60u, seconds % 60u);
}

static int arm_alarm(State *state) {
    Alarm alarm = {0};
    alarm.date = state->deadline;
    alarm.wake = 1;
    /* Event=0: runtime.c handles expiry after the native RTC task unlocks;
     * no unreserved kernel signal ID is consumed. */
    return FN(0xcd21c, int (*)(int, const Alarm *))(5, &alarm) == 0;
}

u32 pomo_remaining(const State *state, const Date *now) {
    if (!state->running)
        return state->remaining;
    i32 left = (i32)(stamp(&state->deadline) - stamp(now));
    return left > 0 ? (u32)left : 0;
}

void pomo_reset(State *state) {
    FN(0x3caa4, int (*)(int))(5);
    FN(0x14794, void (*)(void *, u32))(state, sizeof(*state));
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
    FN(0x3caa4, int (*)(int))(5);
    state->running = 0;
}
