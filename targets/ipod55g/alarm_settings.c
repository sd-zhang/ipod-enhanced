/* 5.5G stock 1.3 preferences. Bit29 is outside every native main-menu key. */
#include <stdint.h>
#define DISABLED 0x20000000u
#define KEY 0x7140u
#define PRID 0x70724944u
#define STR 0x53747220u
#define NATIVE(address, type) ((type)(uintptr_t)(address))

static volatile uint32_t alarm_disabled;
static const uint16_t on = 0x602e, off = 0x602f;
extern void alarm_original_defaults(void *record);
extern void ipod55g_alarm_setting_changed(void);

int ipod55g_alarm_enabled(void) { return !alarm_disabled; }

void alarm_preferences_loaded(const uint8_t *preferences) {
    alarm_disabled = (*(const uint32_t *)(preferences + 0xb34)) & DISABLED;
}

void alarm_defaults(void *record) {
    alarm_original_defaults(record);
    alarm_disabled = 0;
}

static void *query(void *owner) { return *(void **)((uint8_t *)owner + 0x98); }
static uint32_t flags(void *q) {
    return NATIVE(0x9d880, uint32_t (*)(void *))(q);
}

void alarm_reset_main_menu(void *owner) {
    void *q = query(owner);
    NATIVE(0xb34ac, void (*)(void *, uint32_t))(q, 0x0e180201u | (flags(q) & DISABLED));
}

int alarm_setting_get(void *owner, uint32_t tag, uint32_t key, const void **out) {
    if (key != KEY)
        return NATIVE(0x190b00, int (*)(void *, uint32_t, uint32_t, const void **))(owner, tag, key, out);
    const uint16_t *value = (flags(query(owner)) & DISABLED) ? &off : &on;
    if (tag == PRID) *out = value;
    else if (tag == STR) {
        void *resource = NATIVE(0x21f3b8, void *(*)(void))();
        *out = NATIVE(0x21f418, void *(*)(void *, int))(resource, *value);
    } else *out = 0;
    return 1;
}

int alarm_setting_set(void *owner, uint32_t tag, uint32_t key, const uint16_t *value, int option) {
    if (key != KEY)
        return NATIVE(0x1919d8, int (*)(void *, uint32_t, uint32_t, const uint16_t *, int))(owner, tag, key, value, option);
    if (tag != PRID || !value) return 0;
    void *q = query(owner);
    uint32_t before = flags(q), after = before;
    if (*value == 0x60be) after ^= DISABLED;
    else if (*value == 0x602e) after &= ~DISABLED;
    else if (*value == 0x602f) after |= DISABLED;
    else return 0;
    NATIVE(0xb34ac, void (*)(void *, uint32_t))(q, after);
    alarm_disabled = after & DISABLED;
    if (after != before) {
        ipod55g_alarm_setting_changed();
        NATIVE(0x21f37c, void (*)(void *, uint32_t, uint32_t))(owner, STR, KEY);
    }
    return 1;
}
