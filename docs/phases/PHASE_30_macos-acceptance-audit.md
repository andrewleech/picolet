# PHASE 30 — macOS acceptance audit

## Goal

`scrum-po` runs the v1.2 spec audit. Every FR and NFR in
[v1.2-spec.md](../v1.2-spec.md) gets a Yes / No verdict with file:line
evidence and a pointer to the CI run that produced the artifact.

A No on any requirement sends control back to the planner for a
targeted fix-up phase (PH30a, PH30b, etc.) before re-audit.

## Prerequisites

- PH29 complete and green: all 12 macOS artifacts uploaded to a GitHub
  Release; perf-check macOS lane green; all example test suites passing
  on macOS.

## Spec coverage

All FR-RT-MAC-*, FR-WV-MAC-*, FR-LV-MAC-*, FR-BP-MAC-*, FR-CI-MAC-*,
FR-TEST-MAC-*, FR-EX-MAC-*, and NFR-MAC-* from [v1.2-spec.md](../v1.2-spec.md).

## Audit checklist

The PO audits each requirement with a Yes/No verdict and file:line or
CI-run evidence. The phase is complete when all rows are Yes.

### Runtime variants

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| FR-RT-MAC-1 | 6 macOS artifacts exist | Release assets listing | |
| FR-RT-MAC-2 | All single Mach-O executables; no dylib sidecars | `file` command output | |
| FR-RT-MAC-3 | Trailer + romfs works on Mach-O | PH24 test output | |
| FR-RT-MAC-4 | `gc.add_heap()` + `ffi` in all 6 variants | Smoke test output | |
| FR-RT-MAC-5 | No Darwin-specific unix port fork (except where documented) | Source diff | |

### WKWebView renderer

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| FR-WV-MAC-1 | macOS uses WKWebView; `sys.platform == "darwin"` gate in `_app.py` | `_app.py:L*` | |
| FR-WV-MAC-2 | `picolet_webview_mac.c` exists and compiles on both archs | Build log | |
| FR-WV-MAC-3 | All ObjC calls via `objc_msgSend` / `libobjc.dylib`; no ObjC++ | `picolet_webview_mac.c` header | |
| FR-WV-MAC-4 | Window opens, HTML loads, IPC round-trips | hello-webview test output | |
| FR-WV-MAC-5 | picolet:// scheme registered via `WKURLSchemeHandler` | Source + CSS/JS load in screenshot | |
| FR-WV-MAC-6 | `window.picolet.invoke` and `window.picolet.on` work | IPC test in example test output | |
| FR-WV-MAC-7 | `PICOLET_TEST_MODE=1` enables inspector and announces port | Test harness connection log | |
| FR-WV-MAC-8 | Screenshot via `takeSnapshotWithConfiguration:` | `AppHarness.snapshot()` PNG bytes | |

### LVGL renderer

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| FR-LV-MAC-1 | `libSDL2.dylib` from brew in `otool -L` output | `otool` output | |
| FR-LV-MAC-2 | No SDL2 source patch; Cocoa backend default | `build-runtime.sh` log | |
| FR-LV-MAC-3 | Linux LVGL overlay serves macOS without Darwin fork | Source diff | |

### Build pipeline

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| FR-BP-MAC-1 | `picolet build --target macos-x64` downloads artifact | CLI output | |
| FR-BP-MAC-2 | `picolet build --target macos-arm64` downloads artifact | CLI output | |
| FR-BP-MAC-3 | `--from-source` for macos-* emits clear error and exits 1 | CLI output | |
| FR-BP-MAC-4 | `build-runtime.sh` has native macOS build path | Source: `build-runtime.sh:L*` | |
| FR-BP-MAC-5 | Non-Darwin host exits with actionable error for macos-* | Test script output | |
| FR-BP-MAC-6 | SBOM sidecars emitted for macOS builds | Release assets listing | |

### CI build matrix

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| FR-CI-MAC-1 | `release.yml` has `macos-13` + `macos-14` matrix rows | `.github/workflows/release.yml` | |
| FR-CI-MAC-2 | Explicit `brew install` steps; no implicit deps | `release.yml` step names | |
| FR-CI-MAC-3 | `perf-check.yml` has macOS lane on `macos-14` | `.github/workflows/perf-check.yml` | |
| FR-CI-MAC-4 | 12 macOS artifacts in Release assets | GitHub Release page | |
| FR-CI-MAC-5 | PICOLET_TEST_MODE assertion step in macOS jobs | `release.yml` | |

### Test infrastructure

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| FR-TEST-MAC-1 | `AppHarness` autodetects `macos-*-webview` | `_harness.py` + test output | |
| FR-TEST-MAC-2 | WKRP connection works; `WebKitPage` duck works on macOS | Inspector attach log | |
| FR-TEST-MAC-3 | `PICOLET_TEST_MODE=1` announces port in same format | stderr capture | |
| FR-TEST-MAC-4 | Screenshot via WK snapshot API in `AppHarness.snapshot()` | PNG size assertion | |
| FR-TEST-MAC-5 | Perf lane uses process-poll (no xdotool) | `perf-check.py` source | |

### Example apps

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| FR-EX-MAC-1 | All 4 examples build + test suite passes on macOS | CI test job output | |
| FR-EX-MAC-2 | pydfu loads `libusb-1.0.dylib` | `_usb/core.py:L*` + test log | |
| FR-EX-MAC-3 | `romfs_extract.py` returns path unchanged on macOS | `romfs_extract.py:L*` | |
| FR-EX-MAC-4 | notes stores to `~/Library/Application Support/` | `notes/src/main.py:L*` + test | |
| FR-EX-MAC-5 | config-editor uses `~/Library/Application Support/` | `config-editor/src/main.py:L*` | |

### NFRs

| Req | Description | Evidence | Pass? |
|---|---|---|---|
| NFR-MAC-1 | cli ≤ 1 MiB | `build-runtime.sh` size gate log | |
| NFR-MAC-2 | webview ≤ 2 MiB | `build-runtime.sh` size gate log | |
| NFR-MAC-3 | lvgl ≤ 2 MiB | `build-runtime.sh` size gate log | |
| NFR-MAC-4 | cli: no runtime deps; webview: WebKit.framework (system); lvgl: brew sdl2 | SBOM + `otool -L` | |
| NFR-MAC-5 | Startup ≤ 1500 ms median on `macos-14` | `perf-results-macos.json` | |
| NFR-MAC-6 | `docs/macos-unsigned.md` exists with Gatekeeper workaround | File exists | |
| NFR-MAC-7 | webview uses only WebKit.framework; no third-party framework | `otool -L` output | |
| NFR-MAC-8 | No GPL/AGPL static link | SBOM licence check | |

## Audit procedure

1. Pull the `dev` branch artifact for the latest successful CI run of
   `release.yml` on a tag.
2. Walk the checklist above; for each row that is No, file the evidence
   in a `## Blockers` section below.
3. For each blocker, create a fix-up sub-phase (PH30a, PH30b, ...) and
   send control back to the planner.
4. Re-run after fixes; repeat until all rows are Yes.

## Exit gate

All rows in the audit checklist above are Yes, with evidence recorded.

## Model tier recommendation

scrum-po `opus`, all other roles vary per fix-up phase.
