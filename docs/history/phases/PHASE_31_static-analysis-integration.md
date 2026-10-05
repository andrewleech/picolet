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

On 2026-10-06, Semgrep CE was also run with `p/security-audit` plus `p/python`. It loaded 346 rules and ran 228 applicable rules across 145 tracked files: 198 Python, 20 TypeScript, 9 multi-language, and 1 JSON rule. It reported 0 findings and parsed about 100% of selected files; the same 15 default ignores still applied. A temporary `pickle.loads()` fixture triggered `python.lang.security.deserialization.pickle.avoid-pickle`, confirming the broader profile can detect at least one applicable unsafe-deserialization pattern. The profile result is still not a clean bill of health.

The Python analysis should compare this Semgrep profile with CodeQL's `security-extended` suite, not treat `p/python` alone as the security ruleset. Ruff `S` remains a useful Bandit-derived lint pass, not a substitute for dataflow analysis. Pysa needs the shipped library models plus Picolet-specific models for its real inputs and dangerous operations; adding rules without accurate source/sink models will not fix the zero-result problem. Pyrefly is a type checker and has no security rule pack.

A temporary `socket.recv()` to `eval()` fixture was detected by both Semgrep and Opengrep when the SAST-02 custom MicroPython taint rule was explicitly supplied. That confirms the engines can run the custom rule, but does not show that this rule applies to Picolet's host-side examples or that the original Picolet custom rules were valid. The probe was kept outside the repository.

The Pysa zero remains unverified as a security result: its tested sources and sinks were limited to socket input and `eval` / `exec`, while Pyrefly reported 134 diagnostics. The Opengrep alpha also returned zero with 18 warnings and expanded analysis into the MicroPython checkout. The original Pysa and alpha command lines/raw logs are not retained here, so their exact runs cannot currently be reproduced from this ticket.

Treat all zero counts as inconclusive until each selected scanner has realistic positive and negative fixtures, and the run records its exact command, pinned rule packs, target counts, ignores, parse failures, warnings, and unresolved type diagnostics. Use `p/security-audit` plus `p/python` as the Semgrep CE baseline to measure, then decide whether its noise and coverage justify CI use. Evaluate CodeQL `security-extended` for Python and the native-code scopes. Pysa remains conditional on successfully loading relevant shipped models and adding project-specific sources / sinks.

The interfile scan is too slow and broad for an unqualified per-PR gate on this evidence. The Pysa result does not establish clean taint coverage. Neither should be promoted based on a zero-result run alone.

## GitLab rule trial, 2026-10-06

GitLab documents 108 Advanced SAST Python rules, but its Advanced SAST ruleset is proprietary and is not the same ruleset as its Semgrep analyzer. The documented rule list is not runnable rule source, so this trial cannot reproduce Advanced SAST's cross-file and cross-function taint engine or claim equivalent coverage. Running that analyzer itself requires GitLab Ultimate.

The public GitLab-managed `security-products/sast-rules` repository provides the Semgrep rules used by GitLab's standard analyzer. At ruleset tag `v2.10.1` / commit `53bf5cf6df3c51b6c02110f5a638b5e6213666cd`, Opengrep 1.30.0 scanned `examples` with the repository's `python/`, `rules/gitlab/python/`, and `rules/lgpl-cc/python/` rules. It ran 80 rules across 45 tracked Python files and reported 55 findings: 34 weak-random-number matches and 21 `assert` matches. Four directories matching the repo's `.semgrepignore` patterns were skipped, and the run emitted one warning because Opengrep does not support a `metavariable-regex` field in one rule. A separate `pickle.loads()` fixture triggered GitLab's `python_deserialization_rule-pickle` rule.

An unscoped repository-root run with the same rule directories traversed 57,627 files, applied the rules to 2,764 Python files, and reported 1,816 findings; 93 ignored directories were skipped and four files were only partially analyzed. This includes vendored and test code and is too broad to treat as a Picolet-owned findings baseline.

This is a wider GitLab-maintained Semgrep rules trial, not a GitLab Advanced SAST run. The open GitLab pack detects useful local patterns, but the example scan's findings are largely general lint-like checks rather than cross-file taint paths. For an Advanced SAST comparison, use the actual GitLab Ultimate analyzer on CI or report the unavailable proprietary engine/rules as a coverage gap.

## Current status and next work

Further GitLab rule-source exploration is parked. The public GitLab Semgrep pack is recorded as a comparison only; it does not replace GitLab Advanced SAST or establish equivalent coverage. Reopen this question only if running the actual analyzer becomes available or the roadmap decision changes.

The source-boundary inventory is recorded below, and read-only probes verified that MicroPython's manifest resolver can enumerate frozen Python inputs for every checked-in runtime manifest. GitLab rule-source exploration remains parked; remaining PH31 scope work is to capture build-resolved native commands, automate the manifest file list with build-identical variables, and finish the JavaScript / TypeScript ownership map before choosing CI gates.

- Runtime makefiles and variant configurations define native build inputs; a repository-wide source scan is not equivalent.
- Frozen/runtime Python is manifest-selected and host CLI Python remains a separate package. Examples combine app code with tests and tooling.
- The release workflow covers Linux / Windows `cli`, `webview`, and `lvgl` variants plus macOS variants, but is tag/manual only and there is no PR SAST or CodeQL job.
- Runtime builds do not currently emit native compile commands or a frozen-source inventory.


No GitLab rule-pack expansion is part of this work.



## Tool and source-scope inventory

This inventory records the current source boundaries and their authority. It does not select scanners or turn any source class into a CI gate.

### Native C/C++

`build-runtime.sh` invokes the MicroPython unix or Windows port makefiles with `VARIANT_DIR` set to `packages/picolet-runtime/variants/<variant>/<port>`. The makefiles and variant files assemble the build source lists; scanning the runtime checkout recursively would include sources that are not compiled into the selected artifact.

| Native source class | Current source authority | Ownership and scope note |
|---|---|---|
| Picolet runtime glue | `packages/picolet-runtime/variants/`, `variants/common/`, and `user_c_modules/` | Picolet-authored C/C++ sources, selected per variant and port. Includes sources explicitly added by `SRC_C` / `EXTRA_SRC_C` and files picked up through `$(wildcard $(VARIANT_DIR)/*.c)`. |
| MicroPython runtime | `packages/picolet-runtime/micropython/` submodule | Upstream code plus the composed integration branch and downstream overlay changes. The build uses much of this tree, but that does not make the whole checkout Picolet-owned; preserve upstream / downstream ownership in findings. |
| LVGL binding and LVGL | `packages/picolet-runtime/lib/lv_binding_micropython/` and its nested submodules | Registered as a C module by the LVGL manifests. It brings generated binding C plus LVGL and driver sources; it is a separate upstream dependency, not Picolet-authored source. |
| Host build tool | `packages/picolet-runtime/micropython/mpy-cross/` | Built during runtime builds to compile frozen modules, but it is an upstream host executable and not code shipped in the runtime artifact. Do not conflate its compilation with the runtime's native target sources. |

The Unix port adds its base `SRC_C`, its selected variant's `*.c`, and shared sources; variant makefiles can append further Picolet sources. The Windows port sets its own `SRC_C`, adds the variant's `*.c`, then adds `EXTRA_SRC_C` and the shared romfs trailer. In particular, a directory walk cannot model the Windows source list, and the LVGL manifest contributes its C module through MicroPython's `py/manifest.mk`.

The `release.yml` build matrix is `{linux-x64, windows-x64} × {cli, webview, lvgl}` plus `{macos-x64, macos-arm64} × {cli, webview, lvgl}`. This workflow runs for runtime release tags or manual dispatch, not pull requests. Other buildable variants such as `mcp` and `tui` are not in this release matrix, so the eventual analysis target set must say whether those variants are also covered.

No `compile_commands.json` is present at the repository root, and the runtime build commands do not currently emit one. The native analysis input therefore remains to be generated from each selected target/variant build, with a reliable association between each command and its source ownership. The generated C/C++ set should be checked against the actual make invocation, including manifest-added modules, rather than inferred from tracked paths alone.

The Unix MicroPython makefile currently leaves `SRC_CXX` empty. Treat C++ as an analysis input only if a selected build's effective compiler commands include it; native source scope must follow commands actually emitted rather than assuming the language from extensions in the checkout.


### Frozen and host-side Python

Frozen Python scope is selected per variant by `FROZEN_MANIFEST` in `variants/*/*/mpconfigvariant.mk`. The current manifests use `freeze("../python", "picolet")`, `freeze("../python", "picolet_ui")`, or `freeze("../python", "picolet_tui")`; `manifest_lvgl*.py` also registers the LVGL C module. MicroPython's `makemanifest.py` executes the manifest before compiling the resolved files with `mpy-cross`.

The manifests also include `extmod/asyncio` and require selected modules such as `os-path`, `pathlib`, `__future__`, `functools`, and `itertools` from MicroPython / micropython-lib. `add_library()` declares available libraries; it is not by itself an instruction to freeze every file in those libraries. Scanning all of `packages/picolet-runtime/python/` would include packages not frozen in every variant; scanning all of the MicroPython submodule or micropython-lib would include unrelated upstream code.

The MicroPython `ManifestFile` resolver exposes the concrete source list through `files()` after executing a manifest with its build variables. Read-only probes resolved all eight checked-in manifests: `cli` and `mcp` select 21 Python files each, `webview` and `lvgl` select 35 each, and `tui` selects 87. The lists include Picolet runtime modules, MicroPython's `extmod/asyncio`, and micropython-lib modules. This is a workable source for a frozen-file exporter, provided it uses the same `MPY_DIR`, `PORT_DIR`, `MPY_LIB_DIR`, manifest and variant variables as the build. These resolver probes do not yet prove that each list matches every target build or resolve native C modules.

`makemanifest.py --list-c-modules` already exposes manifest-added C modules, and `py/manifest.mk` merges those paths into `USER_C_MODULES`. A final C/C++ inventory still needs the effective build commands so it includes MicroPython's base files and each module's recursive build inputs, with command flags preserved by target.

Keep these Python classes distinct:

| Python source class | Current source authority | Scope note |
|---|---|---|
| Runtime frozen Python | `packages/picolet-runtime/python/` plus each selected manifest's resolved dependencies | Scan only the package files frozen for the analyzed variant. Track Picolet source separately from MicroPython / micropython-lib dependencies. |
| Host CLI | `packages/picolet/picolet/` | This is the installed `picolet` CLI package, separate from the runtime-side `picolet` package under `packages/picolet-runtime/python/`. The workspace pytest path selects the CLI package globally. |
| Tests and fixtures | `tests/` and package-local tests | Not part of a runtime artifact; whether selected test helpers are analysed is a separate policy choice. |
| Examples | `examples/` | Mix of Python application code, Vue / TypeScript sources, build output and test / screenshot tooling. Keep app code distinct from generated bundles, vendored dependencies and test utilities. |

### Web and TypeScript

The JS / TypeScript surface is example-oriented rather than one uniform package scope. `packages/picolet-bridge-js/` and the source directories under `examples/` need to be inventoried against their own package manifests and build scripts before defining analysis inputs. Generated `dist/` output, installed dependencies, test code, and screenshot scripts must not silently become Picolet application source.

### Scope decisions still needed

- Choose which runtime variants and target platforms are part of native analysis, including whether the `mcp` and `tui` variants outside the release matrix are covered.
- Capture each selected make invocation's effective C/C++ compiler commands, then classify commands and files as Picolet, MicroPython integration/overlay, LVGL binding, LVGL, or other dependency. Decide how shared compilation across targets is deduplicated without losing target-specific flags.
- Turn the proven `ManifestFile.files()` resolution into a checked-in/exported input using exactly the build's manifest variables, and report Picolet-owned files separately from library / submodule files.
- Map host CLI, examples, bridge JS, generated outputs, tests and screenshot tooling to explicit include/exclude rules.
- Continue into the per-tool decision table only after these source scopes and ownership boundaries are agreed.

This pass confirms the scope-authority model and its gaps; it does not implement a compile database or frozen-file export.


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
