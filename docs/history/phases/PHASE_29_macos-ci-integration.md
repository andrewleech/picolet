# PHASE 29 — macOS CI integration

## Goal

Expand the GitHub Actions build matrix from 6 artifacts (2 targets × 3
variants) to 12 (4 targets × 3 variants) by adding `macos-13` (x64) and
`macos-14` (arm64) runners. Add a macOS lane to `perf-check.yml`. Verify
all 6 macOS artifacts upload to a GitHub Release. Update CLAUDE.md's
macOS footnote.

## Prerequisites

- PH24 (cli), PH25+PH26 (webview), PH27 (lvgl) all green on CI.
- PH28 (pydfu libusb) done.

## Spec coverage

| FR / NFR | Deliverable |
|---|---|
| FR-BP-MAC-1, FR-BP-MAC-2 | `picolet build --target macos-*` resolves and downloads artifacts |
| FR-CI-MAC-1 | `release.yml` builds 6 new macOS artifacts |
| FR-CI-MAC-2 | Explicit `brew install` steps in CI; no implicit deps |
| FR-CI-MAC-3 | `perf-check.yml` macOS lane (macos-14) |
| FR-CI-MAC-4 | All 12 macOS artifacts uploaded to GitHub Release |
| FR-CI-MAC-5 | PICOLET_TEST_MODE assertion in macOS jobs |
| FR-TEST-MAC-5 | macOS perf lane uses process-poll for window visibility |
| NFR-MAC-5 | Startup ≤ 1500 ms on macOS (measured in perf-check) |

## Dependencies

- PH24–PH28 complete.
- Existing `release.yml` and `perf-check.yml` as templates.

## Key research findings

### GitHub Actions macOS runner pre-installed software

`macos-13` (x64) and `macos-14` (arm64) runners include:
- Xcode and command-line tools (clang, ar, strip, etc.)
- Python 3 (system + brew)
- Node.js 18+
- Homebrew
- `gh` CLI

**Not pre-installed** (must be explicitly installed):
- `automake` and `libtool` (needed for libffi cold-cache builds)
- `sdl2` (needed for lvgl variant)
- `libusb` (needed for pydfu test; not needed for runtime build itself)

Source: https://github.com/actions/runner-images/blob/main/images/macos/

### Runner selection by target

The `release.yml` currently uses a single `runs-on: ubuntu-latest` for
all matrix cells. macOS targets need different runners:

```yaml
strategy:
  matrix:
    include:
      # Linux and Windows (existing)
      - target: linux-x64
        variant: cli
        runs-on: ubuntu-latest
      # ... (all 6 existing cells)
      # macOS x64 (new)
      - target: macos-x64
        variant: cli
        runs-on: macos-13
      - target: macos-x64
        variant: webview
        runs-on: macos-13
      - target: macos-x64
        variant: lvgl
        runs-on: macos-13
      # macOS arm64 (new)
      - target: macos-arm64
        variant: cli
        runs-on: macos-14
      - target: macos-arm64
        variant: webview
        runs-on: macos-14
      - target: macos-arm64
        variant: lvgl
        runs-on: macos-14
```

Or more concisely with the `runs-on` derived from the target:
```yaml
strategy:
  matrix:
    target: [linux-x64, windows-x64, macos-x64, macos-arm64]
    variant: [cli, webview, lvgl]
  fail-fast: false
```
With a step that maps target → runner:
```yaml
runs-on: >-
  ${{
    startsWith(matrix.target, 'macos-x64') && 'macos-13' ||
    startsWith(matrix.target, 'macos-arm64') && 'macos-14' ||
    'ubuntu-latest'
  }}
```
(GitHub Actions expression syntax for ternary chains.)

### Artifact name convention for macOS

Current: `picolet-runtime-{linux-x64,windows-x64}-{cli,webview,lvgl}[.exe]`
New: `picolet-runtime-macos-{x64,arm64}-{cli,webview,lvgl}` (no extension
for macOS — Unix executable, no `.exe`).

The existing `finish_artifact` function and artifact upload step use
`${matrix.target}` and `${matrix.variant}` directly — no special-casing
needed for the artifact name.

The `.sha256` and `.cdx.json` sidecars follow the same `<artifact>.*`
naming.

### Docker layer cache in macOS jobs

The existing `release.yml` has a `Restore Docker layer cache (linux
build image)` step that is guarded by `if: matrix.target == 'linux-x64'`.
The Windows dockcross pull is guarded by `if: matrix.target ==
'windows-x64'`. macOS jobs need neither — they build natively. The
guards already exclude macOS by implication (neither condition fires).

### perf-check.yml macOS lane

The existing `perf-check.yml` runs on `ubuntu-latest` and uses `xdotool`
for window-visible detection. On macOS:
- No `xvfb` or `xdotool`.
- Window visibility proxy: spawn the binary, wait for `picolet:test-port=N`
  on stderr (same as existing NFR-TEST-1 measurement), then poll `pgrep -P <pid>`
  or use `osascript` to check if the process has a window.

The simplest approach for CI: measure NFR-TEST-1 (port announcement
latency) on macOS. NFR-EX-2 (window visible) can use a timed sleep of
500ms after the port is seen (conservative proxy, not precise).

Update `scripts/perf-check.py` to handle macOS:
```python
if sys.platform == "darwin":
    # No xdotool; use port-announcement time as proxy for window visibility
    window_visible_ms = harness.ready_ms  # approximation
else:
    # Linux: use xdotool
    window_visible_ms = _xdotool_wait(harness._proc.pid)
```

### picolet-cli resolver: adding macos-* targets

The CLI's runtime resolver (built in PH05) downloads pre-built artifacts
from GitHub Releases. Adding `macos-x64` and `macos-arm64` as valid
targets requires updating the target validation list in
`packages/picolet-cli/picolet_cli/` (the exact file depends on the
implementation). The artifact names follow the existing pattern, so no
resolver logic changes are needed — only the allowed-target list.

## Files to modify

### `.github/workflows/release.yml`

1. Change `runs-on` to be matrix-derived (see above).
2. Add macOS-specific install step:
   ```yaml
   - name: Install macOS build dependencies
     if: startsWith(matrix.target, 'macos-')
     run: |
       brew install automake libtool
       if [[ "${{ matrix.variant }}" == "lvgl" ]]; then
         brew install sdl2
       fi
   ```
3. Remove the Linux Docker build step for macOS targets:
   ```yaml
   - name: Build linux build image (linux-x64 only)
     if: matrix.target == 'linux-x64'
     ...
   ```
   (This guard already excludes macOS — no change needed.)
4. Remove the dockcross pull for macOS targets:
   ```yaml
   - name: Pull dockcross image (windows-x64 only)
     if: matrix.target == 'windows-x64'
     ...
   ```
   (Also already guarded — no change needed.)
5. Update SHA256 step to handle macOS (no `.exe` suffix):
   The existing script already handles this:
   ```bash
   SUFFIX=""
   if [[ "${{ matrix.target }}" == "windows-x64" ]]; then
     SUFFIX=".exe"
   fi
   ```
   No change needed — macOS artifacts have no suffix.

### `.github/workflows/perf-check.yml`

Add macOS lane as a separate job (not a matrix row — different runner,
different system packages):
```yaml
perf-check-macos:
  name: Measure startup latency (macOS arm64)
  runs-on: macos-14
  timeout-minutes: 30
  steps:
    - uses: actions/checkout@v4
      with:
        submodules: recursive
        fetch-depth: 1
    - name: Install uv
      uses: astral-sh/setup-uv@v3
    - name: Install build dependencies
      run: brew install automake libtool
    - name: Install Node.js
      uses: actions/setup-node@v4
      with:
        node-version: '20'
    - name: Build macos-arm64-webview runtime
      run: |
        bash packages/picolet-runtime/scripts/build-runtime.sh \
          --target macos-arm64 \
          --variant webview
    - name: Build example apps
      run: |
        npm --prefix examples/notes ci && npm --prefix examples/notes run build
        npm --prefix examples/pydfu ci && npm --prefix examples/pydfu run build
    - name: Build example binaries
      run: |
        uv run -p picolet picolet build --no-build-runtime \
          --target macos-arm64 --source examples/notes
        uv run -p picolet picolet build --no-build-runtime \
          --target macos-arm64 --source examples/pydfu
    - name: Run perf-check
      run: |
        uv run --no-project scripts/perf-check.py \
          --binary packages/picolet-runtime/build/picolet-runtime-macos-arm64-webview \
          --example examples/notes \
          --example examples/pydfu \
          --runs 5 \
          --output perf-results-macos.json
    - name: Upload perf results
      if: always()
      uses: actions/upload-artifact@v4
      with:
        name: perf-results-macos
        path: perf-results-macos.json
        retention-days: 90
```

### `packages/picolet-cli/picolet_cli/` (target validation)

Add `"macos-x64"` and `"macos-arm64"` to the allowed target list.
The exact file depends on the v1/PH05 implementation — find it by
searching for `"linux-x64"` and `"windows-x64"` in
`packages/picolet-cli/`.

### `CLAUDE.md`

Update the Build and test policy section:
```
- macOS targets (`macos-x64`, `macos-arm64`) are in scope for v1.2.
  macOS builds run on GitHub Actions `macos-13` and `macos-14` runners.
  There is no local macOS build path from the WSL2 dev box.
  See [docs/v1.2-plan.md](docs/v1.2-plan.md) for details.
```
Remove the sentence: "macOS is out of scope for v1. Do not add macOS
code, CI matrix entries, or guards."

## Integration points

### `release` job in `release.yml`

The `release` job downloads all artifacts via `actions/download-artifact@v4`
and uploads them to the GitHub Release. It already handles arbitrary
artifact names (`find dist/ -type f | sort`). No changes needed.

### `screenshots-release` job in `release.yml`

The screenshots job builds examples and runs screenshot generation on
`ubuntu-latest`. macOS screenshots are not generated in this job (it
would require a macOS runner per-release). This is acceptable for v1.2
— the screenshot gallery uses Linux/WebKit screenshots which are
representative of the layout on all platforms. Document this as a known
gap.

## Testing strategy

1. Trigger a `workflow_dispatch` on `release.yml` (with a test tag or
   `workflow_dispatch` trigger) and verify all 12 macOS artifacts appear
   in the Release assets.
2. Download `picolet-runtime-macos-arm64-cli` from the Release on a macOS
   arm64 machine and verify it runs.
3. Verify `perf-check.yml` macOS lane passes (port announcement < 3s,
   startup < 1500ms median on `macos-14`).
4. Run `picolet build --target macos-x64` from the CLI resolver and verify
   it downloads the artifact from the Release.

## Success criteria

- [ ] `release.yml` workflow produces 12 macOS artifacts (6 binaries +
      6 SBOMs) on a tag push.
- [ ] All 12 artifacts are uploaded to the GitHub Release.
- [ ] `perf-check.yml` macOS lane runs green with ≤ 1500ms median
      startup on `macos-14`.
- [ ] `picolet build --target macos-x64` downloads the artifact from the
      GitHub Release cache.
- [ ] `CLAUDE.md` macOS footnote updated.
- [ ] Matrix has `fail-fast: false` so a single macOS variant failure
      does not block other artifacts.

## Risks

1. **macOS runner billing**: GitHub-hosted `macos-14` runners are
   significantly more expensive than `ubuntu-latest`. The total build
   matrix is now 12 jobs (6 macOS + 4 existing + 2 existing). Consider
   whether the `perf-check.yml` macOS lane should be on a schedule only
   (not on every push) to reduce costs.

2. **Docker layer cache step on macOS**: The existing cache-restore step
   for the Linux Docker image would fail if it ran on macOS. It is
   guarded by `if: matrix.target == 'linux-x64'` which already excludes
   macOS — verify the guard is present.

3. **Workflow syntax for conditional `runs-on`**: GitHub Actions ternary
   expressions for `runs-on` can be finicky. Test with a `workflow_dispatch`
   trigger before merging to `dev`.

## Model tier recommendation

planner `sonnet`, developer `sonnet`, sqe `sonnet`, tester `sonnet`.
This is YAML editing and script wiring. The hard technical work is in
PH24–PH28.
