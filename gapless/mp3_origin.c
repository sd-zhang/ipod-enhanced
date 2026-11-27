/* RetailOS 4G submits the probed Xing/Info frame to its Layer III decoder. */
#include "clip_pcm.h"
#include "diag.h"

static uint32_t probe_cookie;
static uint32_t leading_frames;
static uint32_t probe_header;
static struct track_timing corrected_timing;

void gapless_mp3_probe(const unsigned char *frame, unsigned available)
{
    uint32_t cookie = gapless_engine_timing.cookie;
    DIAG_INC(DG_MP3_PROBE);
    leading_frames = 0;
    probe_header = 0;
    corrected_timing.delay = 0;
    probe_cookie = cookie;
    if (!cookie || available > 2048)
        return;
    /* A short read or a stale selection cannot establish the stream
     * origin. Do not rely on the track's length to reject those states. */
    if (!frame || available < 4 || frame[0] != 0xff ||
        (frame[1] & 0xe6) != 0xe2 || ((frame[1] >> 3) & 3) == 1 ||
        (frame[2] >> 4) == 15 || (frame[2] & 12) == 12)
        return;
    probe_header = 1;

    struct track_timing *library = &gapless_engine_timing.timing;
    struct track_timing checked = *library;
    if (gapless_lame_timing(&checked, frame, available, &leading_frames)) {
        if (library->delay && library->samples_low != checked.samples_low) {
            /* Keep the input on a probe retry. Commit the verified count
             * once this request reaches the decoder's start hook. */
            corrected_timing = checked;
        } else {
            *library = checked;
        }
    }
}

uint32_t gapless_mp3_extra_frames(uint32_t selected_cookie)
{
    return selected_cookie && probe_cookie == selected_cookie ? leading_frames : 0;
}

void gapless_start_mp3_clip(struct engine_timing *selected)
{
    if (!selected->cookie || probe_cookie != selected->cookie || !probe_header) {
        selected->cursor = (struct clip_cursor){0};
        return;
    }
    uint32_t extra = gapless_mp3_extra_frames(selected->cookie);
    if (extra && corrected_timing.delay) {
        /* The tag and database agree on both cuts. Even a track shorter
         * than its own padding has this many playable samples. */
        selected->timing = corrected_timing;
        selected->cursor = (struct clip_cursor){
            corrected_timing.delay + 529 + extra,
            corrected_timing.samples_low, corrected_timing.samples_high, 1
        };
        return;
    }
    gapless_start_clip(&selected->cursor, &selected->timing, 529);
    if (selected->cursor.enabled) {
        if (extra <= UINT32_MAX - selected->cursor.skip_remaining)
            selected->cursor.skip_remaining += extra;
        else
            selected->cursor.enabled = 0;
    }
}
