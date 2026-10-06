# PH30: Complete hosted macOS acceptance evidence

Phase: PH30, with PH31 matrix verification in parallel.
Depends on: successful clean checkout and integration composition, six macOS runtime builds.
Written: 2026-10-06 at HEAD 75fe167.
Revalidated: 2026-10-06 at HEAD 75fe167, current execution ticket.
Revalidated: 2026-10-06 at HEAD 1968a96, after hosted checkout/build portability repairs.
Revalidated: 2026-10-06 at HEAD 7a13671, current compiled Linux app startup verified and native ARM64 CLI build/runtime evidence collected.
Revalidated: 2026-10-06 at HEAD f4a9b22, current CI passes all 17 jobs and all eight packaged native app captures have been viewed; inspector / first-paint and SDK dependency/licence acceptance remain open.

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

All six Mac runtime variants compile and pass FFI/heap checks, and both native GUI paths have produced verified PNGs on both architectures. Packaged example acceptance and inspector-based timing remain unresolved. Linux/Windows CLI, webview and LVGL builds have passed. The existing WKWebView inspector contract is not established: the runtime selects/announces an unused port but starts no listener, while AppHarness assumes HTTP target discovery and WebSocket transport. Apple documents `WKWebView.isInspectable` for Safari's Develop menu, not a supported localhost HTTP/WebSocket automation endpoint. WebKit's `Page` protocol defines snapshot methods returning `dataURL`, not Chromium's `Page.captureScreenshot` response. FR-WV-MAC-7 and FR-TEST-MAC-2 therefore require a contract/design decision before they can be honestly accepted. The native `takeSnapshotWithConfiguration:completionHandler:` implementation is available, but the harness does not route to it. Do not replace this requirement silently with a different test bridge.

Both CLI cells in run `37367503586` passed build, runtime FFI/heap checks and artifact upload. Their downloaded Mach-O files declare deployment target 11.0 and only libSystem as a dynamic dependency: ARM64 is 595,472 bytes / SDK 14.5, x86_64 is 541,544 bytes / SDK 15.5. Both SHA256 sidecars verified locally. Both LVGL cells passed native FFI/heap and the older direct PNG encoder fixture, actual shown-window scene capture remains pending. ARM64 WebView exposed a Clang block compilation failure, corrected at `fc8e4fc`; the replacement native snapshot and all four packaged Mac app frontends still require hosted execution.

Primary references: [Apple isInspectable API](https://developer.apple.com/documentation/webkit/wkwebview/isinspectable), [WebKit inspection guidance](https://webkit.org/blog/13936/enabling-the-inspection-of-web-content-in-apps/), and [WebKit Page protocol](https://github.com/WebKit/WebKit/blob/main/Source/JavaScriptCore/inspector/protocol/Page.json). The phase-25 script also calls nonexistent `AppHarness.snapshot()` rather than the implemented `screenshot(path)` method, so it is not existing runtime proof.

The macOS performance checker now attempts native window visibility and a PNG capture, recording connection/capture failures as failed measurements rather than Linux-only success/skips. Its first-paint verdict depends on resolving the inspector/snapshot contract above. Apple SDK cross-build licensing, signing credentials and post-v1.2 packaging/architecture design are separate decisions and are not guessed.

Acceptance revalidated at `fc8e4fc`: the genuine Linux LVGL scene smoke passes exact background/object colours and displayed SDL window checks, the corresponding Mac jobs remain pending. An isolated locked Python 3.12 installation passed 1,134 tests / 76 subtests across separate host, IPC, system and terminal runs. Hosted CI setup needs the committed workspace lock and complete Mac Opengrep executable assets, both have been checked locally before rerunning hosted acceptance.

Acceptance revalidated at `a472ee7` in run `37396891277`: both WKWebView cells passed native messaging, PNG creation and exact colour checks. Both LVGL cells created shown SDL windows and rendered the expected scene; all four downloaded GUI PNGs were viewed and passed exact 320×240 size / colour checks. The LVGL jobs failed only in the subsequent host colour-check command because `uv` was not installed; that command now uses the same setup-Python / pip Pillow pattern as the WebView lane. Ten full build cells passed, and both LVGL cells still need their corrected workflow to finish artifact upload. Publication and release screenshot regeneration were skipped. The separate packaged-app/performance run remains pending.

PH31's stable supported-matrix receipt is complete at `949c821` in run `37398322821`: lint, unit and all 15 native scanner jobs passed, and all report sets were downloaded. Reports retain six project findings / one project syntax warning per cell and zero or one native finding / 32–59 native syntax warnings. Parsing gaps and audit findings remain visible, this is not a security-clean verdict. Experimental alpha matrix execution is recorded separately.

Acceptance revalidated at `2c763aa` in run [37399330989](https://github.com/andrewleech/picolet/actions/runs/37399330989): all 12 build cells pass, including both corrected LVGL host checks and artifact uploads. All six downloaded Mac runtimes have matching SHA256 sidecars, expected Mach-O architectures and declared macOS 11.0 deployment targets. Native GUI receipts are complete; packaged-app observation is now running with the corrected workspace fixture in [37400593958](https://github.com/andrewleech/picolet/actions/runs/37400593958).

SDK inventory acceptance remains open. Direct Mac LVGL load commands include SDL2 compatibility, FreeType, Cocoa, Metal and AudioUnit, while the curated bill declares generic SDL2 and omits the other LVGL libraries/frameworks. Fixed MicroPython / binding versions also do not identify the composed build sources. CycloneDX parsing passed, dependency/licence accuracy has not. These findings must be reconciled with the SDK dependency/licence review before release, not concealed by an allowlist exception.

Acceptance revalidated at `6f34af2` in alpha run [37407543981](https://github.com/andrewleech/picolet/actions/runs/37407543981): all nine Linux-hosted Linux / Windows native-build, fixture, analysis and upload cells pass. Downloaded findings resolve to the retained manifest/compiler scope, with scanner exit zero and report-only summaries. Project reports retain six findings / five or six notifications per cell; native reports retain one Linux / zero Windows findings and 32–59 notifications, including parser and interfile-depth limits. The separate hosted unit failure was stale derived Notes / Config Editor templates, repaired at `0adb9ca`; the exact hosted host-package invocation passes locally with 904 tests / 76 subtests, 23 skips and one expected failure.

Packaged-app run [37406345509](https://github.com/andrewleech/picolet/actions/runs/37406345509) at `f4f8bc2` completed all four actual frontend / IPC / snapshot commands on both architectures. Viewed ARM64 captures and Intel Config Editor / PyDFU captures show the real UI, but Intel Notes / Dashboard captured only their backgrounds. These are visual failures, not accepted packaged-app receipts. Observer repair `2024701` waits for loaded fonts and two animation frames before notifying the native capture path; all four generated scripts parse and generate on genuine MicroPython. Its hosted visual result remains pending. Dashboard's metric collector is Linux-only, frontend / history-command verification does not establish macOS metric parity.

The same run records NFR-TEST-1 port-announcement medians of 104.0 / 138.3 ms for PyDFU / Notes on ARM64 and 413.3 / 445.1 ms on Intel. Every inspector-based first-paint attempt failed with a refused loopback WebSocket connection. NFR-EX-2 remains unmeasured, port announcement and direct native snapshots are not substitutes for the required AppHarness transport.

Push CI [37409785249](https://github.com/andrewleech/picolet/actions/runs/37409785249) at `2024701` passes lint, units and all 15 stable scanner cells. All downloaded summaries retain scanner exit zero / report-only status, their counts match the selected source groups and actual finding locations resolve to retained scope. Hosted suite counts are 872 host tests / 76 subtests with 55 skips and one expected failure, 100 IPC tests, 84 system tests with three skips and 46 TUI tests with three skips.

Native revalidation [37409785312](https://github.com/andrewleech/picolet/actions/runs/37409785312) at `2024701` passes all packaged frontend / IPC / capture steps and every viewed image shows the UI. Intel Notes / Dashboard still include unfinished finite entrance fades, so final stable captures await the animation-lifecycle repair `f4a9b22`. Its browser smoke proves the entrance animation completes while an infinite indicator keeps running, production animations are not disabled. Both Mac performance jobs still fail their inspector-dependent first-paint attempts; NFR-TEST-1 medians are 77.4 / 88.0 ms for PyDFU / Notes on ARM64 and 178.0 / 211.3 ms on Intel, Linux performance passes.

Final observer revision CI [37412333684](https://github.com/andrewleech/picolet/actions/runs/37412333684) at `f4a9b22` passes all 17 jobs. All 15 stable report sets were downloaded and their status, complete selected-source counts and finding locations verified. Native packaged-app capture run [37412333746](https://github.com/andrewleech/picolet/actions/runs/37412333746) at the same revision produces all eight final images; every image has been viewed and shows the fully rendered app UI, including Intel Notes / Dashboard after their entrance animations. This closes production build / native frontend / actual IPC / direct visual-capture evidence for the four examples on both supported architectures, not AppHarness inspection, first-paint measurement, hardware DFU flashing or macOS metric parity.

The final native run's full workflow remains failed only at the Mac inspector-dependent first-paint measurements. All native app observation / upload steps pass, Linux passes both performance bounds, and Mac NFR-TEST-1 medians pass at 67.6 / 107.4 ms for PyDFU / Notes on ARM64 and 235.4 / 311.1 ms on Intel. All 20 Mac first-paint attempts retain the refused loopback WebSocket error; no Mac first-paint median is available or accepted. The remaining decisions are the supported inspector / AppHarness transport contract and SDK dependency/licence acceptance, with actual curated inventory mismatches still requiring reconciliation. Signing, publication and macOS 11 runtime execution were not performed.
