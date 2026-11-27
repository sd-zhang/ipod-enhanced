/* Read timing from a LAME tag in the first synchronized MPEG frame.
 * Only the selected decoder's private timing snapshot is updated.
 */
#include "clip_pcm.h"
#include <stddef.h>

static uint32_t be32(const unsigned char *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

static int xing_marker(const unsigned char *p)
{
    uint32_t tag = be32(p);
    return tag == 0x58696e67 || tag == 0x496e666f; /* Xing / Info */
}

static unsigned xing_offset(const unsigned char *frame, unsigned available)
{
    if (!frame || available < 4 || frame[0] != 0xff || (frame[1] & 0xe6) != 0xe2)
        return 0;
    unsigned version = (frame[1] >> 3) & 3;
    unsigned mono = (frame[3] >> 6) == 3;
    unsigned offset = version == 3 ? (mono ? 21 : 36) : (mono ? 13 : 21);
    if (available < offset + 8)
        return 0;
    if (!xing_marker(frame + offset)) {
        if ((frame[1] & 1) || available < offset + 10 || !xing_marker(frame + offset + 2))
            return 0;
        offset += 2;
    }
    unsigned bitrate = frame[2] >> 4;
    if (version == 1 || !bitrate || bitrate == 15 || (frame[2] & 12) == 12)
        return 0;
    return offset;
}

static uint16_t tag_crc(const unsigned char *p, unsigned length)
{
    /* Four reflected steps of polynomial 0xa001 for each possible nibble. */
    static const uint16_t nibble[] = {
        0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401,
        0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400
    };
    unsigned crc = 0;
    for (unsigned i = 0; i < length; i++) {
        crc ^= p[i];
        crc = (crc >> 4) ^ nibble[crc & 15];
        crc = (crc >> 4) ^ nibble[crc & 15];
    }
    return crc;
}

static int fits_mpeg_frame(const unsigned char *frame, unsigned bytes)
{
    static const uint16_t rates[] = {44100, 48000, 32000};
    static const uint16_t mpeg1_kbps[] =
        {0,32,40,48,56,64,80,96,112,128,160,192,224,256,320};
    static const uint16_t mpeg2_kbps[] =
        {0,8,16,24,32,40,48,56,64,80,96,112,128,144,160};
    unsigned version = (frame[1] >> 3) & 3;
    unsigned index = frame[2] >> 4;
    unsigned rate = rates[(frame[2] >> 2) & 3];
    unsigned coefficient = 144000;
    unsigned kbps = mpeg1_kbps[index];
    if (version != 3) {
        rate >>= version == 2 ? 1 : 2;
        coefficient = 72000;
        kbps = mpeg2_kbps[index];
    }
    unsigned padding = (frame[2] >> 1) & 1;
    return (bytes - padding) * rate <= coefficient * kbps;
}

int gapless_lame_timing(struct track_timing *timing, const unsigned char *frame,
                        unsigned available, uint32_t *leading_frames)
{
    if (!timing || !leading_frames)
        return 0;
    unsigned xing = xing_offset(frame, available);
    if (!xing) {
        *leading_frames = 0;
        return 0;
    }
    unsigned samples_per_frame = (frame[1] & 0x18) == 0x18 ? 1152 : 576;
    *leading_frames = samples_per_frame;
    if (available < xing + 12)
        return 0;
    uint32_t flags = be32(frame + xing + 4);
    if (!(flags & 1) || (flags & ~15u))
        return 0;
    /* Frame count follows flags. Bytes, TOC and quality are individually optional. */
    unsigned tag = xing + 12 + ((flags & 2) ? 4 : 0) +
                   ((flags & 4) ? 100 : 0) + ((flags & 8) ? 4 : 0);
    if (available < tag + 36 || !fits_mpeg_frame(frame, tag + 36))
        return 0;
    const unsigned char *lame = frame + tag;
    if (be32(lame) != 0x4c414d45 || (lame[9] >> 4) != 0)
        return 0;
    uint32_t delay = ((uint32_t)lame[21] << 4) | (lame[22] >> 4);
    uint32_t padding = ((uint32_t)(lame[22] & 15) << 8) | lame[23];
    if (!delay || delay > 3000 || padding > 3000)
        return 0;
    uint64_t count = (uint64_t)be32(frame + xing + 8) * samples_per_frame;
    if (count <= delay + padding)
        return 0;
    count -= delay + padding;
    if (timing->delay || timing->samples_low || timing->samples_high || timing->padding) {
        uint64_t database_count = ((uint64_t)timing->samples_high << 32) | timing->samples_low;
        /* The pump skips the header separately. A sync record may count it
         * in the delay, or (for CBR Info) in the playable length. */
        int header_in_delay = timing->delay == delay + samples_per_frame && database_count == count;
        int header_in_count = timing->delay == delay && database_count == count + samples_per_frame &&
                              be32(frame + xing) == 0x496e666f; /* Info */
        if (timing->padding != padding || !(header_in_delay || header_in_count))
            return 0;
    }
    uint16_t expected_crc = ((uint16_t)lame[34] << 8) | lame[35];
    if (tag_crc(frame, tag + 34) != expected_crc)
        return 0;
    *timing = (struct track_timing){delay, (uint32_t)count, (uint32_t)(count >> 32), padding};
    return 1;
}
