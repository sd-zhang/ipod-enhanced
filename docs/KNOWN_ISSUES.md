# Known issues and hardware acceptance

This page owns current device observations, unverified hardware behavior and
practical limits. [Release status](FIRMWARE_RELEASES.md) owns candidate hashes
and local validation. Broad positive feedback is not an exhaustive device test.

## Current device reports

| Target | Latest evidence |
| --- | --- |
| 4G | User confirms r46 boots and reported Songs showing A/T for titles beginning with A/The. The host sort-title fix and final-disc corrections are retained. The user subsequently requested Songs jump mode align the highlighted item at the top like other letter menus; r49 reuses that path. Local viewport tests pass; r49 device confirmation is pending. r44/r45 remain withdrawn after the r45 boot loop. |
| 5.5G | User reports good overall behavior and gapless no longer problematic, installed revision unspecified. r18 carries the newer final-disc forward-entry suppression; device confirmation is pending. The new Clicker choices have not been heard on hardware. |

There is no current reproducible 5.5G gapless fault. The affected tracks were
never identified, so no controlled comparison establishes a cause or credits a
particular optimization. Decoder/output behavior remains stock. Investigate
again if specific tracks reproduce it; the report is retained locally in
`build/ipod55g-clicker-01/gapless-hardware-report.json`.

The user also reported rare forward fast-scroll entry inside the final disc
jumping backward to its first song on 4G, with the installed revision unspecified.
The r43 correction is retained. The user subsequently requested ordinary forward
song scrolling without popup activation in the final disc; r47 adds that 4G rule.
The final-disc backward jump also reproduces in the 5.5G native wheel tests.
r17 carries the same direction guard, while r18 also suppresses inactive
forward entry, matching 4G r49. There is no matching 5.5G device report yet.
Real disc jumps retain first-song selection and heading alignment; active
mode retains its endpoint clamp. Regression evidence is in
`build/disc-endpoint-01/` and `build/ipod55g-final-disc-entry-01/`.

The current r50/r19 candidates add the Enhanced revision in About. Neither
new display has been observed on hardware; native layout and text-fit evidence
is recorded separately from device acceptance.

## Remaining hardware acceptance

| Area | What still needs observation | Native evidence |
| --- | --- | --- |
| About versions | Confirm the Version value and unchanged navigation on both models, including non-English labels. | Native getter/resource, layout and font-fit tests pass. |
| 4G startup and sort titles | r46 boot is confirmed; check r50 startup and article/custom-sort title letters with the actual library. | [ARMv4T import and title-sort handling](../navigation_qol/README.md#main-songs-title-jumps). |
| 5.5G Clicker | Listen to each mode while idle and playing music; verify the saved choice after reboot. Apple's video restriction remains. | [Settings, persistence and audio routing](../targets/ipod55g/README.md#clicker-output-choices). |
| Disc positioning | Check that inactive forward scrolling in the final disc stays ordinary without a popup on both models. Check real jumps, reversal, timeout, release and the short final-disc bottom limit. 5.5G r18 retains Apple's reverse-entry thresholds. | [4G disc navigation](../navigation_qol/README.md#disc-headings-and-numeric-scrolling) and [5.5G disc navigation](../targets/ipod55g/README.md#disc-headings-and-numeric-scrolling). |
| 4G wheel delivery | Determine whether intermittent wheel-only freezes recur with the current firmware. The original incident has not been attributed by a controlled device test. | [Rejected-event recovery](../navigation_qol/README.md#wheel-delivery-and-albums-indexing). |
| 4G Songs Quick Scroll | Check top alignment on entry and jumps, full final-page clamping, release, reversal and returning to ordinary scrolling in a real library. r49 device acceptance is pending. | [Title cache, native input and popup tests](../navigation_qol/README.md#main-songs-title-jumps). |
| Pomodoro and alarms | Exercise long-running timers, visibility changes, sleep/wake, cancellation and backlight restoration on both models. | [4G timer/LCD ownership](../clock_pomodoro/ALARM_PATTERN.md) and [5.5G timer/backlight ownership](../targets/ipod55g/CLOCK_NATIVE.md). |

## Performance and playback limits

Long-list song-opening transitions on 5.5G were reported as visibly choppy,
with Songs worst. Later overall positive feedback does not separately measure
that animation. Native queue, script and disc-scroll instruction counts do not
establish physical frame rate, storage latency or battery savings; see the
[4G](../navigation_qol/README.md#queue-and-script-performance) and
[5.5G](../targets/ipod55g/README.md#playback-queue-creation) measurements.

4G highlighted-name scrolling was reported as blurry. It retains the native
8-pixel/140-ms animation; panel response and concurrent playback/UI load have
not been isolated on hardware. See the
[scrolling limits](../navigation_qol/README.md#verification-and-operating-limits).

4G exact ALAC output coverage is limited to the tested packet geometry, sample
rate, channel count and sample depth. Format changes and arbitrary storage
stalls do not inherit a gapless guarantee. The
[playback reference](../comparisons/iPod_4G_Mono_vs_5_5G_Gapless.md#known-limits)
owns codec-specific boundaries and reproduction details.
