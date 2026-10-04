# PHASE 31 — Static analysis integration

## Goal

Choose and integrate the SAST tools from the SAST programme that apply to Picolet, with the same pinned versions, rules and source scope available in local runs and GitHub Actions. Cover Picolet's compiled C/C++, frozen Python, host-side Python and JavaScript / TypeScript without treating every candidate tool as an automatic CI gate.

## Background

Picolet has native runtime code, MicroPython modules frozen from manifests, host-side CLI code, and Vue / TypeScript examples. The existing CI builds runtime variants and examples, while the root Python configuration selects Ruff `E` and `F`; it does not select Ruff `S` or define a SAST job. The existing SAST portfolio includes Python and native-code tools with different scope and licence constraints, so this phase needs to record which tools apply to this public repository and where they run before making findings gating.

The Picolet examples were trialled on 2026-10-05. These are exploratory results, not a security review:

| Tool | Scope and result | Limitations |
|---|---|---|
| Semgrep CE 1.179.0, `p/python` plus MicroPython rules | 45 tracked Python files, 0 findings | Semgrep's default ignore patterns skipped 15 test/config/fixture files; not the full examples tree |
| Opengrep stable 1.30.0, `p/python` plus MicroPython rules | 55 Python files, 0 findings, 13.07 s | Directory scan also walked non-Python files |
| Opengrep interfile alpha `v2.0.0-nopython-interfile.alpha.3`, same rules | 55 Python files, 0 findings, 192.83 s | Expanded companion discovery into the Picolet MicroPython checkout; 18 scan warnings, including parser warnings from out-of-scope files |
| Ruff 0.16.8 `S` | 87 findings: 44 `S101`, 34 `S311`, 9 `S110` | Findings include test and screenshot tooling; no triage was done |
| Pyrefly 1.3.2 | 55 modules and dependencies, 134 diagnostics | 67 missing imports, 54 missing attributes, 6 bad argument types, and 7 other diagnostics |
| Pysa 0.10.0 | 0 issues | Used socket / `eval` / `exec` models only; Pyrefly had errors, so the result is exploratory |

## Zero-result recheck, 2026-10-06

The Semgrep result is reproducible, but it is not evidence of broad security coverage. Re-running Semgrep CE 1.179.0 with `p/python` scanned 45 tracked Python files using 151 rules, reported 0 findings and parsed about 100% of the selected files. Verbose output shows that Semgrep's built-in default ignore patterns skipped 15 files, including all example test files; there is no repository `.semgrepignore` file. The rules were the Registry `p/python` set, not GitLab's managed rules or a security-audit profile.

A temporary `socket.recv()` to `eval()` fixture was detected by both Semgrep and Opengrep when the SAST-02 custom MicroPython taint rule was explicitly supplied. That confirms the engines can run the custom rule, but does not show that this rule applies to Picolet's host-side examples or that the original Picolet custom rules were valid. The probe was kept outside the repository.

The Pysa zero remains unverified as a security result: its tested sources and sinks were limited to socket input and `eval` / `exec`, while Pyrefly reported 134 diagnostics. The Opengrep alpha also returned zero with 18 warnings and expanded analysis into the MicroPython checkout. The original Pysa and alpha command lines/raw logs are not retained here, so their exact runs cannot currently be reproduced from this ticket.

Treat all of these zero counts as inconclusive. Before relying on them, retain exact commands and raw scanner output, pin the intended rule packs, make selected-file counts and parse/ignore/warning errors visible, and run realistic positive and negative fixtures for each chosen source/sink pair.

The interfile scan is too slow and broad for an unqualified per-PR gate on this evidence. The Pysa result does not establish clean taint coverage. Neither should be promoted based on a zero-result run alone.

## Scope

- Inventory the current SAST portfolio and the Picolet languages / source classes each tool can analyse, including licence and CI-platform constraints.
- Decide the role of CodeQL, GitLab SAST and Advanced SAST, Semgrep CE, cppcheck, GCC `-fanalyzer`, CodeChecker / Clang Static Analyzer, Coverity, Ruff `S`, Bandit, Opengrep stable and interfile alpha, and Pyrefly / Pysa. Mark tools as CI gates, report-only, local-only, deferred or not applicable with reasons.
- Derive C/C++ input from the compile commands for the runtime variants actually built in CI. Derive frozen Python from Picolet's manifests and keep it distinct from host CLI / test code and vendored test trees.
- Define the source scope for JavaScript / TypeScript and decide how generated bundles, vendored dependencies, example tests and screenshot scripts are handled.
- Make every selected tool reproducible locally with the same version, configuration, scope and result format as CI. Preserve ownership distinctions for Picolet code, vendored code and upstream submodules.
- Add focused fixtures based on plausible defects to demonstrate signal for each selected analyser, and record known warnings, skips and baseline policy.

## Deliverables

- A tool-by-tool decision table with supported language, scope authority, local command, CI location, output format, licensing and gating role.
- A GitHub Actions workflow or jobs that run the selected tools on pull requests and can be reproduced with documented local commands.
- Scope generation for compiled C/C++ and manifest-frozen Python that follows the build configuration rather than scanning unrelated checked-out source.
- SARIF or another native CI result format where supported, with unowned / vendored findings routed or reported separately from Picolet-authored findings.
- A reviewed baseline and explicit policy for new findings, suppressions, and failures caused by unavailable or incomplete analysis.
- Updated documentation with the selected scanner set, versions, coverage gaps and local run instructions.

## Acceptance

- Every current SAST portfolio tool has an explicit keep / defer / drop / not-applicable decision for Picolet; duplicate coverage and licence or runner constraints are recorded.
- Each selected tool scans the intended source scope, and scope tests show that production source is included while unrelated generated, vendored test or host-only code is handled as documented.
- A clean checkout can run each selected scanner locally using the same pinned version and configuration as CI.
- Pull-request CI publishes readable results and applies the agreed finding policy without treating known baseline findings as newly introduced defects.
- Each selected analyser demonstrates detection on a realistic fixture not authored as a trivial scanner echo, and the expected non-findings / exclusions are also checked.
- The pipeline reports parser failures, excluded files and unresolved Pyrefly diagnostics; a zero-finding report cannot pass as clean coverage when its scope or model is incomplete.
- GitHub Actions checks pass on a Picolet pull request, with no changes to vendored MicroPython sources solely to accommodate the scanners.

## Out of scope

- Fixing vulnerabilities discovered during the rollout; file separate fixes with ownership and evidence.
- Treating experimental interfile Opengrep or currently incomplete Pysa models as mandatory gates without a new decision supported by measured scope, runtime and signal quality.
- SAST qualification against a regulatory standard; the pipeline is a development aid, not tool validation.
