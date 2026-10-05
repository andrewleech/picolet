# Autonomous roadmap execution

Assessed: 2026-10-06 at HEAD 3b159fa.

## Position

PH24–PH29 have source-side implementations. PH30 remains pending binary/runtime verification, not approved. PH31 has manifest-derived scope, compiler capture and six selectable report-only scanners; only Linux x64 CLI has local build evidence. The latest CI run, [37301188542](https://github.com/andrewleech/picolet/actions/runs/37301188542), failed before creating jobs. Local actionlint reproduced an invalid job-level `matrix` reference and obsolete `macos-13` labels.

## Execution order

1. Restore executable CI: select the alpha scanner's Linux-hosted matrix before job expansion, replace retired Intel macOS runners, validate all affected workflows, push and exercise GitHub Actions.
2. Run manual runtime-release verification without publishing a release. Collect the six macOS artifacts, build logs and runner metadata. Repair observed build failures without marking source inspection as runtime proof.
3. Run performance verification on the supported runners. Exercise CLI startup, WKWebView IPC and LVGL behaviour through the existing phase harnesses where the environment supports them. Reconcile PH30 against the full specification and actual evidence.
4. Exercise PH31's supported matrix and retain scope, compile databases, scanner reports and warnings. Fix execution/reporting defects, keeping security findings report-only. Reconcile documentation with the observed runs.
5. Triage scanner findings against actual sources, record justified dispositions and coverage gaps. Do not invent a suppression baseline or promote experimental analysis to a security gate based on zero findings.

Each substantive fix is verified and committed by domain. Luna (`openai-codex/gpt-6-luna`) handles bounded independent surveys/implementation; the coordinating agent owns integration, observed verification and acceptance decisions. Existing user wheel files and dirty nested submodule state are preserved.

## Boundaries

No release tags, public releases, PyPI publishing or signing identities are created. Code signing/notarisation requires credentials and distribution policy. SDK licensing for macOS cross-builds needs an explicit legal decision. Universal binaries, app/DMG packaging and ARM Linux are recorded post-v1.2 backlog, not silently added to acceptance scope. GitLab Advanced SAST remains parked unless the proprietary analyzer becomes available. These items do not prevent source/build/runtime verification of the current roadmap.

## Evidence ledger

- Luna read-only SAST survey completed, source findings require coordinator verification before implementation.
- Corrected CI, release and performance workflow definitions pass actionlint locally (shellcheck/pyflakes disabled). Hosted execution is still required.

## Execution evidence

- Four Luna workers completed two read-only surveys and two bounded implementation slices. The coordinator corrected one inaccurate runner-status sentence and reviewed the scanner contracts before integration.
- Local verification: 860 host tests passed (35 skipped, one expected failure), 100 runtime IPC tests passed, 85 system API tests passed (two skipped), 47 TUI tests passed (two skipped), and 25 PH31 tests passed. Ruff, shell syntax and actionlint checks passed.
- Real Linux CLI execution exercised `ffi.open(None)` / `getpid` and availability of `gc.add_heap`. The macOS release workflow now runs the corresponding Darwin assertion on all six binaries; hosted results are still pending.
- Real Opengrep fixtures passed. Project scan: 165 files, six findings, one partially analysed file. Compiler-derived native scan: 226 source files, one finding, 35 syntax-error notifications. Initial dispositions are in [PH31_findings-review.md](phases/PH31_findings-review.md), with no suppressions.
- Actual scanner startup failure with Opengrep removed from PATH exited 1 rather than reporting success.
- Hosted run [37306676452](https://github.com/andrewleech/picolet/actions/runs/37306676452) reached a macOS 14.8.9 arm64 runner and uploaded metadata, then failed during recursive checkout: MicroPython gitlink `570085eb43469636e27f0e6358178b2f822f77f0` was a local integration merge absent from the remote. The fix records reachable `pr/unix-windows-romfs` tip `8a2067d353c095af567997c6a15792ce00f8f9b7` in the parent index without switching or cleaning the user's local integration checkout. Composition remains a per-build operation.
- The integration script's `mapfile` use was replaced with a Bash 3-compatible read loop for the macOS system shell.

The remaining acceptance work depends on successful hosted checkout/build/runtime execution. Queued or cancelled runs are not pass evidence. Superseded queued CI/release runs were cancelled after pushing the verified fixes.

The exact branch-read loop resolved all 12 configured branches under Bash 3.2.57. A fresh remote clone successfully checked out the replacement gitlink. Full clean integration composition is still being exercised, including transitive dependency downloads.

Remaining phase entry and acceptance checks are captured in [tickets/PH30_hosted-acceptance.md](tickets/PH30_hosted-acceptance.md); [README.md](README.md) explains revalidation and execution conventions.

Hosted release run [37307852754](https://github.com/andrewleech/picolet/actions/runs/37307852754) confirmed the replacement gitlink can be recursively checked out. Linux CLI/webview build jobs passed. The next failures were macOS rerere resolution and obsolete integer conversion calls in Windows FFI / the LVGL binding generator. The rerere copy now names its destination hash explicitly and strips the source's trailing slash, avoiding BSD/GNU directory-copy differences. Integer conversions use the current `mp_obj_int_to_bytes` API with truncation enabled, in maintained source rather than generated C: MicroPython feature commit `84371b4845` and LVGL binding fork commit `15a87a2524d04cf57769dd2a10a4e28c23430ab8`. The parent LVGL gitlink/remote selects that published generator fix.

A scratch Linux checkout successfully composed all 12 integration branches using the seeded resolutions. A throwaway generator input exercised uint64 argument/return generation with the new API. Full Windows/LVGL builds and the macOS rerere fix still require the next hosted run; no successful macOS runtime verdict is recorded yet.

Subsequent hosted runs confirmed successful CLI/webview/LVGL builds on Linux and Windows, and successful integration composition on both macOS architectures. macOS then exposed host prerequisites and GNU-only shell/build assumptions: Homebrew's externally managed Python, the `glibtoolize` executable name, `realpath --relative-to`, Bash 3 empty-array expansion under `nounset`, and ELF-only ROMFS object generation. These are repaired without bypassing package-manager protections or requiring GNU utilities on Darwin. ROMFS now uses the native assembler on Darwin, with ARM64/x64 Mach-O objects and both data/end symbols verified using Clang 18 cross-target assembly on Linux. The maintained ROMFS feature branch also places an empty statement after its execution label, allowing a following declaration with Apple's C compiler. Parent gitlink `7dabbd9bb56411f2e4cf7c169c963dc61e52a5b3` is published and reachable. [Run 37328069295](https://github.com/andrewleech/picolet/actions/runs/37328069295) verifies the combined native build path.

Hosted release run [37328069295](https://github.com/andrewleech/picolet/actions/runs/37328069295) executed the CLI runtime successfully on both Darwin architectures, including `ffi.open(None)` / `getpid`, `sys.platform` and `gc.add_heap`. All Linux/Windows variants built successfully. ARM64 CLI artifact upload failed because its runner lacked `sha256sum`, so Darwin sidecars use `shasum -a 256`. The other Mac variants exposed genuine native compilation/link failures: colon-bearing selector macro arguments, missing Foundation integer ABI types, object initialisers incorrectly using the struct-return message ABI, and ELF linker flags in the LVGL variant.

Two additional Luna workers repaired the native WebView ABI and Darwin LVGL exports/zlib lookup. Integration corrected the remaining selector-cache callsites. The PNG encoder was compiled and exercised on Linux, producing the expected red/green pixels with valid PNG chunk CRCs. This is not a Darwin snapshot verdict.

Performance run [37357721781](https://github.com/andrewleech/picolet/actions/runs/37357721781) failed before measurements: Mac x64 hit the native C errors, Mac ARM64 checkout failed, and Linux used obsolete CLI arguments. Both platforms now build each example from its own app directory against the exact fresh runtime and measure the resulting app binaries. An isolated local build produced both binaries, but their startup measurements failed waiting for the test-port announcement. Failed measurements remain failures, not acceptance evidence.

The Linux startup timeout was reproduced on the real WebKitGTK runtime by registering the `picolet://` asset scheme before creating a view. Scheme registration creates the engine's first context, before the view's inspector setup. The runtime now configures and retains one inspector port before either operation. A current-source native smoke accepted a TCP connection after scheme registration; the unmodified ordering timed out. This smoke used a larger heap for source hot-loading, it is not performance acceptance evidence.

The harness uses the renderer-aware event loop rather than a hard-coded GTK pump, and honours the child's supplied display. The visibility gate requires all xdotool criteria, preventing an empty-name match from accepting the X root window. Two mocked command-wiring assertions were removed rather than updated to match implementation details; the missing-X-server error case remains. Affected host suites passed 58 tests with seven Mac-only skips.

The Mac release checks include a native WKWebView probe that loads a coloured page, verifies its script message, captures through the existing C snapshot primitive, and checks the rendered PNG on the host. This does not replace the specified WebSocket inspector protocol or count an announced port as driver interoperability. A Linux headless screenshot was black, so no frontend-rendering acceptance is claimed from it.
