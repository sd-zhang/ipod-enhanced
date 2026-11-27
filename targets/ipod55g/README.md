# 5.5G navigation and performance

This target starts from late-2006 iPod software 1.3, archive `iPod_25.1.3.ipsw`,
member `Firmware-25.6.3`. Native addresses, metadata, display and input contracts
were traced independently from the 4G. This reference describes the implemented
patches, their ownership rules and host verification limits. Decoder and audio
output remain stock; release readiness and device reports belong to the linked
status documents.

[Controls](../../docs/USER_GUIDE.md) · [Build and install](../../docs/BUILDING.md) ·
[Release status](../../docs/FIRMWARE_RELEASES.md) · [Known issues](../../docs/KNOWN_ISSUES.md)

- [Now Playing actions](#now-playing-actions)
- [Disc headings and numeric scrolling](#disc-headings-and-numeric-scrolling)
- [Playback queue creation](#playback-queue-creation)
- [Script-global lookup](#script-global-lookup)
- [Clicker output choices](#clicker-output-choices)
- [Stock platform boundary](#stock-platform-boundary)

[Clock, Pomodoro, Main Menu integration and alarms](CLOCK_NATIVE.md) share a
separate reference because their timers, widgets and backlight ownership interact.

## Clicker output choices

`clicker.py` restores the existing Settings → Clicker cycle to **On, Off,
Speaker, Headphones**. On requests both outputs, subject to Apple's existing
video guard. The native row, localized strings, script 4320, preference format
and default remain unchanged. No extension code, allocation or state is added.

Settings property `0x6088`, group `0x6000`, uses choice IDs `0x6089..0x608c`
and cycle sentinel `0x60be`. The native getter reads enum byte `Settings+0x4a`.
Preference `query+0xca0 → preferences+0xb04` mirrors to `0x1081d9c4` through
`0xb2e1c`; bit0 selects piezo and bit1 selects PCM. Stock limits cycling to
two entries despite retaining all four cases. The dormant Speaker/Headphones
cases also reverse their physical outputs. Nine checked instruction edits
expand the cycle and fix both setting and refresh mappings:

| Native offsets | Change |
| --- | --- |
| `0x192368` | Cycle maximum enum 1 → 3. |
| `0x192394`, `0x192398` | Speaker clears bit1 and sets bit0. |
| `0x1923a4`, `0x1923a8` | Headphones clears bit0 and sets bit1. |
| `0x193804`, `0x19381c` | Initial refresh maps single-output flags to matching labels. |
| `0x193e3c`, `0x193e4c` | Live refresh uses the same mapping. |

Output stays native: `0x1c2428 → 0xbbdf4 → 0xa69dc` dispatches the click.
Piezo requests `0xcd430(85, 3000)`. PCM uses `0x1b9168` and the native
four-voice mixer, reached through audio-filter callback `0x1d603c → 0x217cb8`.
The click table supplies 166 mono signed16 samples at 11025 Hz, gain 519;
it adds the cue to existing PCM. On's video predicate follows
`0x246158 → 0x1f700c → 0x1f91b0`, reading the video-control active byte.
Active video makes On use piezo only. Filter bypass also remains stock.

`test_clicker_native.py` executes actual setting wrappers, cycle script, initial
and live refresh, Preferences save/reload and Reset Settings. The audio suite
executes the public wheel-click path, cue setup and native mixing for all four
flags, including video and filter bypass. Allocator, file, OS registration,
sound-message and piezo-queue boundaries are supplied. These checks do not
establish physical audibility, DMA behavior or battery impact. Hardware
acceptance remains listening to each mode, including while music plays, and
checking selection after reboot. Research evidence: `build/ipod55g-clicker-01/`.

## Now Playing actions

Hold-Center opens actions for the live playing track and queue. Browse operations
reuse or rebuild safe parents, then open the native Songs or Albums screen;
On-The-Go operations preserve exact playlist-entry identity. Addresses refer to
the [pinned stock image](#stock-interface-evidence).

### Implementation and checked edits

`actions.py` returns resource/script edits and checks the two native method-pointer
words before changing them. `actions.c` has no persistent state or retained track,
query, playlist or menu pointers. Nonnegative native selector calls fall through.

| OSOS offset | Expected little-endian bytes | Replacement |
| --- | --- | --- |
| `0x4fcd68` | `9c 96 1e 00` | linked `actions_artist` |
| `0x4fcd78` | `04 8e 1e 00` | linked `actions_album` |

These are source member-pointer records, not the runtime table. Native initializer
`0x25aea0` reconstructs the runtime table at `0x10821690`, including discontinuities
in the source records. For class 33, artist selector `0x10f` resolves to runtime
`0x10821b70`; album selector `0x111` resolves to `0x10821b80`. Original targets are
`0x1e969c` and `0x1e8e04`, both `(music_ui, selected_row)`.

Stock scripts 4244 (Now Playing activation) and 4239 (shared suspend/leave cleanup)
receive only a prefix: register and unregister hold-Center `(2,0x6f)` respectively.
Their original instruction streams remain intact. Added atoms 5093–5099 follow
the stock global/atom range (stock maximum header is 5093, exclusive):

| Atom | Role |
| --- | --- |
| 5093 | Normalize transient NP controls with 4252(false), set title, push 5094, consume pending Center release with 4008 |
| 5094 | Stock menu keys 4012, playback keys 4073, stock background, show live OTG-context view |
| 5095 / 5096 | Check album/artist metadata, reuse or rebuild safe parents, reread/apply filter, open stock Songs/Albums |
| 5097 | Reconstruct Home 3969, normal Now Playing 4246, then actions screen when the caller rejects parent reuse |
| 5098 / 5099 | Add/remove the live exact playlist entry; flash row on success |

Bytecode uses stock `3b` symbol, `35` member, `39` call frame, `3a` argument count,
`58` integer, `22` negate, `07/08` branch on false/true, `02` discard, `43 05` true/return.
Function records use the native five-halfword header, even declared code length,
and a halfword extension trailer. `scripts.merge_scripts` preserves physical
ordinals and the stock global initializer tail; no 4G table offsets are reused.

Views `0x7100/0x7107/0x710a` represent Add, Remove and unavailable OTG contexts.
Their child lists are `0x7101/0x7108/0x710b`; menu and row-template View resources
are `0x7102/0x7109/0x710c`. All are registered as children of root View `0x5e15`.
Strings occupy `0x7100`, `0x7103..0x7106`, `0x710d..0x710e`.

The native Settings screen template is View `0x6017`, list `0xfffffe85`, row
View `0x6016`. Scrollbar/list references occur at record-relative `0x64`, `0x0c`
and `0x14`. Every new Menu needs a corresponding View(Menu ID), otherwise the
native menu model can load while its visible row widget is absent.

Menu records include their four-byte length. The caption key is the halfword at
record `+24`, confirmed by native item getter `0x1bf740`; changing only `+20/+32`
leaves the original caption. Action rows set `+24` to the new key and clear `+26`.
Shuffle/Repeat rows retain native property/group `0x6057/0x6000` and
`0x605b/0x6000`; command 4320 performs in-place cycling using sentinel `0x60be`.
Sleep Timer retains command 4326 and its stock localized caption.
Its row must also retain the native setting/group `0x6081/0x6000` and duration
choice metadata: the native picker reads these from the selected row. The action
clones that row and removes only its Clock menu visibility condition. Native
selected-row getters, picker scripts 4326/4327 and selection script 4330 then
read/write the global Sleep Timer setting.

### Script, key, Renderer and history contracts

Script globals: App `0x1392` (class 2), Renderer `0x1393` (class 3), Music UI
`0x13a4` (class 33), Settings `0x13b3` (class 35). Class descriptors begin at
`0x4fee68` with 34-byte records. The actual iPod Renderer vtable is `0x66fa6c`;
`0x66cedc` is its base, not the final object table.

| Operation | Selector / native target |
| --- | --- |
| Register/unregister key | App `0x7c/0x7d`, `0x1d27f8/0x1d22b4` |
| Push script history | App `0x88`, `0x1d27e8` |
| Pop / unwind to callback | App `0x8d/0x8e`, `0x1d1f7c/0x1d29ac` |
| Select background | Renderer `0x9d`, `0x19e790` |
| Show resource | Renderer `0xb6`, `0x19e524` |
| Selected row property / group | Renderer `0xc4/0xc5`, `0x19edf0/0x19e844` |
| Flash selected action row | Renderer `0xcc`, `0x19d148` |

Short Center `(3,0x6f)` remains stock menu dispatch 4017. Short Menu `(3,0x6e)`
remains stock back 3973. Hold Menu `(2,0x6e)` is never changed by these edits.
Release consumer 4008 restores the normal Center callback without activating a
menu row. Stock 4252(false) normalizes transient seek/rating keys before the
native history snapshot.

App `+0xe0` owns the history manager, whose native singleton is `0x108840c4`;
constructor `0x13ca24` installs vtable `0x6670b4`. Push `0x13c90c` allocates a
48-byte script node via constructor `0x1ae5ac`; manager `+4` is its head, node
`+0x10` its previous node, `+0x24` its activation callback. Pop/unwind run native
lifecycle methods and free removed nodes. Rebuilding parents removes older
library screens before replacing their shared query. Native tests produce:

- Album Songs 4164 → Actions 5094 → Now Playing 4244 → Home 4002.
- Artist Albums 4184 → Actions 5094 → Now Playing 4244 → Home 4002.
- Stock Menu 3973 returns one level; Home 3969 unwinds to 4002.

Parent reconstruction must suppress intermediate screens. Script helper 3986
temporarily sets global `0x1394`; **true suppresses drawing**, while false permits drawing.
Native conversion `0xe5ac8` maps true to mode 8, skipped by the draw gate at
`0x24b8e8`. The action calls 3986(PARENTS, true), then restores the caller's
mode before opening the destination. A regression executes actual history callbacks,
native drawing-mode conversion/locking and the final draw gate. It changes six
draw requests to one, with renderer nesting and the script global restored.
The final framebuffer draw remains supplied; physical appearance needs testing.

Artist's stock entry 4180 automatically enters Songs when exactly one album
matches. Browse Artist refreshes the native album count (`0x127`) and opens
Albums 4184 directly, retaining its stock key and cleanup setup. Native query/index tests
cover one/two albums, unrelated artists with the same album title, subsequent
album selection into Songs, and Menu/Home return.

### Bounded parent reuse

Album selector operation `-4` performs a read-only history check. Native
`0x1d30d8()` supplies App; its `+0xe0` must have history vtable `0x6670b4` and
must not be unwinding (`+8`). Exactly three nodes must have script vtable
`0x66dbf0`, callbacks `5094/4244/4002`, clear `+0x1c/+0x1d` flags and no further
ancestor. Browse scripts run that guard before the draw-suppression wrapper.
A matching chain skips both reconstruction and the wrapper; any mismatch runs
3986(PARENTS, true), including every stock unwind/activation/cleanup callback.
Private helper 5097 contains the normalization sequence and is referenced only
by the two Browse commands. Native opcode `08` (`0x135f08`) branches on true.
No arbitrary ancestor callback is bypassed, and no history or metadata pointer
is retained.

Without reuse, an already safe Actions → Now Playing → Home chain runs native
callbacks `4244, 4241, 4002, 4244, 4240, 5094`, followed by the destination.
Avoiding that lifecycle work is the main saving. Both paths copy current-track
metadata twice and perform one predicate pass over the supplied library;
these traces do not show a redundant hidden query scan.

The isolated 32-track controls in
`build/ipod55g-r5-browse-latency-01/profile-control.json` compile the frozen
rebuild implementation and reuse implementation with the same actions-only
layout. Their modeled ARM instruction counts are:

| Path | Rebuild control | Reuse guard | Lifecycle callbacks |
| --- | ---: | ---: | --- |
| Repeat Browse Album | 740,252 | 113,216 | 7 → 1 |
| Repeat Browse Artist | 736,062 | 109,018 | 7 → 1 |
| First Album from library history | 793,248 | 796,149 | 8 → 8 |
| First Artist from library history | 789,058 | 791,951 | 8 → 8 |

Reuse removes about 85% of modeled instructions for repeated browsing. First
use with old library parents still pays normalization plus about 0.37% guard
overhead. Native stock Album/Artist row selections in the companion
`profile-comparison.json` cost 99,492/100,646, but start from prepared parents
and are not equivalent full shortcuts.

The separate matched 32-track fixture in
`build/ipod55g-r5-preflash-performance-01/wrapper-minimal-comparison.json`
isolates moving the guard ahead of drawing suppression:

| Path | Guard inside wrapper | Guard before wrapper | Reduction |
| --- | ---: | ---: | ---: |
| Repeat Browse Album | 113,216 | 104,194 | 7.97% |
| Repeat Browse Artist | 109,018 | 99,996 | 8.28% |
| First Album from library history | 796,149 | 796,134 | 15 instructions |
| First Artist from library history | 791,951 | 791,936 | 15 instructions |

These isolate the current history strategy; they are not instruction totals for
the complete current release or physical iPod latency. Tests cover guard
rejection, real initial Now Playing lifecycle setup, repeated Browse/Menu/Home,
missing metadata, second-read and source-selection failure, and initially
enabled or suppressed drawing. They verify parent identities, destination draw,
renderer nesting, script drawing mode, query and playback state. Query tests use
native Songs/Albums indexes, Unicode metadata and overlapping album names.

### Browse query and metadata ownership

Music UI `+0x1c` is the browse query. Its current track wrapper starts at `+0x28`,
vtable `0x676608`, with the record at `+0x2c`. Native `0x255c60(wrapper)` checks
record/query generation validity. Metadata getters `0x9f8a4(record,out)` (album)
and `0x9f8f0(record,out)` (artist) return zero on success and fill a bounded
length-prefixed UTF-16 buffer. Copy the name before changing the query. Empty,
unavailable or overlong metadata causes no action.

`0x9bc6c(query)` resolves its base query. `0xb29b0(query,0)` selects library
source zero and runs native filter/index disposal; `0xb2894(query,1,3)` and
`0x1e7628(ui,3)` restore the native browse context. Artist and album filters are
length-prefixed UTF-16 at query `+0x228/+0x628`. Cached UI counts at `+0xc0..+0xd0`
and selections `+0x3ec..+0x404` are invalidated; UI mode byte `+0x48d` becomes
5 (artist) or 9 (album). Playback has a separate SPlaylist owner at UI `+0x20`;
the browse helper never replaces it or starts playback.

The availability call (`-2`) is nonmutating. The apply call (`-3`) rereads valid
metadata after history cleanup. No metadata pointer survives either call.

### Actual-queue On-The-Go contracts

Music UI `+0x20` points to the native playing manager: `+4` is its raw playlist,
`+0x14` its current index. Base query `+0xce0` is the actual OTG playlist and
`+0xca0` its preferences owner. A valid playlist has tag `0x706c7374` at `+4`;
`0xad56c` validates it. The playlist database owner is at `+0x0c`, header at
`+0x28`, and header `+0x32` is its 16-bit count.

`0x9c838(playlist,order,reverse,index)` resolves the exact active entry. Order is
2 if playlist byte `+0xec` has shuffle bit 0, otherwise 1; reverse is bit 1 of
byte `+0xed`, matching the native SPlaylist accessors. Entry `+0` is its playlist,
`+0x10` its nonzero identity, `+0x20` its track, and bit 0 of byte `+0x1d` marks
non-track entries which are rejected. Two entries may share the same track.

- Add: `0x93504(entry,otg,0,0)` → native clone `0x104edc`; null is failure.
  Enforce the stock 1000-entry cap. Native clone preserves the exact source entry
  identity in new-entry `+0x18`; it does not collapse duplicate songs.
- Remove: `0x95958(entry)` sends `dpli` to playlist `+0x30` before unlink/free,
  then releases the entry. Never read it afterward. Follow with native reindex
  `0xad504(otg)` and query invalidation `0xa376c/0xa37a8`.
- Mark preferences byte `+0xb76` only after success. Refresh UI with native
  `0x1ea6f8(ui)` after Add or `0x1e7f08(ui)` after Remove.

These operations use the native `SDatabaseScope` constructor/destructor
`0x17ca6c/0x17ca8c`, matching the ordinary mode-zero UI playlist wrappers. This
scope does not itself acquire an exclusive lock. No new worker thread or pointer
retention is introduced. Device playback/USB concurrency remains a hardware check.

Selector operations `-5/-6/-7` select contextual View / Add / Remove. The label
is chosen when showing the screen, and the operation rereads the live queue and
index. A stale Add/Remove command cannot reverse its meaning after a queue change:
it fails without mutation. Reentering the screen reevaluates the label.

### Executable evidence and limits

Run with the existing `.venv` and Unicorn JIT permission:

```
.venv/bin/python targets/ipod55g/research_actions.py
.venv/bin/python targets/ipod55g/test_actions_native.py
```

`test_actions_native.py` and `test_actions_query_native.py` provide the executable
contracts. `verify_release.py` runs them against the packaged image.

The tests execute the stock ARM interpreter, complete modified NP activation and
cleanup scripts, added commands, native history nodes/pop/unwind, query index
invalidation, playlist lookup/clone/removal notification/track unlink, widget
factory recursion, menu item models, actual captions and native selected-row
property getters. The stock 4320 toggle script reaches the original setter ABI
with the exact property/group/cycle values and retains the selected row. The
Alarm Backlight record is independently constructed and its actual caption read
in an isolated stock Settings view.

Boundaries supplied by fixtures: allocator/storage pool insertion and final
entry free, kernel timers/event registration, display ancestor/framebuffer,
resource-file provider, metadata record reads, history activation callbacks and
UI refresh. The navigation regression executes history activation
callbacks and native drawing gates; query regressions execute metadata getters,
string comparison, predicate, album index and row-count refresh while supplying
the storage pools and playlist traversal. Tests do not execute a complete boot,
LCD rasterization, physical wheel timing, persistent OTG disk writes, or full
Shuffle/Repeat playback effects.
Device timing, visible transitions, persistent storage and playback concurrency
require hardware evidence; see [validation status](../../docs/FIRMWARE_RELEASES.md).

## Disc headings and numeric scrolling

This implementation targets the pinned late-2006 5.5G stock 1.3 image. All native
addresses and contracts were traced independently from the monochrome 4G.

The extension adds display-only disc headings to eligible album lists and uses
native Quick Scroll to select the first song of a disc. Its footer, popup and
rendering rules preserve native index and widget ownership.

### Display and navigation

An explicit album query whose songs all carry nonzero disc numbers gains one
`Disc N` row per contiguous disc run. Disc 1 of 1 stays native; a partial album
tagged Disc 2 can show its heading. Missing tags or more than 256 runs fall back
to the ordinary list. Run order follows the native song index, including repeated
or nonmonotonic disc numbers; this layer does not sort the library.

The heading uses Contacts Type `0x6d43`: 14px bold Podium Sans in `#666666`.
The one-pixel side rules use Calendar gray RGB565 `0xd6ba`. Native font lookup,
measurement and RGB565 drawing run through a stack graphics context, intersecting
the row's dirty rectangle, list viewport and native surface clip. The renderer
owns no heap allocation or persistent state; first-use font caching stays native.

The heading first publishes its bounded row clip with native
`0x21ac60`, then binds its graphics context with `0x21aad0`. Ordinary row
containers leave their latest clip at display-owner `+0xa8`; it is active draw
state, not an ancestor boundary. Inheriting that stale clip can hide both the
label and rules after a song row has drawn. The native
layout's list `+0x88` remains the viewport boundary. The new draw updates only
the owner's active clip, as native row painting does; other shared state stays
unchanged.

Headings are display rows only. Selection, normal wheel movement, playback,
On-The-Go and retained focus map through the visual/logical boundary. Revealing
the first song of a disc includes its preceding heading without changing the
song's lower edge or global list geometry.

During active disc Quick Scroll, the reveal expands to a full viewport
beginning at the heading. It uses the parent frame height rounded down to whole
rows; native `0x23a6a8..0x23a6cc` adds the remaining partial-row pixels. Native
parent reveal `0x16c930` and scroll `0x16c6d8` retain their bottom clamp, so a
short final disc can remain below the top. No blank rows or content padding are
added. Ordinary selection keeps the two-row heading/song reveal. Already active
mode at a disc's first song can invoke native reveal without a selection
change; repeated input adds no click.

The stationary reveal is skipped only when live row geometry proves the entire
reveal rectangle is already inside the native scroll frame. It requires active
disc mode, no pending direction, nonpaged layout, the stock scroll-container
type, a heading exactly at the frame top and horizontal containment. Otherwise
Apple's reveal still runs, including bottom-clamp cases. There is no remembered
alignment flag; a translated or resized layout is evaluated again. Numeric
publication and timer refresh remain unchanged.

Quick Scroll requires at least two disc runs. A forward step selects the next
run's first song; a backward step first returns to the current run's first song,
then moves to the previous run. While inactive, forward input inside the final
run stays in native song scrolling without opening the popup or starting its
timer. This also applies after release or timeout. Reverse entry retains Apple's
normal magnitude and cadence requirements. Already active mode retains its
endpoint clamp: the current song and viewport stay put, with popup publication
and timer refresh intact. After a genuine disc jump, release or timeout leaves
that disc's first song selected. No new retained state or allocation is added.

### Index ownership and row mapping

The importer copies mhit `+0x5c/+0x60` into track halfwords `+0x84/+0x86`
(disc number/total), at `0xd7a58..0xd7a64`. Track `+0x76/+0x74` instead contain
year/bitrate from mhit `+0x34/+0x38`; using them as disc tags produces headings
such as `Disc 2023`. Native index builder `0xcc7a8` allocates song pointers at
`0xcc810`. The extension reserves an additional 1,032-byte footer in that same
allocation and fills it at `0xcc960`, before the native database unlock. The
footer contains a magic, run count and up to 256 `{u16 first_song,u16 number}`
records. Query `+0xc88/+0xc90` retain their original pointer/count meanings.
Native invalidation at `0xa376c` frees the whole block. No pointer to the query,
index or footer is retained by the extension.

Album filtering uses the native UTF-16 album name at query `+0x628`. Both stock
Songs views (`0x5fa2/0x5fa3`) own list key `0x6381`, vtable `0x677290`. Its model
at `+0xa4` has vtable `0x66b628` and owns the association with Music UI
`0x670f54`. Scope checks include the exact model, list, query count and visual
extent. Mapping uses binary search over the bounded footer.

Native count key `0x6381`, row title/icon properties, selection and row painting
receive the visual mapping. Alternate count key `0x6399` stays native. Renderer
script C7 converts selection to a logical song only at `0x19eccc`; the native
focus remains visual. Renderer CC converts back for On-The-Go blink. The wheel
hooks adjust proposed rows before native clamp/wrap and click decisions. The
reveal hook at `0x23a660` expands the visible rectangle above a disc's first song.

### Native Quick Scroll

The shared native Quick Scroll entry comparison at `0x23a534` changes
from 16 to 13 scaled movement units: 18.75% less. Four native list classes share this gate, including ordinary
alphabet lists and the album disc list. Fresh-accumulator event checks enter at
52 raw units instead of 64 in either direction. The native finger-contact flag,
previous-selection check, letter-boundary check, quantizer, acceleration and
cadence predicate `0x1c25c8` remain in place. The latter still uses strict
10,000/17,000 microsecond limits for the two board profiles. This is an entry
change, not a change to ordinary scrolling speed or active jump size.

For eligible album gestures, the letter comparison after those checks is
replaced by validation of the full disc-run index. The inactive gate also
requires a later run for positive movement; Disc 1 and Disc 10 remain distinct.
At `0x23a56c` native code has already taken the absolute step. The hook recovers
its signed value from the outer wheel handler's saved `r5`: native gate
`0x23a514` saves it at its stack offset 12, and the hook's 24-byte save makes
that offset 36. Caller instructions `0x239ea0`/`0x239ee0` and the prologue are
pinned by both installer and independent audit. Event validity checks pass
zero direction so a blocked forward entry does not prevent later reversal or
cancel an already active popup. A prepared gesture still follows the existing
key, release, hide, query invalidation and destruction cleanup.

The popup uses the existing view `0xfffffed4`, background `0x5f5d` and label Type
`0xfffffeaa` (28px bold white Podium Sans). Native centering places it in the
95×82 popup frame. All five digits of 65535 fit. Model method `0x17f6a4` copies
the temporary numeric string into its own native string before publishing the
existing visibility/text/bitmap properties. No new View, Type, bitmap or string
resource is added for this feature.

Numeric publication reuses the native model's owned string when its
complete caption is unchanged. Passing the model's NativeString at `+0x24` to
`0x17f6a4` takes Apple's pointer-equality branch and avoids copying it again.
All four typed notifications still run, including after hide, external rebind or
partial ownership. Alias registration sends wildcard notifications that do not
fully refresh retained widgets; model values alone cannot justify skipping
publication. The comparison reads at most six bytes and adds no cached state.
Binding behavior, wheel selection and timer refresh are unchanged.

The implementation uses the list's native mode at `+0x12c` and its existing
two-second timer at `+0x104`. Gesture ownership is encoded in the spare range
of the native starting-row field; no new static RAM or separate timer is added.
Lifecycle checks cover keys, ordinary-wheel exit, timeout, hidden views, query
invalidation and destruction. Popup bindings follow native provider ownership.
The App singleton is stored at `0x1081da80`; native model registration uses the
separate provider slot at `0x1081da94`. The stock model's bind(false) path is
unsafe here: it attempts observer registration through a null provider. Cleanup
instead removes only aliases still owned by this model from the provider's two
forward maps and reverse index, then removes its observer. A successor view's
binding survives cancellation of the old list. Native framebuffer tests verify
hide, cleanup, later publication, rebind and replacement ownership.

The App check accepts concrete vtable `0x66a510`, installed by UI constructor
`0x16b194`, as well as base class `0x66fe10`. Checking only the base class would
cancel disc mode on the live App. App presence and provider type checks remain.
The fixture runs the native concrete constructor, and a continuous-input regression
executes the wheel IRQ, position accumulator, timing average and lazy event
decoder. The decoded timing field is a mean sample interval in microseconds;
board profile, wheel packets, timer and event scheduling remain supplied.

### Verification scope

The comparison at `build/ipod55g-r14-preflash-opt-01/profile-comparison.json`
isolates the stationary reveal guard across 22 wheel events. Selection, logical song, frame position, popup, allocations, frees,
timers and clicks match. Repeated input at an already aligned final-disc heading
drops from 12,475 to 11,972 ARM instructions (4.03%) and removes one redundant
row invalidation. Native-clamped repeats retain the reveal and add 42 guard
instructions (about 0.32%); moving disc steps add one. Ordinary scrolling and
alphabet Quick Scroll counts are unchanged in these fixtures. This is a narrow
host-path optimization, not a measured whole-device speed or battery gain.

The probe at `build/ipod55g-r10-review-01/performance/comparison-02.json`
compares complete packaged images to isolate reuse of the native caption string.
Repeated publication drops from 10,945 to 10,896 ARM instructions. A small active
wheel pulse drops from 11,793 to 11,744; holding at the final disc drops from
11,969 to 11,920. Each saves one native allocation in the supplied heap fixture.
Changing a caption adds 14 instructions; initial entry adds four. Inactive
publication and ordinary exit counts are unchanged. Native widget tests verify
identical pixels and correct redraw after both internal and external rebind.
These are modest host-path gains, not whole-device frame rates or physical
allocation/timing observations.

The package audit also checks raw native resource directory order and bounds
before dictionary parsing. Reversing a valid Menu directory preserves its
records but breaks Apple's binary lookup; corruption tests ensure the audit
rejects that case, duplicate IDs and invalid directory extents.

Independent instruction-count probes found no behavioral change in ordinary
Songs or active native alphabet Quick Scroll. The added full-handler guard cost
was 0.15–4.41% across those fixtures; idle popup publication added 15 instructions.
Non-album index construction added 28 instructions at both 60 and 200 songs.
Eligible album metadata construction added about 2.8–2.9%, doing one linear pass;
later row mapping searches only the bounded run table. These are emulator counts,
not measurements of physical latency. The index allocation grows by 1,032 bytes
even for an ordinary query, so footer capacity never changes ownership or requires
a second allocation.

`test_disc_native.py` executes the native query builder, both stock Songs view
constructors, wheel dispatch, viewport reveal, row iteration and playback/OTG
scripts up to the operation boundary. Its shared library fixture starts
with serialized mhit metadata and executes Apple's header read, byte swapping,
short-header zero fill and scalar import. Only file I/O and the track constructor
are supplied at that boundary. Starting from imported metadata is essential:
fixtures that seed native fields directly cannot detect a wrong field mapping.
Regressions cover a 12-disc album sharing year 2023, unrelated year/bitrate
changes, single/missing disc tags, both endian formats, short headers and
numeric wheel navigation through Disc 12. `test_disc_render_native.py` executes
native fonts and RGB565 painting, including preceding-row clips, partial repaint
and scrolled list origins. `test_disc_fast_scroll_native.py` exercises
disc movement and lifecycle through native wheel handling.
Its viewport checks include nonzero origins, partial-row heights, the native
bottom clamp, entry at the final disc, and first-song selection after release.
`test_quick_scroll_native.py` checks signed entry boundaries, both native board
cadences and ordinary alphabet Quick Scroll through full wheel events.
`test_disc_popup_native.py` executes stock binding, notification, view creation,
layout and popup rasterization. Package verification runs these suites against
the exact packaged image and independently checks hooks and preserved stock
regions. Corruption tests cover branch targets, vtables, code and activation
contracts.

These are native-code tests with supplied kernel, heap, clock, disk and display
boundaries. They do not establish real wheel timing, physical display behavior,
audio continuity or full-device reliability. See [release receipts and hardware
status](../../docs/FIRMWARE_RELEASES.md).

## Playback queue creation

`playlist_cache.c` skips empty ordinary sort-index invalidations during native
queue creation. It preserves populated ownership, cleanup order and mutation
notifications. This reduces demonstrated queue-building work; rendering, decoder
startup and device song-opening latency are outside the measured path.

### Selection path and repeated work

Stock script 4166 passes the selected row to Music selector `0x115` at
`0x1e7620`, which enters `0x1ebdd0`. When the existing playback queue cannot
be reused, `0xabb98` / `0x1960b0` create a new queue. The filtered copy loop
at `0x9388c` enumerates source entries through `0x9c838`, checks the native
predicate at `0x1142a8`, and clones through `0x93504` / `0x104edc`.
Only after the selector returns does script 4166 enter Now Playing via 4246.

Each insertion at `0xd6614` calls `0x1137c4`, which invalidates all 48 sort
indexes through `0x110208`. A new queue has empty indexes, but stock still
executes the full per-index function 48 times per cloned song.

### Preserved ownership contract

For each index 1 through 48, native `0x110208..0x110308` checks and releases
five pointer fields at queue + `4*index` + these offsets:

| Array base | Native behavior |
| --- | --- |
| `0x174`, `0x238`, `0x2fc`, `0x3c0`, `0x494` | Release non-null ownership and clear its slot in native order. |

It also owns special representations at index 6 (`+0x488`), 7 (`+0x484`),
9 (`+0x48c`) and 24 (`+0x490`). Empty ordinary indexes perform no stores,
cleanup calls or synchronization. The helper skips only those empty indexes.
Every populated index, every special index and final index 48 still executes
the original function. Index 48 preserves the outer function's return value.

Ordinary runs are split around the special calls to avoid repeated special-ID
comparisons. Reads are volatile and happen anew as traversal advances. Native
destructors can change later slots; no whole-queue snapshot, suppression flag,
retained pointer or batch-invalidation state is introduced. Callee-saved
registers and stack balance are preserved. The three discovered direct callers
at `0x96460`, `0xd671c` and `0xe2578` do not depend on returned condition flags.

The builder patches only the entry word at `0x1137c4`, after SHA-256 checks of
the complete original outer loop and leaf. The independent package audit pins
those same original contracts, validates the branch and extension bytes, and
rejects any unlisted original code change.

### Native notifier and limits of the fixture

A new playback queue does have a query listener. Native initialization uses
`0x941d8(queue+0x30)`, `0x94184(query+0xca4, 0x110110, query)` and registration
`0xa6b0c(queue+0x30, query+0xca4)`. Every clone emits `apli` (`0x61706c69`).
The actual query callback returns at `0x110144`, without changing the query or
writing nonstack memory for this event. The profile executes these native
initializers, registration, notifier and callbacks; omitting the valid notifier
would not model the native queue.

Measurements are retained in
`build/ipod55g-song-queue-review-01/listener-receipt.json` and
`build/ipod55g-song-selection-01/candidate-profile-02.json`.

| Songs copied | Native baseline instructions | Optimized instructions | Reduction |
| --- | ---: | ---: | ---: |
| 16 | 35,377 | 19,105 | 46.0% |
| 256 | 565,537 | 305,185 | 46.0% |
| 2,048 | 4,524,065 | 2,441,249 | 46.0% |

For 2,048 songs the native leaf calls fall from 98,304 to 10,240. The reduction
includes the helper's own execution, not just the avoided native calls. Entry
allocation, storage locking/access and database publication are supplied
boundaries. Rendering, decoder startup, storage latency and concurrent hardware
scheduling are not measured. Queue creation remains linear in list length.

The measured helper adds 644 code bytes and no data or BSS; it does not move
the native heap boundary. Populated indexes pay an additional precheck before
native cleanup; the benefit is strongest for the empty indexes in new queues.

### Regression coverage

`test_playlist_cache_native.py` executes original and patched ARM and checks:

- All 240 ordinary pointer slots and four special representations, separately
  and in populated/sparse combinations, with unrelated-field sentinels.
- Native destructor order, callbacks populating later and already-passed slots,
  final return value, callee-saved registers and stack balance.
- Native queue cloning with filtering, reverse order, selected-song mapping,
  shuffle exclusions, empty results, allocation failure and actual notifications.
- An empty-cache instruction budget and rejection of changed native contracts.

The exact-package verifier runs these tests alongside the feature, alarm, arena
and preservation suites. Device song-opening, playback, Back navigation and
repeat selection remain separate checks; see
[current hardware status](../../docs/KNOWN_ISSUES.md).

## Script-global lookup

Native symbol resolver `0xd6220` scans explicit function IDs at
`0xd6308..0xd6344` before resolving globals. Stock has 15 explicit functions;
the extension adds 13. Without the fast path, ordinary Album row navigation
executes 351 extra loop iterations (2,457 ARM instructions) in the measured
fixture, despite using unchanged stock scripts.

The checked branch at `0xd62f0` replaces `0xe59f210c` with the linked
`ipod55g_script_globals` trampoline. Only decoded atoms 5010 through 5092
skip the function search and resume stock global resolution at `0xd6348`.
All other atoms reload the displaced literal `0x108317b0` into r2 and resume
at `0xd62f4`. The 40-byte trampoline has no writable data, retained pointers,
cache, allocation or resource mutation. It preserves r0, ip, lr, sp and all
callee-saved registers. Both native continuations overwrite its condition flags.

This optimization requires a disjoint native symbol layout. After resource
merging, `script_globals.apply` verifies native instructions/literals, implicit
function base 3961 and endpoint 5010, physical function ordinals, no explicit
function in `[5010,5093)`, and a footer declaring 83 implicit globals and zero
explicit globals. It also checks every trampoline word and branch target before
patching. The independent package auditor repeats the instruction and resource
contracts without importing the installer. Global/footer bytes remain unchanged.

Matched evidence is retained in `build/ipod55g-final-navigation-01/`:

- `resolver-receipt.json`: all 65,536 16-bit atom IDs plus nine malformed,
  negative or overwide values resolve identically; all 18 matched navigation
  profiles preserve UI/query/playback, history, keys, drawing and allocations.
- `resolver-edges-receipt.json`: 64 matched cases cover metadata/source/index
  allocation failures, blank and 255-unit metadata, Unicode/case/whitespace,
  non-track entries, suppression, old history, Back and Home.
- `navigation-receipt.json`: 32/256/2,048-track indexing scales linearly, with
  one predicate pass; 40 alternating Browse/Back cycles have no growth in
  equivalent live-allocation states while freed query indexes are poisoned.

In the matched prototype at 32 tracks, ordinary Album row navigation decreases
from 101,963 to 96,590 instructions; Artist decreases from 103,290 to 97,519.
First Browse Album decreases from 805,884 to 759,752. These are ARM instruction
counts with supplied allocator, storage, display and OS boundaries, not device
latency. `test_script_globals.py` verifies the production linked hook, and
`verify_release.py` reruns it against the exact packaged image. Quick Scroll has
a separate [entry-threshold contract](#native-quick-scroll).

## Stock platform boundary

The target extends the pinned 5.5G platform using its own native services.
Model-specific 4G repairs are not assumed to apply to this firmware. These
boundaries guide changes and define the stock behavior to preserve:

| Native area | Extension boundary |
| --- | --- |
| Gapless playback | Preserve the native decoder/output implementation. The [playback comparison](../../comparisons/iPod_4G_Mono_vs_5_5G_Gapless.md) explains its use as a 4G reference. |
| Long names and alphabet navigation | Native selected-row scrolling and Quick Scroll already exist. Preserve native letter metadata and compilation preference semantics; the [disc feature](#disc-headings-and-numeric-scrolling) defines the scoped changes. |
| Menu and playback controls | Native Hold Menu → Home, short Menu → Back, Volume Limit with optional combination lock, Shuffle and Repeat remain native. The actions menu provides access near playback. |
| On-The-Go | Native list Add/Remove and duplicate-entry identity remain the mutation mechanism. The actions menu operates on the actual playing entry. |
| Clock | World clocks, Alarm, Sleep Timer and Date & Time already exist. Clock has a Main Menu toggle; separate Alarm/Sleep Timer/Date & Time toggles are absent from the inspected customization resource. |
| Pomodoro and Alarm Backlight | Added workflows with independently traced [timer, RTC, widget and backlight ownership](CLOCK_NATIVE.md). The stock stopwatch measures elapsed time/laps rather than interval phases. |
| Other native features | Music Search, color artwork, photos, video and Screen Lock remain native. No general 4G LCD, wheel-delivery or text-scroll tuning is applied. |

The native artist count routine `0x9dec4` calls index builder `0xd080c` with
artist mask 4. The retained disassembly at `0xd0c14..0xd0c48` rejects compilation
tracks before artist deduplication when the compilation preference is enabled.
This is a traced native policy, not a new execution or hardware result.

### Stock interface evidence

`image.py` pins the exact archive/member/OSOS identity. Native code addresses in
these target references are OSOS-relative; RAM and MMIO addresses are explicit.

| Item | Value |
| --- | --- |
| IPSW SHA-256 | `840b2480ad5b692c098cb772c3d2bf8f58bf37557788782b596ac01c27ecdc32` |
| Member | `Firmware-25.6.3` (despite the IPSW name `25.1.3`) |
| OSOS start within member / size | `0x5000` / 7,561,216 bytes |
| OSOS SHA-256 | `784ae3d5540fd2f89e8c947b93629e97f62a06dc311d9745aab143e4db6bb251` |
| Built-in resource root, OSOS-relative | `0x331da0` |
| Resource entries | 2,214 across eight sections; includes 40 menus and 619 views |
| Script resource | `TCmd` ID `0x2380`, OSOS `0x4b441c`, 91,662 bytes |
| Script functions | 1,064 total, including 15 explicit-ID entries |

Command atoms are script IDs. The table records the original binding words.
See the [actions trace](#now-playing-actions) for their native handlers.

| Resource | OSOS offset | Useful records |
| --- | --- | --- |
| Main Menu `0x5e1e` | `0x4aec5c` | Clock action atom 4984. |
| Music `0x5f78` | `0x4af348` | Albums 4182; Artists 4200. These are list-entry actions, not proven current-track shortcuts. |
| Settings `0x6016` | `0x4af530` | Shuffle/Repeat use atom 4320 with bindings `0x60006057` / `0x6000605b`; Volume Limit uses 4384. |
| Main Menu customization `0x6018` | `0x4af804` | 28 rows, including Clock; no separate Alarm/Sleep Timer/Date & Time/Pomodoro toggles. |
| Clock menu `0x6181` | `0x4b01a4` | World Clock 4988, Alarm Clock 5000, Sleep Timer 4326, Date & Time 4309. Presence alone does not prove which model/state displays this menu. |
| Selected-clock menu `0x618e` | `0x4b0248` | Alarm, Change City, DST, Delete This Clock, Sleep Timer; agrees with the documented world-clock flow. |
| Alarm menu `0x61dd` | `0x4b0314` | Alarm toggle, Time, Sound. |

Addresses from differently sliced containers are not interchangeable, and task
labels are not entry points: `AlarmTask` at OSOS `0x25d734` is a name string.
Identify the address space and object from actual bytes; do not apply a blanket
relocation to other references or RAM.

### Verification and remaining limits

Local, ignored evidence lives in `build/ipod55g-stock-assessment-20261125/`:
`inspect_stock.py` and `stock-assessment.json`. Reproduce without modifying the
receipt:

```sh
.venv/bin/python build/ipod55g-stock-assessment-20261125/inspect_stock.py
```

The inspection passed archive identity/ZIP CRC checks, OSOS and RSRC byte sums,
all 2,214 resource-entry bounds, 40 menu record layouts, 1,064 command record
bounds and resolution of every menu script-command reference.

The stock bitmap directory repeats key `0x5f28`, and the stock AUPD stored
checksum does not equal its raw byte sum. The latter's interpretation is
unresolved; this is **not** complete package/flash validation or evidence of a
damaged download. The full stock archive identity and ZIP CRC both match.

This static inspection establishes package identity and structural bounds only.
It does not establish menu reachability, runtime ownership, boot or hardware
behavior. The feature sections above and [Clock reference](CLOCK_NATIVE.md)
describe native execution coverage; [release status](../../docs/FIRMWARE_RELEASES.md)
owns current package validation.
