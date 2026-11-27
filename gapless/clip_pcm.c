#include "clip_pcm.h"

/* Arm a cursor from a track's iTunesDB timing: decoded frames to skip (the
 * encoder delay plus the decoder's own bias) and the audible sample count. */
void gapless_start_clip(struct clip_cursor *cursor,
                        const struct track_timing *timing, uint32_t decoder_bias)
{
    *cursor = (struct clip_cursor){0};
    if (timing->delay == 0 || decoder_bias > UINT32_MAX - timing->delay)
        return;

    uint32_t delay = timing->delay + decoder_bias;
    uint64_t samples = ((uint64_t)timing->samples_high << 32) | timing->samples_low;
    /* samples is the audible length, not the encoded length. A valid
     * short track may contain less music than its priming and padding. */
    if (!samples)
        return;

    cursor->skip_remaining = delay;
    cursor->keep_low = timing->samples_low;
    cursor->keep_high = timing->samples_high;
    cursor->enabled = 1;
}
