/* Disc Quick Scroll uses the stock gesture, list state, popup and timer. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef signed int i32;

#define BYTE(p, offset) (*(u8 *)((u8 *)(p) + (offset)))
#define HALF(p, offset) (*(u16 *)((u8 *)(p) + (offset)))
#define WORD(p, offset) (*(u32 *)((u8 *)(p) + (offset)))
#define POINTER(p, offset) (*(void **)((u8 *)(p) + (offset)))
#define NATIVE(address, type) ((type)(address))

#define LIST_VTABLE 0x677290
#define SONGS 0x6381
#define POPUP_FIRST_KEY 0x281
#define POPUP_LAST_KEY 0x284
#define POPUP_STRING_KEY 0x282
#define STOCK_INITIAL 0xfa
#define STOCK_START_ROW 0xfc
#define STOCK_ENABLED 0x100
#define STOCK_TIMER 0x104
#define STOCK_MODE 0x12c
#define STOCK_TITLE_KEY 0x134
#define DISC_GESTURE 0x40000000u

extern i32 discs_fast_target(void *, i32, i32, u32 *);

static int disc_gesture(void *list)
{
    /* Native +fc otherwise holds -1 or a signed selectable row. Its only
     * consumer checks the -1 sentinel. Use the spare range for this gesture. */
    return WORD(list, 0) == LIST_VTABLE && HALF(list, 0x1c) == SONGS &&
           (WORD(list, STOCK_START_ROW) & 0xff000000u) == DISC_GESTURE;
}

static i32 selected_row(void *list)
{
    if (!POINTER(list, 0xa8) || !NATIVE(0x257250, u32 (*)(void *))(list))
        return -1;
    return NATIVE(0x257260, i32 (*)(void *))(list);
}

static void *popup_provider(void)
{
    void *app = NATIVE(0x1d30d8, void *(*)(void))();
    /* The binder dispatches through App +0xcc. UI startup constructs the
     * base App and installs its concrete vtable at 0x16b1a0. */
    u32 type = app ? WORD(app, 0) : 0;
    if (type != 0x66fe10 && type != 0x66a510)
        return 0;
    /* Native App model registration at 0x1d2c44 reads this static slot. */
    void *provider = POINTER((void *)0x1081da80, 0x14);
    return provider && WORD(provider, 0) == 0x673428 ? provider : 0;
}

static void *popup_owner(void *provider, u32 key)
{
    return NATIVE(0x206ed4, void *(*)(void *, u32))(provider, key);
}

static int caption_unchanged(void *model, const char *caption)
{
    /* Native publication accepts its own retained string without a copy.
     * Keep every typed notification: alias rebinding alone sends a wildcard
     * and does not necessarily refresh the popup's retained widgets. */
    const char *current = POINTER(model, 0x28);
    if (!current)
        return 0;
    for (u32 i = 0; i < 6; ++i) {
        if (current[i] != caption[i])
            return 0;
        if (!caption[i])
            return 1;
    }
    return 0;
}

static void release_popup(void *provider, void *model)
{
    /* Stock bind(false) replaces the alias provider with a null pointer
     * before registering it as an observer. Its native map removal methods
     * let us erase owned aliases without touching a newer view's binding. */
    for (u32 key = POPUP_FIRST_KEY; key <= POPUP_LAST_KEY; ++key) {
        if (popup_owner(provider, key) != model)
            continue;
        NATIVE(0x13b5c8, u32 (*)(void *, u32))((u8 *)provider + 0x14, key);
        NATIVE(0x13b5c8, u32 (*)(void *, u32))((u8 *)provider + 0x3c, key);
        u32 alias = ((key + 4) << 16) | key;
        NATIVE(0x21ebcc, u32 (*)(void *, const u32 *))((u8 *)provider + 0x64, &alias);
    }
    if (NATIVE(0x149ec4, u32 (*)(void *, void *))(model, provider))
        NATIVE(0x14a178, void (*)(void *, void *))(model, provider);
}

void discs_fast_cancel(void *list)
{
    if (!disc_gesture(list))
        return;
    WORD(list, STOCK_START_ROW) = -1;
    HALF(list, STOCK_INITIAL) = 0;
    BYTE(list, STOCK_ENABLED) = HALF(list, STOCK_TITLE_KEY) != 0;

    void *model = POINTER(list, 0xa4);
    if (BYTE(list, STOCK_MODE)) {
        if (POINTER(list, 0xa8) && model) {
            NATIVE(0x23a1f8, void (*)(void *, u32))(list, 0);
        } else {
            NATIVE(0x151c98, void (*)(void *))((u8 *)list + STOCK_TIMER);
            BYTE(list, STOCK_MODE) = 0;
        }
        if (model)
            NATIVE(0x23adcc, void (*)(void *))(list);
        void *accelerator = POINTER(list, 0x140);
        if (accelerator)
            NATIVE(0x1c25a8, void (*)(void *))(accelerator);
    }

    /* Stock-enabled views already own their normal binding lifecycle. For
     * the album-only view release only the temporary binding we still own. */
    if (!BYTE(list, STOCK_ENABLED) && model) {
        void *provider = popup_provider();
        if (provider)
            release_popup(provider, model);
    }
}

u32 discs_fast_begin(void *list)
{
    if (WORD(list, 0) != LIST_VTABLE || HALF(list, 0x1c) != SONGS)
        return 0;
    u32 number;
    i32 row = selected_row(list);
    if (discs_fast_target(list, row, 0, &number) < 0)
        return 0;

    /* Nonzero initial tells the native wheel dispatcher it captured a
     * gesture origin already. The numeric popup reads the live disc run. */
    HALF(list, STOCK_INITIAL) = 1;
    WORD(list, STOCK_START_ROW) = DISC_GESTURE | (u32)row;
    BYTE(list, STOCK_ENABLED) = 1;
    return 1;
}

i32 discs_fast_gate(void *list, i32 step)
{
    if (!disc_gesture(list))
        return -1;
    u32 number;
    i32 row = selected_row(list);
    i32 target = discs_fast_target(list, row, step > 0, &number);
    /* No forward destination in the final run: let the inactive native
     * dispatcher keep scrolling songs, without entering popup/timer mode.
     * Zero is the direction-neutral eligibility check used by events. */
    return target >= 0 && (step <= 0 || target > row);
}

static int heading_is_aligned(void *list, i32 row)
{
    /* Only elide a reveal whose complete rectangle already fits. Leave
     * native paging, pending direction and bottom-clamp work to Apple. */
    if (!BYTE(list, STOCK_MODE) || WORD(list, 0xbc) || (WORD(list, 0xc8) & 1))
        return 0;
    void *parent = POINTER(list, 0x10);
    void *item = POINTER(list, 0xa8);
    if (!parent || WORD(parent, 0) != 0x66a938 || !(WORD(parent, 0x20) & 2) || !item || row <= 0)
        return 0;
    i32 top = (i32)WORD(parent, 0x58);
    i32 viewport = (i32)WORD(parent, 0x60) - top;
    i32 height = (i32)WORD(item, 0x60) - (i32)WORD(item, 0x58);
    if (height <= 0 || viewport > 240 || height > viewport / 2)
        return 0;
    return (i32)WORD(item, 0x58) + (row - 1) * height == top &&
           (i32)WORD(item, 0x5c) >= (i32)WORD(parent, 0x5c) &&
           (i32)WORD(item, 0x64) <= (i32)WORD(parent, 0x64);
}

void discs_fast_advance(void *list, i32 step, u32 velocity)
{
    if (!disc_gesture(list)) {
        NATIVE(0x23a7e4, void (*)(void *, i32, u32))(list, step, velocity);
        return;
    }
    if (!step)
        return;

    i32 row = selected_row(list);
    u32 number;
    i32 target = discs_fast_target(list, row, step, &number);
    if (target < 0) {
        discs_fast_cancel(list);
        return;
    }
    /* The final run clamps to its first song. Forward input inside that
     * run must preserve both selection and viewport instead of rewinding. */
    if (target > row || (target < row && step < 0)) {
        WORD(list, 0xbc) = step > 0 ? 1 : -1;
        NATIVE(0x23a04c, void (*)(void *, i32))(list, target);
        NATIVE(0x1c2428, void (*)(u32))(1);
    } else if (target == row && !heading_is_aligned(list, row)) {
        /* Entering at the final disc's first song still needs its heading
         * aligned. Recheck live geometry; retain no alignment cache. */
        NATIVE(0x23a5ec, void (*)(void *))(list);
    }
    NATIVE(0x151694, void (*)(void *))((u8 *)list + STOCK_TIMER);
}

void discs_fast_publish(void *list)
{
    if (!disc_gesture(list) || !BYTE(list, STOCK_MODE)) {
        NATIVE(0x23adcc, void (*)(void *))(list);
        return;
    }
    u32 number;
    void *provider = popup_provider();
    if (discs_fast_target(list, selected_row(list), 0, &number) < 0 || !provider) {
        discs_fast_cancel(list);
        return;
    }

    void *model = POINTER(list, 0xa4);
    if (!model) {
        discs_fast_cancel(list);
        return;
    }
    if (popup_owner(provider, POPUP_STRING_KEY) != model)
        NATIVE(0x17f58c, void (*)(void *, u32))(model, 1);

    /* The stock native string method copies all digits into the model. */
    const u32 powers[] = {10000, 1000, 100, 10, 1};
    char digits[6];
    u32 length = 0;
    for (u32 i = 0; i < 5; ++i) {
        u32 digit = 0;
        while (number >= powers[i]) {
            number -= powers[i];
            ++digit;
        }
        if (digit || length || i == 4)
            digits[length++] = '0' + digit;
    }
    digits[length] = 0;
    const struct { u32 vtable; const char *text; } string = {0x676a18, digits};
    const void *caption = caption_unchanged(model, digits) ? (u8 *)model + 0x24 : (const void *)&string;
    NATIVE(0x17f6a4, void (*)(void *, u32, const void *))(model, 1, caption);
}

void discs_fast_visibility(void *list, u32 visible)
{
    if (visible != 1)
        discs_fast_cancel(list);
    NATIVE(0x23b694, void (*)(void *, u32))(list, visible);
}

u32 discs_fast_event(void *list, void *event)
{
    if (disc_gesture(list) && event) {
        u32 kind = WORD(event, 4);
        if (kind == 9) {
            void *timer_event = NATIVE(0x222dd0, void *(*)(void *, u32))(event, 0xd00);
            if (timer_event && POINTER(timer_event, 8) == (u8 *)list + STOCK_TIMER) {
                discs_fast_cancel(list);
                return 1;
            }
        }

        int close = kind == 4 || kind == 5 ||
                    (kind == 13 && WORD(event, 8) == 0x564d6178 && HALF(event, 0xc) == SONGS);
        if (kind == 15) {
            void *wheel = NATIVE(0x222dd0, void *(*)(void *, u32))(event, 0x3500);
            if (wheel) {
                NATIVE(0x249054, i32 (*)(void *))(wheel);
                close = !(WORD(wheel, 0x10) & DISC_GESTURE);
            }
        }
        if (close || discs_fast_gate(list, 0) <= 0)
            discs_fast_cancel(list);
    }
    return NATIVE(0x23b704, u32 (*)(void *, void *))(list, event);
}
