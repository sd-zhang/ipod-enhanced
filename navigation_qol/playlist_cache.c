/* Monochrome 4G 4.3.1.1: preserve Apple's ascending cleanup order and
 * ownership releases, skipping only empty ordinary sort-cache slots.
 * Volatile reads are per-index: a native release may populate later slots.
 */
typedef unsigned int u32;
#define INVALIDATE ((u32 (*)(volatile u32 *, u32))0x53d7c)

static inline void ordinary(volatile u32 *queue, u32 first, u32 end)
{
    for (u32 index = first; index < end; ++index) {
        volatile u32 *slot = queue + index;
        if (slot[0x12c/4] | slot[0x1c8/4] |
            slot[0x264/4] | slot[0x300/4])
            INVALIDATE(queue, index);
    }
}

u32 ipod4g_playlist_invalidate(volatile u32 *queue)
{
    ordinary(queue, 1, 6);
    INVALIDATE(queue, 6);
    INVALIDATE(queue, 7);
    ordinary(queue, 8, 9);
    INVALIDATE(queue, 9);
    ordinary(queue, 10, 24);
    INVALIDATE(queue, 24);
    ordinary(queue, 25, 38);
    /* Last native call supplies the caller-visible return value. */
    return INVALIDATE(queue, 38);
}
