# Engineering guide

Use the [user guide](USER_GUIDE.md) for controls, [build guide](BUILDING.md) for
commands and inputs, [release record](FIRMWARE_RELEASES.md) for current candidates
and verification, and [known issues](KNOWN_ISSUES.md) for outstanding hardware work.

## Source boundaries

This is the active firmware repository. The sibling
`ipod-classic-firmware-research` checkout is a reference archive; do not resume
the failed Classic UI/Cover Flow prototype here.

| Target or area | Source and responsibility |
| --- | --- |
| Monochrome click-wheel 4G, Apple 3.1.1 | `gapless/` owns playback; `menu_scroll/` highlighted names; `navigation_qol/` library, navigation and performance; `clock_pomodoro/` Clock, alarms, LCD protection and combined verification. These contain 4G-specific native addresses. |
| Late-2006 5.5G, Apple 1.3 | `targets/ipod55g/` contains its independent builder, addresses, resources and native service contracts. It retains stock playback. |
| Local evidence | `build/` contains pinned inputs, generated releases, frozen sources, traces and immutable receipts. These are build dependencies and evidence, not cleanup targets. |

Keep the source paths and pinned inputs intact. Native addresses, display/input
contracts and installation assumptions are not interchangeable between models.

## Engineering references

| Reference | Contents |
| --- | --- |
| [4G navigation](../navigation_qol/README.md) | Actions, On-The-Go, Volume Limit, library filtering, disc headings and numeric scrolling, native queue/script performance, wheel recovery and highlighted-name scrolling. |
| [4G alarms and LCD](../clock_pomodoro/ALARM_PATTERN.md) | Buzzer/light timing, preference behavior, Pomodoro timer delivery and LCD transaction protection. |
| [4G playback](../comparisons/iPod_4G_Mono_vs_5_5G_Gapless.md) | Gapless backport, codec boundaries, ALAC packet repairs and release diagnostics. |
| [5.5G navigation and performance](../targets/ipod55g/README.md) | Actions, disc metadata/rendering/scrolling, queue creation, script lookup, Clicker output choices and stock platform boundaries. |
| [5.5G Clock, Pomodoro and alarms](../targets/ipod55g/CLOCK_NATIVE.md) | Native widgets, RTC/timers, Main Menu customization and backlight concurrency. |

## Performance work

Both targets skip redundant empty-index cleanup during queue creation and use
checked implicit-global lookup shortcuts. The 5.5G additionally reuses safe
navigation history and trims UI dispatch work. Disc scrolling avoids repeated
lookup, popup text copying or reveal requests where each model's live state
proves the work unnecessary. The references above contain measurements and
tradeoffs; native instruction counts are not measured frame rates or battery gains.

Earlier 4G PCM/index work, wheel-delivery recovery, rejected Pomodoro-tick
recovery, long-track fallback and LCD protection remain part of the combined
build. Exact ALAC output coverage is 44.1 kHz, stereo 16-bit, 4096-frame packets;
other formats and arbitrary disk delays do not inherit that claim.

## Evidence and contribution workflow

The [build guide](BUILDING.md#release-evidence) defines frozen source snapshots,
immutable receipts and reproduction requirements. The same guide owns the
[pre-push checks](BUILDING.md#pre-push-checks); current contracts belong in the
references above, while session plans, handoffs and historical run logs stay
in ignored local evidence directories.
