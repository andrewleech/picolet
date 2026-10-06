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

The implementation resolves frozen Python from each variant manifest for all 15 accepted target/variant combinations, records actual native compiler commands during builds, and preserves source ownership in the exported scope and compile database. A Linux x64 `cli` build captured and normalized 226 compiler commands across 226 source files. The SAST runner applies explicit source boundaries for runtime Python, host CLI Python, bridge TypeScript, example application sources, and compiler-derived native files.

The CI SAST matrix covers all 15 runtime combinations on their supported runners. Hosted run [37398322821](https://github.com/andrewleech/picolet/actions/runs/37398322821) passed every stable-scanner cell; all 15 downloaded report sets retain findings, parser warnings and captured source scope. Successful execution does not establish full parser coverage or a clean security verdict.

Current revalidation [37412333684](https://github.com/andrewleech/picolet/actions/runs/37412333684) at `f4a9b22` passes all 17 jobs: lint, the repaired host-unit invocation and 15 supported stable cells. All 15 report sets were downloaded and checked: summary counts match retained source groups, scanner exits are zero / report-only and every actual finding location resolves to selected scope. Each project report retains six findings / one notification; native reports retain one Linux/macOS or zero Windows findings and 32–59 notifications. This is execution and scope evidence, not a clean security verdict.

### Per-commit quality checks

`.github/workflows/ci.yml` runs changed-file Python lint, maintained unit-test groups, and a SAST report on every push and pull request. The unit suites run in separate invocations where the host and runtime `picolet` packages need different import paths.

Opengrep stable 1.30.0 is the default report-only scanner. Manual workflow dispatch selects one analyzer from Opengrep stable, Opengrep interfile alpha, Semgrep CE 1.179.0, Ruff `S` 0.16.8, Pyrefly 1.3.2, or Pysa 0.10.0. Each scanner has a positive and negative fixture check and produces SARIF or its native JSON output. Findings remain report-only because the existing findings have not been triaged into a reviewed baseline.

Report-only applies to findings/type diagnostics, not scanner startup or operational errors. Each invocation removes its previous report before execution and requires a new report; unexpected tool exit codes fail the command. Opengrep / Semgrep operation failures retain `analysis-summary.json` with `status: operation_failed`, the actual tool exit code where available and the error, alongside captured scope / logs / any partial reports. This cannot preserve artifacts if the runner itself terminates before upload. Pyrefly diagnostics remain non-gating but are recorded in `analysis-prerequisites.json` and the summary; Pysa summaries mark coverage incomplete when that prerequisite has diagnostics. This does not establish complete coverage when diagnostics are absent. Source-scope and execution-status regression tests run in the SAST matrix.

The initial Linux CLI findings dispositions and the fifteen-cell triage at `f4a9b22` are recorded in [PH31_findings-review.md](PH31_findings-review.md), together with a proposed baseline / gating policy awaiting decision. They do not introduce suppressions or a gating baseline.

The SAST matrix captures actual C/C++ compiler calls while building all 15 accepted target/variant combinations. The manifest exporter uses the same MicroPython `ManifestFile` resolver inputs as runtime builds; scope JSON preserves each frozen file's repository path, frozen target path and owner. The interfile alpha runs on Linux-hosted cells, including Windows cross-builds, because its tested binary is Linux x86 and its previous trial was slow and noisy. Its matrix is selected before job expansion rather than through an invalid job-level matrix condition.

Experimental alpha run [37400223017](https://github.com/andrewleech/picolet/actions/runs/37400223017) passed native build, scope capture and positive/negative fixtures in all nine cells, but every full analysis failed. The Linux WebView log records the runner receiving a shutdown signal, followed by exit 143; artifact upload did not run. The shutdown cause is unresolved, this is not an accepted alpha analysis receipt. A contained local CLI run exercised failure-summary retention with a genuine configuration-download failure (tool exit 2 / command exit 1), not a reproduction of the hosted shutdown.

Alpha scans use a temporary workspace containing every selected runtime / host / frontend / native input at its repository-relative path. Companion discovery therefore stays within the declared analysis scope instead of traversing unshipped MicroPython tests and nested SDK tooling. Source ownership and compiler-input selection are unchanged; report locations are rebased to repository paths before the workspace is removed.

The unbounded-checkout diagnosis passed all 179 project inputs and the complete 225-rule `p/security-audit` policy to the pinned alpha binary, inside a 4 GiB / 256-PID container. It parsed out-of-scope MicroPython / SDK files and was OOM-killed after 469.86 seconds (tool exit 137, cgroup `oom_kill: 1`). The complete scoped project finished in 19.39 seconds with the same 179 inputs / policy and six findings. The repaired full CLI completed project plus 245 locally available compiler-derived native files in 26.38 seconds, retaining six / one findings and one / 42 parser warnings, with no cgroup OOM events. Finding locations resolve to selected repository paths. These diagnostics used complete registry policy bodies downloaded through the host reader because direct container configuration retrieval failed; hosted nine-cell revalidation is still required.

Hosted revalidation [37407543981](https://github.com/andrewleech/picolet/actions/runs/37407543981) at `6f34af2` passed all nine alpha build / fixture / full-analysis / upload cells. Every downloaded summary reports tool exit zero; finding locations resolve to declared repository scope. Project reports retain six findings and five / six notifications, including the default interfile-depth limit. Native reports retain one Linux / zero Windows findings and 32–59 notifications. The separate host-unit failure concerns stale derived example templates, not the alpha matrix. A genuine pinned Pyrefly run also exercised the shared staging helper and retained its 115 diagnostics as incomplete coverage.


No GitLab rule-pack expansion is part of this work.

### Runtime-selectable scanner execution

**Settled direction, 2026-10-06:** every scanner trialled so far remains supported as an independently selectable execution path. The selected scanner set at invocation time determines which analyzers run; the scanner portfolio must not be hard-wired as one inseparable run. Scanner availability and selection are separate from whether its result is a CI gate.

The trialled scanners are Semgrep CE, Opengrep stable, Opengrep interfile alpha, Ruff `S`, and Pysa. Pyrefly was also trialled as a type checker and is part of the evaluated analysis set, though it has no security rule pack. The public GitLab Semgrep pack was trialled through Opengrep; that does not make GitLab Advanced SAST available or equivalent.

`scripts/run_sast.py --scanner <name> --target <target> --variant <variant>` runs exactly one selected analyzer. CI uses the same selection names through the `sast_scanner` workflow-dispatch input; pushes and pull requests default to Opengrep stable. The interfile alpha is Linux-only. Tool installation is pinned in the workflow and in the local commands below.





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

The `release.yml` build matrix is `{linux-x64, windows-x64} × {cli, webview, lvgl}` plus `{macos-x64, macos-arm64} × {cli, webview, lvgl}`. This workflow runs for runtime release tags or manual dispatch, not pull requests. PH31 also covers Linux `mcp` and Linux / Windows `tui`, which are outside the current release matrix.

There is no checked-in `compile_commands.json`. The runtime build can now record compiler and assembler invocations from the selected build and normalize them into a target-specific compile database with source ownership. A Linux x64 `cli` build produced 226 entries across 226 source files; the other target/variant builds have not been exercised locally.

The Unix MicroPython makefile currently leaves `SRC_CXX` empty. The compile database records commands actually emitted, so C++ is included only if a selected build invokes a C++ compiler.


### Frozen and host-side Python

Frozen Python scope is selected per variant by `FROZEN_MANIFEST` in `variants/*/*/mpconfigvariant.mk`. The current manifests use `freeze("../python", "picolet")`, `freeze("../python", "picolet_ui")`, or `freeze("../python", "picolet_tui")`; `manifest_lvgl*.py` also registers the LVGL C module. MicroPython's `makemanifest.py` executes the manifest before compiling the resolved files with `mpy-cross`.

The manifests also include `extmod/asyncio` and require selected modules such as `os-path`, `pathlib`, `__future__`, `functools`, and `itertools` from MicroPython / micropython-lib. `add_library()` declares available libraries; it is not by itself an instruction to freeze every file in those libraries. Scanning all of `packages/picolet-runtime/python/` would include packages not frozen in every variant; scanning all of the MicroPython submodule or micropython-lib would include unrelated upstream code.

The MicroPython `ManifestFile` resolver exposes the concrete source list through `files()` after executing each manifest with its build variables. `scripts/static_analysis_scope.py` now resolves frozen Python across all 15 accepted target/variant combinations and records repository path, frozen target path and owner. The manifest tests cover those combinations; actual runtime build / compile capture has so far been exercised only for Linux x64 `cli`.

`makemanifest.py --list-c-modules` exposes manifest-added C modules, and `py/manifest.mk` merges those paths into `USER_C_MODULES`. The captured native compiler commands include the selected build's effective source list, including manifest-added modules; each compile database stays target-specific because compiler flags differ between cells.

Keep these Python classes distinct:

| Python source class | Current source authority | Scope note |
|---|---|---|
| Runtime frozen Python | `packages/picolet-runtime/python/` plus each selected manifest's resolved dependencies | Scan only the package files frozen for the analyzed variant. Track Picolet source separately from MicroPython / micropython-lib dependencies. |
| Host CLI | `packages/picolet/picolet/` | This is the installed `picolet` CLI package, separate from the runtime-side `picolet` package under `packages/picolet-runtime/python/`. The workspace pytest path selects the CLI package globally. |
| Tests and fixtures | `tests/` and package-local tests | Not part of a runtime artifact; whether selected test helpers are analysed is a separate policy choice. |
| Examples | `examples/` | Mix of Python application code, Vue / TypeScript sources, build output and test / screenshot tooling. Keep app code distinct from generated bundles, vendored dependencies and test utilities. |

### Web, TypeScript and example tooling

The Picolet bridge package has TypeScript source in `packages/picolet-bridge-js/src/` (`index.ts` and `picolet.d.ts`), a `build.mjs` esbuild entry point, and a committed `dist/picolet-bridge.js` bundle copied into webview app romfs images. The bundle is a shipped artifact generated from `src/index.ts`; whether it needs separate analysis from its source remains a policy choice.

Five examples have Vue frontends: `notes`, `pydfu`, `config-editor`, `dashboard`, and `with-vue`. Their application source is under `ui/src/`, with `.vue` components and TypeScript modules / declarations. Each Vite project writes generated output to its app-level `dist/`; Vue app build scripts run `vue-tsc --noEmit` before `vite build`. `vite.config.ts`, `tsconfig*.json`, `package.json` and lockfiles are build/dependency inputs rather than frontend application modules.

The PR screenshot workflow installs dependencies and builds four frontends (`notes`, `pydfu`, `config-editor`, `dashboard`), then runs their Python Playwright screenshot scripts. `with-vue` is not part of those screenshot build steps. Example `tests/` are Python tests; `scripts/` include Python screenshot tooling. Keep these separate from the Vue/TypeScript application scope, and do not scan installed `node_modules` as project source.

The other example, `tui-pydfu`, is currently Python-only based on its checked-in source layout. Its presence does not expand the Vue / TypeScript source set.

### Current build targets

The runtime release workflow defines 12 target/variant cells: `linux-x64`, `windows-x64`, `macos-x64`, and `macos-arm64`, each with `cli`, `webview`, and `lvgl`. The perf workflow also builds Linux `webview` and macOS `webview` on x64 / arm64, all combinations already present in the release matrix. Release builds run only on runtime tags or manual dispatch. The runtime build script also supports `mcp` and `tui` combinations that do not appear in these workflow matrices.

The build script accepts 15 target/variant combinations: `cli`, `webview`, and `lvgl` on Linux x64, Windows x64, macOS x64 and macOS arm64; `mcp` on Linux x64 only; and `tui` on Linux x64 and Windows x64. PH31 scope is all five runtime variants across all 15 accepted combinations. This adds Linux `mcp` and Linux / Windows `tui` beyond the current 12-cell release matrix.

### Tool portfolio and scope decisions

The TypeScript bridge source is authoritative; generated `dist/` bundles are excluded. Example application sources are selected under app `src/` and Vue `ui/src/` directories. Example tests, screenshot scripts, generated bundles, installed dependencies, the host package's `_vendor/`, and MicroPython `mpy-cross` are excluded. `analysis-scope.json` records included paths, ownership and the exclusion rules for each run.

| Candidate | Decision and role | Scope, output and constraint |
|---|---|---|
| Opengrep stable 1.30.0 | Selected default; report-only | `p/security-audit` for Python / web app sources and `p/c` for build-derived C/C++; SARIF. Linux and macOS release assets are checksum-pinned. |
| Opengrep interfile alpha | Selected optional; report-only, Linux only | Same project rules with `--taint-interfile`; SARIF and raw log. Release is marked for testing only; prior trial took about 193 seconds and emitted warnings. |
| Semgrep CE 1.179.0 | Selected optional; report-only | Same split rule scopes as Opengrep; SARIF. It does not imply availability of Semgrep's paid managed products. |
| Ruff `S` 0.16.8 | Selected optional security-lint pass; report-only | Runtime, host and example Python; SARIF. Complements rather than replaces taint analysis. |
| Pyrefly 1.3.2 | Selected optional type analysis; report-only | Manifest-resolved runtime Python only; SARIF diagnostics plus the JSON handoff used by Pysa. It is not a SAST tool. |
| Pysa 0.10.0 with Pyrefly 1.3.2 | Selected optional taint analysis; report-only | Manifest-resolved runtime Python only; JSON. Models `input()` to `eval()` as a fixture-backed starting point, not broad library coverage. |
| CodeQL | Deferred | Not trialled against this 15-cell native build matrix. Database setup, runner cost and code-scanning availability were not evaluated; no claim is made about its coverage or licensing for this repository. |
| GitLab SAST / Advanced SAST | Not selected for CI | The project CI runs on GitHub. Advanced SAST requires GitLab Ultimate and is not equivalent to the public Semgrep rule repository trialled above. |
| cppcheck | Deferred | Not trialled against the generated compile databases; retain as a native-analysis candidate rather than assuming coverage. |
| GCC `-fanalyzer` | Deferred | Not trialled as a separate report and compiler support differs across the Linux, Windows-cross and macOS builds. |
| CodeChecker / Clang Static Analyzer | Deferred | Not trialled with the target compilers and generated source set; separate cross-toolchain configuration remains unproven. |
| Coverity | Deferred | Not trialled; requires a separately provisioned commercial analysis service and licence decision. |
| Bandit | Not selected as a separate job | Ruff `S` provides overlapping Bandit-derived rules; no separate signal was demonstrated. |

CI findings and type diagnostics are report-only until owners triage the existing findings and establish a reviewed baseline. Scanner startup failures or a missing result file fail the job; findings themselves do not. Raw logs accompany the reports so parser errors, warnings, partial scans and unresolved Pyrefly diagnostics remain visible.

The build captures compiler invocations, not a guessed directory walk. Each JSON compile entry retains the actual arguments, build directory and owner, while the frozen-Python scope records source path, manifest target path and owner. The target-specific compile database remains separate because compiler flags differ between cells.
Pyrefly and Pysa stage the manifest-selected files in a temporary tree for analysis, then map report file paths back to repository sources before the artifacts are retained.


### Local runs

Run the runtime build from a recursive checkout with the MicroPython integration branch available. The compile log must live under the repository because Linux and Windows builds write it from containers:

```sh
bash packages/picolet-runtime/scripts/build-runtime.sh \
  --target linux-x64 --variant cli \
  --compile-db-log "$PWD/packages/picolet-runtime/build/native-compile.jsonl"
python3 scripts/normalise_compile_database.py \
  --repo-root "$PWD" \
  --input packages/picolet-runtime/build/native-compile.jsonl \
  --output packages/picolet-runtime/build/compile_commands.json
```

Install the selected pinned tool first: `semgrep==1.179.0`, `ruff==0.16.8`, `pyrefly==1.3.2`, and `pyre-check==0.10.0` are Python packages; Opengrep stable and its Linux-only interfile alpha use the checksum-pinned release binaries in `.github/workflows/ci.yml`.

```sh
python3 scripts/check_sast_fixtures.py --scanner opengrep-stable
python3 scripts/run_sast.py \
  --scanner opengrep-stable \
  --target linux-x64 --variant cli \
  --compile-database packages/picolet-runtime/build/compile_commands.json \
  --output-dir packages/picolet-runtime/build/sast-results
```

The runner writes SARIF or native JSON reports, an analysis summary, exact source-scope JSON and captured tool logs. It runs only the selected analyzer.


## Scope

- Inventory the current SAST portfolio and the Picolet languages / source classes each tool can analyse, including licence and CI-platform constraints.
- Decide the role of CodeQL, GitLab SAST and Advanced SAST, Semgrep CE, cppcheck, GCC `-fanalyzer`, CodeChecker / Clang Static Analyzer, Coverity, Ruff `S`, Bandit, Opengrep stable and interfile alpha, and Pyrefly / Pysa. Mark tools as CI gates, report-only, local-only, deferred or not applicable with reasons.
- Derive C/C++ input from compile commands for all 15 supported runtime target/variant combinations. Derive frozen Python from all runtime manifests and keep it distinct from host CLI / test code and vendored test trees.
- Define the source scope for JavaScript / TypeScript and decide how generated bundles, vendored dependencies, example tests and screenshot scripts are handled.
- Make every selected tool reproducible locally with the same version, configuration, scope and result format as CI. Preserve ownership distinctions for Picolet code, vendored code and upstream submodules.
- Add focused fixtures based on plausible defects to demonstrate signal for each selected analyser, and record known warnings, skips and baseline policy.

## Deliverables

- A tool-by-tool decision table with supported language, scope authority, local command, CI location, output format, licensing and gating role.
- A runtime-selectable runner supports the trialled scanner set and executes only the scanner(s) selected for that invocation; local and CI runs use the same selection interface.
- Scope generation for compiled C/C++ and manifest-frozen Python follows the build configuration rather than scanning unrelated checked-out source.
- SARIF or another native CI result format where supported, with unowned / vendored findings routed or reported separately from Picolet-authored findings.
- A reviewed baseline and explicit policy for new findings, suppressions, and failures caused by unavailable or incomplete analysis.
- Updated documentation with the selected scanner set, versions, coverage gaps and local run instructions.

## Acceptance

- Every scanner trialled to date remains supported and individually selectable, regardless of whether its results are configured as a CI gate.
- Portfolio candidates not yet trialled have an explicit keep / defer / not-applicable decision for Picolet; duplicate coverage and licence or runner constraints are recorded.
- Each selected tool scans the intended source scope, and scope tests show that production source is included while unrelated generated, vendored test or host-only code is handled as documented.
- Analysis inputs cover all five runtime variants and every target/variant combination accepted by `build-runtime.sh`, including `mcp` and `tui`.
- A clean checkout can run each selected scanner locally using the same pinned version and configuration as CI.
- Pull-request CI publishes readable results and applies the agreed finding policy without treating known baseline findings as newly introduced defects.
- Each selected analyser demonstrates detection on a realistic fixture not authored as a trivial scanner echo, and the expected non-findings / exclusions are also checked.
- The pipeline reports parser failures, excluded files and unresolved Pyrefly diagnostics; a zero-finding report cannot pass as clean coverage when its scope or model is incomplete.
- GitHub Actions checks pass on a Picolet pull request, with no changes to vendored MicroPython sources solely to accommodate the scanners.

## Out of scope

- Fixing vulnerabilities discovered during the rollout; file separate fixes with ownership and evidence.
- Treating experimental interfile Opengrep or currently incomplete Pysa models as mandatory gates without a new decision supported by measured scope, runtime and signal quality.
- SAST qualification against a regulatory standard; the pipeline is a development aid, not tool validation.
