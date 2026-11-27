# iPod Enhanced User Guide

For the monochrome click-wheel **4G** and the late-2006 **5.5G**. These are
separate firmware targets; the differences below matter when following menus.

## Identifying the installed revision

Open **Settings → About**. The Version value keeps Apple's version and adds the
Enhanced revision: `3.1.1 / r50` on 4G or `1.3 / r19` on 5.5G for the current
candidates. Earlier builds show only Apple's version. This changes the displayed
label, not the firmware's updater identity.

## Browsing and playback

The enhanced 4G adds gapless playback for compatible AAC, MP3 and ALAC tracks,
scrolling highlighted names, compilation filtering in Artists, and Hold Menu
to return to the main menu from most screens. Compilation tracks still appear
when browsing their artist. Format changes or slow disk reads can still leave
a playback gap. The 5.5G keeps its native playback and browsing features.

### Disc headings and disc Quick Scroll

Tagged multi-disc albums show `Disc 1`, `Disc 2`, and so on, separated by thin
rules. The 4G uses small monochrome text; the 5.5G uses its native
Contacts/Calendar font and gray rules. Headings are not selectable or playable:
normal scrolling moves between songs.

Spin the wheel quickly to jump between discs. The usual popup shows just the
disc number. Backward movement from inside a disc first selects its first song,
then moves to earlier discs. The heading moves to the top where space permits;
a short final disc keeps Apple's normal bottom limit, without blank padding.
After a disc jump, releasing the wheel leaves that disc's **first song highlighted**.
On both models, forward scrolling while already in the final disc stays ordinary
song scrolling without activating the popup. Backward input can still activate
disc jumps. If disc mode is already active when you reach the end, forward
input stays there. On **4G**, pause for about a second to return to ordinary
scrolling. On **5.5G**, release the wheel or let its native timeout expire.

Albums tagged as disc 1 of 1 keep their ordinary list. A partial multi-disc
album containing only a later disc can still show its heading. Missing disc
numbers on any song, or more than 256 disc runs, disable grouping for that
album. Disc Quick Scroll does not require 32 songs.

### Letter Quick Scroll

On the **4G**, spin quickly in Artists, Albums, Genres or Composers with at
least 32 entries. The letter follows the library's sort name when available
and skips leading punctuation, so `#Goldrush` shows G and `(R)Evolution`
shows R. The highlighted row moves to the top when possible and the last
page stays full. The existing activation and repeat timing are retained.

The main **Songs** view also supports title-letter jumps. A fast stroke must
cross into another letter before the popup appears; entry keeps your normal
scroll landing, then further movement jumps between letter groups. There is
no 32-song minimum. While the popup is active, the highlighted song moves to
the top when possible, with the same full-last-page limit as Albums and Artists.
Pause for about a second to return to ordinary scrolling.
The letter uses the library's sort title when available, including article
handling such as `The Alpha` under A, and shares Albums' punctuation handling.
Playlist contents and album track lists retain their existing controls,
including numeric disc jumps in multi-disc albums.

On the **5.5G**, native Quick Scroll across lists activates with about 20%
less initial movement (18.75% at Apple's whole-unit threshold). Ordinary
scrolling speed, repeat cadence and popup timing are unchanged.

## Now Playing shortcuts

On either model, hold **Center** on music Now Playing, then use the wheel and
Center to pick an action. A quick press still cycles the usual controls.

| Action | What it does |
| --- | --- |
| Browse Album | Opens the current song's album. |
| Browse Artist | Opens the current artist's albums, including compilations. On 5.5G it stays at Albums even if there is only one. |
| Shuffle | Each press cycles Off, Songs and Albums. Both shuffle modes use the same icon. |
| Repeat | Each press cycles Off, One and All. All loops the current album, playlist or other list. |
| Sleep Timer | Opens the native Sleep Timer controls. On 5.5G, it goes directly to the duration picker without selecting a world clock. |
| Add to On-The-Go | Adds the current song to the playlist. Duplicates are allowed. |
| Remove from On-The-Go | Appears while playing from On-The-Go itself. Removes the playing entry; other copies stay on the list. On 4G, this also stops playback. |

Menu goes back one level. On 4G, returning from Browse Album or Browse Artist
reopens the shortcuts; another Menu press returns to Now Playing.

## Setting a volume limit

Open **Settings → Volume Limit**. On 4G, turn the wheel to set the maximum
headphone volume, then press **Center** to save or **Menu** to cancel. You can
change it later without a passcode. The 5.5G keeps Apple's native Volume Limit,
including its optional combination lock.

## Clicker output on 5.5G

In **Settings → Clicker**, press Center to cycle **On → Off → Speaker →
Headphones**. On enables both outputs; Speaker uses the built-in clicker,
and Headphones sends the click through the audio output. The choice is saved.
Apple's video behavior is retained: On uses the built-in clicker during video
playback, and headphone clicks depend on the active audio path.

## Using Pomodoro

| Model | Location and appearance |
| --- | --- |
| 4G | **Extras → Clock → Pomodoro**, using Clock's native large digits. |
| 5.5G | **Extras → Pomodoro**, immediately below Stopwatch, with the native Stopwatch backdrop, digits and silver/blue buttons. The menu row has the usual submenu arrow. |

Select **Start**. The timer alternates 25 minutes of focus with a 5-minute
break and beeps at each change. Every fourth break lasts 15 minutes.

| To | Do this |
| --- | --- |
| Pause or resume | Select Pause or Start. |
| Cancel | Select Reset to return to Focus 25:00. |
| Leave the timer running | Press Menu. |

Play/Pause still controls your music. Restarting the iPod cancels the timer.

## Adding shortcuts to the main menu

On **4G**, in **Settings → Main Menu**, look under Extras for **Alarm Clock**,
**Sleep Timer**, **Date & Time** and **Pomodoro**. Turn on the ones you want
next to Clock, above Contacts, Calendar and Notes.

On **5.5G**, the Extras group in **Settings → Main Menu** lists **Pomodoro**
immediately below Stopwatch. Its pin is off by default, is saved when enabled,
and is removed by Reset Main Menu. Clock retains its native Main Menu option;
the separate 4G Clock shortcuts are not added to the 5.5G.

## Alarm Backlight

**Settings → Alarm Backlight** is on by default on both models. Clock and
Pomodoro alarms use the full native beep with four flashes, each 350 ms on
and 250 ms off, then restore normal backlight policy. On 4G, shorter Calendar
alerts use a single pulse. The sound comes from the built-in buzzer. With the
setting off, the screen can still light up normally when the iPod wakes.

## Design references

The additions follow each model's native interface: Clock digits on 4G,
Stopwatch graphics and Contacts/Calendar-style headings on 5.5G. In-place
Shuffle/Repeat, numeric disc jumps and the chosen menu ordering are project choices.
New labels are mostly English; reused stock labels follow the language setting.

Pomodoro is inspired by [Francesco Cirillo's Pomodoro Technique](https://www.francescocirillo.com/blog/pomodoro-technique-was-never-just-a-timer/).
The alarm flash cadence is 350 ms on / 250 ms off, taken from
[Android's notification flash implementation](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/master/services/accessibility/java/com/android/server/accessibility/FlashNotificationsController.java).
The Now Playing menu adapts contextual browsing and the selected-entry
On-The-Go action from the [Classic guide, pages 6, 28 and 29](https://cdsassets.apple.com/live/6GJYWVAV/user/ma1195_ipod_classic_160gb_user_guide.pdf).

## Generation references

Apple calls the 5.5G the **iPod (5th generation Late 2006)**. It added
Search, a brighter display and an 80 GB option. The original 5G also got
the Quick Scroll letter overlay and gapless playback in firmware 1.2.
[Apple's model guide](https://support.apple.com/en-us/103823) ·
[2006 announcement](https://www.apple.com/newsroom/2006/09/12Apple-Introduces-the-New-iPod/) ·
[Contemporary 5G update report](https://daringfireball.net/2006/09/fifth_generation_ipod_features)

- **Volume Limit** arrived on the original 5G in firmware 1.1.1.
  **Hold Menu** is documented in Apple's original 5G guide.
  [Volume Limit announcement](https://www.apple.com/ie/newsroom/2006/03/29Apple-Provides-Volume-Limit-Setting-for-iPod/) ·
  [Original 5G Features Guide, p. 6](https://usermanual.wiki/Document/ipod5thgenerationfeaturesguideenglish.1651781314.pdf)
- **Compilation filtering** predates the 5G refresh. The iPod photo had a
  Compilations switch. On the enhanced 4G, this filter is always on.
  [Archived Apple support note](https://savagetaylor.com/til/TA23005.html) ·
  [iPod photo owner report](https://forums.ilounge.com/threads/why-are-artists-in-compilation-albums-not-grouped-on-the-ipod.77327/)

[Current releases](FIRMWARE_RELEASES.md) · [Known issues](KNOWN_ISSUES.md) ·
[Engineering details](README.md)
