# 4G navigation and performance

Engineering reference for navigation, list rendering and performance in the
monochrome click-wheel 4G firmware. Addresses and native contracts are specific
to RetailOS 3.1.1. [Controls](../docs/USER_GUIDE.md) ·
[Build](../docs/BUILDING.md) · [Release status](../docs/FIRMWARE_RELEASES.md)

- [Artists compilation filtering](#artists-compilation-filtering)
- [Hold Menu cleanup](#hold-menu-cleanup)
- [Now Playing actions](#now-playing-actions-and-bytecode-packing)
- [On-The-Go ownership](#on-the-go-ownership-and-failure-behavior)
- [Volume Limit](#volume-limit-storage-and-audio-boundary)
- [Sleep Timer](#sleep-timer-row-contract)
- [Disc headings and numeric scrolling](#disc-headings-and-numeric-scrolling)
- [Queue and script performance](#queue-and-script-performance)
- [Wheel delivery and Albums indexing](#wheel-delivery-and-albums-indexing)
- [Alphabet navigation](#alphabet-navigation)
- [Highlighted-name scrolling](#highlighted-name-scrolling)

Keep the pinned `build/` inputs and source paths required by the combined builder.

## Artists compilation filtering

The Artists index always excludes artists whose only matching tracks are tagged
as compilations. An artist with an ordinary matching track remains visible;
selecting that artist still includes compilation tracks. Other browse indexes
and the general playback predicate keep compilation content. There is no
Compilations setting for this 4G addition.

The native path is Artists script 2107 → library selector `0xfc` (`0xe0498`)
→ count `0x7157c` → index builder `0x4a1d0`, with artist mask 4. Composers uses
script 2082. The importer at `0xb698c..0xb69a0` already maps `mhit+0x1e` to
native track byte `+0x11 & 2`. The hook at `0x4a350` checks that bit only after
the existing query predicate accepts a track and before deduplication commits
its name. Rejecting earlier through the shared predicate would hide playable
compilation tracks; rejecting after name commit could suppress a later ordinary
track by the same artist.

Query `+0xa8c/+0xa90` owns the artist cache/count. Display getter `0x6e60c` and
selection setter `0xc5594` consume that same index. The native All row remains
usable in an all-compilation library: the guard at `0xc5640`, inside UI selector
`0xc560c`, accepts row zero when the artist cache exists even if its count is
zero. Empty underlying playlists, allocation failures and invalid rows retain
native rejection. A nonempty query without named artists can also open All;
other query filters remain effective.

`patch.py` owns the checked edits. `verify_native.py` executes the native index,
UI selector, invalidation and allocation-failure paths, including both track
orders for mixed artists and all-compilation libraries. Its storage, allocator,
locks and display services are supplied boundaries.

## Hold Menu cleanup

Hold Menu uses the native history unwind, with animation disabled around that
operation. Script 2120 at `0x1bb16a`, bound to `(event 2, key 0x6e)`, calls
`1868(1855)` and returns true. Wrapper 1868 restores the previous animation setting. Home helper
1855 unwinds to home command 1882 or calls constructor 1875 when home is absent.
Ordinary Menu 1860 still pops one level; Backlight 2122 stays native. Special
screen scripts 2004/2007 continue removing and restoring the hold binding.

The unwind retains native lifecycle callbacks and node destruction; it is not
a direct replacement of the visible screen. Native unwind/pop are
`0xaad88/0xaab6c`, and history construction/destruction are `0xeaf74/0xfc520`.
Recording entry 2360 installs leave callback 2357, whose native dispatch
`0x91404 → 0x9cf04` reaches recorder-stop selector `0x19c` before home opens.
Playback entry 2011 installs `2020 → 2019 →` library selector `0x114`
(`0x97370`); that path commits an old enough pending seek through `0x2dfe4`,
submits resume when needed and clears seek-mode flags.

`verify_native.py` executes these leave callbacks, the VM and native history
destruction. Recorder hardware, audio-command submission, final home widgets,
graphics and physical key timing remain fixture boundaries.

## Now Playing actions and bytecode packing

Hold Center opens the actions menu; its pending release restores normal Center
selection without choosing a row. A subsequent Center press activates an
action. Entry calls stock `2030(false)` before history captures key bindings,
so returning from seek, rating or the alternate panel restores normal controls.
The original suspension path still commits pending seeking exactly once.

Browse Album and Browse Artist validate current metadata before navigation,
unwind old library screens through native cleanup, reconstruct
Home → Now Playing → Actions, then reread metadata before applying the filter.
This prevents old screens from referring to the replaced shared browse query.
Menu from the new branch returns through Actions and Now Playing; deeper artist
browsing retains its additional levels. Missing metadata keeps Actions open,
including metadata that disappears during cleanup.

The helpers copy at most 255 UTF-16 code units from UI `+0x628` (album) or
`+0x428` (artist). Native `0xc6724` clears browse indexes and predicates without
replacing the playing queue; filters live at query `+0x670/+0x270`. Explicit
album ordering through `0xe2dac(ui,3)` prevents inheritance of Podcast ordering.
Artist shortcuts include compilation tracks. Grouping uses stock names, so
identically named albums retain stock grouping. Fixed English headings avoid
changing a browse title when the playing track changes. Helpers retain no
metadata pointers, timer or writable static state.

Shuffle and Repeat use stock immediate-cycle command 2155 and the native
Settings value providers; Repeat is property `0x27b9`, with Off/One/All. Repeat
does not reconstruct a playback queue. The retained `repeat_album` helper is
unreachable from the current menu; some former Repeat script/resource slots
are now used by On-The-Go. Do not interpret preserved helper code or historical
Repeat tests as evidence of an additional user-visible Album mode.

When rebuilding TCmd, declared bytecode lengths must include padding to an even
length. The native loader at `0x68efc/0x68f00` reads a halfword extension count
immediately after the declared code; aligning only the following function does
not prevent an ARM7 unaligned halfword read. `actions_patch.py` and downstream
script builders preserve this contract. The native playback fixture rejects
odd halfword accesses that Unicorn otherwise permits.

Current sources are `actions.c`, `actions_patch.py`, `native_controls_patch.py`
and `otg_patch.py`. `verify_actions.py`, `verify_actions_playback.py`,
`verify_actions_downstream.py`, `verify_actions_widgets.py`,
`verify_actions_shuffle.py` and `verify_repeat_toggle.py` cover native history,
key snapshots, scripts, selection, widget construction and setting dispatch.
Shuffle checks establish queue flags and reorder dispatch; their supplied
database-reorder service does not establish audible shuffle order.

## On-The-Go ownership and failure behavior

The action follows the actual playing queue, independently of the last browse
screen. Query `+0x44` owns that queue and `+0x48` is the On-The-Go playlist;
audio `+0x2a4/+0x2a8` identifies its queue and exact playing entry. Remove is
offered only when the playing queue is that same On-The-Go object. Entry
identity distinguishes duplicate copies of one song. Every invocation rereads
the live context; a stale Add/Remove command fails instead of reversing its
meaning after a queue change.

Add acquires the native lock at query `+0xa70`, revalidates context after locking,
checks the 1000-entry limit and calls native append `0x47ea4`. Remove calls
`0x515e0`, which sends `dpli` before unlink/free. The audio observer stops
playback and detaches the current entry before it is freed; never dereference
that entry afterward. Native reindex/query refresh completes removal, and the
preferences dirty byte `+0xb46` is set only on success. No item pointer is kept
between commands.

Success returns one level to Now Playing. Missing/incoherent context, a full
playlist, failed allocation or stale action keeps the menu open with
**On-The-Go Unavailable**. `otg.c` and `otg_patch.py` own this contract;
`verify_otg.py` executes native append, deletion notification, audio detachment,
Center dispatch and history. Disk persistence, decoder teardown, physical input
and final UI delivery remain supplied or hardware boundaries.

## Volume Limit storage and audio boundary

The Volume Limit slider owns its draft. Wheel movement performs no disk access;
Center saves, Menu cancels, and Hold Menu cancels while returning home. Full
range removes the user cap while retaining the native regional ceiling. Raising
the cap never raises current output. Reset All Settings leaves the separate
saved cap intact; full range explicitly removes it. No PIN is provided.

Runtime preferences `+0xb48` holds the effective ceiling. Two alternating
20-byte files, `iPod_Control/Device/VolumeLimit0` and `VolumeLimit1`, store five
32-bit words: magic `0x564c4d31`, generation, generation complement, cap and cap
complement. Caps are 0–256; 256 represents no user cap. Generation comparison
handles wrap. Boot can use the other valid record after a read failure, but a
save refuses to overwrite either unreadable slot. The newest valid record is
retained while the other slot is written. Stock Preferences serialization is
unchanged.

Reads, byte counts, explicit flush and close are checked. The lower read-open
service `0xa33d4` distinguishes absent files (`-12`) from errors that wrapper
`0x93974` would collapse. Write wrapper `0x93c50` can report short writes as
success and hide automatic-flush errors, so the helper checks count and calls
flush `0xa2a34` itself. Failure leaves **Couldn't Set Limit** on the open screen.
Flush/close failure can occur after bytes reached disk; disk rollback is not
promised. Backend failure after a successful save may already persist the new
cap, and Center retries applying an unchanged cap.

The headphone limiter runs at final codec boundary `0x14fae0`, after song gain,
so queued volume commands and codec restores cannot bypass the current cap.
Mode 2 scales the two native pan values to the ceiling, within one native
rounding step; the peak channel's quotient is exactly the ceiling, so only the
quieter channel needs division. Mode 3 line-out retains native handling.
Valid-preferences headphone requests refresh both registers even if a channel
appears unchanged: native cache `0x14fc68` compares only the left channel and
updates it before I/O succeeds. Bypassing that cache preserves right-channel
changes and failed-write retries without mutating the shared force flag.

The preferences owner clears its `+0x124` pointer before registry publication;
early startup or destruction uses native codec behavior until a valid owner
exists. Decoder, queue and sample processing are unchanged. The screen copies
Contrast's read-only property `0x27b3` for the native chevron and uses stock
speaker bitmaps with its independently owned slider.

Sources: `volume.c`, `volume_output.c`, `volume_hooks.S`, `volume_patch.py`,
`native_controls_patch.py` and `settings_order_patch.py`. `verify_volume.py`,
`verify_volume_output.py`, `verify_volume_ui.py` and `verify_hotpaths.py` cover
record corruption/I/O failures, generation wrap, draft save/cancel, both-channel
failure/retry, owner publication, codec restore and all cap values. Physical
loudness, real filesystem failure timing and full-device scheduling are outside
these native fixtures.

## Sleep Timer row contract

The actions menu's Sleep Timer row clones the native Clock menu `0x4581` row,
preserving command 2160, property/group `0x27d9/0x2780`, submenu and value
metadata. Only its Main Menu customization flag and shortcut icon are removed.
The native choices are Off/15/30/60/90/120 minutes. Choosing one returns to
Actions; Menu cancels one level. Reopening preselects the saved duration without
restarting or canceling the running timer.

`sleep_patch.py` changes resources only. `verify_sleep.py` executes Center event
construction, VM, native settings and history; it checks all six durations
(Off passes native sentinel 65535), including reopen and cancellation. Generic
submenu rendering, OS event delivery and actual timer expiry remain supplied
or hardware boundaries.

## Disc headings and numeric scrolling

An album's Songs list displays a nonselectable `Disc N` row before each disc
run, using Espy Sans 9 bold with one-pixel rules beside the centered label.
Song text, controls and the native 18-pixel row height are retained. Fast wheel
input jumps between disc starts and displays the disc number. Disc jumps align
the heading at the top where possible, retain Apple's bottom limit, and leave
the chosen disc's first song selected after release.

### Metadata and scope

The stock iTunesDB importer at `0xb697c..0xb6988` copies `mhit` disc number
and disc total (`+0x5c/+0x60`) into native track fields `+0x88/+0x8a`.
They are unsigned 16-bit values. No database or sync-format change is needed;
the library must already contain the disc tags.

Headings apply only to the native Songs list, resource `0x3301`, with an
explicit album filter at query `+0x670`. Composer is `+0x470`; those filters
are not interchangeable. All-songs and other list resources remain native.

- Disc 1 of 1 stays unchanged. A disc number or total greater than one, or
  multiple contiguous disc runs, enables headings.
- A partial album containing only Disc 2 still shows `Disc 2`.
- A missing disc number on any song disables headings for that list.
- Disc numbers need not be consecutive. Existing song order is preserved,
  including repeated or nonmonotonic disc runs.
- More than 256 runs falls back to the original song list.

The literal heading is English, matching the current addition's scope.
Hardware status is tracked in [Known issues](../docs/KNOWN_ISSUES.md).

### Index and row mapping

The native Songs index builder (`0x4bc64`) allocates 1,032 additional bytes
in the same block as its array of song pointers. After filtering, the footer
contains a magic value, group count and up to 256 first-song indexes. It is
initialized before publication; native invalidation at `0x9129c` frees the
array and footer together. This adds no static RAM, timer or independent
cache lifetime. Every Songs index allocation includes the footer, even when
headings are disabled.

Only the displayed count includes heading rows. The song array, native song
count, playback queue and track indexes retain their original meaning.
Binary search maps display rows to song indexes without disk reads.

The model mapping is scoped through its owning list at model `+0x1c`.
Properties `0x3367/0x3368` supply song text and icons. Count property `0x330b`
shares a native code path with `0x3301`, but remains unchanged, including
when its screen retains an active album filter.

Native selection skips headings in both directions and respects the list's
wrap/clamp positions. The overlay supplies the first selectable row to native
previous-row code at `0x9ca60`, before it computes movement and boundary
feedback. This preserves the normal `LstF` notification and avoids an extra
click request at the first song. The stored minimum remains zero for drawing
the heading; other lists retain their native minimum.
Renderer property AA returns the logical song index to
playback scripts; native list selection stays visual for drawing and saved
history. Renderer AF maps a logical song back to its display row for Hold
Center's On-The-Go confirmation blink.

Normal album changes reset the UI's count and emit the native count
notification, including when two albums have the same song count but different
disc counts. A query-only invalidation with in-place tag mutation bypasses
that UI contract and is not a supported refresh mechanism.

### Viewport and numeric Quick Scroll

For a song immediately following a heading, `discs_reveal_rect` at `0xbfe64`
extends the selected-row rectangle's top by one row, leaving its bottom
unchanged during ordinary scrolling. Native visibility and viewport code reveal
both rows together. Selection, logical track mapping, stored minimum and list
geometry retain their meanings; ordinary lists use the original rectangle.

Fast input in an album with at least two disc runs selects the next disc's
first song. Backward from within a disc selects its first song; another backward
jump selects the previous disc. While inactive, forward input inside the final
disc stays in native song scrolling without starting the popup or its timer.
This applies after timeout too; reversal can still enter disc mode. Already
active mode remains clamped at the forward endpoint without selecting backward,
revealing or clicking. Small
multi-disc albums are eligible; a single partial disc and untagged albums keep
normal behavior. General Songs uses the separate title-letter path below.

Disc mode shares the 128-byte letter accelerator, timer, one-second timeout,
150 ms repeat limit and 64-count fast-stroke activation described under
[Alphabet navigation](#alphabet-navigation). Key, hide and destruction paths
own cancellation. Group searches use the index footer in RAM, with no string
or storage reads and no additional persistent state or allocation. The checked
wheel target returns the visual first-song row and popup number together on the
caller's stack. Drawing validates its own live query; it does not retain that
lookup result.

The no-entry guard requires positive row movement. A zero-row sample can still
contain backward physical wheel travel, which must accumulate toward the stroke
threshold. r47 cleared this travel in the final disc; r48 preserves it. Native
packet regressions exercise 36 two-count packets at 10 ms intervals in both
wheel modes, including positive motion that remains ordinary.

During a disc jump, the reveal hook extends the requested rectangle to a
viewport's whole rows. Native `0xbfeb0..0xbfec4` adds the partial-row remainder;
the scroll container (`0x22770`, reveal `0xc00a0`) applies the normal bottom
clamp. A short final disc stays on a full last page, and an album that fits in
the viewport does not scroll. The 4G division routine at `0x348` takes the
denominator before the numerator.

Active mode already on the final disc's first song can still request alignment.
Repeated stationary input skips reveal only when live geometry proves the whole rectangle
fits, with its heading at the top and no pending direction or paging. Shifted
or clipped layouts run native reveal again; there is no cached alignment.

The numeric popup uses the letter mode's 44-pixel monochrome frame and 5×7
glyphs. Digits scale to fit values through 65535. It centers on the ancestor
viewport at any scroll position, with native fills restricted by the dirty clip.
The heading itself remains `Disc N`.

The wheel branch at `0x86700` and list draw slot `0x29090` dispatch disc mode.
General Songs uses title-letter navigation; other lists call the existing
wheel `0x37fee0` and drawing `0x35c304` paths.
Empty or unselected rows are checked before selected-index lookup. Idle drawing
delegates immediately when no popup or unfinished gesture exists; an unfinished
gesture still checks the live query so invalidation cancels it.

### Drawing and package boundaries

The header uses a temporary native drawing context. Its clip is the intersection
of the row, dirty rectangle and ancestor viewport; the shared caller clip is
unchanged. Native text measurement/rasterization and fills draw the label and
rules. The paint helper retains no allocation or state.

[disc_build.py](disc_build.py) and [verify_disc_package.py](verify_disc_package.py)
use explicit pinned overlay profiles. The validator checks exact ARM text,
hooks, metadata, resource preservation and the complete parent package. Profiles
include both size and ARM digest because different overlays can have equal
sizes. Current package sizes and image headroom belong to the release record;
image headroom is not available runtime RAM.

### Measured cost

Native instruction profiles support two bounded optimizations:

| Operation | Earlier implementation | Current implementation |
| --- | ---: | ---: |
| Idle popup bookkeeping, two / 256 runs | 192 / 255 | 34 / 34 |
| Post-timeout popup bookkeeping, two / 256 runs | 190 / 258 | 56 / 56 |
| Active popup drawing, two / 256 runs | 5,880 / 6,011 | 5,760 / 5,823 |
| 256-disc wheel jump | 976 | 777 |
| Aligned stationary final-disc pulse | 638 | 496 |

The drawing comparison supplies native list-content drawing at the boundary;
removing duplicate group queries leaves wheel counts unchanged. Returning the
caption with the wheel target saves 20.4% on the 256-disc jump and 22.3% on the
stationary pulse. It costs four extra instructions on ordinary/invalidated
fallback and 7–13 on measured slow, rate-limited or zero-motion disc events.
Across 35 compared wheel events, selection, geometry, all accelerator bytes,
meaningful timer/click events, allocations, registrations and storage reads
agree. These are host instruction counts with supplied OS/input/layout
boundaries, not device timing or frame rates. Evidence:
`build/ipod4g-r39-once-over-01/` and `build/ipod4g-r41-review-opt-01/`.

### Verification and limits

[verify_disc_separators.py](verify_disc_separators.py) exercises index ownership,
filtering, missing/partial tags, allocation failure, native freeing, both wheel
directions, wrap/clamp feedback, Hold Center mapping and stock Songs playback.
It constructs the stock Songs tree to check that initial selection and return
to the first song reveal its heading. The shared `0x330b` screen remains native.
Equal-song-count album switches must still update displayed counts when their
disc counts differ.

[verify_disc_fast_scroll.py](verify_disc_fast_scroll.py) checks entry, reversal,
endpoints, timeout and cancellation, zero/empty rows, numeric rasterization and
clipping. Constructed-screen cases cover a nonzero viewport origin, partial
rows, stationary alignment, final-disc clamping, short albums and return to
ordinary scrolling. [verify_disc_render.py](verify_disc_render.py) executes
native font/raster paths. Package corruption checks are separate.

Storage, heap, OS scheduling, input, initial font/layout and some final display
translation boundaries are supplied by these fixtures. Native execution does
not establish device appearance, responsiveness or arbitrary scheduling safety.
Hardware observations and remaining checks belong to
[Known issues](../docs/KNOWN_ISSUES.md).

## Queue and script performance

The combined build reduces repeated work in playback queue construction and
script-global lookup. These paths use independently traced 4G addresses; the
similar 5.5G work uses a separate implementation.

### Queue construction

Native `0x53478` calls the per-index invalidator `0x53d7c` for all 38 sort
indexes. During queue copying, most ordinary indexes are already empty, yet
this cleanup repeats for each inserted song.

The replacement skips only empty ordinary indexes. Special indexes 6, 7, 9,
24 and 38 retain unconditional native calls. Volatile reads occur in the
original ascending order so a release callback can populate a later slot.
Native ownership releases, notifications and the final return value remain
unchanged. There is no new static state.

| Native instruction fixture | Before | After |
| --- | ---: | ---: |
| Empty-cache invalidation | 1,376 | 567 |
| Queue copy, 128 songs | 220,182 | 116,630 |
| Queue copy, 2,048 songs | 3,522,582 | 1,865,750 |
| Fully populated cache invalidation | 2,159 | 2,444 |

Queue-copy measurements include the actual native query listener, with
storage/allocation services supplied. Stock and the pinned parent match in that fixture;
the overlay executes about 47% fewer instructions for 2,048 songs.
The tradeoff is about 13% more work for a single fully populated cleanup,
where the added emptiness checks cannot skip anything. That case still
releases the same 156 owned objects in the same order.

Tests cover every owned slot, adjacent guards, sparse/full caches, callbacks
that mutate later slots, reversed/filtered copying, allocation failure,
notifications, callee-saved registers and return values.

### Script-global lookup

The native resolver at `0x94a9c` scans the enhanced global table before finding
implicit globals. The pinned package reserves atoms 2461 through 2535 for
those implicit globals. A checked unsigned range branch reaches the original
implicit-global continuation directly; all other atoms retain native lookup.

For atom 2461, the isolated resolver uses 106 instructions in stock, 260 in
the enhanced parent and 89 with the shortcut. Other measured implicit globals save the same
171 instructions against that parent. This is the resolver cost, not the speedup
of an entire menu interaction.

The package validator verifies the reserved range against the actual resource
profile. Differential tests cover all 65,536 atoms, malformed/high-bit inputs,
registers, flags, memory writes and native fallback behavior.

### Evidence and release boundaries

The performance overlay adds 404 code bytes and no static RAM. The builder
and strict validator are `performance_build.py` and
`verify_performance_package.py`; the latter verifies the exact ARM text,
hooks, resource profile and complete pinned-parent preservation.

Evidence: `build/ipod4g-stock-performance-01/queue-profile.json`,
`vm/verification.json`, `vm/green-lookup-cost.json`, and
`build/ipod4g-disc-info-01/cache-cost-boundaries-01.json`. These are native ARM
instruction counts with supplied service boundaries, not measured device
latency, frame rate, battery use or energy savings.

Release validation and hardware status are in the
[release record](../docs/FIRMWARE_RELEASES.md) and [known issues](../docs/KNOWN_ISSUES.md).

## Wheel delivery and Albums indexing

Wheel rejection recovery prevents a failed event post from suppressing later
movement. Albums indexing uses cached host sort hints or a direct ASCII path
to reduce CPU work while preserving native order and storage access.

### Wheel notification ownership

Native wheel movement at `0x862dc` accumulates displacement and asks `0xc88f8`
to notify the input task. That sets `0x10419c60` to one, suppressing duplicate
notifications until the lazy wheel reader consumes movement. A rejected post
must release this notification ownership even when no event reaches the reader.

There are two rejection boundaries:

1. Raw-ring posting at `0xb1394` calls `0xabbcc`, which returns false if full.
2. The UI publisher at `0x8f5dc` can have no free queue node; it returns the
   allocated event to its pool. Its separate event-allocation failure path
   already drains wheel input.

[wheel_recovery.c](wheel_recovery.c) wraps the wheel post at `0xb13d8` and
rejected-event cleanup at `0x8f668`. On rejection, native `0x6263c` consumes
discarded movement and clears the flag, matching the stock allocation-failure
policy. The next movement can notify without replaying a large backlog.
Accepted events retain native coalescing. A rejected non-wheel event cannot
clear a live wheel notification.

[verify_wheel_recovery.py](verify_wheel_recovery.py) executes packet decoding,
movement production, ring capacity/copy, event-pool operations, UI insertion/
rejection and lazy consumption. Locks, wakeup and consumer scheduling are
supplied. This proves the rejection path, not the cause of every physical
wheel stall. The fixture covers the primary wheel only; native `0x6263c` also
consumes an alternate input source when present, whose concurrent timing is
not simulated. The failure can be reproduced without invoking the LCD mutex.

### Albums indexing

The native index reads track names on first construction. A cached query
returns without track/name reads or allocations. Letter-cache generation adds
CPU work without adding name reads: a 2,048-track/256-album supplied-storage
fixture uses 1,505,089 ARM instructions on stock, 1,545,558 without the fast
path and 1,522,774 with it; each makes 2,048 name reads. An all-unique
2,048-album fixture uses 1,740,806 instructions with the fast path. These are
synthetic instruction profiles, not physical load times for the user's library.

The 88-byte [album_index_fast.S](album_index_fast.S) checks only accepted Album
rows. A nonzero host sort hint wins; otherwise an immediate ASCII A–Z/a–z
initial is folded directly. Other names and selectors use the complete grouping
helper. The fast path rejoins its existing cache write and saved-register
restoration, preserving order, allocation size and disk access.

For 128 unique ASCII albums, instructions fall from 108,029 to 96,637 versus
92,779 on stock, removing about 75% of the added work. Prefix/Unicode fallbacks
pay a small dispatch cost; other indexes pay two instructions per accepted row.
Every cached query takes 25 instructions with no name reads or allocations.
[verify_album_index.py](verify_album_index.py) checks hints, case folding, empty
names, duplicates, other selectors and cached queries. Profiles are under
`build/wheel-delivery-r33-investigation/`.

The combined recovery/index overlay occupies 212 bytes, with no added RAM,
timer or setting. [verify_wheel_package.py](verify_wheel_package.py) pins its
three hook words, image metadata and preservation of the parent artifacts.
Hardware checks compare first and immediate second Albums openings and wheel
behavior before/after idle; their status belongs to the release and issue docs.

## Alphabet navigation

Artists, Albums, Genres and Composers lists with at least 32 names support
letter jumps. Entry uses 64 raw counts in a same-direction 500 ms stroke or
the native accelerated 16-row threshold. Reversal, a 200 ms pause, keys and
hiding reset an unfinished stroke. Repeated jumps are limited to 150 ms;
letter mode expires after one second without effective wheel movement.

The 4G uses its own cached grouping data. It does not import the 5G's playlist
letter-run table, and its grouping is not complete modern macOS collation.
The 5.5G has a separate
[native Quick Scroll implementation](../targets/ipod55g/README.md#disc-headings-and-numeric-scrolling).

### Index ownership and grouping

The native index builder already fetches and copies each accepted UTF-16 name.
The allocation hook at `0x4a2c8` increases capacity from four to five bytes per
source item. The acceptance hook at `0x4a450` writes a grouping byte after
native query/compilation filtering and deduplication. At `0x4a46c`, the bytes
move directly behind the accepted name IDs; forward copy handles overlap and is skipped when every source row survives.
Native invalidation frees the original base pointer. There is no separate
allocation or global side table.

Wheel navigation reads these bytes in RAM, with no name/storage lookup. Exact
linear run search preserves short groups even if a letter recurs later in host
sort order. All remains row zero and takes the first name's quick letter.
Forward goes to the next run; backward goes to the current run's beginning or,
if already there, the previous run. List ends clamp. A 5,001-name regression
checks forward/end/reverse behavior while rejecting any name or storage read.

### Host sort initials and display fallback

The importer caches normalized initials from track mhod types 23 (artist),
28 (album) and 30 (composer). At native dispatch `0xb6c10`, the hook receives
track `r4`, importer `r6`, normalized type at `sp+418` and payload length
`sp+414 − sp+410`; it preserves live registers and rejoins `0xb6ed8` with
native status in `r0`. Track bytes `+0xfc/+0xfd/+0xfe` hold artist/album/composer
initials; zero requests display-name fallback.

Parsing is bounded by the record length and native 256 KiB scratch area. The
shared parser supports UTF-8 and both UTF-16 byte orders, validates consumed
sequences and leading surrogate pairs, skips padding and propagates I/O errors.
It skips leading punctuation, symbols, separators, format characters and
nonspacing marks, including emoji variation selectors and joiners. A fixed
Unicode 16 classification table occupies 5,752 ROM bytes across 719 ranges;
ASCII names take a short path. Empty/format-only strings fall back, while
symbol-only strings group under `#`.

The remaining character uses the pinned 5G folding map: for example É→E,
Å/Æ→A, Œ/ø→O, ß→S, and numbers/Greek/Cyrillic→`#`. Unmapped characters at or
above U+2E80 use `!`; supplementary characters do not establish full Unicode
collation. Prefix parsing runs during import/index construction, not wheel input.

Track-pool size at `0x8bf18` and clone count at `0xda208` are `0x100` bytes.
The allocator `0x9d7ec` already rounds the earlier `0xfc` size to `0x100`, and
`0x9d6cc` clears the full slot. Native release/reuse (`0x53634`) and cleanup
(`0x53720`) own the extra bytes without another allocation. Gapless timing at
`+0xec..0xfb` is preserved. The alternate constructor uses the same pool;
`0xaa5f4`'s `0xec` literal is a property-operation argument, not a copy size.

Acceptance at `0x4a450` reads track `[r6+20]` with selector `r11`: 6=album,
7=artist, 9=genre, 24=composer. Host kinds 4/5/7/18 map to those selectors.
The accepted track's hint is used after filtering/deduplication; group compaction
keeps row alignment. Native mhod52 order and displayed names are unchanged.

A captured version-117 library had 8,451 tracks, no mhod53 tables, 1,352 sort
records and 240 album rows. Its host order placed `#Goldrush, Vol. 1` under G
and `(R)Evolution: The Remixes` under R despite neither having a sort-album
record. Those literal expectations are asserted independently of the general
folding oracle. This explains why importing mhod53 or reading only the first
display character would be insufficient. The audit's heap, locks, OS reads,
track lookup and string storage are supplied; it does not measure device latency.

### List, timer and drawing ownership

The native list (`0xf9304`, vtable `0x29030`) remains 232 bytes: subclasses
also invoke its constructor on existing allocations. Its separately owned wheel
accelerator at list `+0xe4` grows to 128 bytes, preserving the original 44-byte
prefix and registration. The extension contains a 40-byte native timer and
state. Hook `0x866a4` handles allocation failure; destructor `0x103180` stops
the timer before unregistering/freeing the accelerator.

Only base-list event/draw/visibility/key slots `0x24/0x60/0x94/0x160` redirect.
Eligibility checks exact list/model/library classes, supported resource ID,
live query cache, row bounds and count. No cache pointer survives an event.
Main Songs uses the title path below; manually ordered playlists do not enter
letter mode. Album Songs uses the separately checked disc extension.
The existing list `+0xb4` timer
belongs to selection blinking and is not reused.

The wheel hook at `0x86700` runs after native acceleration/clamping. Native
`0x3c258` retains pre-division displacement at accelerator `+16`; removing its
power-of-two multiplier measures raw movement without another driver hook or
event read. Native `0x86394` can retain stale displacement when its previous
millisecond tick was zero. A remembered tick rejects that sample and clears the
unfinished stroke, including at clock rollover.

Time comes from `0x87cec` → `0x154144` in raw microseconds. Unsigned subtraction
happens before unit conversion: `0x6c218` divides a wrapping counter by 1000
and is not itself a modular millisecond clock. Native accelerator reset still
uses its original millisecond convention. Tests cover the approximately
71-minute wrap and the exact zero-tick boundary.

Native `0x3c62c` (slot `+0x104`) maintains ancestor-clipped visible bounds at
list `+0x78`. The marker centers there and subtracts the list content origin
for its drawing context. It must not center on the entire content rectangle
at `+0x58`: long lists can extend far beyond the viewport. Dirty repaint clips
restrict writes without moving the marker; `0x9107c` invalidates the visible
rectangle. A 68-byte stack context and native fill `0xa4a38` render into the
one-bit framebuffer with no retained graphics allocation.

### Event recovery and bounded redraw work

Effective wheel input refreshes the actual native timeout deadline, even when
selection stays at a boundary. Zero-delta packets do not refresh it. The interval
setter is skipped only when the native interval is already 1000 ms; a shortened
interval left by stale delivery is restored before restarting. Native start
already stops/purges the timer. Relying on an earlier timeout to reschedule would
be unsafe because the publisher can silently discard the event.

Unchanged row and marker skip redraw requests; entering mode, selecting a
different row or changing the marker still repaints. If the final timeout was
dropped, the next paint closes expired mode and requests full native list
invalidation: the current dirty clip may miss the old marker. This adds no
polling or background timer. Adjacent lit glyph cells share horizontal fill
spans with identical native clipping. Index compaction skips the copy when
every source row survived; otherwise the forward copy remains safe for overlap.

`letters.c`, `verify_letter_performance.py`, `verify_hotpaths.py`,
`verify_marker_recovery.py` and `verify_optimizations.py` cover native deadlines,
clock wrap, discarded publication, partial clips, pixel equality and preserved
allocation bounds. These contracts describe event and drawing work, not a
measured device refresh rate.

### Native data-path reference

Native list-model constructor `0xede78` creates a 32-byte model, vtable `0x22fd4`.
Its temporary row is `+0x14`; `+0x18` points to the library UI context (vtable
`0x26294`). Property resolver `0x75584` → model virtual `0x7315c` → indexed
resolver `0x6f648` → library virtual `0x6f290` supplies the row name.

The normal 4G tag is `Str ` (`0x53747220`). Its library dispatcher does not
implement the 5G `StQN` distinction: requesting that tag still gives the full
name and returns localized All at row zero. The full name arrives as UTF-8 in
shared scratch storage at library context `+0x1520`. Consume it before the
next property lookup overwrites it. Conversion `0xcbda8` also runs native
Unicode composition `0x42188`; this is not simply a raw UTF-16 pointer.

| Index | 4G label property | Native query getter | Row numbering |
|---|---:|---:|---|
| Artists | `0x336c` | `0x6e60c` | All=0, first name=1 |
| Albums | `0x336a` | `0x6e5d0` | All=0, first name=1 |
| Genres | `0x336d` | `0x6e81c` | All=0, first name=1 |
| Composers | `0x336b` | `0x6e774` | All=0, first name=1 |

Tests cover all four original property paths, accents/CJK, filtered artists,
empty filtered indexes, All, out-of-range lookup, and unchanged query/row
state after a name read. Storage and localized All text are supplied. Property
`0x336f` is a different track-list path (`0x6e648`), not Composers.

On the 5G, letter navigation requests `StQN`; normal labels request `Str `.
Artist property `0x6423` at `0x1ec614` dispatches to ordinary getter `0x9cabc`
or quick-name getter `0x9cb10`. If query `+0xc4c` has a letter-run table,
`0x9cec4` returns a one-character UTF-16 key without reading string storage;
otherwise it returns the name. All uses the first artist's grouping key.
A version check via `0x165b18` returns empty below `0x113`; its meaning is not
established by the characterization tests.

The 5G imports playlist mhod53 at `0xd7700`: payload sort kind/count and
12-byte entries at +16, with UTF-16 letter at +0 and source index at +4.
Allocator `0xa9608` makes a `u32 count` followed by 8-byte entries
(`u16 letter`, `u16 reserved`, `u32 first_source_index`). Translation
`0x10a15c` chooses `playlist + 0x494 + 4 * selector`; builder `0xd080c` rebases
accepted source positions to filtered/deduplicated output rows. For source
runs A@0/B@2/C@6, accepting positions 2,3,7 produces B@0/C@2. Publication is at
`0xd0edc..0xd0ef0`, alongside name index `+0xc48` and count `+0xc50`.

The 4G dispatch at `0xb666c` handles mhod52 and skips other types via `0xb6694`.
Its cached-byte implementation therefore does not assume a host letter table.
The 5G boundary search at `0x239b28` samples every 32 rows then scans linearly,
assuming contiguous groups; the 4G's exact run search handles recurring letters.

Stock 5G enables title Quick Scroll specifically in its general Songs view:
script `4161` selects the true branch of `4162 -> 4165`, opening `0x5fa2` with
quick-title property `0x641e` in list template `0xfffffec4`. Playlist selection
`4142` and album selection `4185` take the false branch and open `0x5fa3`, whose
template `0xfffffec3` has a zero quick-title property. The playlist directory
itself (`4140 -> 4141`, view `0x5fa1`, template `0xfffffed1`) also disables it.
This is separate from this project's added numeric album-disc mode.

Native 5G wheel probes show no 32-song minimum: even a two-song A/B fixture
can enter when fast input crosses the starting letter. Contact and native
cadence gates still apply. The entering event retains its ordinary scroll
landing and displays that row's letter; subsequent input jumps between groups
in existing order. Release hides the popup without changing the selected song.
Stock native ARM probes and static menu-route evidence are under
`build/ipod4g-song-quick-scroll-01/`. They establish the reference behavior,
not physical-device timing.

### Main Songs title jumps

The 4G port follows that scope: resource `0x3301`, exact list/model/UI classes,
title sort selector `query+0xa74 == 4`, no album predicate, and a live index
whose count matches both UI and list bounds. Main Songs script `2073` sets
selector `0xfb(4)`; playlist selection `2056` sets `0xfb(5)`. Playlist names,
playlist contents and album track ordering do not gain title mode.

[song_scroll.c](song_scroll.c) caches the title's normalized initial in
track byte `+0xff`, the remaining byte of the existing 256-byte track slot.
The native title-import classification call at `0x3cff4` already has the title's
UTF-16 pointer and length. Its Thumb helper folds an immediate ASCII A–Z/a–z
initial directly, matching Albums' shortcut. It uses byte reads, including for
unaligned text, and rejects Unicode values with ASCII-looking low bytes. Other
names use the existing prefix parser at `0x35c718`. The ARM trampoline saves/restores the original
arguments and link register, then tail-calls classifier `0x92e58` in ARM state.
Native
track initialization, copying and release cover the cache; `+0xfc..0xfe` and
playback fields are preserved. Missing or empty initials fall back to `#`.

The host's sort-title metadata (`mhod27`) takes priority, just as sort-album
metadata does in Albums. This supplies article/custom-sort behavior; the shared
prefix parser itself skips punctuation and symbols, not English words. A valid
host initial sets bit 7 of `+0xff`; lookup masks it off, and later display-title
imports leave it intact. Invalid, empty or failed sort imports preserve the
previous cached value, including an earlier valid host hint. This last-valid
rule differs from Albums' handling of duplicate empty hints: Songs retains only
one initial and cannot recover an overwritten display fallback without another
title read. Without a host hint, repeated display imports still update.

ARM dispatch at `0x35caa0` redirects only type 27 to the adapter. It reuses the
checked album importer `0x35c5bc` with selector 28 and a destination shifted by
two bytes: that importer only stores to `track+0xfd`, now `+0xff`. The existing
UTF-16/UTF-8, byte-order, scratch-limit and I/O rules remain in use. Other types
retain their original ARM return address because the unknown-type path can
tail-call Apple's ARM-only stream skipper. The new field is read during import;
there is no new allocation or wheel/paint storage access.

The shared wheel handler remembers the initial letter in inactive popup state.
Entry requires both the existing fast-stroke gate and a change of letter, with
no minimum list length. It retains the ordinary clamped landing on entry.
Later events scan contiguous runs in the current RAM index after the rate gate;
forward endpoints preserve the current song, and backward jumps select the
previous run. Repeated letters remain separate runs in their original order.
Slow wheel and paint eligibility checks are constant time. No index pointer
survives an event, and wheel/paint perform no title reads or allocations.

The existing alphabet renderer, timer, hide/key/destructor cancellation and
clipping are reused. Unlike the 5G, the traced 4G release packet does not post
a list movement event: this port retains its one-second idle timeout. Release
never changes the selection. Title import and query validation use Thumb;
the run scan uses ARM with an inlined cache accessor to avoid per-row mode
switches and register spills. ARMv4 BX veneers connect the paths.
`song_scroll.c`, `disc_separators.c` and `disc_fast_scroll.c` use `-Oz`;
the renderer retains `-Os`. The image
boundary is unchanged, with no added runtime allocation or retained state.

Songs also reuses named-list letter mode's top alignment. On entry, movement
or a changed caption, `discs_jump_redraw` tail-calls the existing guarded
`pomo_fast_scroll_top` at `0x384db0`; that helper uses native `0xab52c` to reveal
the selected row at the top, then invalidates the list. Apple's viewport still
clamps the final page, so short final groups leave no blank tail. Selection
and the ordinary landing on entry are unchanged. Stationary strokes with an
unchanged caption skip the helper, and timeout restores ordinary positioning.
Disc mode retains its heading-aware reveal and calls native invalidate directly.

The 12-byte dispatcher stays in ARM and preserves LR for both destinations.
Its absolute symbols are resolved by the linker; assembly-local absolute
operands would be encoded as displacements. The stationary disc geometry
guard uses arithmetic halving without signed-division rounding: positive row
heights reject every nonpositive viewport either way. This saves one ARM
instruction. Together the changes add eight image bytes, ending OSOS exactly
at its existing `0x390000` limit, with no new runtime state. Native constructed
viewport tests cover entry, both directions, in-page jumps, partial heights,
missing geometry, idle boundaries, timeout and the final-page clamp.

The r49 preflash profile measures 2,732 instructions for a representative
entry and 2,806 for the next forward jump through the constructed native
viewport. Forward movement performs ordinary visibility followed by top
alignment; reverse jumps need only one translation. The first reveal also
owns dirty publication, paging and pending-direction clearing, so it cannot
simply be removed. Stationary endpoints, rate-limited input and zero movement
perform no top reveal, translation or redraw. Across 20 compared input steps,
selection, events, allocation behavior and storage reads match r48; only the
requested viewport positioning differs. These are host ARM instruction counts,
not measured display timing. Evidence: `build/ipod4g-r49-review-01/`.

**Boot compatibility:** r44/r45 directly called Apple's classifier from Thumb.
Its `POP {...,pc}` exits cannot switch instruction sets on ARMv4T, so it returned
to Thumb instructions in ARM state. The user reported an r45 boot loop with
Disk Mode available, and the actual title-import path reproduces the fault
under ARMv4T rules. The user confirms r46 boots. r46 moves that legacy call into
the ARM trampoline; Apple's
return address is again `0x3cff8`. The prefix parser and run scan are compiled
extensions with `BX` returns and remain safe across the mode boundary.
[Arm's LDM documentation](https://support.arm.com/documentation/ddi0406/c/Application-Level-Architecture/Instruction-Details/Alphabetical-list-of-instructions/LDMDB-LDMEA)
distinguishes the interworking behavior added in ARMv5T.

The shared 4G UI test machine now selects Unicorn's TI925T ARMv4T model before
memory initialization. This models the relevant instruction/return rules, not
ARM7 device timing or the whole iPod. A calibration test executes Apple's actual
POP with a Thumb-marked target; a separate test rejects an odd return address
at classifier entry. Native title import in both UTF-16 byte orders exercises
the real classifier and continuation. Earlier default-model receipts are
preserved as historical results, not evidence that r44/r45 boot correctly.
Reproduction and correction evidence: `build/ipod4g-r45-boot-01/`.

[verify_song_scroll.py](verify_song_scroll.py) covers native full-track import,
sort/display ordering, duplicate and malformed hints, native clone priority,
real buffered read/skip boundaries, both encodings and byte orders,
ASCII-boundary/parser equivalence, unaligned input, classification/field preservation, physical wheel
entry, two-song lists, slow/pause/reversal cases, scope exclusions, recurring
letters, native viewport selection, existing popup pixels/clipping, cancellation,
allocation failure and RAM-only large-list search. Host instruction budgets
are regression limits, not hardware response-time measurements.

The active painter refreshes the caption from the current index. A native
same-count index rebuild can change the selected row's letter without changing
its row number; r44 retained the old popup caption. The r45 fix invalidates the
full list once when that letter changes, including when the current dirty clip
misses the popup. Inactive paints preserve the starting letter of an unfinished
stroke. Native invalidation/rebuild and pixel comparisons reproduce the fix;
an on-device UI trigger has not been reported.

Measured helper instruction counts with 1,000-song letter runs:

| Lookup | r44 | r45/r46 | r47 |
| --- | ---: | ---: | ---: |
| Current letter for eligibility/paint | 96 | 87 | 85 |
| Next letter from a run's beginning | 16,098 | 14,086 | 12,084 |
| Forward inside the final run | 16,087 | 14,076 | 12,076 |
| Backward from the end of a run, across the previous run | 35,092 | 26,083 | 22,083 |
| Backward from a run's beginning | 16,111 | 13,096 | 11,094 |

In the r44-to-r45 comparison, all 16 Songs/disc wheel steps preserve selection, model state, all
accelerator bytes, notifications, timers, allocation maps and storage counts.
The valid comparison is `build/ipod4g-r44-review-01/profile-comparison-verified.json`;
its fresh input snapshots copy mutable event/allocation containers. Earlier
profiling receipts that aliased those containers remain preserved and are
superseded. These host counts do not establish device latency or frame rate.

The r46-to-r47 comparison under `build/ipod4g-r46-review-01/` retains all eight
Songs steps' observable behavior. Disc selection/events/allocations/storage
also match; the final timeout step intentionally clears unfinished-stroke state
instead of preparing unavailable forward disc mode. Some constant-cost entry
paths add a few instructions (the measured first disc entry is 816 to 828).
The simplified single-load, masked cache accessor reduces long scans, while
ARM size optimization keeps the complete overlay 12 bytes smaller than r46.

r48 adds a 28-byte ASCII shortcut to title caching. The complete native
title-import fixture falls from 138 to 61 instructions for ordinary ASCII
initials (about 56%); a previously cached host hint remains 45. Accent and
punctuation fallbacks add 13 instructions, and empty titles add 3. These are
small per-title CPU costs, not measured library-startup times. Profiles and
the final-disc reversal reproduction are under `build/ipod4g-r47-review-01/`.

### Verification

[verify_letters.py](verify_letters.py), [verify_wheel_input.py](verify_wheel_input.py)
and [verify_letter_viewport.py](verify_letter_viewport.py) cover ownership,
selection-before-notification, 56-count non-entry versus 64-count entry, both
accelerator modes/directions, slow input, normalization, cancellation, rollover,
long-list geometry and clipped raster output. [verify_sort_letters.py](verify_sort_letters.py)
executes full native import/reader paths, Unicode boundaries, malformed records,
clone/free behavior, filtering and all four index kinds.
[audit_sort_library.py](audit_sort_library.py) exercises the captured library's
host order. These tests supply storage, scheduling and some graphics services;
host framebuffer output is not a photograph or a measurement of wheel feel.

For native 4G/5G characterization, run:

```sh
.venv/bin/python navigation_qol/research_letters.py
```

The script accepts pinned `--reference` and `--candidate` inputs, verifies
OSOS hashes and executes original grouping/search instructions. It does not
write an IPSW, access a device, simulate disk latency or boot the operating system.
Run Unicorn with JIT permitted, following the build guide.

## Highlighted-name scrolling

Source and test filenames in this section are relative to `menu_scroll/`.

The combined firmware pauses **840 ms** between text segments.
[Current build](../docs/BUILDING.md)

This resource patch enables the 4G's native marquee text control in seven
music lists that only supplied a static row. It adds a separate selected-row
template, following the pattern already present in three other stock 4G
lists and in the Albums list of the late-2006 5G firmware we examined. Ordinary rows retain their original text
controls, fonts, icons and layout. Generic settings menus are outside this
change.

The base resource layer adds 6,520 bytes without changing executable playback
bytes. Segment dwell is a separate timing hook. Resource preservation does not
establish the effect of concurrent UI activity on physical playback.

### Native rendering and timing

All addresses below are OSOS offsets for 4.3.1.1.

| Native mechanism | Address / detail |
|---|---|
| Built-in resource directory | `0x1a507c`, View section |
| Memory-resource constructor / lookup | `0xee004` / `0x7ae24` |
| List child loader | `0x2f458`: second child becomes object `+0x94` |
| List renderer | `0x5cdc4`: alternate child used only for selected index |
| Selected row class | `0x1780`, relative vtable `0x22eac` |
| Scrolling text class | `0x3180`, relative vtable `0x248b8` |
| Text change detection / bitmap draw | `0x5b74c` |
| Timer scheduler / event / tick | `0xd2a78` / `0x809b0` / `0xc42e8` |
| Show/hide callback | `0xd0410`: reset/start on show, cancel on hide |
| Stock menu timing fields | interval 140 ms, initial dwell 1250 ms, step 8 pixels, gap 25 pixels |

The timer backend at `0x9fd90` multiplies the interval by 1,000 before adding
it to the PortalPlayer microsecond counter (`0x154144`, register
`0x60005010`). Thus the moving phase requests about 7.1 updates per second
and advances about 57 pixels per second, excluding dwell pauses and event
dispatch time. This is animation timing, not a measurement of LCD refresh
or pixel response.

The native tick at `0xc42e8` also pauses after each approximately 90%-of-width
segment, while enough text remains before the wrap gap. Scheduler `0xd2a78`
shortens these intermediate pauses to 30% of the configured dwell: **375 ms**
for these menu rows. The dwell hook intercepts only that timing conversion at
`0xd2ad8`, returning **840 ms** for the menu profile. It preserves the initial
and wrap pause, ordinary motion, and Now Playing's 1500-ms intermediate pause.
`verify_dwell.py` exercises actual timer events through a full scroll cycle,
including native suppression of a final pause too close to the wrap gap.

Lists `3301`, `3302` (Albums), `3303`, `3304`, `3305`, `3307`, `330f` gain
selected-row resources `6000`–`6006`. Lists `3306`, `330a`, `330b` already
contain scrolling selected rows and remain untouched. Existing row resources
with negative 16-bit IDs are looked up using sign-extended 32-bit keys.

The patch appends new resources and a sorted View directory before a new
OSOS end marker. Within the original image it changes only the View count
and directory offset, image length at `0xe8`, and end-marker addresses at
`0x2e8` and `0x4a8`. Existing resource payloads remain in place. The firmware
container is repacked with an updated OSOS checksum and updater offset;
the updater contents and manifest are retained.

The original resource-blob length at `ROOT-4` remains unchanged. The native
constructor stores it, but lookup uses root-relative directory offsets and
the payload base without applying that length as a bound. The native lookup
test resolves all 334 View resources, including the appended ones, using
that original constructor argument.

### Verification and operating limits

`menu_scroll/verify_resources.py` checks selected-row bindings/layout, unchanged
resources and package preservation. `menu_scroll/verify_native.py` checks native
lookup of all resources, first/middle/last/empty selection dispatch, scrolling
control construction, short/exact-fit timer suppression, dwell/tick/wrap,
text-change reset, parent hide/show and a pending timer delivered after changing
to a short name. `menu_scroll/verify_dwell.py` checks a complete scroll cycle and
preservation of Now Playing timing.

The resource builder accepts only its pinned stock or gapless base and verifies
the original container. The combined build composes later layers; use its
instructions rather than applying the standalone resource builder to a current
release. Builders refuse to overwrite existing candidates.

The harness supplies parent objects, initial parent-dependent layout, graphics,
font services, allocation and OS timer-queue boundaries. It does not boot the
OS, render a physical LCD or measure simultaneous audio/UI scheduling.

The native string comparison uses text and width, so selecting identical long
names may retain scroll position. Right-edge constraints bind to the preceding
icon, and parent hiding traverses the selected row. These are stock behaviors.
The established 8-pixel/140-ms animation and unmeasured panel response both
matter when interpreting blur; the latter is not isolated by host tests.
Device checks include long/short-name changes, leaving/re-entering lists and
scrolling during a known gapless transition. Current observations belong to
[Known issues](../docs/KNOWN_ISSUES.md).
