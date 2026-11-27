/* iPod Video 5.5G 1.3. All rectangles are half-open top/left/bottom/right. */
typedef unsigned int u32;
#define NATIVE(address, type) ((type)(address))
#define FIELD(object, offset, type) (*(type *)((unsigned char *)(object) + (offset)))

typedef struct {
    int x, y;
    unsigned char drawing_state[0x14];
    void *surface;
    u32 font[2];
    u32 font_flags;
    int origin_x, origin_y;
    int clip[4];
} Graphics;

static int constrain(int *out, const int *limit)
{
    if (out[0] < limit[0]) out[0] = limit[0];
    if (out[1] < limit[1]) out[1] = limit[1];
    if (out[2] > limit[2]) out[2] = limit[2];
    if (out[3] > limit[3]) out[3] = limit[3];
    return out[0] < out[2] && out[1] < out[3];
}

static void fill(Graphics *graphics, int top, int left, int bottom, int right)
{
    if (top >= bottom || left >= right) return;
    int rect[4] = {top, left, bottom, right};
    NATIVE(0x211a00, void (*)(Graphics *, const int *))(graphics, rect);
}

static void format_number(char *text, unsigned disc)
{
    const unsigned places[] = {10000, 1000, 100, 10, 1};
    char *start = text;
    for (unsigned i = 0; i < 5; ++i) {
        unsigned digit = 0;
        while (disc >= places[i]) {
            disc -= places[i];
            ++digit;
        }
        if (digit || text != start || i == 4) *text++ = (char)('0' + digit);
    }
    *text = 0;
}

void discs_header_draw(void *list, const int *clip, const int *rect, unsigned disc)
{
    if (!list || !clip || !rect || !disc || disc > 65535) return;
    int bounded[4] = {rect[0], rect[1], rect[2], rect[3]};
    const int *visible = (const int *)((unsigned char *)list + 0x88);
    if (!constrain(bounded, clip) || !constrain(bounded, visible)) return;

    /* Stock row containers leave their own active clip on the display owner.
     * Publish this row's clip before binding, as native row drawing does. */
    NATIVE(0x21ac60, void (*)(void *, const int *))(list, bounded);
    Graphics graphics;
    NATIVE(0x210638, void (*)(Graphics *))(&graphics);
    NATIVE(0x21aad0, void (*)(void *, Graphics *))(list, &graphics);
    if (!graphics.surface) return;

    int list_top = FIELD(list, 0x58, int);
    int list_left = FIELD(list, 0x5c, int);
    int dy = graphics.origin_y - list_top;
    int dx = graphics.origin_x - list_left;
    bounded[0] += dy;
    bounded[1] += dx;
    bounded[2] += dy;
    bounded[3] += dx;
    if (!constrain(graphics.clip, bounded)) return;

    int top = rect[0] - list_top;
    int left = rect[1] - list_left;
    int bottom = rect[2] - list_top;
    int right = rect[3] - list_left;
    u32 white = 0xffffffff;
    NATIVE(0x2106e4, void (*)(Graphics *, const u32 *))(&graphics, &white);
    fill(&graphics, top, left, bottom, right);

    enum { INSET = 4, GAP = 4 };
    if (right - left <= 2 * INSET) return;
    /* Contacts detail Type 6d43 is centered, bold 14px Podium Sans in #666.
     * The paired one-pixel rules use the stock gray Calendar cell palette.
     */
    const void *style = NATIVE(0x21f3dc, const void *(*)(u32, int))(0x54797065, 0x6d43);
    if (!style) return;
    unsigned char alignment;
    NATIVE(0x21a168, void (*)(void *, const void *, Graphics *, unsigned char *))(
        list, style, &graphics, &alignment);
    if (!(graphics.font[0] & ~1u)) return;

    char text[11] = {'D', 'i', 's', 'c', ' '};
    format_number(text + 5, disc);
    int width = NATIVE(0x2108d0, int (*)(Graphics *, const char *))(&graphics, text);
    int x = left + (right - left - width) / 2;
    if (x < left + INSET) x = left + INSET;
    int ascent = NATIVE(0x2551ec, int (*)(Graphics *))(&graphics);
    int descent = NATIVE(0x255220, int (*)(Graphics *))(&graphics);
    int baseline = top + (bottom - top + ascent - descent) / 2;
    int inset[4] = {bounded[0], left + INSET + graphics.origin_x,
                    bounded[2], right - INSET + graphics.origin_x};
    if (!constrain(graphics.clip, inset)) return;
    NATIVE(0x210290, void (*)(Graphics *, unsigned))(&graphics, 0x21);
    NATIVE(0x211754, void (*)(Graphics *, int, int))(&graphics, x, baseline);
    NATIVE(0x2101dc, void (*)(Graphics *, const char *))(&graphics, text);

    int line = top + (bottom - top) / 2 - 1;
    u32 divider = 0xffd6d6d6; /* RGBA bytes in little-endian storage. */
    NATIVE(0x210290, void (*)(Graphics *, unsigned))(&graphics, 0);
    NATIVE(0x2106e4, void (*)(Graphics *, const u32 *))(&graphics, &divider);
    fill(&graphics, line, left + INSET, line + 1, x - GAP);
    fill(&graphics, line, x + width + GAP, line + 1, right - INSET);
}
