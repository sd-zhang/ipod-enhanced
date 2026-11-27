/* Now Playing shortcuts. Called on the UI thread by Pixo selectors.
 * -2 checks availability; -3 starts a fresh browse after script history unwind.
 * Album -4 replaces the playback list with that album and restores transport.
 * All stock row arguments retain the original native selector.
 * No retained metadata pointers, timers, or writable globals.
 */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#define WORD(p, o) (*(u32 *)((u8 *)(p) + (o)))
#define HALF(p, o) (*(u16 *)((u8 *)(p) + (o)))
#define CALL(a, t) ((t)(a))

static int browse(u8 *ui, int operation, int album) {
    u8 *query = (u8 *)WORD(ui, 0x1c);
    u16 *name = (u16 *)(ui + (album ? 0x628 : 0x428));
    if (!query || !WORD(query, 0x44) || !WORD(query, 0xa7c) ||
        !WORD(query, 0xa80) || !HALF(ui, 0x28) ||
        !name[0] || name[0] > 255)
        return 0;
    if (operation == -2)
        return 1;
    /* Only a metadata copy is needed. c6724 invalidates all six native browse
     * indexes and per-track predicates, leaving the playing queue untouched.
     */
    u16 copy[256];
    u32 length = name[0];
    for (u32 i = 0; i <= length; ++i)
        copy[i] = name[i];
    if (CALL(0xc6724, int (*)(u8 *, int))(query, 0))
        return 0;
    /* Match the ordinary Albums screen, including clearing podcast ordering.
     * The Songs activation does not set this itself; it inherits its parent.
     */
    CALL(0xe2dac, void (*)(u8 *, int))(ui, 3);
    u16 *filter = (u16 *)(query + (album ? 0x670 : 0x270));
    for (u32 i = 0; i <= length; ++i)
        filter[i] = copy[i];
    for (u32 offset = 0x1500; offset <= 0x1510; offset += 4)
        WORD(ui, offset) = (u32)-1;
    for (u32 offset = 0x1824; offset <= 0x1838; offset += 4)
        WORD(ui, offset) = (u32)-1;
    ui[0x18a4] = 0;
    ui[0x18a5] = album ? 9 : 5;
    return 1;
}

static int can_seek(u8 *audio) {
    return CALL(0xa7acc, int (*)(u8 *))(audio) &&
           CALL(0x6446c, u8 *(*)(u8 *))(audio) != 0;
}

static int restore_transport(u8 *audio, u32 elapsed, int state) {
    /* ca4f8 discards seek errors. Its callee also returns success after only
     * updating cached elapsed when no seekable decoder exists, so check both
     * native prerequisites before trusting its error-returning seek path.
     */
    if (elapsed && (!can_seek(audio) ||
        CALL(0xa9c14, int (*)(u8 *, u32))(audio, elapsed)))
        return 0;
    if (state == 2 && WORD(audio, 0x2768) == 1 &&
        CALL(0xa8b28, int (*)(u8 *))(audio))
        return 0;
    return 1;
}

static int repeat_album(u8 *ui) {
    u8 *query = (u8 *)WORD(ui, 0x1c);
    if (!browse(ui, -2, 1) || !WORD(query, 0))
        return 0;
    u8 *prefs = (u8 *)WORD(query, 0);
    u8 *audio = (u8 *)WORD(prefs, 4);
    if (!audio)
        return 0;
    u8 *old = (u8 *)WORD(query, 0x44);
    u8 *current = (u8 *)WORD(audio, 0x2a8);
    if (!current || WORD(audio, 0x2a4) != (u32)old ||
        WORD(current, 0) != (u32)old || !WORD(current, 0x20))
        return 0;
    /* Use raw elapsed here: 6bc5c clamps it to zero for an unknown duration. */
    if (WORD(audio, 0x2778) && !can_seek(audio))
        return 0;
    u32 track = WORD(current, 0x20);
    if (!browse(ui, -3, 1) || CALL(0x4bc64, int (*)(u8 *))(query))
        return 0;
    u32 count = WORD(query, 0xab0), row = 0;
    u8 **index = (u8 **)WORD(query, 0xaac);
    if (!index || !count)
        return 0;
    while (row < count && WORD(index[row], 0x20) != track)
        ++row;
    if (row == count)
        return 0;
    /* a7168 deletes the playing queue before allocation. Stage its native
     * create/copy operations instead, and reject a partial copy: 48878 hides
     * append allocation errors. The source and ordering match the index.
     */
    u8 *next = 0;
    if (CALL(0x9d92c, int (*)(u8 *, u8 **))(query, &next) || !next)
        goto discard;
    u8 *source = CALL(0x6bdac, u8 *(*)(u8 *))(query);
    int order = CALL(0x79418, int (*)(u8 *))(query);
    CALL(0x48878, void (*)(u8 *, u8 *, int, int, u8 *))
        (query, source, order, query[0xa78], next);
    if (!WORD(next, 0x24) || HALF((u8 *)WORD(next, 0x24), 0x2e) != count)
        goto discard;
    u8 *selected = CALL(0x6e400, u8 *(*)(u8 *, int, int, u32))(next, 1, 0, row);
    if (!selected || WORD(selected, 0x20) != track ||
        WORD(audio, 0x2a8) != (u32)current || WORD(query, 0x44) != (u32)old)
        goto discard;
    u32 elapsed = CALL(0x6bc5c, u32 (*)(u8 *))(query);
    int state = CALL(0x6ba14, int (*)(u8 *))(query);
    if (!state || (WORD(audio, 0x2778) && !can_seek(audio)))
        goto discard;
    WORD(query, 0x44) = (u32)next;
    next[0xe5] = (next[0xe5] & 0x7f) | (WORD(query, 0x58) ? 0x80 : 0);
    /* Preserve stock preparation, shuffle preference and explicit selected
     * song. This is a native stop/start followed by a seek, not seamless audio.
     */
    if (CALL(0xa91b0, int (*)(u8 *, u8 *))(audio, selected))
        goto rollback;
    CALL(0xcbb54, void (*)(u8 *, int))(next, prefs[0x20] != 0);
    if (CALL(0xa8be8, int (*)(u8 *, u8 *, int))(audio, selected, 1) ||
        !restore_transport(audio, elapsed, state))
        goto rollback;
    if (old != (u8 *)WORD(query, 0x48))
        CALL(0x537bc, void (*)(u8 *, int))(old, 0);
    return 1;
rollback:
    WORD(query, 0x44) = (u32)old;
    if (!CALL(0xa8be8, int (*)(u8 *, u8 *, int))(audio, current, 1))
        restore_transport(audio, elapsed, state);
    /* A failed decoder rollback must never leave pointers into a freed queue.
     * Native null selection detaches its observer and current wrapper.
     */
    if (WORD(audio, 0x2a4) == (u32)next)
        CALL(0xa91b0, int (*)(u8 *, u8 *))(audio, 0);
discard:
    if (next && WORD(audio, 0x2a4) != (u32)next)
        CALL(0x537bc, void (*)(u8 *, int))(next, 0);
    return 0;
}

int actions_artist(u8 *ui, int row) {
    if (row == -2 || row == -3)
        return browse(ui, row, 0);
    return CALL(0xc560c, int (*)(u8 *, int))(ui, row);
}

int actions_album(u8 *ui, int row) {
    if (row == -4)
        return repeat_album(ui);
    if (row == -2 || row == -3)
        return browse(ui, row, 1);
    return CALL(0xc5430, int (*)(u8 *, int))(ui, row);
}

/* d2ad8 receives three times the configured dwell. Change only the menu
 * profile (1250 ms -> 3750) to six 140-ms animation intervals. Now Playing
 * uses 5000 ms and retains its native 1500-ms intermediate pause. Initial
 * selection and wrap pauses bypass this call, as do ordinary moving ticks.
 */
int actions_scroll_pause(int scaled_dwell) {
    if (scaled_dwell == 3750)
        return 840;
    return CALL(0x14a0c, int (*)(int))(scaled_dwell);
}
