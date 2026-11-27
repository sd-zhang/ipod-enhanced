# 5.5G Clock, Pomodoro and alarms

Current implementation contracts for the interval timer, native Stopwatch
widgets, Main Menu customization and shared alarm/backlight work. Native-code
tests cover the ownership rules below; device scheduling and physical output
require separate hardware evidence. For controls, use the
[user guide](../../docs/USER_GUIDE.md); current validation and hardware reports
live in [Release status](../../docs/FIRMWARE_RELEASES.md) and
[Known issues](../../docs/KNOWN_ISSUES.md).

- [Clock and Pomodoro runtime](#clock-and-pomodoro-runtime)
- [Main Menu customization](#main-menu-customization)
- [Alarm and backlight ownership](#alarm-and-backlight-ownership)

Other 5.5G native services are in [Navigation and performance](README.md).

## Clock and Pomodoro runtime

All addresses below are from the
[pinned stock 25.1.3 OSOS](README.md#stock-interface-evidence).
RAM and MMIO addresses are explicit; no 4G addresses are reused.

The current presentation is **Extras → Pomodoro**, immediately below Stopwatch,
with a submenu arrow and a saved [Main Menu option](#main-menu-customization).
It uses the native Stopwatch backdrop, digit atlas and silver/blue buttons;
Clock's own navigation remains native. Focus lasts 25 minutes, short breaks
5 minutes and the break after four Focus rounds 15 minutes. Runtime state is
volatile; reboot starts stopped at Focus 25:00.

### RTC ownership

| Interface | Verified contract |
| --- | --- |
| `0xb0124(Date *out)` | Reads the 12-byte civil date, using the software calendar when native selector `0x120878` selects that route. |
| `0x88454(out, in, days, hours, minutes, seconds)` | Normalizes additions through days, leap days and year rollover. |
| `0xb3870(slot, Alarm *in)` | Copies a 20-byte record, enables it and reschedules; return 0 means success. |
| `0x9e850(slot, Alarm *out)` | Copies the full record; null output returns -4. |
| `0x906e0(slot)` | Cancels and reschedules; inactive slot returns -3. |
| `0xffee0()` | Selects the earliest enabled record. |
| `0x285460` | RTC worker; waits on signal `0x88`, retires due records, performs native wake when requested, signals a record's nonzero event, and reschedules. |
| `0xb3764(Date *)`, `0xbb118(zone, daylight)` | Native clock and timezone setters; wrappers preserve Pomodoro's remaining duration. |

`Date` is 12 bytes: year u16 at +0, month/day bytes +2/+3, zero-based
ordinal day u16 +4, weekday/zone bytes +6/+7, hour/minute/second +8..+10,
flags +11. `Alarm` is date +0, reserved u16 +12, enabled byte +14, wake
byte +15, event u32 +16. Original epoch is `0x10882298`; original records
begin at epoch+12 (`0x108822a4`). Calendar code `0x113470` derives this
adjacency directly. The selected-record state is `0x1081df8c`.

Stock setters accept slot 5, but stock iteration only visits five records.
Writing slot 5 corrupts the adjacent buffer at `0x10882308` and never
schedules it. A native regression checks this corruption boundary. The
extension relocates the **epoch and complete six-record table together**,
retaining original slots 0..4, and extends both initialization and scheduling
loops to six. No unreserved kernel event is used: Pomodoro has event 0 and
wake 1, with a background callback after native retirement.

Mutex `0x124` protects RTC records/runtime; `0x123` protects the civil
calendar. The wrappers preserve RTC → calendar lock order. Start/Pause and
Reset clear deferred alerts. Clock edits reconcile an overdue phase, retain
its remaining duration across the edit, and schedule a one-second worker
wake for a deferred alert. Catch-up skips complete 130-minute cycles and then
processes at most one cycle of transitions; it publishes at most one alert for
the elapsed boundaries. An RTC scheduling failure leaves the timer stopped.

Alert publication occurs under `0x124`, so Reset/Pause cannot fall between
the state decision and publication. Native RTC wake precedes the background
callback. The adapter publishes only tagged AsyncPiezo requests; see
[alarm lock and worker contracts](#alarm-and-backlight-ownership). A request already
published belongs to the native sound queue; Reset cancels future Pomodoro
boundaries and pending runtime alerts, without cancelling unrelated Clock
sounds.

### UI timer ownership

| Interface | Verified contract |
| --- | --- |
| `0x13d7c8(owner, timer, 0, queue)` | Binds a 40-byte timer. Queue 0 takes current task `0x101800()` + `0x1c`. |
| `0x151c24(timer, milliseconds)` | Sets interval. Pomodoro uses 1000 ms. |
| `0x151694(timer)` | Starts through native deadline list insertion `0xc6c50`. |
| `0x151c98(timer)` | Stops, waits for in-flight expiry cleanup when needed, and purges matching queued expiry only. |
| `0x1519ac(timer)` | Reports native UI running state. |
| `0x151934(timer)` | Publishes raw TIME event and marks UI state expired. |
| `0x1519e8` | Delivery worker calls publication then `0xd1e24(timer)` cleanup. |

Timer fields: next +0, interval ms +4, deadline us +8, kernel queue +12,
parameter +16, native reserved +20, kernel state +24, UI state +28, owner
+32, owner queue +36. States are `run ` / `stop` / `expi`. Kernel list head
is `0x10800004`, next-wake value `0x10800010`, scheduler mutex wrapper
`0x10800014`; the UI timer mutex wrapper is `0x1081e10c`.

The raw event begins `[TIME, timestamp, 0, timer]`; the native copier
`0x21f20c` copies seven words. Native `0x13cf8c` translates raw delivery
through `0x244e00` into the typed owner event: type 9 at +4, timer identity
at +8. The renderer hook consumes only that type and exact owned identity.
Leaving stops only the refresh timer; RTC continues. Closed, paused and reset
screens do not restart when stale delivery arrives.

Native publication returning false still reaches
cleanup, leaves the timer stopped, and no UI event exists to restart it.
The scoped replacement at worker `0x1519fc` preserves both original calls
for unrelated timers. For the owned timer it retains the actual publication
result, initializes all seven event words, completes cleanup, then retries
under the scheduler mutex only if visible, running, kernel STOP and UI
EXPIRED still match. This preserves a newer timer and avoids UI-object or
RTC-lock access on the delivery task. The replaced cleanup instruction at
`0x151a04` becomes NOP because the helper performs it exactly once.

### Stock widgets and scripts

The actual iPod Renderer vtable is `0x66fa6c` (constructor `0x1d0718`),
not the base renderer table `0x66cedc`. Three checked slots are replaced:

| Slot | Original | Extension |
| --- | --- | --- |
| `0x66fb34` (+c8) | `0x19e790(owner, selection)` | Private negative commands; ordinary selections fall through. |
| `0x66fac8` (+5c) | `0x1d021c(owner, tag, key, out)` | Owned strings and button bitmaps; everything else falls through. |
| `0x66fa9c` (+30) | `0x19f2e0(owner, event)` | Exact owned timer and button-direction events; all other events fall through. |

Renderer resource provider is `0x5e00`, native resource lookup `0x21f3dc`,
property lookup `0x21f254`, notification `0x21f37c`. Ordinary stock property
keys outside the owned `0x7122`..`0x7128` range delegate before the Pomodoro
checks. With supplied provider boundaries, dispatch takes 12 ARM instructions
instead of 21–32 in the unoptimized wrapper fixture; native arguments, return
values and output pointers are preserved for standard, unknown and signed resource IDs. This is wrapper cost,
not overall rendering speed. `test_pomodoro_ui.py` checks that ABI and instruction
budget alongside the live widget and timer regressions.

String tag is mixed-case
`0x53747220` (`Str `). The screen reuses native Stopwatch widgets from View
`0x568d` and button group View `0x568e`, with Pomodoro-owned property keys.
It uses the standard title header `0x5fba`, leaving a 320×216 content area.
Renderer selector `0x9d` switches sibling visibility; it does not assign focus.

Native class registration `0x170874` precedes widget factory
`0x21a4f4(provider,parent,recordWithoutSize,0x1d2310,0)`. Native geometry
`0x21a5e4` resolves the inset panel to (top8,left8)-(bottom174,right312),
phase label to (26,16)-(50,304), digits to (68,86)-(128,234), and controls
to (182,0)-(214,320). Buttons occupy x53..150 and x168..265, each 97×32.
The phase and buttons retain stock text styles, including the centered 18px
button font. Generic Draw class `0x5b80`, resource `0x5fe9`, delegates the grey
textured backdrop to Renderer `0x1d021c` and native graphics `0x18fb5c`.
Panel resources are `0x56b8`/`0x56b9`/`0x56ba`, retaining native horizontal
repeat on the middle strip. Silver/blue buttons are `0x56bb`/`0x56be`.

The countdown uses native digit class `0x5c80`, factory `0x1abb5c`, glyph
strip `0x5f51` and size60 (record+0x54=`0x003c5f51`). Its property updater
`0x1abcc4` requests `Str 0x7123` and copies the returned MM:SS into its own
string. The digit record's notification tag at +8 is explicitly changed from
stock `None` to `Str `: its generic event handler compares that field even
though the getter always requests Str. Leaving the tag as `None` prevents
countdown notifications from reaching the widget. A real `0x21f37c` subscription
and delivery regression checks entry, ticks, pause/reentry, Reset, phase
transition and live button bitmap updates. The adapter needs six bytes of
temporary text. Each query takes a current RTC snapshot, including while paused
or on reentry. Native draw `0x1abba0`
uses table `0x500cde`: four 33px digits and a 16px colon, width148 total.
The wrapper uses the stock white color key and restores prior drawing state.
The font, glyph widths and drawing contract are those of the native Stopwatch.

Generic button class `0x5b00` has destination `0x7121` at full resource+0x60
(object+0xa4). Native enclosing-screen enable `0x17de1c` and activation
`0x19d024` select it at Renderer+0x28. Native lookup `0x19d3b0` finds it by
key if focus has moved before the leave callback. No widget pointer is retained
across screen changes. Each entry resets selection to Start/Pause.

The generic group does not automatically publish to Renderer. Entry subscribes
through `0x149f28(group,owner)` only if `0x149ec4` reports no membership.
Leave removes only that observer through `0x14a178`, guarded by membership.
Removing the final observer clears the group list and its global registry entry.
Native key handler `0x16cc54` maps `0x57`/`0x58` to signed -1/+1; native
wheel handler `0x16cd4c` uses quantizer `0x1c252c`. Center remains a script.

The group publishes a typed16 event: event+8 points to payload
`[vtable0x66eb94,destination0x7121,size4,dataPointer]`. The hook consumes
the signed delta only during the callback, while visible and active; -1
selects Start/Pause and +1 selects Reset. Selection clamps and only changes
notify the two BMap properties. Foreign destinations fall through; malformed
owned, closed and unfocused messages cannot change selection. Native publisher
`0x14a018` owns event destruction. No event or payload pointer is retained.

Owned resources are View `0x7120`/`0x7121`, strings `0x7122`..`0x7125`,
and button bitmap aliases `0x7127`/`0x7128`. Key `0x7126` remains reserved
for the Main Menu customization caption. Explicit UI atoms are 5111..5116.
Root records are returned separately for package composition. Extras Menu
`0x5f75` (stock `0x4af138`) inserts Pomodoro immediately below Stopwatch
(key `0x5f85`). Every original row, conditional metadata and relative order
remain byte-for-byte stock.
Its stock hierarchy is root View `0x5f71` → list View `0xfffffedf` → menu
`0x5f75`; stock scripts 4078/4079 open Extras. The new type-`0x26` row uses
caption/key `0x7120`, action atom 5111 and Stopwatch's secondary bitmap/provider
pair (`0x5ecc` / resource-file provider `8`).
Native widget construction, caption getter `0x1bf740` and action getter
`0x254264` resolve the row to Pomodoro and its opening script. Native secondary
bitmap getter `0x1bf6d8` tests flag `2` at item +4, reads the signed bitmap key
from record +28, and resolves it through the provider selected by record +30.
The returned BMap `0x5ecc` is the stock 8×11 chevron. Both the type flag and
bitmap/provider pair are required: a row with type `0x24`, zero bitmap key or
provider sentinel `0x7a01` cannot resolve the indicator. The regression test
runs stock Stopwatch and Pomodoro through the actual Extras hierarchy and these native getters, supplying only resource-file
registry/lookup and existing OS boundaries. Other Extras rows need additional
device controllers and are checked for exact preservation separately.
Native Clock Menu `0x6181` and selected-clock Menu `0x618e` retain their exact
stock payloads. Screen scripts use
native generic bindings 4012 and music Play binding 4073. After those bindings,
short Center `(3,0x6f)` is explicitly replaced with SELECT5116, which runs the
selected button's action. Menu `(3,0x6e)` remains native3973 and Play remains
4220. Native VM tests confirm the final binding order, both leave callbacks,
and selected Start/Reset/Leave execution.

### Checked binary edits

`pomodoro_patch.py` verifies each original word before editing:

- Epoch literals: `a22ec a7a28 b01d8 b3864 bb21c 1135d8`.
- Table literals: `90730 9e890 a22f4 b38e0 fff38 1020d0 285580`.
- Count instructions: `fff28` and `a229c`, five → six.
- Boot callback `a22a8`, background callback `28557c`, clock setter `b3764`,
  timezone setter `bb118`. Assembly trampolines retain displaced instructions.

`pomodoro_ui.py` verifies the three vtable slots and two worker instructions
above. Common `layout.py` owns code/data placement and resource-directory
extension; this feature does not choose another memory arena.

The combined builder gathers all feature resource edits before emitting one
replacement directory per tag and one shared root View. This removes obsolete
intermediate copies while preserving all live resource payloads and the stock
bytes at their original locations. Package tests require every appended resource
byte to be reachable, allowing only alignment padding.

### Reproducible evidence

Run with the existing `.venv` and Unicorn JIT permission:

```sh
.venv/bin/python targets/ipod55g/research_clock.py
.venv/bin/python targets/ipod55g/test_pomodoro_native.py
.venv/bin/python targets/ipod55g/test_pomodoro_ui.py
```

The native suites cover RTC bounds/corruption, calendar rollover, preservation
of stock slots, cancellation, four-round phases, long catch-up, clock/timezone
edits, deferred-alert cancellation, Reset interleaving, native wake ordering,
widget construction/geometry/focus, native digit strings/crops, button selection
and observer lifecycle, real script VM
bindings, stale refresh and rejected/successful/unrelated timer publication.
These suites exercise the native Extras hierarchy, caption/action/chevron
getters, property delivery and observer lifecycle. They preserve the Clock
menus and unrelated Extras rows exactly. Supplied boundaries include kernel,
allocation, resource-file registry/lookup, queue and MMIO operations; these
checks do not prove full display rendering, device scheduling, suspend or boot.

Native widget traces and geometry evidence live in
`build/ipod55g-pomodoro-style-ui-01/` and
`build/ipod55g-pomodoro-style-controls-01/`. The asset-based layout study at
`build/ipod55g-pomodoro-visual-01/preview.html` is an approximation, not a
firmware screenshot. Current hardware evidence belongs to
[release status](../../docs/FIRMWARE_RELEASES.md).

## Main Menu customization

Pomodoro can be pinned below Stopwatch using the native Settings → Main Menu
row. The pin is saved in the existing preference mask, defaults Off and is
removed by Main Menu reset without changing Alarm Backlight.

### Native consumers

Main Menu `Menu 0x5e1e` has 31 stock records; Settings → Main
Menu `Menu 0x6018` has 28. The stock Stopwatch records are indexes 16 and 17,
respectively. The customization Extras heading precedes Clock and Stopwatch.

Adding records and a preference bit alone is insufficient. Stock script 3996
explicitly enumerates Main Menu keys. For each key it calls 3995, which invokes
Settings selector `0x1b3`, native `0x193470`. Scripts 3994/3993 pass that Boolean
to renderer selector `0xbe`, native `0x19ed58` → `0x19f058`. That consumer
changes native item visibility and refreshes its filtered model. Native
`0x21845c` counts visible items; `menu+0xb8` is not the filtered count.

The key-to-bit mapper at `0x105a04` has exactly two direct branch callers in
the stock executable region: `0x193488` (get) and `0x19353c` (set), and no
aligned function-pointer literals in the image. The get function also has
the Settings VM binding at `0x4fd118`. All of these paths converge on the
patched calls. The original mapper, its holes, and all stock key behavior are
preserved. The native keys use mask `0x1fffbfff`; existing Alarm Backlight owns
bit29 (`0x20000000`). Pomodoro uses bit30 (`0x40000000`).

### Integration contract

`build.py` includes `pomodoro_home.c` in the extension and calls
`pomodoro_home.apply` after `alarm_settings.apply(image, symbols)`.
It merges `resource_updates(image)` into the common resource batch and
`script_updates(image)` into the common script batch before emitting TCmd.
This requires no assembly, additional startup hook, separate preference file
or new global state.

| Original site | Checked existing value | Replacement |
| --- | --- | --- |
| `0x193488` | `0xebfdc95d` | BL `pomo_home_mask` |
| `0x19353c` | `0xebfdc930` | BL `pomo_home_mask` |
| `0x66c94c` | linked `alarm_setting_get` | `pomo_home_setting_get` |
| `0x66c950` | linked `alarm_setting_set` | `pomo_home_setting_set` |

The two wrappers recognize only key `0x7120`; every other setting chains to
the existing alarm wrapper. Pomodoro writes use native `0x19351c` → mask
setter `0xb34ac`, including native dirty tracking. On, Off and Cycle use the
same native property identifiers as other customization rows.

Resource allowlist additions are `Menu 0x5e1e`, `Menu 0x6018`, and `Str 0x7126`.
The plain `0x7120` Pomodoro caption and OPEN atom 5111 are supplied by the
Pomodoro UI module. `0x7126` is the indented `\u2003\u2060Pomodoro` caption.
Both new records clone their own menu's Stopwatch record and are inserted
immediately below it. Main Menu keeps its stock submenu bitmap/provider and
uses action `5111*4+2`; customization keeps native Cycle action `4320*4+2`,
Settings provider `0x6000`, and property key `0x7120`. All stock rows remain
byte-for-byte unchanged and in their original order.

TCmd allowlist addition is existing function 3996. Its complete original
record SHA-256 is
`b901c43fd9ec78fb95f50ea52c87e19f3a3cb52db0b7ff4e1c2b7cec027d0f7e`.
`script_updates` inserts `3995(0x7120)` immediately after `3995(0x5f85)`,
increases the single outer conditional's displacement by the 11 inserted bytes
(`0x146` → `0x151`), preserves the native header/stack size and trailer, and
updates the even code length. The outer guard still skips work outside Main Menu.

### Persistence and reset

Native preferences store the Main Menu mask at `preferences+0xb34`, serialized
at file offset `0xb2c`. Native defaults `0x0e180201` leave Pomodoro unpinned.
The existing Alarm Backlight Main Menu reset wrapper restores those defaults
while preserving only bit29, so it also removes the Pomodoro pin. Native full
reset restores defaults for both settings. No additional reset patch is used.

### Verification

`test_pomodoro_home.PomodoroHome` runs against either a freshly linked fixture
or an exact packaged image supplied by the release verifier. The packaged
test class needs only `image`, `stock`, and `symbols` attributes.

The tests execute the actual native customization widget, secondary text
getter and selected-row script4320; the complete visibility loop3996 and
native Main Menu item/model filtering; On/Off/Cycle property dispatch; native
save/reload and supported loader migrations (`0x14`, `0x15`, `0x16`, `0x38`,
`0x39`); Main Menu reset and full reset; and every native mask key including
holes. They check emitted order, unchanged stock rows, OPEN action and native
submenu bitmap resolution, pin/default/reset behavior, Alarm Backlight
preservation, and unrelated serialized preference data.

The widget fixture supplies resource registry/provider lookups, allocation,
current-screen state, and external event publication. Four unrelated stock
device-query scripts (3997, 3998, 3999, 4001) return true in that fixture;
the shipped 3996 and its native visibility helpers execute unchanged. No
mock implements pin state, row filtering, settings selection, or persistence.

The visibility regression is essential: correct row records and persistence
alone leave the item visible while Off unless script 3996 enumerates its key.
These checks do not access an iPod.

## Alarm and backlight ownership

The extension preserves the native alarm tones while requesting four nominal
350/250 ms light/dark pairs through BacklightTask. The sound worker never waits
on the backlight fade mutex, and completion restores the latest native output
policy. Physical cadence and restoration require the device checks below.

`research_alarm.py` pins the native signatures and characterizes the stock
workers. `alarm_flash.c`, `alarm_hooks.S` and `alarm_patch.py` implement the
extension; `alarm_settings.c` and `alarm_settings.py` own its default-On
persistent preference.

### Stock sound and ownership

| Contract | Exact native evidence |
| --- | --- |
| Full alarm pattern | `0x91438`: four pairs `(period 180, duration 200000)` then `(period 144, duration 400000)`. Total nominal duration 2.4 seconds; all eight requests are attempted even when earlier queue submissions fail. |
| Clock entry points | Conditional tail branch `0x238070` and conditional call `0x2381d0` target `0x91438`. The other caller, `0x28449c`, belongs to WatchdogTask; globally altering the shared pattern would also alter that warning. |
| Queue submission | `0xcd430(period, microseconds)` constructs a 32-byte stack message. Opcode byte `+0x10=0`, period `+0x14`, duration `+0x18`, result `+0x1c`. It holds native mutex `0x12c` over synchronous `0xa6770(queue 0x15, message, 5, acknowledgment 0x96)`, then unlocks and returns result. |
| Queue consumer | AsyncPiezo `0x285060`, registered at `0x25d544..0x25d55c`. RAM `0x1081dfb0` contains head, tail, active and deadline. Periods are at `0x10882314`; durations at `0x10882354`. Sixteen physical slots provide fifteen usable entries; full returns result 1 and does not advance head. |
| Sound worker | `0xc719c` runs from task call `0x285174`. It checks the signed difference between native microsecond counter `0x282860()` and deadline, consumes a tone, updates deadline, stops the previous tone, programs the new buzzer and timer, and advances tail. |
| Actual hardware ownership | The worker itself writes buzzer `0x7000a000` and timer `0x60005008`; these were obtained from this image's literal pools. New application code should not take over these registers independently. |
| Drain | At `0xc71d4`, native stop `0x11c750` zeros the buzzer and clears timer enable, then worker clears active. |
| IRQ | `0x27d8a0` reads timer acknowledgment `0x6000500c` and returns signal `0x95`. It does not change the buzzer. This permits an extra worker wake within a tone without changing its waveform. |
| Cancellation caveat | Opcode 1 at `0x2850e4..0x285100` sets head=tail and active=0. It does **not** execute the hardware-stop service. A later worker call returns immediately when inactive. The characterization preserves this behavior; it is not evidence that cancelling a sound physically stops it. No public caller of this message form has yet been identified. |

The stock request's stack message remains owned by its caller until native
acknowledgment; do not retain its pointer for visual state. Flash state belongs
to the receiving task. Rejected requests never reach the worker and cannot
start a cue; cancellation, untagged tones and final drain invalidate the visual
state. The stock queue's rejection behavior is retained.

The implemented 350/250 ms cue uses the unchanged 200 ms first tone plus the
first 150 ms of the 400 ms second tone as the lit interval. An additional timer
wake after those 150 ms can request darkness, then re-arm only the remaining
part of the original sound deadline. A delayed wake must not extend the tone
or replay a missed transition. Counter wrap already has signed-difference
handling in the original worker. Native execution verifies the split timer,
unchanged buzzer writes and original final deadline, including wrap, late
callbacks and disabling the setting between the extra wake and tone retirement.
The latter must re-arm the remaining sound time even after cancelling light.

### Color backlight and the timing obstacle

Native public output `0x2831b4(on)` routes by hardware model. For runtime model
families `0xb`/`0xc`, it sends the cached brightness through
`0x266300 → 0x281420`; this stores requested brightness and signals a native
transition event. The tests supply both family codes; they do not establish
the physical device's board-code value.

BacklightTask is `0x28453c`. Its state at RAM `0x1081ff40` is:

| Offset | Meaning |
| --- | --- |
| `+0`, byte | 0 off, 1 steady on, 2 fading down, 3 fading up |
| `+4`, word | Requested brightness |
| `+8`, word | Current native fade brightness |
| `+12`, word | Owned native timer identifier |

Its zero-terminated wait list at `0x4fc0a8` is `0xa6, 0xa7, 0xa8, 0xa9, 0xaa`.
It uses brightness service `0x26617c`, power service `0x266288`, timer scheduler
`0xa67e0` and timer cancellation `0xa6814`. The fade-capable path takes mutex
`0x136` and **keeps it across timer waits**, only unlocking when it reaches a
steady state. Before taking it on a subsequent event, it compares lock owner
`0xa63e0(0x136)` with current task `0xa6420()`.

Normal output Off uses event `0xa8`. From brightness 31, the native fade-down
schedule sums to **300 ms before power Off**, including 63 ms at brightness
zero. Brightness zero is reached after 237 ms of scheduled delays. Thus an
ordinary native Off request cannot establish the requested 250 ms dark period.
Event `0xa7` is immediate Off only from steady-on state 1; from fading-up state
3 it changes fade direction instead. It is not an unconditional pulse API.

Acquiring mutex `0x136` from the piezo worker can block that sound worker through
a fade. That would delay tone retirement and violate unchanged stock sound.
Do not make that substitution for a 4G LCD lock.

### Owner task integration

The piezo worker writes only the latest desired phase (restore/lit/dark) and
signals BacklightTask. It never takes mutex `0x136`. The light task consumes the
private event and applies native power/brightness services while retaining any
mutex ownership it already holds across a fade. Other stock backlight events
continue through the original state machine. Pending phase changes coalesce;
delayed handlers apply the newest state, without replaying obsolete flashes.

The private event is **owned signal `0x188`**, obtained by relocating and
extending the complete native signal table, not by stealing an existing ID:

| Contract | Evidence |
| --- | --- |
| Original table | RAM `0x108b777c`, 392 words (indices 0..391), ending exactly at next referenced global `0x108b7d9c`. |
| Initialization | `0x2812dc` loads the first-entry pointer from `0x281334` and the inclusive maximum ID from constant `0x4f8904=0x187`, then initializes entries 1..391 to -1. |
| Relocation sites | Four base-pointer literals `0xa641c`, `0x27e948`, `0x2804dc`, `0x286084`, plus first-entry pointer `0x281334`. The patcher checks the complete inventory of aligned pointers into this array. |
| Extended table | 393 owned zero-filled words; native initializer's maximum becomes `0x188`. Native indices are retained unchanged. |
| Signal / wait | `0xa67b0` → syscall 2; `0xa6958` → syscall `0x22`, native wait block `0x27f834`. Table values: -1 idle, 0 pending, positive waiter task. Native signal `0x286034` and wait-block execution verify coalescence and restoration of every visited event entry. |

Normal driver requests are retained while the pulse temporarily controls
hardware output. Finish/cancel restores the **latest** stock brightness and
power requests, including native policy changes that precede delivery of the
first cue. A generation check retries restoration if another stock request
interleaves with it. It retries at most three times per private event, then
queues another owner-task event instead of spinning indefinitely.

Generation checks alone miss an output already in flight: native display
brightness setter `0x164428 → 0x283230 → 0x26614c` reaches the power and
brightness hooks without mutex `0x136`. A hook can compute a dark-phase override,
then finish its physical write after BacklightTask has restored stock policy.
Each native output completion requests owner-task repair when it overlaps
a projection. The completion only signals; it never changes the current phase.
A projection epoch, written only by BacklightTask, also detects an entire cue
starting and ending during one native call. Repair-only intervals take the same
ownership until stable restoration, so writers can detect them and three failed
attempts still queue another event. The owner publishes active ownership before
the epoch; wrappers read epoch before ownership, closing the interval between
those two stores. No caller gains a new lock wait.

Repair producers only store a pending flag. The owner clears an observed flag
before reading policy and applying output; a zero read never clears a later
completion. Repair can restore stock policy even after `projected` was cleared.
This handles completed writes independently of the generation counter's
non-atomic increments. Fallback power/brightness belong only to the owner;
native writers alone update their separate policy values and presence flags.
This prevents first-capture preemption from overwriting a native request. That
fallback state is tested, but its device startup reachability is unproven.
Native regressions suspend the actual display setter
at both physical-output boundaries, including a newer phase, newer brightness
and a complete intervening cue. Scheduling and physical GPIO remain supplied
boundaries; the tests establish the repair contract, not device frequency.

The native LCD-enabled byte at `0x1081e028` guards the cue. Native service
`0xb7c64` changes that byte and invokes the display object's power methods;
RTC wake `0xa77e8(1)` uses that path. The native RTC task performs this
wake for the Pomodoro alarm record before invoking the background hook.
`ipod55g_alarm_signal` therefore only publishes the eight tagged tones;
it performs no additional UI/wake call. Pomodoro holds RTC mutex `0x124`
through publication, serializing Pause/Reset against alert dispatch.
The native `0xcd430` request takes mutex `0x12c`; AsyncPiezo acknowledges
without taking RTC/calendar mutexes or waiting for the light worker. Hardware sleep/wake races still require device evidence.

The existing `0xa8` event was tested in all four states and cannot be reused as
an idempotent wake: steady On with current brightness equal to requested
brightness turns Off, and fade Up reverses direction. The dedicated event
avoids those changes to native policy.

### Persistent setting

Settings uses a native cycling row with property/resource key `0x7140`, named
**Alarm Backlight**. The disabled flag is bit 29 (`0x20000000`) of the native
preference record's main-menu mask at `+0xb34`. This is independently mapped
5.5G storage; bit 24 used by the older 4G extension is a native Podcasts bit
here and is not available.

The native used-bit mapping is `0x1fffbfff`; native load upgrades OR defaults
only into `0x1fffffff`, preserving bit 29. The native setter `0xb34ac` marks the
record dirty through `0xa983c`. The separate settings tests execute getter,
setter, native loading/migrations and resets: default On, native save/load
storage, unrelated-bit preservation, Main Menu reset preserving the alarm
choice and full reset returning On. Turning Off calls
`ipod55g_alarm_setting_changed()` to request visual restoration and leaves the
buzzer requests intact. See `test_alarm_settings_native.py` for that evidence;
the alarm-extension tests supply the preference getter as an explicit boundary.

### Evidence still required

The extension tests execute original queue/worker/IRQ and kernel signal-table
instructions, plus the compiled new hooks. The native BacklightTask fade
characterization runs separately. Physical power/brightness calls in the
extension fixture are recorded at the native-driver trampoline boundary.
Tests do not execute the complete OS scheduler, GPIO controller or actual LCD.

Device checks must establish four nominal 350/250 ms flashes and full buzzer
sound, including Hold, sleep/wake, an already-dark display, brightness changes,
Settings Off during a cue, playback and ordinary input during/after alerts.
Scheduler and physical transition delays can shift the observed cadence; the
implementation does not prolong a beep to manufacture a missed dark interval.

### Reproduction

```sh
.venv/bin/python targets/ipod55g/research_alarm.py
.venv/bin/python targets/ipod55g/research_alarm.py --native \
  --output build/ipod55g-alarm-trace/native-05.json
.venv/bin/python targets/ipod55g/test_alarm_native.py --output \
  build/ipod55g-alarm-trace/patched-05
```

The native command requires a JIT-capable execution environment. It runs stock
ARM with synthetic OS locks, queue acknowledgment, timers and MMIO registers;
it is not a full scheduler or device emulator. The queue tests execute the
consumer block before its normal worker call; the worker is then exercised
separately at controlled counter values. The backlight tests execute the
fade-capable native event dispatch and supply its hardware-call boundary.

The `--candidate path.ipsw` option verifies archive/OSOS hashes against its build
manifest and uses packaged symbols and the real preference cache. The extension
suite covers in-flight output, complete intervening cues, repair capture and
ownership publication as well as nominal alarm behavior. Exact stock signatures
and checked patch-site bytes live in `research_alarm.py` and `alarm_patch.py`.
