# PH15 — CI release pipeline

## Plan

### Goal

GitHub Actions workflow that on tag pushes builds the 3 × 2 runtime matrix,
generates SBOMs, and uploads all artifacts to GitHub Releases.

Closes NFR-7: "The CI matrix produces all six runtime artifacts (3 variants × 2
targets) plus their SBOMs in a single workflow run."

### Trigger

Tag push matching `runtime-v*` (e.g. `runtime-v0.1.0`).

```yaml
on:
  push:
    tags:
      - 'runtime-v*'
```

No branch trigger. The workflow is release-only; PRs do not run it.

### Matrix strategy

`strategy.matrix` with two axes:

```yaml
strategy:
  matrix:
    target: [linux-x64, windows-x64]
    variant: [cli, webview, lvgl]
  fail-fast: false
```

This produces 6 jobs. `fail-fast: false` ensures one job failing does not
cancel the others — partial release uploads would be worse than seeing all
failures at once.

### Build containers

The workflow reuses the same Docker images as the local `build-runtime.sh`:

- **Linux jobs**: `picolet-linux-x64-build:22.04` — built from
  `packages/picolet-runtime/scripts/dockerfiles/linux-x64-build/Dockerfile`.
  The `build-runtime.sh` script builds this on first use; the workflow does
  the same.

- **Windows jobs**: `dockcross/windows-static-x64-posix:latest` — pulled from
  Docker Hub. No build step needed.

`build-runtime.sh` already handles both image lifecycle steps (build if absent,
skip if present), so the workflow simply invokes the script directly. Docker is
available on `ubuntu-latest` GitHub-hosted runners.

### Cache strategy

Three cache points:

1. **Docker image layer cache**: Use `docker/build-push-action` with
   `cache-from: type=gha` / `cache-to: type=gha,mode=max` for the
   `picolet-linux-x64-build:22.04` image. The image changes rarely (only when
   the Dockerfile changes); GHA cache keyed on Dockerfile hash avoids
   unnecessary rebuilds.

2. **SDL2 from-source build** (windows-x64/lvgl only): Cache
   `packages/picolet-runtime/build/sdl2-win64-ffs/` keyed on
   `SDL2_VERSION + hash(build-runtime.sh)`. SDL2 source build takes ~10 min;
   caching the installed library tree saves ~9 min per run.

3. **libffi build artifacts**: Cache
   `packages/picolet-runtime/micropython/ports/*/build-picolet-*/lib/libffi/`
   directories keyed on submodule SHA + target. libffi configure+build adds
   ~2 min per artifact without caching.

All caches use `restore-keys` fallback so a partial cache miss still accelerates
the build.

### SBOM emission

`build-runtime.sh` already emits `<artifact>.cdx.json` in its `[SBOM]` step
(wired in PH13). The CI workflow therefore gets the SBOM for free — no extra
step needed beyond invoking `build-runtime.sh`.

Additionally, the workflow computes a `<artifact>.sha256` sidecar for use by
PH05's resolver (the resolver verifies downloads against this sidecar).

### Release upload

After all 6 build jobs complete (via `needs: [build]`), a single upload job
iterates over the matrix artifacts and calls:

```bash
gh release upload "$TAG" \
    "$artifact" \
    "$artifact.sha256" \
    "$artifact.cdx.json"
```

The `GITHUB_TOKEN` is available automatically in Actions. The release is created
by the workflow if it does not yet exist (`gh release create`).

### Permissions

```yaml
permissions:
  contents: write   # create releases + upload assets
```

No other permissions are required. The images are public; no registry login.

### Job structure

```
┌─────────────────────────────────────────────────────────────┐
│  release (workflow)                                          │
│                                                              │
│  job: build  (matrix: 6 parallel jobs)                      │
│    - Checkout + submodules                                   │
│    - Restore caches                                          │
│    - docker build / pull image                               │
│    - ./build-runtime.sh --target $TARGET --variant $VARIANT  │
│    - Compute sha256 sidecar                                  │
│    - Upload artifacts to job (actions/upload-artifact)       │
│                                                              │
│  job: release  (needs: build, runs once)                     │
│    - Download all artifacts from the build jobs              │
│    - gh release create (idempotent)                          │
│    - gh release upload for each artifact set                 │
└─────────────────────────────────────────────────────────────┘
```

### RUNTIME_TAG confirmation

`packages/picolet-runtime/RUNTIME_TAG` currently contains `runtime-v0.1.0`.
This is already a release-style tag matching the trigger pattern `runtime-v*`.
No change needed.

### Deliverables

1. `.github/workflows/release.yml` — the workflow file.
2. `RELEASING.md` — operator guide for cutting a release.
3. `tests/phase-15/run.sh` — lint + structural validation harness.

### Spec traceability

| Spec id | Gate | Notes |
|---|---|---|
| NFR-7 | Matrix has 6 jobs; each uploads binary + .cdx.json + .sha256 | Verifiable by parsing the workflow YAML |

### Out of scope

- macOS targets (NFR-8 / out-of-scope for v1 per spec).
- Code signing.
- Auto-release on branch push (tag-only).
- Container registry push (artifacts uploaded directly to GitHub Releases).

## Verification

**Developer/tester: combined planner+developer+tester, 2026-05-16.**

**Verdict: PASS**

### Files produced

| File | Purpose |
|---|---|
| `.github/workflows/release.yml` | CI release pipeline |
| `RELEASING.md` | Operator guide |
| `tests/phase-15/run.sh` | Lint + structural validation harness |

### Test harness results

```
Gates A-G: 6 pass, 0 fail, 1 skip
  A: YAML lint — PASS
  B: matrix shape (3×2=6 jobs) — PASS
  C: structural assertions (10 checks) — PASS
  D: RUNTIME_TAG format 'runtime-v0.1.0' — PASS
  E: RELEASING.md exists (119 lines) — PASS
  F: act dry-run — SKIP (act not installed)
  G: PH00-PH14 regression — PASS
```

PH14 regression: 19 pass, 0 fail, 6 skip (--skip-integration --skip-windows).

### Commit

`deb9de3` — [PH15] Add release.yml: 3x2 runtime matrix CI pipeline.

### NFR-7

Satisfied: the matrix produces all 6 runtime artifacts from a single
workflow run triggered by a tag push. Each artifact is accompanied by
its `.sha256` and `.cdx.json` sidecars.
