# Firmware release status

These are local developer-preview candidates. Use the [user guide](USER_GUIDE.md)
for controls and [build guide](BUILDING.md) for reproduction and installation.
Native ARM tests supply OS and hardware boundaries; they do not establish
full-device reliability, rendering or timing. Nothing has been published.

| Target | Current candidate | About → Version | Local validation |
| --- | --- | --- | --- |
| Monochrome 4G, Apple 3.1.1 | r50 | `3.1.1 / r50` | 74 suites, 450 counted tests; exact frozen rebuild. |
| Late-2006 5.5G, Apple 1.3 | r19 | `1.3 / r19` | 222 release checks; exact frozen rebuild. |

Both candidates retain the existing feature set and add the Enhanced revision
inside the native About Version row. Apple's firmware compatibility identifiers
are unchanged. Hardware acceptance and outstanding observations are maintained
in [Known issues](KNOWN_ISSUES.md); neither new About display has device acceptance.

**4G r44 and r45 remain withdrawn.** The r45 boot loop came from a boot-time
Thumb call whose Apple ARM return could not switch back on ARMv4T. r44 shared
the defect. r46 corrected it, and the user confirmed r46 boots. Earlier tests
using a newer emulator CPU model did not establish compatibility. Keep those
archives as evidence, not installation candidates.

## Current monochrome 4G candidate

```text
build/ipod4g-r50-attempt-01/iPod_4.3.1.1-gapless-navigation-complete-r50.ipsw
SHA-256 6fc37ae7747831b543b57a2b08ea0bd9dc7a4431db003d308e7c3bf8a3f26a28
OSOS SHA-256 55f98984e50f9c81254e88e0cefd13dccdc606e25feee509ede7a9c2a0e8bce4
```

The About change replaces 34 bytes in its dedicated version formatter, reusing
the native string conversion, localized label and fonts. It adds no code extent
or persistent state. Native property reads, preserved registers, output guards
and stock font sizing are covered. The English value measures 69 pixels and
fits the existing row. OSOS remains 3,735,552 bytes, exactly within its image
limit; the navigation overlay remains 6,976 bytes.

This retains r49's shared top-reveal path for Songs, host sort-title priority
for articles/custom names, ARMv4T boot correction and ordinary forward scrolling
inside the final disc. Real disc jumps still select the first song and align
the heading within Apple's bottom limit. Native performance measurements and
input contracts remain in the [4G engineering notes](../navigation_qol/README.md).

The full frozen-source release run passed 74 suites and 450 counted tests.
All 218 frozen source files were checked; the rebuild matches the firmware and
44 artifacts. Three private installer tests passed without device access.
Independent comparison confirms that resources, scripts, carried ELFs and
updater identity are unchanged from r49. The adjacent `.artifacts` directory
contains `verification.json`, `source-rebuild.json`, `independent-review.json`,
`source-integrity-audit.json` and `installer-offline-audit.json`.

## Experimental 5.5G candidate

```text
build/ipod55g-oemplus-r19-attempt-03/iPod_25.1.3-oemplus-r19.ipsw
SHA-256 dabe75b6eccbb460b34f7e6080c3915eccae1f1879e2f2d66dddc61457fe334b
OSOS SHA-256 7184e27eb8daaaa3727f63d3859dde52c4bc804ea105b9ced03cb7f84c3deb44
```

The About change binds only the Version value to a new native string resource.
The stock version getter still returns `1.3`; the localized label, fonts,
controls, scripts and compatibility identifiers remain unchanged. Native widget
construction and layout run in the host fixture. FreeType measures the exact
stock Podium Sans 18 font: the new value occupies 64 pixels. This checks fit,
not Apple's glyph rasterizer or a physical LCD.

This retains r18's suppression of inactive forward disc-jump entry in the final
disc, normal reverse-entry thresholds, active-mode endpoint clamp and four
saved Clicker choices. Playback remains stock. See the independent
[5.5G contracts](../targets/ipod55g/README.md).

The image's loaded extent is 7,717,668 bytes; disk OSOS is 7,718,912 bytes.
The final r19 run passed 222 native/package checks, and all frozen sources
reproduce the archive and ELF exactly. The current verifier also passed 215
checks on a fresh copy of r18, selecting About tests only for packages with
the new resource. Firmware bytes are identical across all three attempts;
the later attempts adjust host tests only. Forty-eight private installer tests passed,
including r18 upgrades, suffix preservation and repeat-install no-op. No device
was accessed. Current evidence is under `build/release-prep-01/about-55g/` and
in the candidate's adjacent `.artifacts` directory; earlier attempts stay immutable.

## Offline patch previews

These data-only `.ipepatch` files reconstruct the candidates above from the
matching stock IPSWs. Each ZIP contains exactly `manifest.json` and
`firmware.bsdiff`, using `ipod-enhanced-bsdiff-v1`. Complete firmware, private
installers and device data are excluded. See
[patch instructions](BUILDING.md#apply-a-release-patch).

| Target | Bundle size | Delta size |
| --- | ---: | ---: |
| 4G r50 | 36,270 bytes | 35,754 bytes |
| 5.5G r19 | 16,499 bytes | 15,990 bytes |

```text
build/release-prep-01/patches-01/ipod4g-r50.ipepatch
SHA-256 959685bf6fd87607d9c4a92fb1bbd0df1cd91a616d33fb655634cfbb0946b033

build/release-prep-01/patches-01/ipod55g-r19.ipepatch
SHA-256 1e1d39895f68abac0cb622cdfc70a305c48abca69976971bb4acae0398445502
```

Both reconstruct the exact archive through the public Python helper, native
`bspatch` plus independent ZIP reconstruction, and both tested browser engines.
Checksums verify consistency; an unsigned patch does not authenticate its author.
No public installation method has been validated. These checks do not establish
Finder/iTunes, iPodWizard, iPodFirm or other third-party installer compatibility.

## Browser patcher preview

Choose an original Apple IPSW and a downloaded `.ipepatch`, then save the verified
result. Files are processed locally in browser memory. The app contains no
firmware payloads or release catalog; future compatible patches do not require
a site rebuild. It runs as local HTML or on static HTTPS hosting.

```text
build/release-prep-01/site-01/index.html
Size: 114,076 bytes
SHA-256: 7f749e74f315688ae74a24544f4f4202c4e608dbd7a41b0ae1e588f7109e4aa5
```

The page is byte-identical to the previously hardened build. Source, delta,
firmware member and final archive hashes must all match. Uploaded metadata is
text, not executable code. Strict ZIP/BSDIFF/bzip validation bounds allocation
and work; the page policy blocks injected scripts, frames and runtime network
requests. Vendor provenance and corrections are in
[sources.json](../release_tools/web/vendor/sources.json), with licenses retained.

Fresh release validation passed 29 JavaScript core checks and 17 checks each in
Brave and Playwright WebKit on macOS. Both downloaded the exact r50/r19 archives
and covered wrong inputs, corruption, cancellation, stale downloads, mobile-width
layout and enforced browser security policy. No HTTP requests or application
errors occurred; deliberate policy violations have separate expected diagnostics.
This is browser-engine coverage on macOS, not Windows/Linux/mobile device testing.
Receipts are under `build/release-prep-01/browser-brave-01/` and
`build/release-prep-01/browser-webkit-01/`.

The original hardening evidence under `build/patcher-hardening-01/` includes
51 independently generated bzip cases, 1,500 bounded deterministic mutations,
regressions for malformed Huffman tables and signed BSDIFF movement, and exact
source rebuilds. The current source-only Pages build also reproduces the page
without Apple files or patches. [Publishing instructions](BUILDING.md#publishing-patches-and-the-web-app)
cover the manual workflow and separate patch assets; neither has been published.

Earlier firmware releases, `.ipepatch` bundles, helper ZIPs, embedded-patch pages
and their receipts remain immutable in local `build/` evidence. Reproduce them
from their frozen sources, not the current working documentation.
