# Alarm sound and light

Engineering reference for the monochrome 4G Clock/Pomodoro alert and LCD
transaction protection, including countdown event recovery. With **Alarm Backlight** enabled, the screen requests
**350 ms lit / 250 ms dark**, four times during the 2.4-second beep. Sound
routing and tone durations remain native. [Controls](../docs/USER_GUIDE.md) ·
[Release status](../docs/FIRMWARE_RELEASES.md)

## Sound and light contract

Pomodoro uses the stock Clock alarm: four pairs of buzzer period 180 for
200 ms and period 144 for 400 ms. These are hardware periods, not frequencies
in Hz. The first tone stays lit throughout; the second stays lit for 150 ms,
then dark for 250 ms. Native calendar alerts retain their two 200 ms tones,
finishing after 350 ms lit / 50 ms dark.

Disabling Alarm Backlight removes the added flashes while preserving illumination
from native wake itself. At completion or cancellation, cleanup resumes the
normal backlight timeout. A rejected or truncated queue produces no extra sound
or prolonged alert. Native queue cancellation flushes pending tones without
introducing a new sound-stop policy.

[alarm_pattern.c](alarm_pattern.c) tags alarm-only durations in the existing
AsyncPiezo queue. The worker strips the tag before deadline arithmetic. Its
hardware timer wakes it at the dark boundary and again at the unchanged tone
deadline; the native timer ISR only acknowledges the interrupt, leaving pitch
active. No UI pointer is retained, no new work runs in the ISR, and no polling
loop, extra timer or preference is introduced.

One startup-zeroed word at `0x14534654` owns cleanup state:

| Value | Meaning |
| --- | --- |
| 0 | Inactive |
| 1 | Lit, without a split in the current tone |
| 2 | Dark tail |
| Greater than 2 | Lit split tone; dark-tail microseconds plus 2 |

The duration veneer passes the native period-table entry alongside the duration,
so a partial queue needs no pair-history state. Before skipping a redundant On
between tones, the helper checks actual native backlight state; an intervening
UI Off can therefore be followed by the second tone relighting the screen.

Sound deadlines are dated after light-service overhead at hardware tone start.
Late delivery or slow native backlight work shortens or skips a visual gap
instead of underflowing a timer or extending a tone to manufacture the gap.
The requested cadence is nominal; scheduler latency and physical dimming are
not measured by host execution.

## Alarm Backlight preference

Alarm Backlight defaults On and uses bit 24 of the serialized Main Menu mask
at preferences `+0xb34`, with a set bit meaning disabled. Native preference
save/load and supported migrations preserve it. Main Menu customization changes
and Reset Main Menu retain the setting; Reset All Settings restores On. The
native On/Off value strings and immediate-cycle command provide its Settings
row, immediately below Backlight Timer. The caption is English.

The option controls added alarm illumination, not native wake or sound routing.
Off therefore does not force the screen dark: native wake can illuminate it,
including an early RTC wake before Pomodoro rejects an alert that is not due.
No new preference file, polling task or timer is needed. `alarm_light.c`,
`alarm_light_hooks.S`, `verify_alert_preferences.py`, `verify_alert_ui.py` and
the alarm pattern suites cover storage/reset, dispatch and sound/light behavior.

## Pomodoro UI timer delivery

The countdown uses a one-second native UI timer independently of RTC phase and
alarm progression. If UI event allocation or publication rejects its expiry,
`pomo_timer_deliver` retries the same timer after native in-flight expiry cleanup
`0x9f688`. Rearming before cleanup could wait on the worker itself or have the
new RUN state overwritten. All seven words read by the native event copier
are initialized; only four carry time-event meaning.

Retry is serialized with start/stop using recursive scheduler mutex
`0x103cf118`. It requires a visible, running Pomodoro and the expected
STOP/EXPIRED timer states before calling native `0x9fd90`; Leave, Pause, Reset
and newer manual restarts therefore prevail. The delivery task performs no
painting, dereferences no UI object and never takes the RTC lock under the
scheduler lock. Unrelated timers retain native delivery and cleanup. There is
no additional RAM, timer or polling loop.

`timer_recovery.c`, `timer_build.py`, `verify_timer_recovery.py` and
`verify_timer_package.py` own implementation and validation. Tests execute both
native timer workers, queue ownership and cancellation/restart interleavings;
task switches, wakeup and physical drawing remain supplied. This recovery does
not establish the cause of the separately reported physical wheel freeze.

## Inactive-path cost and package boundaries

Inactive operations go directly to native continuations, with a shared cleanup
routine. Mid-function veneers use only dead `r12`/flags; active paths preserve
live registers, and cancellation retains its conditional flag contract.
The current alarm overlay occupies 1,052 image bytes and four owned RAM bytes.
The separate LCD overlay below adds its own code/state; current total image
headroom belongs to the release record.

`verify_alarm_cost.py` uses the same supplied schedule to compare sound MMIO,
queue handling, preferences and final cleanup. Idle events take 17 executed
ARM instructions and three ordinary tones take 492. The full eight-tone alert
takes 3,972, versus 5,678 for the earlier two-flashes-per-pair implementation:
four additional timer wakes replace eight. These counts explain the cadence's
bounded scheduling work; they are not CPU cycles or physical device timing.

[pattern_build.py](pattern_build.py) appends the pinned alarm layer, including
Pomodoro's private beep callee. [verify_pattern_package.py](verify_pattern_package.py)
uses explicit ARM/veneer profiles, checks period arguments and tables, and rejects
matching image/ELF corruptions as well as preservation errors. Playback/ALAC
artifacts and UI resources are preserved. The historical runtime installer
builds an earlier layer; use the combined build for current firmware.

## LCD transaction protection

A native backlight update selects LCD contrast register 4 while the drawing
task can stream pixels to register `0x12`. Busy polling alone does not protect
a complete command/data transaction: forced interleavings can send pixels to
contrast or contrast to pixel RAM, including at final restoration. This
reproduces a software race without proving the cause of a reported physical
screen incident or establishing electrical damage.

[lcd_serialization.c](lcd_serialization.c) uses a native recursive mutex around
six complete transactions: redraw, LCD initialization, display mode, suspend
command, power transition and contrast. The command/data helper and bus setup
run within guarded initialization. Native recursion permits nested initialization/
power calls. Lock-holding paths do not call UI, RTC or piezo request services,
avoiding graphics/RTC lock-order inversion. Interrupts stay enabled and the
native kernel blocks contending tasks; native 10/30 ms power delays remain.

Four startup-zeroed bytes at `0x14534658` hold the mutex ID. Construction reserves
it before surface publication and drawing-task creation; the pool-reset hook
invalidates it before native IDs are repopulated. If the 400-ID pool is exhausted,
initialization retains the native unlocked fallback instead of using an invalid
ID. That exceptional failure has no transaction-protection guarantee.

The two-instruction batched/no-I/O redraw return is untouched. A supplied full
frame emits identical MMIO writes with 83,951 executed ARM instructions versus
83,864 without locking: 87 additional instructions, about 0.10%. The separate
[lcd_build.py](lcd_build.py) overlay adds 524 image bytes while preserving the
alarm, playback and UI artifacts. These are package and instruction measurements,
not physical elapsed time or current total headroom.

## Verification and limits

[verify_alarm_pattern.py](verify_alarm_pattern.py) executes request generation,
queue acceptance/advancement, timer ISR, MMIO programming, backlight services
and normal timeout. It checks literal 350/250 timelines, both sound sources,
calendar timing, rejection/partial queues, cancellation, early/late delivery,
wrap, preference changes, simultaneous alarms, slow light services, short
boundary durations and UI Off between tones.

[verify_lcd_serialization.py](verify_lcd_serialization.py) executes the LCD driver
and native mutex with supplied task switches and kernel wakeup. Cases cover both
collision directions, split-byte writes, alarm start/dark/cancel/final restore,
recursive calls, allocation failure/restart, ABI/early returns and full frames.
The nominal cadence and all native tones stay exact in that supplied schedule.
[verify_lcd_package.py](verify_lcd_package.py) independently checks changed words,
relocations, startup allocation and unchanged artifacts.

The complete release suite remains required for firmware changes. Host checks
supply scheduling boundaries and do not establish arbitrary RTOS behavior,
physical dimming or LCD appearance. Device checks cover one Pomodoro completion
and one Clock alarm, normal timeout afterward, and sound with Alarm Backlight
disabled. Their current status belongs to
[Known issues](../docs/KNOWN_ISSUES.md); building does not flash a device.
