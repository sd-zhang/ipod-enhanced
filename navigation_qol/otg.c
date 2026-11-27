/* UI-thread Now Playing OTG actions. No saved pointers or writable globals.
 * -5 chooses the contextual view; -6 adds; -7 removes. Other selector inputs
 * retain r18's album behavior. Menu labels cannot reverse a stale operation.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#define WORD(p, o) (*(u32 *)((u8 *)(p) + (o)))
#define HALF(p, o) (*(u16 *)((u8 *)(p) + (o)))
#define CALL(a, t) ((t)(a))

struct context { u8 *query, *prefs, *otg, *item; int playing_otg; };

static int current(u8 *ui, struct context *c) {
    if (!ui || !(c->query = (u8 *)WORD(ui, 0x1c)) ||
        !(c->prefs = (u8 *)WORD(c->query, 0)))
        return 0;
    u8 *audio = (u8 *)WORD(c->prefs, 4);
    u8 *queue = (u8 *)WORD(c->query, 0x44);
    c->otg = (u8 *)WORD(c->query, 0x48);
    if (!audio || !queue || !c->otg ||
        WORD(queue, 4) != 0x706c7374 || WORD(c->otg, 4) != 0x706c7374 ||
        WORD(queue, 0xc) != WORD(c->otg, 0xc) || !WORD(c->otg, 0x24) ||
        WORD(audio, 0x2a4) != (u32)queue)
        return 0;
    c->item = (u8 *)WORD(audio, 0x2a8);
    if (!c->item || WORD(c->item, 0) != (u32)queue ||
        !WORD(c->item, 0x10) || !WORD(c->item, 0x20) || (c->item[0x1d] & 1))
        return 0;
    c->playing_otg = queue == c->otg;
    return 1;
}

int otg_album(u8 *ui, int operation) {
    if (operation != -5 && operation != -6 && operation != -7)
        return CALL(0x35e604, int (*)(u8 *, int))(ui, operation);
    struct context c;
    int valid = current(ui, &c);
    if (operation == -5)
        return valid && c.playing_otg ? 0x6107 : 0x6100;
    if (!valid || c.playing_otg != (operation == -7))
        return 0;
    if (operation == -6) {
        u8 *lock = (u8 *)WORD(c.query, 0xa70);
        if (!lock)
            return 0;
        /* Same lock and append primitive as native 472f0; reread the live
         * selection after acquiring it. Allocation failure must not mark dirty.
         */
        CALL(0x98b48, void (*)(u8 *))(lock);
        int success = 0;
        if (current(ui, &c) && !c.playing_otg && WORD(c.query, 0xa70) == (u32)lock &&
            HALF((u8 *)WORD(c.otg, 0x24), 0x2e) < 1000 &&
            CALL(0x47ea4, u8 *(*)(u8 *, u8 *, int, int))(c.item, c.otg, 0, 0)) {
            c.prefs[0xb46] = 1;
            success = 1;
        }
        CALL(0xdfa74, void (*)(u8 *))(lock);
        if (success)
            CALL(0xe0de4, void (*)(u8 *))(ui);
        return success;
    }
    /* Exact wrapper identity matters when the same song occurs twice.
     * Native 515e0 dispatches dpli BEFORE unlink/free. The audio observer stops
     * and detaches the current item. Never dereference c.item after this call.
     * These are the remaining native 50aec / bd7b8 refresh operations.
     */
    if (CALL(0x515e0, int (*)(u8 *))(c.item))
        return 0;
    CALL(0xaa490, void (*)(u8 *))(c.otg);
    CALL(0x9129c, void (*)(u8 *))(c.query);
    CALL(0x912c4, void (*)(u8 *))(c.query);
    c.prefs[0xb46] = 1;
    CALL(0xe2d40, void (*)(u8 *))(ui);
    return 1;
}
