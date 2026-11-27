/* Monochrome 4G 4.3.1.1. Native Espy Sans 9 bold, text and clipped fills.
 * The caller supplies an absolute row rectangle. No widget/model state or
 * retained allocation belongs to this paint function.
 */
typedef unsigned int u32;
#define FN(a, t) ((t)(a))
#define WORD(p, o) (*(u32 *)((unsigned char *)(p) + (o)))

static void fill(void *context, int top, int left, int bottom, int right)
{
    if (top >= bottom || left >= right) return;
    int box[4] = {top, left, bottom, right};
    FN(0xa4a38, void (*)(void *, const int *))(context, box);
}

void discs_header_draw(void *list, const int *clip, const int *rect, unsigned disc)
{
    if (!list || !clip || !rect || !disc || disc > 65535 ||
        rect[2] - rect[0] < 13 || rect[3] - rect[1] < 9 ||
        !FN(0xe3ddc, u32 (*)(void *))(list)) return;
    const int *visible = (const int *)((unsigned char *)list + 0x78);
    int bounded[4];
    for (u32 i = 0; i < 4; ++i) {
        int v = rect[i];
        if (i < 2) {
            if (clip[i] > v) v = clip[i];
            if (visible[i] > v) v = visible[i];
        } else {
            if (clip[i] < v) v = clip[i];
            if (visible[i] < v) v = visible[i];
        }
        bounded[i] = v;
    }
    if (bounded[0] >= bounded[2] || bounded[1] >= bounded[3]) return;

    char text[11] = {'D', 'i', 's', 'c', ' '};
    const u32 powers[5] = {10000, 1000, 100, 10, 1};
    u32 length = 5;
    for (u32 i = 0; i < 5; ++i) {
        unsigned digit = 0;
        while (disc >= powers[i]) { disc -= powers[i]; ++digit; }
        if (digit || length > 5 || i == 4) text[length++] = (char)('0' + digit);
    }
    text[length] = 0;

    u32 context[17];
    const u32 style[4] = {0x252f, 9, 0, 1}; /* native Type257a */
    unsigned char alignment;
    FN(0xf745c, void *(*)(void *))(context);
    FN(0x66efc, void (*)(void *, void *))(list, context);
    /* Keep the caller's shared drawing clip untouched. The new context owns
     * the intersection of this row, the dirty region and ancestor viewport. */
    FN(0xca9d8, void (*)(void *, void *, const int *))(
        context, *(void **)0x103cdcc8, bounded);
    FN(0xcac44, void (*)(void *, const void *, void *, void *))(
        list, style, context, &alignment);

    int top = rect[0] - (int)WORD(list, 0x58);
    int left = rect[1] - (int)WORD(list, 0x5c);
    int height = rect[2] - rect[0], width = rect[3] - rect[1];
    u32 white = 0x00ffffff, black = 0;
    FN(0xcac3c, void (*)(void *, const void *))(context, &white);
    fill(context, top, left, top + height, left + width);
    FN(0xcac3c, void (*)(void *, const void *))(context, &black);

    int text_width = FN(0x9b440, int (*)(void *, const char *))(context, text);
    int x = left + (width - text_width) / 2;
    if (x < left + 4) x = left + 4;
    int baseline = top + (height - 13) / 2 + 10;
    FN(0x9cc0c, void (*)(void *, int, int))(context, x, baseline);
    FN(0x5aed8, void (*)(void *, const char *, int, u32))(
        context, text, left + width - 4 - x, alignment);
    int line = top + height / 2 - 1;
    fill(context, line, left + 4, line + 1, x - 4);
    fill(context, line, x + text_width + 4, line + 1, left + width - 4);
    FN(0x102318, void (*)(void *, u32))(context, 0);
}
