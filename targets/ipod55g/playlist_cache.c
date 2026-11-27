/* Native playlist mutations invalidate 48 sort indexes. Empty ordinary
 * indexes have no cleanup side effects; retain Apple's destructor path for
 * populated indexes and every index with an additional cached representation.
 * Read afresh on each iteration because native cleanup can change later slots.
 */
typedef unsigned int u32;
#define INVALIDATE ((u32 (*)(volatile u32 *, u32))0x110208)

static inline void ordinary(volatile u32 *queue, u32 first, u32 end)
{
    for (u32 index = first; index < end; ++index) {
        volatile u32 *slot = queue + index;
        if (slot[0x174/4] | slot[0x238/4] | slot[0x2fc/4] |
            slot[0x3c0/4] | slot[0x494/4])
            INVALIDATE(queue, index);
    }
}

u32 ipod55g_playlist_invalidate(volatile u32 *queue)
{
    /* Split ordinary runs so the empty fast path does not test four special
     * index numbers on every iteration. Keep the original ascending order. */
    ordinary(queue, 1, 6);
    INVALIDATE(queue, 6);
    INVALIDATE(queue, 7);
    ordinary(queue, 8, 9);
    INVALIDATE(queue, 9);
    ordinary(queue, 10, 24);
    INVALIDATE(queue, 24);
    ordinary(queue, 25, 48);
    /* Preserve the native routine's final return as well as its cleanup. */
    return INVALIDATE(queue, 48);
}
