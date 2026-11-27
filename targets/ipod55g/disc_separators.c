/* Album disc headings for the stock 5.5G 1.3 Songs list. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef signed int i32;

#define WORD(p, offset) (*(u32 *)((u8 *)(p) + (offset)))
#define HALF(p, offset) (*(u16 *)((u8 *)(p) + (offset)))
#define POINTER(p, offset) (*(void **)((u8 *)(p) + (offset)))
#define NATIVE(address, type) ((type)(address))

#define SONGS 0x6381
#define LIST_VTABLE 0x677290
#define MODEL_VTABLE 0x66b628
#define MUSIC_UI_VTABLE 0x670f54
#define SONG_TITLE 0x641e
#define SONG_ICON 0x641f
#define SONG_STATUS_ICON 0x63eb
#define MAX_RUNS 256
#define FOOTER_MAGIC 0x44495343
#define TRACK_DISC_NUMBER 0x84 /* Native mhit +0x5c import at 0xd7a58. */
#define TRACK_DISC_TOTAL 0x86  /* Native mhit +0x60 import at 0xd7a60. */

struct disc_run {
    u16 first_song;
    u16 number;
};

struct disc_groups {
    u32 magic;
    u32 count;
    struct disc_run runs[MAX_RUNS];
};

extern void discs_header_draw(void *, const i32 *, const i32 *, u32);

void *discs_index_alloc(u32 size)
{
    return NATIVE(0xa8178, void *(*)(u32))(size + sizeof(struct disc_groups));
}

static int album_filter(void *query)
{
    return query && HALF(query, 0x628) && HALF(query, 0x628) <= 255;
}

static u32 disc_number(void *track)
{
    return track ? HALF(track, TRACK_DISC_NUMBER) : 0;
}

/* The array lives in one native allocation. Its unpublished tail can be
 * filled under the existing database lock and is freed with the index. */
void discs_index_finish(void *query, void **index, u32 songs)
{
    struct disc_groups *groups = (struct disc_groups *)(index + songs);
    groups->magic = FOOTER_MAGIC;
    groups->count = 0;
    if (!album_filter(query))
        return;

    u32 previous = 0, runs = 0, multiple = 0;
    for (u32 song = 0; song < songs; ++song) {
        void *track = POINTER(index[song], 0x20);
        u32 disc = disc_number(track);
        if (!disc)
            return;
        if (disc > 1 || HALF(track, TRACK_DISC_TOTAL) > 1)
            multiple = 1;
        if (disc != previous) {
            if (runs == MAX_RUNS)
                return;
            groups->runs[runs].first_song = song;
            groups->runs[runs++].number = disc;
            previous = disc;
        }
    }
    if (multiple || runs > 1)
        groups->count = runs;
}

static const struct disc_groups *ui_groups(void *ui)
{
    if (!ui || WORD(ui, 0) != MUSIC_UI_VTABLE)
        return 0;
    void *query = POINTER(ui, 0x1c);
    if (!album_filter(query))
        return 0;
    void **index = POINTER(query, 0xc88);
    u32 songs = WORD(query, 0xc90);
    if (!index || !songs || songs > 65535 || WORD(ui, 0xc0) != songs)
        return 0;

    const struct disc_groups *groups = (const struct disc_groups *)(index + songs);
    if (groups->magic != FOOTER_MAGIC || !groups->count || groups->count > MAX_RUNS)
        return 0;
    return groups;
}

static const struct disc_groups *list_groups(void *list, void **ui)
{
    if (!list || WORD(list, 0) != LIST_VTABLE || HALF(list, 0x1c) != SONGS ||
        WORD(list, 0xb4))
        return 0;
    void *model = POINTER(list, 0xa4);
    if (!model || WORD(model, 0) != MODEL_VTABLE || POINTER(model, 0x1c) != list)
        return 0;
    *ui = POINTER(model, 0x18);
    const struct disc_groups *groups = ui_groups(*ui);
    return groups && WORD(list, 0xb8) == WORD(*ui, 0xc0) + groups->count ? groups : 0;
}

/* Find the last heading at or before the displayed row. */
static u32 visible_run(const struct disc_groups *groups, u32 row)
{
    u32 low = 0, high = groups->count;
    while (low < high) {
        u32 mid = low + (high - low) / 2;
        if (groups->runs[mid].first_song + mid <= row)
            low = mid + 1;
        else
            high = mid;
    }
    return low - 1;
}

static u32 logical_row(const struct disc_groups *groups, u32 row, u32 *header)
{
    u32 group = visible_run(groups, row);
    const struct disc_run *run = &groups->runs[group];
    *header = run->first_song + group == row ? run->number : 0;
    return *header ? run->first_song : row - group - 1;
}

/* The fast-scroll caller receives a number and row, never a footer pointer.
 * With direction zero it can check eligibility and read the selected disc. */
i32 discs_fast_target(void *list, i32 row, i32 direction, u32 *number)
{
    void *ui;
    const struct disc_groups *groups = list_groups(list, &ui);
    if (!groups || groups->count < 2 || (u32)row >= WORD(list, 0xb8))
        return -1;

    u32 group = visible_run(groups, row);
    u32 first = groups->runs[group].first_song + group + 1;
    if ((u32)row < first)
        return -1;
    if (direction > 0) {
        if (group + 1 < groups->count)
            ++group;
        row = groups->runs[group].first_song + group + 1;
    } else if (direction < 0) {
        if ((u32)row == first && group)
            --group;
        row = groups->runs[group].first_song + group + 1;
    }
    *number = groups->runs[group].number;
    return row;
}

static u32 visual_row(const struct disc_groups *groups, u32 row)
{
    u32 low = 0, high = groups->count;
    while (low < high) {
        u32 mid = low + (high - low) / 2;
        if (groups->runs[mid].first_song <= row)
            low = mid + 1;
        else
            high = mid;
    }
    return row + low;
}

u32 discs_visible_count(void *ui)
{
    u32 songs = NATIVE(0x254bcc, u32 (*)(void *))(ui);
    const struct disc_groups *groups = ui_groups(ui);
    return songs + (groups ? groups->count : 0);
}

u32 discs_model_property(void *model, u32 tag, u32 key, u32 *result)
{
    if (key == SONG_TITLE || key == SONG_ICON || key == SONG_STATUS_ICON) {
        void *ui;
        void *list = POINTER(model, 0x1c);
        const struct disc_groups *groups = list_groups(list, &ui);
        u32 row = WORD(model, 0x14);
        if (groups && POINTER(list, 0xa4) == model && row < WORD(list, 0xb8)) {
            u32 header;
            u32 song = logical_row(groups, row, &header);
            if (header)
                *result = key == SONG_TITLE ? (u32)"" : 0;
            else
                *result = NATIVE(0x169f40, u32 (*)(void *, u32, u32, u32))
                    (ui, tag, key, song);
            return 1;
        }
    }
    return NATIVE(0x17f49c, u32 (*)(void *, u32, u32, u32 *))(model, tag, key, result);
}

u32 discs_select(void *list, i32 row, u32 message, u32 redraw)
{
    void *ui;
    const struct disc_groups *groups = list_groups(list, &ui);
    if (groups && (u32)row < WORD(list, 0xb8)) {
        u32 header;
        logical_row(groups, row, &header);
        if (header) {
            i32 selected = NATIVE(0x257260, i32 (*)(void *))(list);
            row += row > 0 && selected > row ? -1 : 1;
        }
    }
    return NATIVE(0x23a064, u32 (*)(void *, i32, u32, u32))(list, row, message, redraw);
}

/* Native movement compares its proposed row before selection is sent to
 * the widget. Leave that comparison in place, with a visible song position
 * for the proposal and a separate first selectable row for clamp/wrap. */
i32 discs_step(void *list, i32 row, i32 delta, i32 *minimum)
{
    void *ui;
    const struct disc_groups *groups = list_groups(list, &ui);
    if (minimum)
        *minimum = groups ? 1 : (i32)WORD(list, 0xb4);
    if (!groups || (u32)row >= WORD(list, 0xb8))
        return (i32)((u32)row + (u32)delta);

    u32 header;
    i32 song = logical_row(groups, row, &header) + (u32)delta;
    if (song < 0)
        return 0;
    if ((u32)song >= WORD(ui, 0xc0))
        return WORD(list, 0xb8);
    return visual_row(groups, song);
}

i32 discs_first_selectable(void *list)
{
    void *ui;
    return list_groups(list, &ui) ? 1 : (i32)WORD(list, 0xb4);
}

/* Reveal the leading heading with its first song. The list still draws from
 * its true minimum and native scrolling clamps the combined rectangle. */
void discs_reveal_heading(void *list, i32 *rect, i32 height, i32 row)
{
    void *ui;
    const struct disc_groups *groups = list_groups(list, &ui);
    if (groups && row > 0 && (u32)row < WORD(list, 0xb8)) {
        u32 header;
        logical_row(groups, row - 1, &header);
        if (header) {
            rect[0] -= height;
            /* During disc Quick Scroll, request a viewport-sized reveal
             * beginning at the heading. Native scrolling still clamps at
             * the album bottom. Its continuation adds any partial-row
             * remainder, so supply only the whole-row height here. */
            void *parent = POINTER(list, 0x10);
            if ((WORD(list, 0xfc) & 0xff000000u) == 0x40000000u &&
                *((u8 *)list + 0x12c) && parent && height > 0) {
                i32 viewport = (i32)WORD(parent, 0x60) - (i32)WORD(parent, 0x58);
                if (viewport >= 2 * height) {
                    i32 rows = NATIVE(0x7d024, i32 (*)(i32, i32))(viewport, height);
                    rect[2] = rect[0] + rows * height;
                }
            }
        }
    }
}

i32 discs_selected_song(void *list)
{
    i32 row = NATIVE(0x257260, i32 (*)(void *))(list);
    void *ui;
    const struct disc_groups *groups = list_groups(list, &ui);
    if (groups && (u32)row < WORD(list, 0xb8)) {
        u32 header;
        u32 song = logical_row(groups, row, &header);
        return header ? -1 : (i32)song;
    }
    return row;
}

u32 discs_flash_song(void *renderer, i32 row)
{
    void *list = NATIVE(0x222dd0, void *(*)(void *, u32))(POINTER(renderer, 0x28), 0x1700);
    if (!list)
        return 0;
    void *ui;
    const struct disc_groups *groups = list_groups(list, &ui);
    if (groups && (u32)row < WORD(ui, 0xc0))
        row = visual_row(groups, row);
    return NATIVE(WORD(POINTER(list, 0), 0x160), u32 (*)(void *, i32))(list, row);
}

void discs_draw_row(void *list, void *context, const i32 *clip, u32 row,
                    const i32 *rect, u32 selected, u32 alternate)
{
    void *ui;
    const struct disc_groups *groups = list_groups(list, &ui);
    if (groups && row < WORD(list, 0xb8)) {
        u32 header;
        logical_row(groups, row, &header);
        if (header) {
            discs_header_draw(list, clip, rect, header);
            return;
        }
    }
    NATIVE(0x2397a8, void (*)(void *, void *, const i32 *, u32, const i32 *, u32, u32))
        (list, context, clip, row, rect, selected, alternate);
}
