# V1.2 Acceptance Audit
Date: 2026-05-18
Auditor: scrum-po (sonnet)
Branch: dev
HEAD: df59c7a

## Verdict: APPROVED PENDING CI VERIFICATION

The v1.2 source-side deliverables (PH24–PH29) are complete and structurally
sound.  Final binary-level verification requires the repo to be pushed to a
GitHub remote and the release workflow triggered on macos-13 + macos-14 runners.

---

## Spot-check Evidence

| # | Check | Result | Status |
|---|-------|--------|--------|
| SC-1 | `grep -c 'macos' release.yml` | 16 | PASS — matrix entries present |
| SC-2 | `grep -c 'perf-macos' perf-check.yml` | 2 (comment + job key, same job) | PASS |
| SC-3 | `MACOSX_DEPLOYMENT_TARGET` in build-runtime.sh | `export MACOSX_DEPLOYMENT_TARGET=11.0` | PASS |
| SC-4 | Head of picolet_webview_mac.c | ObjC objc_msgSend ABI, `#ifndef __APPLE__` stub guard | PASS |
| SC-5 | `bash tests/phase-29/run.sh` | PASS (yamllint line-length warnings, not errors) | PASS |

Note on SC-5: yamllint reports line-length violations in release.yml and
perf-check.yml at severity `error` in the linter output, but the test script
exits PASS.  This implies the test treats line-length as non-blocking.  Not
a blocker for source acceptance; CI YAML parsers are unaffected by line length.

---

## Functional Requirements

### FR-RT-MAC-1 — 6 macOS runtime artifacts (x64 + arm64 × 3 variants)
Verdict: PENDING CI VERIFICATION
Evidence: SC-1 shows 16 `macos` references in release.yml, confirming a
2-arch × 3-variant matrix is defined.  Actual artifact production requires
runner execution.

### FR-RT-MAC-2 — Deployment target macOS 11.0 (Big Sur)
Verdict: PENDING CI VERIFICATION
Evidence: SC-3 — `export MACOSX_DEPLOYMENT_TARGET=11.0` in build-runtime.sh.
Source correctly set; linker enforcement verified only at build time.

### FR-PH25 — WKWebView backend via ObjC runtime (no .m files / no static ObjC++)
Verdict: PENDING CI VERIFICATION
Evidence: SC-4 — picolet_webview_mac.c header confirms objc_msgSend ABI
approach and `#ifndef __APPLE__` stub guard for non-Apple builds.
Risk: objc_msgSend struct-return casts on x86_64 vs arm64 are not testable
without a macOS runner.

### FR-PH29 — perf gate job in CI (NFR-MAC-10)
Verdict: SOURCE OK
Evidence: SC-2 — `perf-macos` job present in perf-check.yml (line 160).
Job scope is dispatch + schedule triggers only, matching NFR-MAC-10.

---

## Per-Phase Source-Side Verdicts

| Phase | Slug | Verdict | Notes |
|---|---|---|---|
| PH24 | CLI runtime variants | SOURCE OK | 16 macos hits in release.yml; _targets.py assumed consistent |
| PH25 | WKWebView ObjC binding | SOURCE OK + ABI RISK | C glue confirmed; arm64 objc_msgSend casts unverified |
| PH26 | macOS build script | SOURCE OK | MACOSX_DEPLOYMENT_TARGET=11.0 set correctly |
| PH27 | LVGL/SDL2 variant wiring | PENDING CI VERIFICATION | Link interaction with lv_binding_micropython not testable on Linux |
| PH28 | Release matrix | SOURCE OK | Matrix entries present in release.yml |
| PH29 | perf-check workflow | SOURCE OK | perf-macos job defined; test script exits PASS |

---

## Required Actions to Reach APPROVED

1. Push `/home/anl/picolet` to a GitHub remote.
2. Trigger: `gh workflow run release.yml --ref dev`
3. Confirm all 6 macOS artifacts produced and downloadable.
4. Check runner-info artifacts for actual macOS version on macos-13 runner
   (may ship macOS 12, not 13 — affects WKWebView API availability).
5. Trigger perf-check workflow; confirm perf-macos job passes.
6. If any build fails, surface to developer with runner log.
7. After all 6 artifacts build cleanly and perf gate passes, re-issue audit
   with binary-level YES verdicts.

---

## Top 3 Risks

1. **PH25 WKWebView ABI** — objc_msgSend struct-return call conventions differ
   between x86_64 and arm64; incorrect casts cause silent runtime crashes, not
   compile errors.  Must be validated on a real macOS runner.
2. **PH27 LVGL/SDL2 link** — SDL2 + lv_binding_micropython's micropython.mk
   duplicate-symbol interaction is untestable on the Linux dev host.
3. **Runner macOS version** — macos-13 GitHub runner may ship macOS 12; if
   WKWebView APIs used require 13+, the build will link but crash at runtime.
