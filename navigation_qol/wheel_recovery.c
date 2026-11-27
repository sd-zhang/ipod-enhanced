/* A rejected notification has no consumer to clear the native pending flag.
 * Use the same drain as the stock event-allocation failure path, so the next
 * movement can notify again without replaying an overloaded input backlog.
 * Accepted/coalesced input and all non-wheel events keep native behavior.
 */
typedef unsigned int u32;
#define FN(a, t) ((t)(a))

u32 wheel_post(void *event, u32 synchronous) {
    u32 accepted = FN(0xabbcc, u32 (*)(void *, u32))(event, synchronous);
    if (!accepted) FN(0x6263c, u32 (*)(void))();
    return accepted;
}

void wheel_reject(void *message) {
    if (((u32 *)message)[1] == 0x5765656c)
        FN(0x6263c, u32 (*)(void))();
    FN(0xb1774, void (*)(void *))(message);
}
