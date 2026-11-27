/* One-shot Pomodoro UI timer recovery on the native timer-delivery task.
 * No UI work runs here. Preserve the native publisher for unrelated timers,
 * and always finish native in-flight expiry cleanup before scheduling a retry.
 */
#include "interval.h"

extern volatile u32 pomo_ui_timer[10];
extern volatile u32 pomo_ui_visible;
extern State pomo_state;

#define UI_LOCK ((void *)0x103c9f44)
#define TIMER_LOCK ((void *)0x103cf118)
#define STOP 0x73746f70u
#define RUN 0x72756e20u
#define EXPIRED 0x65787069u

void pomo_timer_deliver(volatile u32 *timer) {
    if (timer != pomo_ui_timer) {
        FN(0xd94c4, void (*)(volatile u32 *))(timer);
        FN(0x9f688, void (*)(volatile u32 *))(timer);
        return;
    }

    /* Exact native d94c4 time-event fields and lock scope. Unlike its void
     * interface, retain the actual publisher result across unlock/cleanup.
     * The native copier always reads seven words; initialize its three unused
     * trailing fields rather than copying beyond a four-word C object.
     */
    u32 event[7];
    FN(0x9f5d8, void (*)(void *))(UI_LOCK);
    timer[7] = EXPIRED;
    void *destination = (void *)timer[9];
    event[0] = 0x74696d65u;
    FN(0x7b710, void (*)(u32 *))(&event[1]);
    event[2] = 0;
    event[3] = (u32)timer;
    event[4] = event[5] = event[6] = 0;
    void *publisher = *(void **)(0x103c9f18 + 0x18);
    u32 accepted = FN(0x8f5dc, u32 (*)(void *, void *, u32 *, u32, u32))
        (publisher, destination, event, 0, 0);
    FN(0x9fce4, void (*)(void *))(UI_LOCK);

    /* This clears timer+18 and wakes a native stop waiting for this expiry.
     * Rearming before it would either wait on ourselves or have RUN overwritten
     * by the original worker cleanup. This helper replaces that cleanup too.
     */
    FN(0x9f688, void (*)(volatile u32 *))(timer);
    if (accepted) return;

    /* Serialize the check and insertion with native start/stop. This mutex is
     * recursive: 9fd90 takes it again. Leave clears visible before stopping;
     * Pause/Reset clear running before stopping. Their stop therefore either
     * suppresses this retry or removes it after this lock is released.
     * RUN/STOP statuses also prevent replacing a newer manually started timer.
     * Read only the aligned running word; never take the RTC lock under this
     * scheduler lock, nor dereference a UI object from the delivery task.
     */
    FN(0x9f5d8, void (*)(void *))(TIMER_LOCK);
    if (pomo_ui_visible && *(volatile u32 *)&pomo_state.running &&
        timer[6] == STOP && timer[7] == EXPIRED) {
        timer[7] = RUN;
        /* Failed publication owns no UI event to purge. Schedule directly,
         * retaining the existing 1000-ms interval and native wrap arithmetic.
         */
        FN(0x9fd90, void (*)(volatile u32 *))(timer);
    }
    FN(0x9fce4, void (*)(void *))(TIMER_LOCK);
}
