# Working in iPod Enhanced

Start with [docs/README.md](docs/README.md), then consult the relevant engineering
reference. [Release status](docs/FIRMWARE_RELEASES.md) owns current candidates;
[known issues](docs/KNOWN_ISSUES.md) separates hardware reports from host tests.
Session plans and handoffs belong under ignored `build/`, not public documentation.

## Implementation

- Preserve the native Apple interface and controls. Keep OEM+ additions small
  and reuse native services only where their contracts have been traced.
- This is the active repository. The sibling firmware-research repository is
  an archive; do not resume its failed Classic UI/Cover Flow prototype.
- `gapless/`, `menu_scroll/`, `navigation_qol/` and `clock_pomodoro/` contain
  monochrome 4G addresses. Independent 5.5G work belongs in `targets/ipod55g/`.
  Do not transfer native addresses or installation assumptions between models.
- Use the existing uv `.venv`; never system Python or global package installs.
  Unicorn needs JIT permission. Request the appropriate execution environment
  instead of repeatedly crashing it in a restricted sandbox.
- Preserve pinned inputs, historical binaries, frozen sources and receipts.
  Build into fresh paths and never replace failed evidence with a passing run.
- Complete authorized work and relevant verification. Distinguish static review,
  native host tests and observed hardware behavior. Do not flash to verify a build.

## Before committing or pushing

Use the authenticated `gh` CLI for GitHub operations. Do not use the GitHub
connector; this is the user's standing preference.

Follow [the local pre-push rules](docs/BUILDING.md#pre-push-checks). Their source
is this repository's `.pre-commit-config.yaml`, `requirements-dev.txt` and
`release_tools/verify_repository.py`; no TREX checkout is needed.
Install both hooks with `.venv/bin/python -B release_tools/install_hooks.py`.

- Work on a branch, keep unrelated edits, inspect the full diff and new files,
  and stage explicitly reviewed paths. Never use `git add .` or `git add -A`.
- Keep firmware, generated patches, device data, private installers, credentials
  and local evidence out of Git. Ignoring a path does not untrack it.
- Run pre-commit against the reviewed index and the full repository verifier.
  Pre-push additionally requires a clean committed tree. Never bypass hooks.
- Firmware changes also require the target's package/native release checks and
  frozen rebuild from [the build guide](docs/BUILDING.md). A passing source gate
  alone does not validate firmware. Keep supplemental evidence outside reserved
  artifact names such as `*-layout.json`.
- Update existing documentation owners for changed behavior and validation;
  consolidate obsolete plans without deleting unique engineering evidence.
- Preparing a commit is separate from publishing. Verify the intended remote
  and give the contributor the push command; agents do not push or publish.
