/* Pinned 5.5G 1.3 Main Menu customization; bit30 is outside native keys. */
#include <stdint.h>
#define KEY 0x7120u
#define PIN 0x40000000u
#define PRID 0x70724944u
#define STR 0x53747220u
#define NATIVE(address, type) ((type)(uintptr_t)(address))

extern int alarm_setting_get(void *, uint32_t, uint32_t, const void **);
extern int alarm_setting_set(void *, uint32_t, uint32_t, const uint16_t *, int);
static const uint16_t on = 0x602e, off = 0x602f;

uint32_t pomo_home_mask(uint32_t key) {
    if (key == KEY) return PIN;
    return NATIVE(0x105a04, uint32_t (*)(uint32_t))(key);
}

int pomo_home_setting_get(void *owner, uint32_t tag, uint32_t key, const void **out) {
    if (key != KEY) return alarm_setting_get(owner, tag, key, out);
    const uint16_t *value = NATIVE(0x193470, int (*)(void *, uint32_t))(owner, KEY) ? &on : &off;
    if (tag == PRID) *out = value;
    else if (tag == STR) {
        void *resource = NATIVE(0x21f3b8, void *(*)(void))();
        *out = NATIVE(0x21f418, void *(*)(void *, int))(resource, *value);
    } else *out = 0;
    return 1;
}

int pomo_home_setting_set(void *owner, uint32_t tag, uint32_t key,
                          const uint16_t *value, int option) {
    if (key != KEY) return alarm_setting_set(owner, tag, key, value, option);
    if (tag != PRID || !value) return 0;
    int before = NATIVE(0x193470, int (*)(void *, uint32_t))(owner, KEY);
    int after;
    if (*value == 0x60be) after = !before;
    else if (*value == 0x602e) after = 1;
    else if (*value == 0x602f) after = 0;
    else return 0;
    NATIVE(0x19351c, void (*)(void *, uint32_t, int))(owner, KEY, after);
    if (after != before)
        NATIVE(0x21f37c, void (*)(void *, uint32_t, uint32_t))(owner, STR, KEY);
    return 1;
}
