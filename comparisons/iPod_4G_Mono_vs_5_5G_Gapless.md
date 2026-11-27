# Gapless playback in 4G monochrome and 5.5G RetailOS

Engineering reference for the monochrome 4G backport: metadata import, decoder
trimming, retained output, ALAC packet boundaries and optional diagnostics.
It targets RetailOS 3.1.1 from `iPod_4.3.1.1.ipsw`; the native playback reference
is the 5.5G firmware in `iPod_25.1.3.ipsw`. All firmware offsets below are relative
to extracted OSOS, unless a runtime address is identified explicitly.

The 4G keeps audible queued audio running across a validated automatic track
change and trims codec priming/padding without changing native buffer ownership.
The 5.5G decoder remains stock; its queue-construction optimizations are separate
from decoding/output. Current release and listening observations belong to
[Release status](../docs/FIRMWARE_RELEASES.md) and
[Known issues](../docs/KNOWN_ISSUES.md).

- [Backport and output ownership](#why-the-stock-4g-has-a-gap)
- [Known limits](#known-limits)
- [Playback comparison](#playback-parity-against-the-stock-55g)
- [ALAC packet boundaries](#alac-packet-boundaries)
- [Release diagnostics](#release-diagnostics)

## Why the stock 4G has a gap

The decoder only knows the file it is playing. At EOF, stock drains output at
`0x116e94`, sets engine state 2 at `0x116de4` and waits for a command. The UI
chooses the next song and sends play command 5. That runs `0x116da0` →
`0x116e0c`, fading output (`0x15e0bc`), stopping it (`0x157df4`) and opening the
next file cold.

## How the patch removes it

| Stage | Code | Current contract |
| --- | --- | --- |
| EOF | `gapless/handoff.S`, drain `0x1165bc` | Keep A's ring and hardware FIFO playing while the decoder waits in state 2. AAC/MP3 can retain about 0.6 s in 28 slots. |
| UI wait | `gapless/trim.c`, BL `0x123f98` | While handoff phase 1 still holds queued audio, avoid the state-2 play-mode result that makes TX `0x133038` select its silence buffer. |
| Next song | `gapless/trim.c`, `0x116da0` | Select B without the stock fade/stop; validate its request cookie, decoder, format and endpoint before reusing the running ring. |
| Trimming | Publish runtime `0x40015a78`, worker `0x15a8cc` | Publish per-slot audible-frame notes. The CPU worker passes those frames through its FIFO, retaining 1,152 AAC/MP3 frames plus bounded tail padding across ordinary blocks. |
| Seek/scan | `gapless/trim.c` | AAC position derives from decoder packet index `+0x2cd4` minus jobs in flight (`+0x2d10 − +0x2d14`); tail removal at the join uses padding independently of that position. MP3 stays untrimmed after a seek. |

Metadata plumbing in `engine_binding.S` and `patch_*.py` carries iTunesDB
`mhit+0xb8/+0xbc/+0xc8` timing through independent request/file-list snapshots
to the selected decoder. `mp3_origin.c` and `lame_timing.c` handle MP3 frame
origin and verified LAME timing. The output play-mode guard matters even while
the ring is full: state 2 otherwise selects silence without consuming that ring.
Logged UI response intervals of 52–472 ms explain the retained-tail design;
they are observations from specific runs, not a scheduling guarantee.

A retired music request may be disposed 600 ms after the UI releases it. Its
native disposal posts a stop command, so the handoff distinguishes the request
currently playing from an older request: disposing an old request must release
it without stopping its successor. `gapless/tests/dispose.py` covers that boundary.

### Short-track transitions and descriptor ownership

Once playback has started, a reserve smaller than 512 frames stays in the FIFO
and the current descriptor becomes empty. Hook `0x133548` omits hardware
submission. Native completion releases by queue position rather than buffer
address, so empty descriptors cannot be freed ahead of older audio. Hook
`0x133be8` releases them only when the completion cursor reaches them, using
stock ownership checks and release routines.

This path is CPU-only and scoped to the validated primary stereo16 output.
Updates to the empty-slot map mask interrupts and restore the previous mask;
each scan is bounded by at most 32 ring slots. Nonempty submissions bypass the
helper. There are no added allocations or waits. Physical interrupt latency
has not been measured. Cold start still supplies a full first descriptor;
waiting for the normal reserve would swallow very short standalone tracks.

### Complete track endings

A natural ending must drain both the hardware queue and the worker's private
FIFO. At `0x116e94`, the overlay appends a bounded number of empty input slots
through native producer reservation, publication and the output mailbox.
Their `NOTE_END` notes ask the CPU worker to emit retained audio; the COP never
reads or changes the CPU FIFO. Empty slots retire in order. Only the final
audible output buffer may receive trailing zeros for minimum length/alignment.
A successful handoff bypasses this drain and keeps the reserve.

Poll hook `0x116ea8` uses the existing wait loop when the ring is full and waits
for pending worker processing. Hook `0x15a9d0` excludes synthetic input from the
source sample clock. Incompatible geometry, a stopped engine or disabled route
cancels the extra work and restores the native drain check, preventing a wait
for worker work that can no longer complete.

### Timing validation and recovery

The native AAC-LC reader starts at packet 1. Trimming therefore begins at
`delay − 1024` frames in its decoded stream and ends at `delay − 1024 + count`,
or removes known padding at the join after a seek. MP3 accounts for 529 frames
of synthesis latency plus a decoded Info/Xing frame: 1,152 frames for MPEG-1,
576 for MPEG-2/2.5.

Playable count is audible length, independent of priming and padding. A short
valid track may have less music than either cut. MP3 trimming requires a valid
header probe for the current selection; stale or truncated probes cannot arm it.
LAME data may supply missing timing, or correct a verified metadata-frame offset
in database delay/count when the related tag values agree.

AAC producer-check failures pass the block untrimmed while retaining the audio
reserve. Padding already excluded from prior blocks is counted so EOF removes
only the remainder: if 504 of 1,528 padding frames were removed, the final drop
is 1,024, avoiding a second cut into audible samples. These recovery fixtures
deliberately supply inconsistent final-block sizes; ordinary valid files have
not established reachability of every injected failure.

The signed `producer.position` counter has a conservative fallback: a value
below −1024 disables further trimming for the track, preserving the armed flag,
FIFO and final EOF drain. This can occur after about 13.5 hours at 44.1 kHz;
AAC's valid packet-zero/recovery range −1024..−1 stays eligible. Playback continues
untrimmed and end padding may remain. `publish_overflow_guard.S` preserves this
contract in the pinned layer; `tests/long_position.py` seeds the boundary rather
than claiming a thirteen-hour hardware run.

Packed stereo16 copying in `copy_frames.S` requires word alignment and either
disjoint buffers or forward-safe overlap (`destination <= source`). The helper
loads all four frames before storing that group, handles the short tail, and
uses eight stack bytes. `tests/copy_frames.py` checks overlap and ABI boundaries;
these invariants matter to FIFO compaction as well as ordinary output.

## Build and test

Use [Building and verification](../docs/BUILDING.md) for the current combined
firmware and complete release receipt. The lower-level `gapless/build.py` builds
a standalone playback base and does not compose later navigation/Clock layers.
Choose a fresh output directory and keep pinned baseline archives unchanged.
The tools use the repository's uv Python environment, Xcode Clang, Zig's ELF
linker, `lame` and `ffmpeg`; optional Apple AAC fixtures use `afconvert`.

`gapless/verify.py` checks package/hooks, startup layout, metadata/request plumbing,
engine binding, LAME timing, native decoders and output joins. Focused transport
fixtures execute the installed decode pump, EOF handoff and output worker with
supplied decoded blocks. `tests/empty_retirement.py` checks ownership under
early/late completion, large priming, repeated ring wraps and stop/rebuild;
`tests/track_end.py` requires every audible frame, empty carry and retired
descriptors on both output adapters. Real Apple AAC tracks from 24 to 25,000
samples and MP3 fixtures supplement synthetic cases.

Compressed decoder checks and transport checks are separate. The ownership
fixture executes native TX completion but supplies hardware programming; stop/
rebuild coverage does not exercise every UI stop/restart sequence. Host tests do
not boot the OS or validate physical disk timing, arbitrary interrupt scheduling,
cross-core cache behavior or analog output. Run Unicorn with JIT permitted as
described in the build guide.

## Known limits

- Pausing within roughly the last 0.6 s of a track retains the stock gap at
  that change.
- A manual skip during the roughly 0.1–0.5 s wait after EOF can first play the
  outgoing track's retained tail.
- If the UI asks for the next song after the tail has nearly run out, silence
  can begin before B's first block. About 55 ms after the play command was
  observed in specific runs; the handoff falls back to stock stop/restart when
  no audio remains queued.
- Early wake is limited to ordinary forward-ordered local files. Shuffle,
  repeat and wrap stay under the native selector. Ring reuse additionally
  requires compatible format/geometry; rate changes and incompatible ALAC/
  AAC/MP3 geometry drain and reconfigure. A missing database record can use
  validated LAME timing or codec-specific fallback, not assumed exact trimming.
- Very long tracks can hit the signed-position fallback described above.

## Playback parity against the stock 5.5G

The comparison tools decode common inputs through both firmware images and
compare stock 5.5G published PCM with 4G output. One frame is a left/right sample
pair. The 5.5G reads supplied timing through its native `mhit` mapper, getters,
command-53 producer and engine receiver. File I/O, task scheduling and source
clock are supplied; a synthetic PCM fixture does not prove physical continuity.

`verify_aac_parity.py`, `verify_mp3_parity.py`, `verify_pcm_parity.py` and
`verify_mixed_album.py` retain the native comparison fixtures. The MP3 decoder
emits the same raw PCM in the tested headerless, Xing, CRC-protected Xing and
CBR Info cases; playback windows can still differ because metadata-frame
origin and timing interpretation differ. The worked CBR measurement below
supports the 4G's origin correction, not a blanket claim about current 5.5G
listening quality. Current hardware observations belong to Known issues.

The stock MP3 inspector initializes format byte 2 at `0x277990`; probe `0xc9400`
recognizes the literal `Xing`. In the measured files it sends the first MPEG
frame to the decoder at file byte zero, or byte 42 after a 42-byte ID3v2 prefix.
For Info frames, its timing includes that leading decoded metadata frame.
The 5.5G imports a 64-bit count but command 53 passes the low word; the 4G clip
cursor retains both words. Signed producer-position limits remain separate.

### Listening comparison for constant-bitrate LAME MP3

This saved host measurement isolates the metadata-frame rule carried by the
current implementation; it is not a fresh measurement of a current release.

Two independently encoded 128 kb/s CBR files contain adjacent parts of a 44.1 kHz stereo recording. A sweep continues through the cut on the left. The right channel has a short marker 360 frames before the cut and another 300 frames after it. The test joins the two captured playback windows in memory with zero added silence. This isolates trimming from disk and scheduling latency.

| Measurement | Original WAV | Patched 4G | Stock 5.5G |
|---|---:|---:|---:|
| Pre-cut marker | −360 frames | −360 frames | Absent (peak 1 on the 16-bit scale) |
| Post-cut marker | +300 frames | +300 frames | +1,452 frames |
| Left-channel RMS over the first 20 ms after the cut | −13.4 dBFS | −13.8 dBFS | −52.1 dBFS |

Both firmware decoders produced identical raw PCM, and each pair of playback windows totaled 26,014 stereo frames. Changing only the four marker bytes from `Info` to `Xing` made the 5.5G playback window match the patched 4G byte for byte with the same database timing and the same raw PCM. The header edit was a diagnostic control.

LAME and macOS `afinfo` agreed on a 576-frame delay for both original files. Core Audio independently decoded exactly 13,211 and 12,803 playable frames and placed both markers at the original positions. Setting the simulated iTunesDB delay to 1,728 frames (576 + 1,152) also aligned the stock 5.5G. The patched 4G produces the same 26,014 frames byte for byte with either delay value when supplied the encoder's count and padding. The backport corrects a database delay that includes one metadata frame when the sample count and padding match the verified LAME tag. For CBR `Info` it also corrects a playable count that is one MPEG frame too long when both delay and padding match the tag. A 2007 comparison of an iPod database and an MP3 found matching LAME delay and padding for a **VBR** track. [Apple packet table fields](https://developer.apple.com/documentation/audiotoolbox/audiofilepackettableinfo) · [Original iPod database comparison](https://sourceforge.net/p/gtkpod/mailman/gtkpod-questions/thread/20070511231258.GK2807%40psilocybe.teonanacatl.org/)

After extracting stock OSOS files, set `playback_elf` to the matching 4G
playback ELF and `comparison_output` to a fresh output directory. This command renders the source and both firmware outputs, and saves the two original MP3s for a physical sync comparison.

```sh
.venv/bin/python gapless/verify_mp3_join.py "$research_dir/4g.osos" "$research_dir/55g.osos" "$playback_elf" --output "$comparison_output"
```

### Timing written by iTunes 7 for Mac

Both tested releases wrote **576 samples of front delay** for the two CBR tracks above. The difference was where iTunes accounted for the 1,152 samples in the leading `Info` frame. Each table entry lists the front delay, playable sample count and end padding in that order.

| Input at 44.1 kHz | LAME tag | iTunes 7.0 iPod record | iTunes 7.7.1 iPod record |
|---|---:|---:|---:|
| A, marker 360 samples before the cut | 576 / 13,211 / 1,189 | 576 / 14,363 / 1,189 | 576 / 13,211 / 2,341 |
| B, marker 300 samples after the cut | 576 / 12,803 / 1,597 | 576 / 13,955 / 1,597 | 576 / 12,803 / 2,749 |
| Three-minute 128 kb/s CBR control | 576 / 7,938,000 / 1,008 | 576 / 7,939,152 / 1,008 | 576 / 7,938,000 / 2,160 |

The 7.0 MP3 reader estimates a frame count from the file length and bitrate at `0x1559b9`. It replaces that estimate with the tag's frame count for `Xing`, and retains the estimate for `Info` at `0x75b78`. The LAME delay is copied at `0x76146`. In 7.7.1, `0x47dfb1` still copies the LAME front delay. The instruction at `0x47dfcf` adds one MPEG frame to the end padding before the subtraction that produces the playable sample count. The library scan preserves the tagged values in both releases. The iPod database writers copy the resulting delay to `mhit+0xb8`, the count to `+0xbc` and the padding to `+0xc8`.

With the **7.0** values, the stock 5.5G emitted an extra 1,152 samples at the start of each track. Removing those first samples from each captured window recovered the reference window byte for byte. With the **7.7.1** values, it emitted exactly the source length for each track, lost A's end marker, and played B's marker at sample 1,452. B's marker was 1,152 samples (26.12 ms) late relative to the start of B with either release.

With the measured values from either iTunes release, the corrected 4G reproduces both reference playback windows byte for byte.

The probe runs the complete MP3 inspection routine from each original Intel Mac executable, followed by the scan's final decision, the library track field copy and the iPod record writer's timing instructions. It supplies memory allocation, integer division, file I/O and a synthetic seek offset for the scan. The stock 5.5G test uses those three serialized timing values and the original compressed files. A capture of a physical sync would additionally verify the final on-disk database and transferred file together. [Apple iTunes 7.0 installer](https://secure-appldnld.apple.com/iTunes7/Mac/061-2326.20060912.iiTnz/iTunes.dmg) · [Apple iTunes 7.7.1 installer](https://secure-appldnld.apple.com/iTunes7/Mac/061-5123.20080730.fr4Ym/iTunes.dmg)

To reproduce, extract `iTunes.app/Contents/MacOS/iTunes` from the installer's `iTunesX.pkg/Contents/Archive.pax.gz` and run the following for each version. The probe checks the SHA-256 of the Intel slice. Omitting `--stock-55g` runs only the host metadata inspection and also supports song-length inputs.

```sh
lipo -thin i386 "$itunes_executable" -output "$research_dir/iTunes-i386"
.venv/bin/python gapless/inspect_itunes_mp3.py "$research_dir/iTunes-i386" "$comparison_output"/{A,B}.mp3 --stock-55g "$research_dir/55g.osos"
```

### Finder sync on macOS 27.3

On build `26C5047e`, with Music 1.7.3, the installed library and device sync agents produced the iTunes 7.7.1 values in all three CBR rows above. Replaying the two join tracks with those values through the patched 4G produced the reference playback windows byte for byte. The padding rule in this build has an exception.

| Source length at 44.1 kHz | macOS iPod timing (delay / count / end padding) | Samples emitted by patched 4G |
|---:|---:|---:|
| 12,959 | 576 / 12,959 / 2,593 | 12,959 |
| 12,960 | 576 / 14,112 / 1,440 | 12,960 |
| 12,961 | 576 / 12,961 / 2,591 | 12,961 |

These three fixtures used the same 128 kb/s stereo LAME encoding and differed by one source sample on either side of the middle clip. For this `Info` path, the device agent calculates a provisional playable count from the estimated number of MPEG frames, the encoder delay and the tagged padding. If that provisional count is divisible by 588, it keeps it. Otherwise it adds one MPEG frame to end padding and reduces the count accordingly. The 12,960-sample clip produced a provisional 14,112 (24 × 588). The 4G correction uses the verified LAME count of 12,960. All three clips now match the reference playback windows byte for byte. Two additional Finder records reported 1,176 and 2,940 samples for clips containing 24 and 1,788 samples respectively. The patched 4G emitted the exact source-aligned window for both. The probe validates the record again on a retry and associates the corrected count with the current track.

The test executed the arm64e routines in the installed `AMPLibraryAgent` and `AMPDevicesAgent` using the original compressed frame data, file sizes and MPEG-1 stereo header parameters. Device-agent offsets relative to its Mach-O base are `0x6ebf0` for the frame-count estimate, `0x6e1bc` for the tag reader, `0x6e7d4` for the divisibility check, `0xfed48` for the library field copy and `0x1d41ec` for the iPod record's timing fields. The device writer was exercised in memory. The firmware replay used those values with the original MP3s. No physical iPod was mounted for this measurement.

## Extraction

Both OSOS payloads begin with ARM exception vectors. The directory entries at firmware file offset `0x4200` specify the lengths and additive byte checksums. The payload starts at the directory entry's device offset plus the container's initial block size, which is `0x200` for the 4G image and `0x800` for the 5.5G image.

| IPSW member | OSOS file offset | OSOS length | Byte sum |
|---|---:|---:|---:|
| `Firmware-4.3.1.1` | `0x4600` | `0x356f14` | `0x1259ef03` |
| `Firmware-25.6.3` | `0x5000` | `0x736000` | `0x2c7c48f3` |

## Track metadata

Both stock builds read an `mhit` record into a stack buffer and compare its tag. The 4G reads `0xf4` bytes into the buffer and checks the record at `0xb68f4`. The 5.5G reads `0x1b4` bytes and checks it at `0xd79c4`. If the record declares a shorter header, both call a helper to zero-fill the buffer past that declared length.

The 5.5G explicitly copies these fields from the stack buffer into the track object:

| Field | `mhit` offset | Track object offset | 5.5G instruction offsets |
|---|---:|---:|---|
| Encoder delay | `0xb8` | `0xec` | `0xd7ba4`, `0xd7ba8` |
| Sample count, two 32-bit words | `0xbc`, `0xc0` | `0xf8`, `0xfc` | `0xd7b78`, `0xd7bb4`–`0xd7bb8` |
| End padding | `0xc8` | `0xf0` | `0xd7bac`, `0xd7bb0` |
| Gapless data | `0xf8` | `0x100` | `0xd7bbc`–`0xd7bc8` |

The stock 4G read covers the delay, sample count, and padding fields, but its track construction code does not copy them into the track object. The 4G read ends before the gapless-data field. The different track object layouts matter when porting these loads and stores. Field names follow the [libgpod iTunesDB parser](https://github.com/gtkpod/libgpod/blob/master/src/itdb_itunesdb.c#L2428-L2451).

## Audio queue and next-track references

| Reference | 4G OSOS offset | 5.5G OSOS offset |
|---|---:|---:|
| References to the `TrackCacheReadTask` name | `0x4c1a8` | `0x94948` |
| References to the `TPCMQUEUE` name in queue initialization | `0xf9610` | `0x240650` |
| Call to the queue constructor | `0x1bd20` | `0x25e298` |
| `TPCMQUEUE` final virtual table assigned during initialization | `0x293ac` | `0x67782c` |

The queue initializers set different member offsets and sizes. In the 5.5G trace event name table at `0x213cd4`, event 4 maps to `Next Track` and event 5 maps to `Next Track User Initiated`. The routine at `0xac148` calls `0xac4c4` before emitting event 4 on its main path. The call at `0xa9750` emits event 5. These labels provide entry points for tracing when the next decoder starts and whether PCM output stays active across the boundary.

## How the 5.5G trims decoded audio

At `0xacabc`, the 5.5G reads the playable sample count, padding, encoder delay and two additional fields through the track getters. The producer at `0x1447bc` builds audio command 53. Its receiver at `0x249cfc` places the count, padding and delay at engine offsets `+0x90b4`, `+0x90b8` and `+0x90bc`. For MP3, `0x723070` adds 529 sample frames to a nonzero delay. The loop at `0x723330` computes the front cut and the audible length for each decoded block. The two calls at `0x7233d4` and `0x7233e4` convert those frame counts into byte ranges.

For example, a tagged recording with 576 frames of encoder delay and 44,100 playable frames needs exactly 44,100 frames at the output. Tail padding helps validate the metadata. Subtracting it again from that playable count shortens the song. The 4G implementation and retail 5.5G code produced identical audible intervals over 72 combinations of delay, block size and playable length.

## Reproduce the disassembly

From the repository root on macOS with the Xcode command-line tools installed:

```sh
research_dir=$(mktemp -d)
.venv/bin/python - "$research_dir" <<'PY'
from pathlib import Path
from struct import unpack_from
import sys
from zipfile import ZipFile

output = Path(sys.argv[1])
for model, archive, member, block_size in (
    ("4g", "iPod_4.3.1.1.ipsw", "Firmware-4.3.1.1", 0x200),
    ("55g", "iPod_25.1.3.ipsw", "Firmware-25.6.3", 0x800),
):
    with ZipFile(archive) as ipsw:
        firmware = ipsw.read(member)
    directory = unpack_from("<I", firmware, 0x104)[0] + 0x200
    assert firmware[directory:directory + 8] == b"!ATAsoso"
    offset, length = unpack_from("<II", firmware, directory + 0x0c)
    checksum = unpack_from("<I", firmware, directory + 0x1c)[0]
    start = offset + block_size
    osos = firmware[start:start + length]
    assert len(osos) == length and sum(osos) == checksum
    (output / f"{model}.osos").write_bytes(osos)
PY

(
    cd "$research_dir"
    for model in 4g 55g; do
        printf '.syntax unified\n.text\n.arm\n.incbin "%s.osos"\n' "$model" > "$model.s"
        xcrun clang -target armv4t-none-eabi -c "$model.s" -o "$model.o"
    done
    xcrun llvm-objdump -d --start-address=0xb68c0 --stop-address=0xb6b00 4g.o
    xcrun llvm-objdump -d --start-address=0xd798c --stop-address=0xd7bdc 55g.o
)
```

## ALAC packet boundaries

Source and test filenames in this section are relative to `gapless/`.
The validated trimming geometry is 4096-frame stereo 16-bit PCM; the real
library exercised here is 44.1 kHz. No broader ALAC format support or gapless
format-change support is implied.

The native ALAC open routine (`0xdc040`) starts packet index zero, but decode
(`0x4f2d4`) increments before lookup. Without the fresh-start correction this
skips packet zero: 4096 audio frames, or 92.88 ms at 44.1 kHz. These samples
are music rather than AAC-style priming. The decoder also zero-fills a short
last packet to 4096 frames. Boundary measurements on 38 individual tracks
found 2.99–87.89 ms of added padding: for example, 3204 real frames leave 892
zeros (20.23 ms), while 220 real frames leave 3876 (87.89 ms).

### Packet and transport contract

- On a fresh normal ALAC start, set the index from zero to −1 before the native
  pre-increment. Check decoder type, fresh counters, error/EOF state, seek targets
  and scan state first. Explicit seek and scan keep native positioning; correction
  runs at startup rather than in the decode loop so failed packets cannot retry
  forever.
- After successful decode at `0x4f540`, capture the actual frame count from the
  native stack against the reserved descriptor. Find it by buffer pointer because
  the pump can reserve another buffer before publishing this one. Native zero fill
  and fixed DSP/ring geometry remain intact.
- Publish count through existing per-slot notes. The CPU worker copies real frames
  into its 4992-frame FIFO, retaining 512 plus at most 127 alignment frames between
  ordinary blocks. Maximum normal occupancy is 4096 + 639 = 4735 frames.
- Missing or invalid count metadata passes the block untrimmed through the FIFO,
  retaining earlier audio and allowing the next valid block to recover. Even a
  missing first count establishes the reserve, so a later short packet is not
  mistaken for a cold start.
- EOF drains all remaining real audio before stop, with hardware alignment zeros
  only after the final source sample. Incompatible ALAC/AAC/MP3 geometry changes
  use native drain/reconfiguration.

`alac_build.py` redirects four existing helpers and adds the successful-decode
hook without RAM allocation. `verify_alac_package.py` pins the shared playback
state and preservation of other feature artifacts. Current image headroom and
release identity belong to the release record.

### Evidence and limits

`verify_alac_boundaries.py` runs the native MP4 parser, decoder, ready/seek methods
and decode pump. Generated files check complete source PCM, one-frame and short
packets, exact multiples, reopen, seek/scan guards, malformed packets and repeated
EOF. Real-library checks compare native first/last packets with independent
FFmpeg PCM; optional exports record source/output hashes for transport replay.

`tests/alac_output.py` supplies decoded PCM at the native successful-decode tail,
then runs native zero fill, capture, publish, DSP/output worker, I²S descriptors,
retained handoff and EOF drain. Both output frontends, delayed completions,
ring wrap, a full ring at EOF, missing first/middle counts and mixed-format drain
fallbacks are covered. Boundary replay compares every emitted source frame with
its independent reference.

Compressed decoding and transport fixtures are separate; they are not a full-device
emulator. Task scheduling, disk latency and physical I²S timing are supplied.
Listening reports do not establish every format or scheduler boundary; see the
release and known-issues records for the current hardware scope.

## Release diagnostics

Normal releases omit the custom playback logger, trace buffer, About counters
and diagnostic event hooks. The explicit `gapless/build.py --diag` option enables
them for a standalone playback base; it does not compose later navigation/Clock
layers. Apple's own diagnostic code is untouched.

`GAPLESS_DIAG` guards the active `producer.first` trace-budget initialization,
read and decrement in `trim.c`. The unused four-byte field stays in the normal
layout because the ALAC overlay shares pinned playback state. Nothing moves.
`release_build.py` implements the normal-release contract in the pinned binary
with seven changed ARM words in `alac.elf`:

- `0x38ca6c` and `0x38ca78` replace counter initialization/store with NOPs;
  the old `r0` value is overwritten before use.
- `0x38d480..0x38d490` retain `NOTE_TRIM`, branch to the existing position update
  and NOP the skipped counter work. Continuation overwrites `r3` and sets flags
  before its next conditional operation.

Image size, headers, state allocation and entry points remain fixed. The same
seven words change in the companion ELF; other carried ELFs and maps remain
identical. Bypassed playback bodies remain archived in the image: the removal
is on the active path and does not repack older code.

`tests/release_diagnostics.py` watches actual ARM memory accesses during retained
handoff, ring wrap, delayed completions and final drain on both frontends. It
requires exact PCM and no normal-release trace-counter accesses, while a diagnostic
build still records starts and initializes the budget. `verify_release_package.py`
checks the entire image/ELF, container, updater and metadata and rejects deliberate
instruction/preservation corruptions. No hardware performance or battery gain
is inferred from this small code removal.

## Diagnostics

With `--diag`, **Settings > About > Version** (`0x766c0` → `0x128b4c`) displays
hexadecimal counters. Visits at least 0.6 s apart advance pages covering retained/
abandoned/dry handoffs, silent fills/untrimmed/failed checks and seeks/padding drops.

An About visit also writes `iPod_Control/gapless.log` (8 KiB, at most every two
seconds) using native volume `0xa2b60`, open `0x93974` (`data`, mode 2), flushed
write `0x93c50` and close `0x938d4`, like the Preferences writer at `0xe52cc`.
The log contains counters and the last 160 trace events: starts, trim points,
seeks, EOF, padding removal, post-join output, TX play-mode changes and engine
state broadcasts. Read it from the mounted device after opening About.
