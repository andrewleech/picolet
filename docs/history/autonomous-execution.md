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
