/* Shared native alarm illumination and one persisted preference. No new state,
 * timers, files or audio path. The only wake callers are the two RTC sites. */
typedef unsigned int u32;
typedef unsigned char u8;
#define FN(a, t) ((t)(a))
#define WORD(p, n) (*(u32 *)((u8 *)(p) + (n)))
#define PTR(p, n) (*(void **)((u8 *)(p) + (n)))
#define DISABLED 0x01000000u

void alarm_wake(u32 wake) {
    /* Retain every native wake side effect, including LCD restoration. Stock
     * wake can leave an already-awake LCD's timed-out backlight off. */
    FN(0x99afc, void (*)(u32))(wake);
    if (!wake) return;
    void *app = FN(0x70290, void *(*)(void))();
    void *prefs = app ? PTR(app, 0x124) : (void *)0;
    if (prefs && !(WORD(prefs, 0xb34) & DISABLED))
        FN(0x3408c, void (*)(void *, void *))((u8 *)app + 0x11c, app);
}

/* Called from the native Settings property's existing default branch. */
u32 alarm_set(void *settings, u32 tag, const unsigned short *value) {
    if (tag != 0x70724944u || !value) return 0; /* prID */
    void *bridge = PTR(settings, 0x58);
    void *prefs = bridge ? PTR(bridge, 0) : (void *)0;
    if (!prefs) return 0;
    u32 mask = WORD(prefs, 0xb34);
    switch (*value) {
    case 0x27a1: mask &= ~DISABLED; break; /* On */
    case 0x27a2: mask |= DISABLED; break;  /* Off */
    case 0x283f: mask ^= DISABLED; break; /* native cycle/toggle operation */
    default: return 0;
    }
    FN(0xcb258, void (*)(void *, u32))(bridge, mask);
    return 1;
}

void alarm_home_reset(void *settings) {
    void *bridge = PTR(settings, 0x58);
    void *prefs = bridge ? PTR(bridge, 0) : (void *)0;
    if (prefs)
        FN(0xcb258, void (*)(void *, u32))(
            bridge, 0x81201u | (WORD(prefs, 0xb34) & DISABLED));
}
