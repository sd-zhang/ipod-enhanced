#ifndef IPOD_4G_CLIP_PCM_H
#define IPOD_4G_CLIP_PCM_H

#include <stdint.h>
#include <stddef.h>

/* Matches the four little-endian words in the patched track and request. */
struct track_timing {
    uint32_t delay;
    uint32_t samples_low;
    uint32_t samples_high;
    uint32_t padding;
};

struct clip_cursor {
    uint32_t skip_remaining;
    uint32_t keep_low;
    uint32_t keep_high;
    uint32_t enabled;
};

/* Layout is also used by the ARM file-selection and automatic-advance hooks. */
struct engine_timing {
    struct track_timing timing;
    struct clip_cursor cursor;
    uint32_t cookie;
};
extern struct engine_timing gapless_engine_timing;
_Static_assert(sizeof(struct track_timing) == 16, "track timing ABI");
_Static_assert(offsetof(struct engine_timing, cookie) == 32, "ARM selection cookie offset");
uint32_t gapless_mp3_extra_frames(uint32_t cookie);
void gapless_start_mp3_clip(struct engine_timing *selected);
uint32_t gapless_output_tx(const void *queue);

/* decoder_bias is in sample frames. Start only at a known stream origin. */
void gapless_start_clip(struct clip_cursor *, const struct track_timing *,
                        uint32_t decoder_bias);

/* Report the size of a recognized Xing/Info header in PCM frames, including
 * when its LAME timing is missing or invalid. Only verified timing is written. */
int gapless_lame_timing(struct track_timing *, const unsigned char *frame,
                        unsigned available, uint32_t *leading_frames);

/* Single-owner producer. Retry the same descriptor if publication fails. */
#endif
