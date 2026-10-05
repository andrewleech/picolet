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
