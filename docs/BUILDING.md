# Build, verify and install

Run commands from the repository root. Select the tool for the target: the
4G and 5.5G builders and private installers share a command style, but have
independent native addresses, package layouts and device checks. Current archive
names, hashes and validation results are in [Release status](FIRMWARE_RELEASES.md).

## Environment

Firmware builds on macOS use Xcode Command Line Tools (Clang), uv, FFmpeg and
LAME. The Python patch tools use the separate patch dependency below; using
the generated browser patcher requires no Python environment. Use the existing
`.venv` if configured, or create it with these commands.

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -B release_tools/install_hooks.py
```

Run Unicorn tests in a terminal with JIT allowed. The dependency pins match
the current build environment. The shared 4G UI fixture explicitly selects
ARMv4T instruction semantics; newer Unicorn defaults can hide invalid legacy
ARM/Thumb returns. r44/r45 are withdrawn following the r45 boot-loop report.

## Local inputs

The layered 4G build and tests use these untracked inputs:

- `iPod_4.3.1.1.ipsw`: stock monochrome 4G input.
- `iPod_25.1.3.ipsw`: stock 5.5G decoder/reference input for comparison tests.
- Pinned historical 4G releases and adjacent `.artifacts` directories under
  `build/`, including r31, r28 and earlier verification baselines.
- The r32 archive, frozen sources and original release receipts.
- The r33 archive and its artifacts, required by the r34 cleanup builder.
- The r34 archive and its artifacts, required by the r35 timer-recovery builder.
- The r35 archive and its artifacts, required by the r36 performance builder.
- The r36 archive and its artifacts, required by the r37–r50 disc-heading builder.

A fresh clone needs the local fixture bundle in addition to the stock IPSW.
The local migration manifest records the original copies and their hashes;
it is retained with development evidence and excluded from Git. These build
commands target the monochrome click-wheel 4G.

## Stock inputs and manifest scope

The stock IPSWs remain at the repository root because the builders and native
test fixtures use those paths. They are local files, not tracked source.
The download links below point directly to Apple; both files match these pinned
SHA-256 values. The [tested compatibility chart](../README.md#tested-compatibility) distinguishes
Apple's full package identifiers from the versions displayed in About.

| File | Target | SHA-256 |
| --- | --- | --- |
| [iPod_4.3.1.1.ipsw](https://secure-appldnld.apple.com/iPod/SBML/osx/bundles/061-2691.20060912.ipDcw/iPod_4.3.1.1.ipsw) | Monochrome click-wheel 4G, software 3.1.1 | `576e05def7800e28cfd9a28ec36518964ab7a356f3b035e3fc223b0816be9ed9` |
| [iPod_25.1.3.ipsw](https://secure-appldnld.apple.com/iPod/SBML/osx/bundles/061-2967.20080313.Cnvkg/iPod_25.1.3.ipsw) | Enhanced Video 5.5G, software 1.3 | `840b2480ad5b692c098cb772c3d2bf8f58bf37557788782b596ac01c27ecdc32` |

The ignored `inputs/local-manifest.json` records files copied during the original
repository split. It is a historical migration manifest, **not** a continually updated
inventory of all newer releases. New releases carry their own manifests and
receipts in adjacent `.artifacts/` directories under `build/`.

The [local input list](#local-inputs) identifies additional pinned releases.
Do not delete old artifacts or move stock inputs as a cosmetic cleanup: the
current build is layered and still depends on them.

## Apply a release patch

The [release record](FIRMWARE_RELEASES.md#offline-patch-previews) identifies the
current `.ipepatch` downloads. Each is a small ZIP with exactly two files:
`manifest.json` and `firmware.bsdiff`. Supply the matching stock Apple IPSW and
keep both files intact. The patch contains a BSDIFF40 delta, not a complete
firmware image, private installer, device backup or captured library.

The versioned manifest remains `ipod-enhanced-bsdiff-v1`. It records `format`,
`model`, `revision` and the `Firmware-*` member name in `member`. Its
`source_archive`, `source_firmware`, `target_archive`, `target_firmware` and
`patch` records each specify byte `size` and `sha256`. One patch converts an
exact stock image to a complete OEM+ release; start from stock for every update.

### Browser patcher

Open the generated `index.html`, select the **Stock IPSW** and
**Patch (.ipepatch)**, then click **Apply patch**. The patch
supplies its model and revision. The download appears only after the source,
delta, firmware member and complete rebuilt IPSW match the patch's hashes.
Changing either input, cancelling or restoring the page clears the previous
download. Output filenames are derived from the patch's model and revision;
the IPSW bytes still match the original release archive exactly.

The page contains no release payloads or release catalog. New compatible
`.ipepatch` releases work without rebuilding or redeploying the web app. Use
patches from a trusted source: uploaded checksums verify consistency with the
patch, not the publisher's identity or the firmware's safety.

Firmware and patch files stay in browser memory. The self-contained HTML works
locally or on a static HTTPS host such as GitHub Pages. It needs Web Workers,
Web Crypto and file/Blob APIs, with no uploads, accounts, analytics, runtime
CDNs or Python installation. A worker handles decompression and patching;
the page's content policy blocks network connections. File patching does not
install firmware on the iPod.

The [release record](FIRMWARE_RELEASES.md#browser-patcher-preview) identifies
the current page and tested browsers. Build a fresh copy without any firmware
or patch inputs:

```sh
.venv/bin/python -B release_tools/build_web.py --output build/browser-patcher-local
```

The builder embeds only allowlisted page/worker sources and license notices.
It checks vendored source hashes, refuses an existing output directory and
publishes the page atomically. No embedded release whitelist is used by this
upload-based app; patch provenance belongs to the distribution source.

The parser accepts exactly the two named ZIP entries, in either order, using
stored or deflated data. Encryption, split archives, ZIP64, data descriptors,
duplicate/extra entries and inconsistent offsets or headers are unsupported.
The IPSW limit is 32 MiB; delta limit 16 MiB; manifest limit 16 KiB; `.ipepatch`
file limit 16 MiB + 64 KiB. Decompression is bounded before accumulating output.
BSDIFF controls use signed 64-bit values within JavaScript's safe-integer range,
with at most 65,536 records and a 32 MiB output. Invalid inputs yield no download.

Uploaded metadata is parsed as JSON and displayed as text; patch bytes are never
evaluated as JavaScript. The hash-based content policy blocks injected scripts,
inline handlers, embedded frames and network requests. Blob workers inherit
that policy, including its bans on dynamic evaluation and script imports
([CSP worker behavior](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy#csp_in_workers)).
The worker remains cancellable and is terminated after 120 seconds.
Bzip validation covers individual and combined CRCs, exact stream consumption,
zero padding, bounded Huffman lengths, selector exhaustion and invalid symbols.
These controls reduce the attack surface; checksums inside an unsigned patch
do not authenticate its author or establish that its firmware is safe to install.

The browser reuses pinned Rom Patcher JS BSDIFF40/BinFile code and pako 2.1.0.
Vendor provenance and local corrections, including bounded bzip decoding and
signed control handling for uploaded patches, are recorded in
[`sources.json`](../release_tools/web/vendor/sources.json). MIT and zlib notices
remain in the page. Both firmware and final archive hashes must match, so a
compression mismatch cannot produce a downloadable file.

```sh
.venv/bin/python -B -m unittest discover -s release_tools
node --test release_tools/web/test_core.cjs
IPOD_WEB_REAL=1 node --test release_tools/web/test_core.cjs
```

The last command uses local stock IPSWs and the immutable `.ipepatch` files
listed in the release record. `release_tools/web/test_browser.cjs` tests the
actual page, both file inputs, rejection/cancellation, downloads and mobile
layout using Playwright. Its security probes also submit hostile patch metadata
and attempt script injection, worker evaluation/imports, frame embedding and
network access with the real page policy enforced. Expected CSP diagnostics from
these probes are separate from the error-free normal workflow.
Set `PLAYWRIGHT_MODULE` to its package directory and
pass the built HTML path and a fresh evidence directory. Chromium defaults to
installed Edge; `IPOD_WEB_BROWSER_EXECUTABLE` selects another installed Chromium
browser. `IPOD_WEB_BROWSER=webkit` uses a Playwright WebKit installation. Browser
test dependencies belong in ignored `build/` storage.

### Apply with Python

The `.ipepatch` is data-only. Extract its two files into a directory and use the
repository's public helper with its optional dependency in the existing venv:

```sh
uv pip install --python .venv/bin/python -r release_tools/requirements.txt
unzip path/to/ipod4g-r50.ipepatch -d build/local-patch
.venv/bin/python release_tools/apply_patch.py iPod_4.3.1.1.ipsw --bundle build/local-patch --output build/local/ipod4g-r50.ipsw
```

Create the output parent first and choose a new filename. The helper checks all
five identities and never overwrites existing output. It makes no network or
device requests and needs no administrator privileges; dependency installation
may download a package. Use a local filesystem supporting hard links, such as
APFS, NTFS or ext4. FAT/exFAT output is unsupported by atomic publication.

The delta patches the uncompressed `Firmware-*` member inside the IPSW. Exact
archive reconstruction depends on reproducible ZIP compression. A different
compression implementation fails without publishing output. `--firmware-only`
emits the exact verified member instead; this is neither raw OSOS nor a device
partition. Standard `bspatch` can apply the extracted delta, with source and
output identities checked separately against the manifest.

Earlier helper ZIPs remain usable and include `apply_patch.py`, dependency
instructions, licenses and checksums. The browser accepts the two-entry
`.ipepatch` format; those seven-entry helper ZIPs are not browser patch inputs.
No public installation workflow has been validated. Keep private flashers
private; compatibility with Finder/iTunes, iPodWizard, iPodFirm or Rockbox tools
requires separate validation.

### Build patch bundles

The builder uses pinned bsdiff4 and verifies stock/release identities and exact
reconstruction before publishing a `.ipepatch`. It does not rebuild firmware.
Choose fresh files in an existing directory:

```sh
.venv/bin/python -B release_tools/build_patch.py --target ipod4g --stock iPod_4.3.1.1.ipsw --firmware build/ipod4g-r50-attempt-01/iPod_4.3.1.1-gapless-navigation-complete-r50.ipsw --output build/local/ipod4g-r50.ipepatch
.venv/bin/python -B release_tools/build_patch.py --target ipod55g --stock iPod_25.1.3.ipsw --firmware build/ipod55g-oemplus-r19-attempt-03/iPod_25.1.3-oemplus-r19.ipsw --output build/local/ipod55g-r19.ipepatch
```

Use `--with-tools` and a `.zip` output name for the legacy seven-file helper
bundle. Both formats preserve the existing manifest contract and BSDIFF40
delta. Private installers are outside both artifact allowlists.

### Existing iPod tools

[iPodWizard](https://github.com/DavidBuchanan314/iPodWizard-mirror) is the
historical firmware-editing project. Its
[`.ipw` themes](https://github.com/DavidBuchanan314/iPodWizard-mirror/blob/main/ThemesDialog.cpp)
are ZIP packages of resources. The
[revival's README](https://github.com/Stehfyn/iPodWizard-stable) documents its
legacy Visual Studio/MFC dependencies and incomplete original source.
[iPodFirm's `.ipodpatch`](https://github.com/TheFauxDreamer/iPod5.5Firm/blob/main/docs/patch-system.md)
is a ZIP of a patch folder containing `patch.json` and C source or compiled ARM
code; byte-change patches can omit code. Its Mac app applies these feature
patches to Video firmware 1.3.
[Rockbox's ipodpatcher](https://github.com/Rockbox/rockbox/tree/master/utils/ipodpatcher)
uses `.ipod` and `.bin` files with distinct firmware-image and partition
read/write modes. These projects use different packaging contracts; they do not
establish a shared iPod patch format. None is established as an installer for
these OEM+ releases, and our bundles are not iPodWizard or iPodFirm modules.

## 4G build and verify

Choose a fresh output path. Builders refuse to overwrite existing evidence.

```sh
.venv/bin/python navigation_qol/disc_build.py --output build/local/ipod4g-r50.ipsw
.venv/bin/python navigation_qol/verify_disc_package.py build/local/ipod4g-r50.ipsw --self-test
.venv/bin/python clock_pomodoro/verify_release.py build/local/ipod4g-r50.ipsw
```

`verify_release.py` runs the native ARM, audio, UI and package tests and uses
FFmpeg and LAME to generate audio fixtures. It writes per-suite logs and
`verification.json` alongside the candidate and refuses to replace the receipt.
Reserve `*.elf`, `*-symbols.json`, `*-layout.json` and `*-edits.json` for build
artifacts: preservation validators inspect that complete set. Name additional
review evidence with a distinct suffix such as `-audit.json`.

## 5.5G build and verify

Run these commands from a checkout with the 5.5G source and stock
`iPod_25.1.3.ipsw`. Use the uv `.venv`, Clang and the pinned Zig linker.
Choose a fresh output path for each build. [Input hashes](#stock-inputs-and-manifest-scope)

```sh
.venv/bin/python targets/ipod55g/build.py --output build/local55g/candidate.ipsw
.venv/bin/python targets/ipod55g/verify_package.py build/local55g/candidate.ipsw
.venv/bin/python targets/ipod55g/verify_release.py build/local55g/candidate.ipsw
.venv/bin/python targets/ipod55g/verify_rebuild.py build/local55g/candidate.ipsw
```

Run the native ARM checks in a terminal with JIT allowed. Logs, build files
and verification receipts go in the adjacent `.artifacts` directory.

## Release evidence

Working documentation may be updated after a release. Reproduce that release
from its frozen sources; do not rewrite its source manifest, receipt or failed
attempts to match later documentation. A documentation-only update does not
create a new firmware revision. Keep build-local handoffs and supplemental
audits outside reserved artifact names.

The imported source included enhancements from the reference repository's
working tree, so its Git HEAD alone cannot reproduce that import. Current
releases depend on the retained snapshots and pinned inputs listed above;
original import manifests describe the initial copy, not today's source tree.

For r37–r50 and r33–r35, use `verification.json`, `source-rebuild.json` and
`independent-review.json` in the adjacent `.artifacts` directory. For r32, use
`verification-final.json`. r36's successful receipt and frozen rebuild live at
`build/ipod4g-stock-performance-01/r36-attempt-02.artifacts/`; its canonical
directory retains an earlier failed validator receipt. The archives are byte
identical. [Release record](FIRMWARE_RELEASES.md)

The optional `--diag` flag on the historical base playback builder retains
development diagnostics. Current release builders do not enable it. See
[release diagnostics](../comparisons/iPod_4G_Mono_vs_5_5G_Gapless.md#release-diagnostics).

## Pre-push checks

This repository owns its pre-commit and pre-push rules in
[`.pre-commit-config.yaml`](../.pre-commit-config.yaml), with tool pins in
[`requirements-dev.txt`](../requirements-dev.txt). They adapt TREX's publication,
branch, documentation and verification rules to these native firmware fixtures;
no TREX checkout is needed. Hooks check files without rewriting them.

Keep current contracts, controls, build instructions and validation limits in
their existing document owners. Remove obsolete plans, completed checklists
and revision diaries from the publishable tree after migrating unique durable
content and repairing inbound links. Preserve local originals and release
evidence under ignored `build/` paths.

Inspect `git status --short` and the full diff, including new files. Keep
firmware, generated binaries/patch bundles, private installers, credentials and editor/session
state out of Git; `.gitignore` does not untrack files already in the index.
Stage only explicitly reviewed paths, without `git add .` or `git add -A`.

For firmware changes, run the target's build, package, native release and frozen
rebuild checks above before claiming the candidate is ready. Use fresh output
paths and retain failed attempts. For documentation or comment/format-only
changes, check links and heading anchors across both tracked and new documents,
scan for references to removed files, validate affected Python/JSON, and run:

```sh
git diff --check
```

After explicitly staging reviewed public paths, run:

```sh
.venv/bin/pre-commit run --all-files --hook-stage pre-commit
.venv/bin/python -B release_tools/verify_repository.py
```

The verifier checks the exact public inventory, private/generated-file exclusions,
credential and personal-path patterns, vendor hashes, file hygiene, Markdown links
and anchors, Python correctness, public release-tool formatting/imports/types,
Python and JavaScript patcher tests, Bandit and installed dependency vulnerabilities.
Direct requirement pins are checked first; pip-audit then covers the complete local
venv, including transitive packages and additional developer tools.
It rejects unstaged public changes and omitted new source. Its fresh receipts live
under `build/repository-checks/`; a failed run is never overwritten. `--static`
skips tests and dependency auditing and reports partial verification.

Native firmware harnesses retain their existing unittest style and dynamic
fixtures; critical correctness lint covers all public Python, while conventional
formatting and type checks apply to `release_tools/`. Vendored files retain their
pinned bytes. Bandit records all severities: medium/high and untriaged low findings
block. Reviewed low subprocess findings are limited to local build/test/Git calls
with argument lists, no shell, and a trusted developer PATH. Noncryptographic RNG
is accepted only in the three named deterministic emulator fixtures. Native
assertions are allowed in explicit test/contract scopes; optimized Python is
rejected so those checks remain active. Public patch parsers have no assertion
or hardcoded-secret exemption.

For an unstaged review, `--candidate path/to/inventory.json` accepts an explicit
`{"files": {"relative/path": "sha256", ...}}` manifest. It verifies each hash and
rejects omitted public files. This does not replace the final index checks. After
committing, the direct pre-push hook checks the original worktree before any
temporary stash, validates outgoing refs and scans their complete history. It
rejects protected main/master destinations and commits other than the checked-out
commit, then runs the full verifier with `--require-clean`. It must not be bypassed.
Use `install_hooks.py` to install both hooks; installing pre-commit's own pre-push
wrapper directly would omit these checks. Dependency auditing needs network access. Browser-engine
tests and native firmware release checks remain the separate release gates above.

Confirm that published package hashes and their frozen artifacts remain
unchanged. Report previous native test receipts as previous evidence, not as a
new full-suite run. Pattern scans are useful checks, not a guarantee that every
possible form of personal information has been identified; review the full diff
and history before first publication.

Preparing a commit or PR is separate from publishing. Verify the intended
branch and remote first, keep commits off the shared default branch, and hand
the push command to the contributor; agents do not push.

## Publishing patches and the web app

Publish the two reviewed `.ipepatch` files as GitHub Release assets, with their
SHA-256 hashes from the release record. Do not upload IPSWs, device snapshots,
private installers or the development `build/` tree. Users supply their own
matching stock IPSW to the browser patcher. These remain developer previews
until a public device-installation path has been validated.

The [Pages workflow](../.github/workflows/pages.yml) builds the self-contained
page from public source and uploads only `build/pages`. It needs no Apple inputs,
patch payloads or third-party Python packages. All actions are pinned to commit
hashes. The build job has read access; only deployment receives Pages and OIDC
write permissions.

After creating the personal repository and pushing the reviewed branch, merge
the workflow into its default branch, choose **GitHub Actions** in repository
**Settings → Pages**, and manually run **Publish browser patcher** from Actions.
The workflow has no push trigger. Publishing new compatible patch releases does
not require redeploying the page. Repository creation, source push, patch release
and page deployment are separate publication actions.

## Local installation tools

The private tools are not distributed with the public repository. In the
development checkout, both take the firmware archive as a positional argument:

```sh
sudo .venv/bin/python gapless/flash_firmware.py build/ipod4g-r50-attempt-01/iPod_4.3.1.1-gapless-navigation-complete-r50.ipsw
sudo .venv/bin/python targets/ipod55g/private/flash_firmware.py build/ipod55g-oemplus-r19-attempt-03/iPod_25.1.3-oemplus-r19.ipsw
```

Choose only the command for the connected model. The 4G tool supports the
Mac-formatted monochrome model; the 5.5G tool validates its separately captured
device layout. Do not reuse an old disk number or a staged `read_device.py`
path as the general installation command. Build/review validation does not
require flashing, and an installer readback is not a runtime hardware test.

The 5.5G installer preserves the complete native firmware-partition suffix,
including metadata and HIBE allocations absent from the IPSW directory.
Installing stock firmware restores active payloads; exact restoration of every
original partition byte requires the full-partition backup. Keep that backup
after a successful stock install. The private 5.5G restore command is:

```sh
sudo .venv/bin/python targets/ipod55g/private/flash_firmware.py --restore path/to/backup
```

Device identity, backup hashes and planned write ranges must pass the installer's
checks before restoration. Neither a recognized revision nor an already-installed
no-op establishes that a new write or runtime hardware test occurred.
