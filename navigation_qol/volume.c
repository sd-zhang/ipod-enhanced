/* Volume Limit: the visible native slider owns the draft; prefs+b48 owns the
 * effective runtime ceiling. Two checked Device records retain the preceding
 * valid setting across a short write. There is no writable static state.
 */
typedef unsigned char u8;
typedef unsigned int u32;
#define WORD(p, o) (*(u32 *)((u8 *)(p) + (o)))
#define CALL(a, t) ((t)(a))

typedef struct {
    u32 magic, generation, generation_inverse, cap, cap_inverse;
} Record;

typedef struct {
    int drive;
    char path[36];
} Path;

static int make_path(Path *path, int slot) {
    const char *name = "iPod_Control\\Device\\VolumeLimit0";
    unsigned i = 0;
    path->drive = CALL(0xa2b60, int (*)(void))();
    do {
        path->path[i] = name[i];
    } while (name[i++]);
    path->path[i - 2] = (char)('0' + slot);
    return path->drive != -1;
}

static int read_record(int slot, Record *record) {
    Path path;
    int handle = -1;
    u32 size[2] = {0, 0}, count = 0;
    if (!make_path(&path, slot))
        return -1;
    /*93974 collapses every read-open failure to -43. The lower native open
     * preserves -12 (file absent) versus real errors, so an unreadable prior
     * record cannot accidentally become a replaceable slot during save.
     */
    handle = CALL(0xa33d4, int (*)(int, const char *, int))
        (path.drive, path.path, 1);
    if (handle < 0)
        return handle == -12 ? 0 : -1;
    int status = CALL(0x938f8, int (*)(int, u32 *))(handle, size) ? -1 : 0;
    if (!status && size[0] == sizeof(*record) && size[1] == 0)
        status = CALL(0x939e0, int (*)(int, void *, u32, int, u32 *))
                     (handle, record, sizeof(*record), 0, &count) ||
                 count != sizeof(*record) ? -1 : 1;
    if (CALL(0x938d4, int (*)(int))(handle))
        return -1;
    if (status != 1)
        return status;
    return record->magic == 0x564c4d31 && record->cap <= 256 &&
           record->generation_inverse == ~record->generation &&
           record->cap_inverse == ~record->cap;
}

static int newest(Record records[2], int saving) {
    int first = read_record(0, &records[0]);
    int second = read_record(1, &records[1]);
    /* Do not destroy an unreadable slot when committing another value. Boot
     * can still use the other readable, valid record after an I/O error.
     */
    if (saving && (first < 0 || second < 0))
        return -2;
    if (first != 1)
        return second == 1 ? 1 : -1;
    if (second != 1)
        return 0;
    /* Adjacent generations differ by one, including 0xffffffff -> 0. */
    return (int)(records[1].generation - records[0].generation) > 0 ? 1 : 0;
}

static int save_record(u32 cap) {
    Record previous[2], next;
    int latest = newest(previous, 1);
    if (latest == -2)
        return 0;
    next.magic = 0x564c4d31;
    next.generation = latest < 0 ? 0 : previous[latest].generation + 1;
    next.generation_inverse = ~next.generation;
    next.cap = cap;
    next.cap_inverse = ~cap;
    Path path;
    int handle = -1;
    u32 count = 0;
    if (!make_path(&path, latest == 0 ? 1 : 0) ||
        CALL(0x49dc4, int (*)(void))() ||
        CALL(0x49f14, int (*)(void))() ||
        CALL(0x93974, int (*)(Path *, u32, int, int *))
            (&path, 0x64617461, 2, &handle))
        return 0;
    /* The native write wrapper reports short writes as success and discards
     * automatic-flush errors. Ask for no automatic flush and check both.
     */
    int ok = !CALL(0x93c50, int (*)(int, const void *, u32, int, u32 *))
                 (handle, &next, sizeof(next), 0, &count) && count == sizeof(next);
    if (ok && CALL(0xa2a34, int (*)(int))(handle))
        ok = 0;
    if (CALL(0x938d4, int (*)(int))(handle))
        ok = 0;
    return ok;
}

/* Replace only the constructor's BL b5408 at8db08. Native initialization and
 * Preferences migration run first; audio startup has not happened yet.
 */
int volume_load(u8 *prefs) {
    int result = CALL(0xb5408, int (*)(u8 *))(prefs);
    Record records[2];
    int latest = newest(records, 0);
    if (latest >= 0 && records[latest].cap < WORD(prefs, 0xb48))
        WORD(prefs, 0xb48) = records[latest].cap;
    return result;
}

static u8 *slider_for(u8 *renderer) {
    u8 *root = renderer ? (u8 *)WORD(renderer, 0x18) : 0;
    if (!root)
        return 0;
    u32 table = WORD(root, 0);
    /* Search the already constructed tree, without63f30's lazy-create path. */
    return CALL(table + WORD((u8 *)table, 0xc8), u8 *(*)(u8 *, u32, int))
        (root, 0x2a2a2a2a, 0x6121);
}

int volume_control(u8 *renderer, int operation) {
    if (operation > -1 || (operation < -4 && operation > -16))
        return CALL(0x1b6f8, int (*)(u8 *, int))(renderer, operation);
    u8 *ui = CALL(0x64a48, u8 *(*)(u32))(0x3300);
    ui = CALL(0x5d4e4, u8 *(*)(u8 *, u32))(ui, 0x3300);
    u8 *prefs = ui ? (u8 *)WORD(ui, 0x18) : 0;
    u8 *slider = slider_for(renderer);
    if (!prefs || !slider)
        return 0;
    u32 maximum = CALL(0x799e8, int (*)(void))() ? 216 : 256;
    if (operation == -1) {
        CALL(0xcb474, void (*)(u8 *, int))(slider, 0);
        CALL(0xcb2e8, void (*)(u8 *, int))(slider, (int)maximum);
        CALL(0xce1f4, void (*)(u8 *, int, int))(slider, (int)WORD(prefs, 0xb48), 0);
    } else if (operation == -2 || operation == -3 || operation <= -16) {
        int value = (int)WORD(slider, 0x6c);
        u32 magnitude = 1;
        int decrease = operation == -3;
        if (operation <= -16) {
            /* Even/odd negative operations encode wheel magnitude and
             * direction. Unsigned arithmetic also handles INT_MIN safely.
             */
            magnitude = (0u - (u32)operation - 16u) >> 1;
            if (magnitude > 256)
                magnitude = 256;
            decrease = (u32)operation & 1;
        }
        if (!magnitude)
            return 1;
        CALL(0xce1f4, void (*)(u8 *, int, int))
            (slider, value + (decrease ? -(int)magnitude : (int)magnitude), 0);
    } else {
        u32 cap = WORD(slider, 0x6c);
        if (cap > maximum || !save_record(cap == maximum ? 256 : cap))
            return 0;
        u32 previous = WORD(prefs, 0xb48);
        WORD(prefs, 0xb48) = cap;
        WORD(ui, 0x184c) = cap;
        u8 *audio = (u8 *)WORD(prefs, 4);
        int result = 0;
        /* A lower ceiling must also be applied after per-song gain. Reapply
         * an unchanged ceiling to allow retry after a backend error. Raising
         * the ceiling leaves the current output and balance untouched.
         */
        if (audio && cap <= previous)
            result = CALL(0xa9d30, int (*)(u8 *, int, int, int))
                (audio, (int)WORD(audio, 0x2770), (int)WORD(audio, 0x2774), 1);
        CALL(0x39b2c, void (*)(u8 *, u32, int))(ui, 0x436e746c, 0x333e);
        return result == 0;
    }
    CALL(0xcb558, void (*)(u8 *))(slider);
    return 1;
}
