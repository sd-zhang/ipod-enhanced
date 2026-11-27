/* Host-written sort initials for the 4G's existing named indexes. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
#define FN(a, t) ((t)(a))
#include "sort_prefix.h"

u32 letters_group(const u16 *name, u32 length);

static u32 word(const u8 *p, u32 big) {
    if (big) return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3];
    return p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

/* Ignore leading punctuation/symbols and their nonspacing/format characters.
 * Return 1 for whitespace/formatting, 2 for visible punctuation/symbols,
 * zero for a meaningful prefix.
 * ASCII names take the short path; Unicode classification uses fixed ranges. */
static u32 prefix_kind(u32 c) {
    if (c < 128) {
        if (c == 32 || (c >= 9 && c <= 13)) return 1;
        if ((c >= 33 && c <= 47) || (c >= 58 && c <= 64) ||
            (c >= 91 && c <= 96) || (c >= 123 && c <= 126)) return 2;
        return 0;
    }
    u32 low = 0, high = sizeof(prefix_ranges) / sizeof(prefix_ranges[0]);
    while (low < high) {
        u32 middle = low + ((high - low) >> 1);
        u32 end_kind = prefix_ranges[middle][1];
        if (c < prefix_ranges[middle][0]) high = middle;
        else if (c > (end_kind & 0xffffff)) low = middle + 1;
        else return end_kind >> 24;
    }
    return 0;
}

/* Find the first significant sort character in either stored encoding.
 * Validate each consumed sequence; no allocation or unaligned reads. Empty
 * and whitespace-only sort names fall back; symbol-only names group as #. */
static u32 first_group(const u8 *p, u32 bytes, u32 encoding, u32 big) {
    if ((encoding != 1 && encoding != 2) || (encoding == 1 && (bytes & 1))) return 0;
    u32 empty_group = 0;
    while (bytes) {
        u32 c;
        if (encoding == 1) {
            if (bytes < 2) return 0;
            c = big ? (u32)p[0] << 8 | p[1] : p[0] | (u32)p[1] << 8;
            p += 2; bytes -= 2;
            if (c >= 0xd800 && c <= 0xdfff) {
                if (c >= 0xdc00 || bytes < 2) return 0;
                u32 low = big ? (u32)p[0] << 8 | p[1] : p[0] | (u32)p[1] << 8;
                if (low < 0xdc00 || low > 0xdfff) return 0;
                c = 0x10000 + ((c - 0xd800) << 10) + low - 0xdc00;
                p += 2; bytes -= 2;
            }
        } else {
            c = *p++; --bytes;
            if (c >= 0x80) {
                u32 count, minimum;
                if (c >= 0xc2 && c <= 0xdf) { count = 1; minimum = 0x80; c &= 0x1f; }
                else if (c >= 0xe0 && c <= 0xef) { count = 2; minimum = 0x800; c &= 0xf; }
                else if (c >= 0xf0 && c <= 0xf4) { count = 3; minimum = 0x10000; c &= 7; }
                else return 0;
                if (bytes < count) return 0;
                bytes -= count;
                while (count--) {
                    u32 next = *p++;
                    if ((next & 0xc0) != 0x80) return 0;
                    c = (c << 6) | (next & 0x3f);
                }
                if (c < minimum || c > 0x10ffff || (c >= 0xd800 && c <= 0xdfff)) return 0;
            }
        }
        if (!c) return 0;
        u32 kind = prefix_kind(c);
        if (kind) {
            if (kind == 2) empty_group = '#';
            continue;
        }
        u16 first = c > 0xffff ? 0xd800 : (u16)c;
        return letters_group(&first, 1);
    }
    return empty_group;
}

u32 letters_import_sort(void *reader, u8 *track, u32 type, u32 bytes) {
    u8 *scratch = reader;
    u32 slot;
    switch (type) {
    case 23: slot = 0xfc; break; /* sort artist */
    case 28: slot = 0xfd; break; /* sort album */
    case 30: slot = 0xfe; break; /* sort composer */
    default:
        return FN(0x316ac, u32 (*)(void *, u32))(*(void **)(scratch + 0x40000), bytes);
    }
    u32 status, group = 0;
    if (bytes < 16 || bytes > 0x40000) {
        status = FN(0x316ac, u32 (*)(void *, u32))(*(void **)(scratch + 0x40000), bytes);
    } else {
        /* The native importer supplies this 256 KiB scratch area, also used
         * by its ordinary text reader. State begins at +0x40000. */
        status = FN(0x4d208, u32 (*)(void *, void *, u32))(reader, scratch, bytes);
        if (!status) {
            u32 big = scratch[0x40083], length = word(scratch + 4, big);
            if (length <= bytes - 16)
                group = first_group(scratch + 16, length, word(scratch, big), big);
        }
    }
    if (!status) track[slot] = (u8)group;
    return status;
}

u32 letters_track_group(const u16 *name, u32 length, const u8 *track, u32 selector) {
    u32 group = 0;
    /* Native selectors: album=6, artist=7, genre=9, composer=24. */
    if (selector == 6) group = track[0xfd];
    else if (selector == 7) group = track[0xfc];
    else if (selector == 24) group = track[0xfe];
    if (!group) group = first_group((const u8 *)name, length * 2, 1, 0);
    return group ? group : '#';
}
