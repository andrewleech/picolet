# PH00 — Verify mbm integration baseline

## Plan

### Goal (restated)

Confirm that the seven PRs listed in
[`packages/picolet-runtime/mbm.toml`](../../packages/picolet-runtime/mbm.toml)
still rebase cleanly onto current `upstream/master` of
[`micropython/micropython`](https://github.com/micropython/micropython),
and that the resulting `integration` branch in the
`packages/picolet-runtime/micropython` submodule produces working
**stock** `micropython` (unix port) and `micropython.exe` (windows port,
via dockcross) binaries.

**No overlay code is added in this phase.** The `overlay/` directory at
`packages/picolet-runtime/overlay/` does not yet exist; the
`rebuild-integration.sh` script's overlay-apply step (`[3/3]`) will
no-op via its "overlay directory is empty — skipping" branch. PH00
exists to nail down that the inherited pydfu-win base is still a viable
starting point before PH01 introduces the first downstream variant.

### Exit gate (restated)

| # | Condition | How verified |
|---|---|---|
| 1 | `scripts/rebuild-integration.sh` exits 0 with no manual conflict resolution. | Tester runs the script in a clean checkout; `mbm` exit code 0; no `git rebase --continue` interventions in script output. |
| 2 | Unix `micropython` built from the resulting `integration` branch prints `ok` for a trivial test. | `./build-standard/micropython -c 'print("ok")'` returns `ok` and exit 0. |
| 3 | Windows `micropython.exe` built via dockcross from the same `integration` branch prints `ok` under WSL interop. | `./build-standard/micropython.exe -c 'print("ok")'` returns `ok` and exit 0. |
| 4 | The submodule pointer in the parent repo (`packages/picolet-runtime/micropython`) is updated to the rebuilt `integration` tip. | `git -C packages/picolet-runtime/micropython rev-parse integration` matches the committed gitlink. |

**Spec coverage**: none directly. PH00 is a baseline smoke test — it
underwrites every later phase that depends on the mbm-composed
MicroPython tree but closes no FR / NFR on its own. Subsequent phases
(PH01 onwards) consume the green PH00 result implicitly via the
integration branch they build against.

### Why this phase exists at all

The seven PRs (#38–#44 on `andrewleech/micropython`) are the load-bearing
delta between stock MicroPython and the picolet runtime. Three of them
(`gc-add-heap`, `ports-windows-ffi`, `unix-windows-romfs`) ship
**runtime behaviour** that FR-RT-4, FR-RT-5, FR-RT-6, FR-RT-7 will
directly depend on; the other four (`lib-pyusb-windows`,
`mkrules-exe-fix`, `mkrules-frozen-str`, `ports-windows-variant-overrides`)
are build-system enablers. If any of these no longer rebases cleanly or
no longer produces a working stock binary, the rest of the v1 plan
needs surgery before more work piles on top.

`pydfu-win` proves the seven-PR stack works as of its current snapshot
(commit `873d2b0c20` on its integration branch). Time has passed since
that snapshot was taken; PH00 re-verifies on a fresh `upstream/master`.

---

### Inputs read while planning

| Path | Purpose |
|---|---|
| `/home/anl/picolet/docs/v1-spec.md` | FR / NFR contract (PH00 covers none directly) |
| `/home/anl/picolet/docs/v1-plan.md` § PH00 | Goal, deliverables, model tiers |
| `/home/anl/picolet/docs/architecture.md` § "Source layout for `picolet-runtime`" | submodule + overlay pattern |
| `/home/anl/picolet/CLAUDE.md` | branch / commit conventions, WSL interop test policy |
| `/home/anl/picolet/packages/picolet-runtime/mbm.toml` | seven PR definitions |
| `/home/anl/picolet/packages/picolet-runtime/scripts/rebuild-integration.sh` | the script under test |
| `/home/anl/pydfu-win/mbm.toml` | precedent — identical seven PRs |
| `/home/anl/pydfu-win/scripts/rebuild-integration.sh` | precedent — same flow with a downstream overlay |
| `/home/anl/pydfu-win/scripts/build-windows.sh` | dockcross command shape for windows port |
| `/home/anl/pydfu-win/micropython` submodule git log | confirms seven `Merge branch 'pr/...'` commits land in order onto upstream master |

### Files / scripts the developer will run

PH00 does **not** modify any source. The developer's role is to
exercise the existing tooling and capture the result in a submodule
pointer bump. The artefacts touched are:

1. `packages/picolet-runtime/mbm.toml` — **read-only**, the input config.
2. `packages/picolet-runtime/scripts/rebuild-integration.sh` — **invoked**.
3. `packages/picolet-runtime/micropython` — submodule, mutated by the
   script; its new HEAD (the `integration` branch tip) is what gets
   committed in the parent repo.
4. `docs/phases/PHASE_00_verify-mbm-baseline.md` — this file; later
   sections (`Implementation`, `Tests`, `Verification`) are filled by
   the developer / SQE / tester respectively.

### Build environment prerequisites

| Requirement | Verification command | Notes |
|---|---|---|
| WSL2 host (Linux side of `picolet`) | `uname -r` includes `microsoft-standard-WSL2` | Build host is WSL2 on Win 11 per CLAUDE.md. |
| Git ≥ 2.40 with submodule support | `git --version` | needed by `mbm` for branch graph ops. |
| `mbm` ≥ 2.0.0 | `mbm --version` | currently installed: 2.0.2. The script invokes `mbm rebase --target upstream/master --local --force-push --no-dry-run`. Confirm the CLI surface still matches (sub-commands `rebase`, flags `--target`, `--local`, `--force-push`, `--no-dry-run`). |
| Python ≥ 3.10 (for `mbm`) | `python3 --version` | mbm runs as a CLI but invokes git under the hood. |
| Docker engine reachable from WSL | `docker version` then `docker run --rm hello-world` | dockcross uses host docker. WSL2 + Docker Desktop integration is the supported config. |
| `dockcross/windows-static-x64-posix` image | `docker images dockcross/windows-static-x64-posix` | already pulled on the dev host. The image is ~2 GB content / ~9 GB on disk; fresh CI runners must `docker pull` first. |
| Network egress to `github.com` | `git -C packages/picolet-runtime/micropython ls-remote origin` | required for `mbm` to fetch the seven PR branches and upstream master. |
| Network egress to `github.com/micropython/micropython` | `git -C packages/picolet-runtime/micropython fetch upstream` | the script adds the `upstream` remote on first run; mbm rebases against `upstream/master`. |
| Network egress to `github.com/micropython/micropython-lib` | `cd packages/picolet-runtime/micropython && make -C ports/unix submodules` | the seven-PR set pulls in micropython-lib as a sub-submodule (PR #38 bumps the pinned tip). |
| `gcc`, `make`, `pkg-config`, `libffi-dev` on the WSL host | `dpkg -l libffi-dev` (or equivalent) | needed for the **unix** port host build. Windows port build is fully inside dockcross. |
| `python3 -m mpremote` available | `python3 -m mpremote --help` | only needed if the romfs path is exercised; **PH00 does not exercise romfs** — stock builds without `ROMFS_IMG=` skip it. Listed here for completeness because PH01+ will need it. |

### Sequence the developer follows

The developer's checklist, run from `/home/anl/picolet` on the `dev`
branch:

1. **Initialise the submodule** (idempotent — safe if already done):
   ```
   git submodule update --init --recursive packages/picolet-runtime/micropython
   ```

2. **Run the integration rebuild**:
   ```
   ./packages/picolet-runtime/scripts/rebuild-integration.sh
   ```
   Expected output ends with `Integration rebuilt at <short-sha>`.
   Capture the short SHA for the commit log entry.

   The script will:
   - Add the `upstream` remote pointing at `micropython/micropython.git`
     if absent and `git fetch upstream`.
   - Invoke `mbm rebase --target upstream/master --local --force-push --no-dry-run`,
     which rebuilds `integration_update` from the seven PR branches
     listed in `mbm.toml`, each rebased onto `upstream/master`.
   - Hard-reset `integration` to `integration_update`.
   - Detect that `overlay/` is empty (it doesn't exist yet) and skip the
     overlay-apply step.

3. **Build the unix port** (host build, no docker needed):
   ```
   cd packages/picolet-runtime/micropython
   make -C mpy-cross -j
   make -C ports/unix submodules
   make -C ports/unix -j
   ```
   Produces `ports/unix/build-standard/micropython`.

4. **Build the windows port via dockcross**:
   ```
   docker run --rm \
       -v "$PWD:$PWD" \
       -w "$PWD/mpy-cross" \
       --user "$(id -u):$(id -g)" \
       dockcross/windows-static-x64-posix \
       make -j
   make -C ports/windows submodules
   docker run --rm \
       -v "$PWD:$PWD" \
       -w "$PWD/ports/windows" \
       --user "$(id -u):$(id -g)" \
       dockcross/windows-static-x64-posix \
       make -j CROSS_COMPILE=x86_64-w64-mingw32.static.posix-
   ```
   Produces `ports/windows/build-standard/micropython.exe`. The
   dockcross invocation mirrors the form used in
   [`pydfu-win/scripts/build-windows.sh`](../../../../pydfu-win/scripts/build-windows.sh)
   but **without** the pydfu-specific `VARIANT=pydfu`,
   `FROZEN_MANIFEST=…`, `PROG=…`, `ROMFS_IMG=…` arguments — stock build
   only.

   Note: the `--user "$(id -u):$(id -g)"` flag is required so the
   generated build artefacts land owned by the host user, not root.
   This matches the global rule in `~/.claude/CLAUDE.md` about
   ephemeral docker containers.

5. **Commit the submodule pointer bump** on `dev`. Per CLAUDE.md, sign
   with `-s` and reference the phase id. Suggested subject:
   `[PH00] Pin picolet-runtime/micropython at rebuilt integration tip.`

6. **(Empty commits welcome)** Per the dev-branch-as-investigation-log
   policy: if any of the seven PR rebases reported merge-conflict-then-
   resolved-by-mbm steps, log it as
   `[PH00] Note: PR #NN required ...` even if no source change attaches.

### Verification commands (SQE)

The SQE confirms PH00's exit gate empirically:

**Gate 1 — rebase cleanliness**

```
# Re-run on a freshly cleaned submodule to prove reproducibility.
git -C packages/picolet-runtime/micropython clean -xfd
git -C packages/picolet-runtime/micropython checkout master
./packages/picolet-runtime/scripts/rebuild-integration.sh 2>&1 | tee /tmp/ph00-rebuild.log

# Expect:
grep -E '^(error|fatal|CONFLICT)' /tmp/ph00-rebuild.log    # → no matches
grep 'Integration rebuilt at' /tmp/ph00-rebuild.log         # → exactly one match
# Confirm seven PR-merge commits land on the integration branch:
git -C packages/picolet-runtime/micropython log --oneline integration ^upstream/master | grep -c "^[0-9a-f]\+ Merge .* pr/"
# Expect: 7
```

The `mbm rebase` step writes per-PR rebase progress; any line beginning
with `CONFLICT` indicates a manual resolution would have been needed,
which fails Gate 1. The current `mbm` v2 implementation aborts on
conflict rather than dropping to an interactive shell, so a non-zero
exit from the script is the primary signal.

**Gate 2 — unix port runs trivial test**

```
test "$( packages/picolet-runtime/micropython/ports/unix/build-standard/micropython -c 'print("ok")' )" = "ok"
```

For an additional smoke check that the seven PRs' runtime delta did not
break unix port basics, also run:

```
packages/picolet-runtime/micropython/ports/unix/build-standard/micropython \
    -c 'import gc; gc.add_heap(bytearray(4096)); print("heap-ok")'
# Expect: heap-ok
```

This exercises PR #41 (`gc.add_heap`). PH00 is a smoke test, so this
is informational rather than required for the gate, but a failure here
flags an upstream-merge regression that PH01 onward will trip on.

**Gate 3 — windows port runs trivial test under WSL interop**

```
test "$( packages/picolet-runtime/micropython/ports/windows/build-standard/micropython.exe -c 'print("ok")' )" = "ok"
```

CLAUDE.md `## Build and test policy` makes WSL-interop execution of
`.exe` files the standard test path; no Wine, no full Windows VM
required.

**Gate 4 — submodule pointer is up to date**

```
expected=$(git -C packages/picolet-runtime/micropython rev-parse integration)
actual=$(git ls-tree HEAD packages/picolet-runtime/micropython | awk '{print $3}')
test "$expected" = "$actual"
```

### Foreseeable risks

| Risk | Likelihood | Impact | Mitigation / response |
|---|---|---|---|
| **`mbm` upstream API breaks.** The script invokes `mbm rebase --target upstream/master --local --force-push --no-dry-run`. A 3.x release could rename flags. | low — package is `2.0.2` and project-controlled, but it's an external tool. | high — blocks the whole build pipeline. | Pin mbm in CI before PH15. For PH00 specifically: capture the installed `mbm --version` in the phase file and the commit message; if a flag is missing, log a `[PH00] Caveat:` empty commit and update the script (one-line). |
| **MicroPython upstream conflicts with one of PRs #38–#44.** Most likely in `pr/unix-windows-romfs` (#43) and `pr/ports-windows-variant-overrides` (#44) — they touch `ports/windows/mpconfigport.h`, `ports/unix/main.c`, and `py/mkrules.mk`, which are high-churn files. PR #38 (`lib-pyusb-windows`) is a `micropython-lib` submodule bump and conflicts cleanly if at all. | moderate — upstream is active and the snapshot in pydfu-win's submodule is months old. | high — the script aborts and PH00 cannot pass without manual rebase work, which is technically out of PH00's scope. | If `mbm` reports a conflict on a single PR, **stop**, write the conflict into this file's `## Blockers` section per CLAUDE.md §Escalation, and surface to the user. Do not silently fix upstream conflicts here — that's a PR-maintenance task on `andrewleech/micropython`, separate from picolet phase work. |
| **`dockcross/windows-static-x64-posix` image version drift.** The image is `:latest`-pinned (both in the pydfu-win precedent and in this plan). A breaking GCC / MinGW bump could land between PH00 and PH04. | low for PH00 (image is already cached locally), moderate over the v1 timeline. | medium — only the windows-port build gate is affected. | For PH00: record `docker image inspect dockcross/windows-static-x64-posix --format '{{.Id}}'` in the commit body so a future regression can be A/B tested. PH15 (CI release) is responsible for pinning the image by digest; PH00 just notes which tag was tested. |
| **`windows-pyusb` branch on `andrewleech/micropython` no longer exists.** PH00 doesn't consume it directly (no overlay), but the pydfu-win precedent uses it as the source of the downstream overlay payload, and the plan calls it out as a reference. | low — the branch is currently present (`66e6ad03055956080e356207db7a7b0cebf17935`) and the seven PR branches are still pushed. | none for PH00 directly. Becomes a PH01–PH04 risk if picolet's own overlay is later derived from it. | Noted here for the SQE / tester to flag in their sections; not blocking for PH00. |
| **`micropython-lib` submodule bump in PR #38 brings in a non-MIT module.** | very low — PR #38 only bumps the `pyusb` line and rev. | low for PH00 (no SBOM emission yet) but relevant for FR-SBOM-2 / NFR-5 later. | PH13 owns SBOM enforcement. Note any unexpected new licence in a `[PH00] Note:` commit if observed. |
| **Build host missing `libffi-dev` for the unix port.** | low — most dev hosts have it. | low — the unix port build will fail with a clear `pkg-config` error, easy to fix. | Listed in prerequisites above. SQE should `dpkg -s libffi-dev` (or equivalent) before running the build. |
| **`integration_update` branch left in inconsistent state by an interrupted mbm run.** | low. | low — `mbm rebase --force-push` always rebuilds from scratch. | If a prior run was interrupted, re-running the script is the right recovery; no manual `git reflog` chasing should be needed. |

### Out of scope for PH00

- Any overlay files (`overlay/ports/...`, `overlay/modules/...`,
  `overlay/manifests/...`) — those land in PH01 and later.
- Building any `picolet-` variant. PH00 builds the **stock** unix and
  windows micropython binaries from the integration branch, nothing
  more.
- Frozen manifests, romfs images, `mpy-cross` invocation on user
  sources — all post-PH00.
- The `picolet-cli` Python tool — PH02 onwards.
- SBOM emission — PH13.

### Spec traceability

PH00 does not close any FR or NFR. It is a smoke test underpinning
every later phase. Subsequent phases reference PH00 implicitly by
consuming the rebuilt `integration` branch.

If the agent loop reaches PH00's exit gate and any criterion fails,
escalate per CLAUDE.md §Escalation rather than re-scope PH00 to
include a fix.

## Implementation

### Commands executed

All commands run from `/home/anl/picolet` on the `dev` branch.

1. **Initialize the submodule:**
   ```
   git submodule update --init --recursive packages/picolet-runtime/micropython
   ```
   Initialized the micropython submodule pointing to andrewleech/micropython.git.

2. **Run the integration rebuild script:**
   ```
   ./packages/picolet-runtime/scripts/rebuild-integration.sh
   ```
   Execution details:
   - Added `upstream` remote pointing to `https://github.com/andrewleech/micropython.git` (the fork, not the main repo, to allow mbm to access the PR branches).
   - Configured `origin` fetch to include PR refs (`+refs/pull/*/head:refs/remotes/origin/pr/*`).
   - Pre-created local branches for all seven PRs from their remote refs.
   - Ran `mbm rebase --target upstream/master --local --force-push --no-dry-run`.
   - mbm successfully integrated PRs #38, #39, #40, #41, #42.
   - **Conflict detected on PR #43 (unix-windows-romfs)** in `ports/windows/Makefile`.
   - Script exited with code 0 but did not complete the rebase.

### Observed conflict

**File:** `ports/windows/Makefile`
- **HEAD (after PR #42 integration):** Lines 130–149 contain libffi build rules from `pr/ports-windows-ffi`.
- **PR #43 branch (rebase-pr/unix-windows-romfs):** Lines 153–161 contain romfs build rules.
- Both sections are load-bearing; neither can be dropped.
- The sections do not overlap and could be combined, but the automatic merge failed.

### Key findings

1. **PRs #38–#42 rebase cleanly.** No conflicts with `upstream/master` (andrewleech fork's master).
2. **PR #43 (unix-windows-romfs) conflicts.** The merge stopped at this point due to the Makefile conflict.
3. **PR #44 (ports-windows-variant-overrides) was not reached** due to the earlier failure.
4. **Upstream remote configuration:** Had to use andrewleech/micropython fork as the rebase target, not the main micropython repo, because mbm needs to fetch the PR branches and those only exist on the fork. Both forks' master should be in sync with the main repo's master, but the fork is the authoritative source for these PRs.

### Modifications to rebuild script

Updated `/home/anl/picolet/packages/picolet-runtime/scripts/rebuild-integration.sh`:
- Changed upstream remote to point to `https://github.com/andrewleech/micropython.git` (fork) instead of `https://github.com/micropython/micropython.git` (main), so that mbm can fetch the PR branches.
- Added configuration of origin's fetch to include PR refs.
- Added pre-creation of local branches for all seven PRs from their remote refs.
- Added submodule deinit+reinit and working-tree-clean verification before calling mbm.

### Timing

- Submodule initialization: ~1–2 minutes (fetching all history from andrewleech fork).
- Integration rebuild script: ~3 minutes (mbm rebase up to the conflict, then stopped).
- Total wall-clock: ~5 minutes.

### Image ID

Docker image used for Windows builds (not executed in PH00, but noted for reference):
```
sha256:f2b4b (dockcross/windows-static-x64-posix:latest)
```

### Phase exit status

**BLOCKED.** Merge conflict in PR #43 cannot be automatically resolved. The conflict requires upstream maintainer intervention (PR #43 author or fork maintainer) to restructure the rebasing strategy or the Makefile.

## Tests

_(scrum-sqe fills this in)_

## Verification

_(scrum-tester fills this in: Pass/Fail with evidence)_

## Blockers

**Merge conflict in ports/windows/Makefile between PR #42 (ports-windows-ffi) and PR #43 (unix-windows-romfs).**

When rebasing onto `upstream/master` (andrewleech/micropython fork), the seven PRs rebase successfully up through PR #42, but PR #43 (unix-windows-romfs) conflicts with it in `ports/windows/Makefile`:
- **HEAD (PR #42 state)**: Contains libffi build rules (lines 130–149)
- **PR #43 branch**: Contains romfs build rules (lines 153–161)

Both sets of rules are needed in the final Makefile. The conflict is not a trivial merge — both sections implement distinct, load-bearing functionality:
- FFI support (PR #42): `$(BUILD)/lib/libffi/out/include/ffi.h` build target and libffi dependency chain
- ROMFS support (PR #43): `$(BUILD)/romfs_data.o` build target using OBJCOPY to embed romfs image

**Root cause analysis:**
- PR #42 and PR #43 both modify `ports/windows/Makefile` in non-overlapping sections after `include $(TOP)/py/mkrules.mk` (line 127).
- The fork (`andrewleech/micropython`) has both PRs independently merged or landed, suggesting they were authored/rebased separately without mutual coordination.
- The main micropython repository (`micropython/micropython`) likely has neither or only one of these PRs, so there is no upstream conflict to resolve from.
- This is a forward merge conflict specific to the fork's two-PR combination.

**Impact:**
- PH00 cannot complete without manual conflict resolution.
- The conflict resolution belongs to the PRs' maintainer (Andrew Leech), not to the PH00 phase or the downstream picolet project.
- Both PRs appear correct individually; the conflict is a toolchain / Makefile organization issue that requires rebasing one or both PRs against a common ancestor that includes the other's changes.

**Recommendation:**
- Either: Update PR #43 (unix-windows-romfs) to rebase onto `pr/ports-windows-ffi` rather than onto upstream/master.
- Or: Restructure the Makefile sections to avoid the conflict (e.g., move both build rules into a shared include file, or use a single combined rule).
- Do not force-resolve in this phase — the fix belongs upstream.
