/* Serialize complete native LCD transactions, independently of graphics/RTC
 * ownership. Never hold this mutex around UI callbacks or sound requests.
 * Native mutexes sleep on contention and are recursive; IRQs remain enabled.
 */
typedef unsigned int u32;
#define FN(a, t) ((t)(a))
typedef u32 (*transaction)(u32, u32, u32);

u32 lcd_mutex;

__attribute__((noinline)) void lcd_initialize(void) {
    if (lcd_mutex) return;
    u32 id = 0;
    /* Same native pool used by the immediately following graphics constructor.
     * Before this constructor there is no published surface/drawing task.
     * An exhausted pool retains native unlocked behavior, never a bogus ID. */
    if (!FN(0x96500, u32 (*)(u32, u32 *))(2, &id) && id >= 308 && id <= 707)
        lcd_mutex = id;
}

void lcd_display_start(void) {
    lcd_initialize();
    FN(0xcf428, void (*)(void))();
}

void lcd_pool_reset(void) {
    /* Native 87d04 repopulates the entire free-ID pool at startup/restart. */
    lcd_mutex = 0;
    FN(0x87d04, void (*)(void))();
}

__attribute__((noinline)) static u32 lcd_run(transaction native, u32 a, u32 b, u32 c) {
    u32 id = lcd_mutex;
    if (id) FN(0x96710, u32 (*)(u32))(id);
    u32 result = native(a, b, c);
    if (id) FN(0x96a70, u32 (*)(u32))(id);
    return result;
}

/* Replay the displaced native entry instruction in tiny ARM trampolines.
 * Guard the whole pixel stream, including the two halves of each bus word.
 * Initialization/power sequences can nest the contrast/display-mode guards.
 */
#define WRAP(name) \
    extern u32 name##_native(u32, u32, u32); \
    u32 name(u32 a, u32 b, u32 c) { return lcd_run(name##_native, a, b, c); }
WRAP(lcd_redraw)
WRAP(lcd_init)
WRAP(lcd_display_mode)
WRAP(lcd_suspend)
WRAP(lcd_power)
WRAP(lcd_contrast)
