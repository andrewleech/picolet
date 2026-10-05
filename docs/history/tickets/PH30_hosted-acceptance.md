# PH30: Complete hosted macOS acceptance evidence

Phase: PH30, with PH31 matrix verification in parallel.
Depends on: successful clean checkout and integration composition, six macOS runtime builds.
Written: 2026-10-06 at HEAD 75fe167.
Revalidated: 2026-10-06 at HEAD 75fe167, current execution ticket.
Revalidated: 2026-10-06 at HEAD 1968a96, after hosted checkout/build portability repairs.
Revalidated: 2026-10-06 at HEAD 7a13671, current compiled Linux app startup verified and native ARM64 CLI build/runtime evidence collected.

## Context

The existing [audit](../V1_2_ACCEPTANCE_AUDIT.md) only spot-checks a subset of the [specification](../v1.2-spec.md). Source completion does not establish binary/runtime acceptance. The [autonomous execution ledger](../autonomous-execution.md) records fixes and local observations; its hosted run links are the starting point for further investigation.

## Scope and constraints

Complete every v1.2 FR/NFR verdict using observed source/build/runtime evidence, clearly distinguishing these evidence classes. Collect PH31 matrix reports in parallel. Do not publish releases, create tags or push local integration merges. Preserve user checkout/submodule state, use scratch checkouts for destructive build composition. Do not treat test-port announcements, a live process, source-text matches or a skipped harness as proof of working UI/IPC/snapshots.

## Anchors and approach

- `.github/workflows/release.yml`: manual dispatch builds/uploads without publishing a GitHub Release. The six macOS jobs now exercise Darwin platform selection, FFI symbol calling and heap API availability.
- `packages/picolet-runtime/scripts/rebuild-integration.sh`: compose the branches from `mbm.toml`; the parent gitlink is the remotely reachable feature tip, not the local composition.
- `tests/phase-25/run.sh`, `tests/phase-26/run.sh`, `tests/phase-27/run.sh`: existing scripts are starting points only. Bare-runtime launches do not necessarily load a UI application; PH25 may skip snapshot checks when its import is missing. Repair the exercised path rather than accepting skip output.
- `packages/picolet/picolet/testing/_webkit.py` and runtime `variants/webview/unix/picolet_webview_mac.c`: verify the real inspector endpoint/protocol and snapshot route on the hosted binary, do not infer interoperability from port-announcement wording.
- `.github/workflows/perf-check.yml`: both supported macOS runners are dispatch/schedule-only; exercise actual built examples and inspect timing observations.

## Acceptance evidence checklist

- FR-RT-MAC-1..5: six Mach-O binaries, romfs entry execution, heap/FFI APIs, shared variant source/build path.
- FR-WV-MAC-1..8: WKWebView selection and ObjC ABI, visible application, HTML/sub-assets from romfs, invoke/event round-trip, actual inspector connection and PNG snapshot through AppHarness.
- FR-LV-MAC-1..3: SDL2 dynamic linkage and a visible/functioning LVGL application on both architectures, shared manifest/build path.
- FR-BP-MAC-1..6: runtime resolver for both architectures, explicit from-source/non-Darwin refusals, native build path and valid SBOM sidecars.
- FR-CI-MAC-1..6: correct runner architecture, declared prerequisites, perf lane, artifact/SBOM production and release-job wiring, test-mode assertion and retained OS metadata. Actual publication remains outside this autonomous verification run.
- FR-TEST-MAC-1..5: AppHarness platform selection, inspector connection, port announcement, snapshot and genuine timing measurement.
- FR-EX-MAC-1..5: all four examples build/run on both architectures; pydfu library load/mock behaviour, extraction semantics and macOS data/schema locations.
- NFR-MAC-1..10: observed binary sizes, runtime dynamic dependencies, startup bounds, unsigned-use documentation, framework/licence/SBOM review, Mach-O deployment target and trigger restrictions.
- PH31: all 15 supported matrix cells produce manifest/native scope and reports; the alpha scanner selects nine Linux-hosted cells. Findings/parse warnings/type diagnostics remain visible; tool operational failure is not report-only success.

## Execution model

Use Luna for independently bounded fixes and evidence collection, coordinator owns contracts/integration and verifies actual runtime behaviour. At phase entry, revalidate against `git log 75fe167..HEAD`, diffs of anchored files and later ledger/decision records. Append the revalidation date/SHA rather than replacing the Written stamp. A change that alters acceptance scope updates the roadmap explicitly.

## Unresolved evidence

Hosted macOS runtime results remain pending. Linux/Windows CLI, webview and LVGL builds have passed. The existing WKWebView inspector contract is not established: the runtime selects/announces an unused port but starts no listener, while AppHarness assumes HTTP target discovery and WebSocket transport. Apple documents `WKWebView.isInspectable` for Safari's Develop menu, not a supported localhost HTTP/WebSocket automation endpoint. WebKit's `Page` protocol defines snapshot methods returning `dataURL`, not Chromium's `Page.captureScreenshot` response. FR-WV-MAC-7 and FR-TEST-MAC-2 therefore require a contract/design decision before they can be honestly accepted. The native `takeSnapshotWithConfiguration:completionHandler:` implementation is available, but the harness does not route to it. Do not replace this requirement silently with a different test bridge.

The ARM64 CLI cell in run 37367503586 passed build, runtime FFI/heap checks and artifact upload. Its downloaded 595,472-byte Mach-O declares ARM64, deployment target 11.0 / SDK 14.5 and only libSystem as a dynamic dependency; its SHA256 sidecar verified locally. The other native variant/architecture verdicts are still pending, including actual LVGL scene capture and all four packaged Mac app frontends.

Primary references: [Apple isInspectable API](https://developer.apple.com/documentation/webkit/wkwebview/isinspectable), [WebKit inspection guidance](https://webkit.org/blog/13936/enabling-the-inspection-of-web-content-in-apps/), and [WebKit Page protocol](https://github.com/WebKit/WebKit/blob/main/Source/JavaScriptCore/inspector/protocol/Page.json). The phase-25 script also calls nonexistent `AppHarness.snapshot()` rather than the implemented `screenshot(path)` method, so it is not existing runtime proof.

The macOS performance checker now attempts native window visibility and a PNG capture, recording connection/capture failures as failed measurements rather than Linux-only success/skips. Its first-paint verdict depends on resolving the inspector/snapshot contract above. Apple SDK cross-build licensing, signing credentials and post-v1.2 packaging/architecture design are separate decisions and are not guessed.
