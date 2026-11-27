/* Final WM8975 headphone ceiling. No decoder, queue or sample processing changes.
 *
 * Native 14f824 applies pan to a master value before the device bridge; 14fae0
 * subsequently translates those channel values to codec register units. Limit
 * at that final device boundary so queued volume changes and codec restores
 * cannot reuse a value above the current ceiling. Mode3 is separate line-out.
 */
typedef unsigned int u32;

extern int volume_codec_original(u32 driver, u32 mode, u32 left, u32 right);
extern int volume_codec_refresh(u32 driver, u32 mode, u32 left, u32 right);

static u32 read32(u32 address)
{
    return *(volatile u32 *)address;
}

/* f3368 registers the new owner before f3380 initializes its prefs pointer.
 * Clear that field before publication, including on uncleared allocator memory.
 * The subsequent native constructor still owns and publishes the real prefs.
 */
int volume_owner_register(u32 owner, u32 key)
{
    *(volatile u32 *)(owner + 0x124) = 0;
    return ((int (*)(u32, u32))0xbac78)(owner, key);
}

int volume_codec(u32 driver, u32 mode, u32 left, u32 right)
{
    /* The native registry exists before the preferences owner. Its checked
     * accessor returns NULL until registration and after the base-destructor
     * vtable takes over. Owner+124 is published only after prefs construction.
     * Early startup therefore keeps the native device initialization behavior.
     */
    if (mode == 2 && read32(0x1041a218) == 0x21c30) {
        u32 owner = ((u32 (*)(void))0x70290)();
        u32 prefs = owner ? read32(owner + 0x124) : 0;
        if (prefs) {
            u32 ceiling = read32(prefs + 0xb48) >> 1;
            u32 peak = left > right ? left : right;
            if (ceiling > 127)
                ceiling = 127;
            if (peak > ceiling) {
                /* This scales the native pre-codec pan values, not amplitude
                 * or dB. It matches pan at the limited master within one native
                 * integer rounding step. All callers supply byte-range values.
                 * Native1494c takes divisor first, numerator second.
                 */
                /* peak is nonzero here. Its own scaled quotient is exactly
                 * ceiling; only a quieter channel needs integer division. */
                left = left == peak ? ceiling :
                    ((u32 (*)(u32, u32))0x1494c)(peak, left * ceiling);
                right = right == peak ? ceiling :
                    ((u32 (*)(u32, u32))0x1494c)(peak, right * ceiling);
            }
            /* Native14fc68 compares only the left channel, then caches it
             * before either I/O write succeeds. Always refresh valid-prefs
             * headphone requests: a right-only change or an I/O retry can be
             * necessary even when neither incoming channel needs limiting.
             * This keeps native caches current without changing the shared
             * force flag. Identical successful requests now write and return0
             * instead of the stock cache-hit no-op returning1.
             */
            return volume_codec_refresh(driver, mode, left, right);
        }
    }
    return volume_codec_original(driver, mode, left, right);
}
