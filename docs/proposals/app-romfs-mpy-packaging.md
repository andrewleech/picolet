# Proposal: app code must ship as `.mpy`, and picolet should make that the default

Status: proposal / feature request
Origin: surfaced by the claude-net-mpy plugin rollout, 2026-07-24
Owner: TBD (picolet-runtime + picolet-cli)

## Summary

picolet's appended-app-romfs production path ships **raw `.py`**, which the
runtime (compiler enabled) recompiles on **every process start**. That cost
is invisible for a single process but becomes a failure mode under
concurrency. picolet already has every ingredient to ship bytecode
(`mpy-cross` built + version-stamped per runtime, `manifest.py` freezing,
frozen `main.mpy` support) — the gap is that the packaging path doesn't use
them for the appended app, and nothing prevents raw `.py` from shipping.

This proposal makes **bytecode the default for production app code** and adds
a forcing function so raw `.py` can't silently regress.

## Motivation — the incident

Rolling the claude-net MicroPython plugin out across ~15 concurrent agents on
one host, a mass reconnect started ~21 plugin processes near-simultaneously.
Each cold start **compiled the plugin's `.py` modules from romfs**; 21
concurrent compiles (plus heap alloc + TLS) saturated the host CPU, and
several plugins' MCP `initialize` exceeded the client's 30 s deadline →
"connection timed out". A single start is <1 s; the compile cost only bit
under concurrency.

Root cause: **`.py` in the shipping romfs, compiled at startup.** Not a plugin
bug — a packaging-format default.

## Current state (what exists vs the gap)

Exists:
- **`mpy-cross`, built and version-matched to each runtime.**
  `scripts/build-runtime.sh` builds `mpy-cross` from the same micropython
  submodule and writes a `.version` sidecar with the bytecode-format token
  (step [7b]).
- **Frozen-module support via `manifest.py`**, used in production: the `tui`
  and `webview` variants freeze their Python packages as `.mpy` baked into
  the binary (e.g. NFR-TUI-19 budgets are expressed in `.mpy`).
- **Frozen `main.mpy` entry**: `ports/unix/main.c` resolves `main.mpy` as well
  as `main.py`.

Gap:
- **The appended-app-romfs packaging path emits raw `.py`.** It never invokes
  `mpy-cross` over the app, and because shipping variants keep the compiler
  enabled, raw `.py` "works" (slowly) instead of failing.
- **No guard** flags `.py` in a shipping romfs.
- **No guidance** distinguishes dev (`.py`) from ship (`.mpy`/frozen) code.

## Two production formats (both are bytecode)

picolet has two legitimate production code formats. Neither should ship `.py`.

1. **Frozen-in-binary** (`manifest.py`): app bytecode baked into the runtime
   at link time. Fastest import, smallest, no VFS. Cost: packaging is a full
   runtime rebuild (C toolchain), so the binary is rebuilt per app change.
   Best when app+runtime are versioned together and rebuilt in CI anyway.
2. **App-in-appended-romfs**: prebuilt generic runtime + appended app romfs,
   assembled without a C toolchain (seconds). Decouples app releases from
   runtime rebuilds. **Must contain `.mpy`, not `.py`.**

Note: "generic binary" is a property of format 2's *developer workflow*
(download one runtime, append many apps without rebuilding) — it is NOT a
reason to avoid freezing for a single-app artifact. A hub/registry serving a
purpose-built app binary serves an app-specific artifact under either format.

## Proposed changes

### F1 — Cross-compile the appended app romfs to `.mpy` by default (core)
The romfs-append step (`build-runtime.sh` / `picolet build`) runs the
runtime-matched `mpy-cross` over every app `.py` → `.mpy` and includes only
bytecode (+ non-code assets). Raw `.py` in a *shipping* romfs becomes an
explicit dev opt-in (e.g. `--dev-romfs`).
- Acceptance: a default `picolet build` produces a romfs with no `.py`
  modules; cold start does zero compilation; app import behaviour unchanged.

### F2 — mpy-cross version-match enforcement (enabler)
A `.mpy` built for the wrong bytecode version silently fails to load. Packaging
must cross-compile with *the target runtime's* `mpy-cross` and fail the build
if the produced `.mpy` version doesn't match the runtime's `.version` sidecar.
Ship/pin the matched `mpy-cross` alongside each runtime artifact.
- Acceptance: packaging refuses to emit `.mpy` whose version ≠ the runtime's;
  the matched `mpy-cross` is discoverable from the runtime artifact.

### F3 — Compiler-off "ship" variants (size + forcing function)
Offer shipping variants with `MICROPY_ENABLE_COMPILER=0`: smaller binary, and
raw `.py` in romfs then **fails to import** — catching the antipattern at test
time instead of costing CPU forever. Keep compiler-on "dev" variants for
`runtime app.py` iteration.
- Acceptance: a compiler-off variant runs a `.mpy`-only romfs; importing a
  `.py` raises; documented dev vs ship variant pairing.

### F4 — `main.mpy` in the appended romfs
Frozen `main.mpy` already works; ensure the *romfs* auto-run resolves
`main.mpy` too (or document a tiny frozen `main.py` stub that imports the
`.mpy` app), so the entry point isn't the one uncompiled `.py`.
- Acceptance: an appended romfs with `main.mpy` (no `main.py`) auto-runs.

### F5 — Build guard against `.py` in a shipping romfs
Mirror the single-binary import-table guard: packaging fails (or loudly warns)
if a shipping romfs contains `.py` without a matching `.mpy`.
- Acceptance: a shipping build with a stray `.py` fails with a clear message.

### F6 — Docs/guidance
Document the rule in the manifest/packaging guide: **frozen or `.mpy`-in-romfs
for shipping; `.py` for dev only**, with the cold-start/CPU rationale and the
two-formats section above. (The claude-net Q4 decision "ship libs via app
romfs" never mentioned `.py` vs `.mpy` — exactly the gap this closes.)
- Acceptance: `docs/manifest.md` / packaging guide states the rule.

### F7 — Optional: `picolet build --freeze` / `--optimize`
An opt-in tier that recompiles the port with the app frozen via manifest for a
max-performance, app-specific binary (format 1). Trades the append-without-
rebuild workflow; not the default.
- Acceptance: `--freeze` produces a binary with the app frozen; default
  behaviour unchanged.

## Priority / sequencing

- **Core:** F1, F2, F3 — cross-compile by default, make it correct, make raw
  `.py` fail in shipping builds. These are what "push future designs away from
  `.py` in romfs".
- **Hardening/guidance:** F4, F5, F6.
- **Optional:** F7 (performance escape hatch).

## Cross-references

- Incident + immediate app-side fix (mpy-cross the app in `package-plugin.py`):
  claude-net-mpy worktree, `planning/` rollout notes, 2026-07-24.
- Existing machinery this builds on: `scripts/build-runtime.sh` steps [5]-[7]
  (romfs build/append, `.version` sidecar), `manifests/` (`manifest.py`
  freezing), `ports/unix/main.c` (`main.mpy` frozen entry).
