# iPod Enhanced

I wanted gapless playback on my monochrome iPod 4G. With help from Claude
and Codex, I got it working and added a few features I liked on later iPods,
using the 4G's own firmware. Pomodoro was just for fun.

The project now also has a separate **5.5G** target, built on Apple's 1.3
firmware. Both keep the native Apple interface and controls.

[Open the browser patcher](https://stevenz.me/ipod-enhanced/) ·
[Download patches](https://github.com/sd-zhang/ipod-enhanced/releases/tag/r50-r19)

Download the patch for your model, then supply it and your own matching stock
Apple IPSW to the patcher. Processing happens locally in your browser.

## What's new compared with stock

### Monochrome 4G

- **Gapless playback** for compatible AAC, MP3 and ALAC tracks. [5G+]
- **Long names scroll** when highlighted, with longer pauses for reading. [Enhancement]
- **Artists** hides artists who appear only on compilations. [iPod photo · Enhancement]
- **Disc headings** separate tagged multi-disc albums with small labels and thin rules. [New]
- **Quick Scroll** adds letter jumps in Songs, Artists, Albums, Genres and Composers. In multi-disc albums it jumps between discs and shows the disc number. [5G+ · Enhancement]
- **Hold Menu** to return to the main menu from most screens. [5G+]
- **Hold Center on Now Playing** to browse the current album or artist, or get to Shuffle, Repeat, Sleep Timer and On-The-Go. [Classic-inspired · Enhancement]
- **Volume Limit** caps headphone volume. Find it in Settings. [5G+]
- **Pomodoro** in Extras → Clock alternates focus and breaks, using the Clock's large digits. [New]
- **Clock shortcuts** in Settings → Main Menu can pin Sleep Timer, Pomodoro and other Clock items. [Enhancement]
- **Alarm Backlight** in Settings flashes the backlight for Clock alarms and Pomodoro. [New]

### 5.5G

- **Now Playing shortcuts** for album/artist browsing, Shuffle, Repeat, Sleep
  Timer and On-The-Go, opened by holding Center.
- **Pomodoro** below Stopwatch in Extras, with native Stopwatch graphics and
  a saved Main Menu option.
- **Alarm Backlight** for Clock and Pomodoro alarms.
- **Disc headings and numbered Quick Scroll**, using the native color interface.
- **Easier Quick Scroll activation** across lists: about 20% less initial movement.
- **Clicker output choices** restore Speaker and Headphones alongside On and Off.

The 5.5G retains its stock gapless playback, letter scrolling, Hold Menu and
Volume Limit. Both targets reduce repeated work in native queue creation,
script lookup and disc scrolling. [Performance evidence](docs/README.md#performance-work)
Settings → About also shows the Enhanced revision alongside Apple's version.

## Documentation

[User guide](docs/USER_GUIDE.md) · [Build and install](docs/BUILDING.md) ·
[Current releases](docs/FIRMWARE_RELEASES.md) · [Known issues](docs/KNOWN_ISSUES.md) ·
[Engineering guide](docs/README.md) · [MIT license](LICENSE)

The [browser patcher](docs/BUILDING.md#browser-patcher) takes an original Apple IPSW
and a separate `.ipepatch` file to create enhanced firmware on your computer. It is a
self-contained HTML file using Rom Patcher JS; no uploads, Python or terminal
are needed to use it. [Python patch tools](docs/BUILDING.md#apply-with-python)
remain available. Complete firmware images and private device installation
tools stay local. These are developer previews, with no public installation
method validated.
