# PHASE 16 — picolet dev

**Goal**: File watcher that triggers `build` + `run` on UI-asset or
Python-source change.

**Exit gate**: FR-CLI-7.

---

## Plan

### Watch backend choice

**Chosen: stdlib polling (`os.stat` mtime+size tracking, 500 ms interval).**

Rationale:
- `watchdog` is a non-trivial C-extension dependency (inotify/FSEvents
  bindings) that does not belong in a PEP 723 inline dep list for a CLI
  tool. The inline header already has `mpremote`; adding more packages
  increases install overhead.
- 500 ms poll is imperceptible to a developer editing in an IDE (save
  latency >> 500 ms). The FR says "on change" with no latency SLA.
- Stdlib polling is cross-platform with zero additional dependencies,
  consistent with the project's stated goal of a self-contained tool.
- `watchdog` would be correct for a production watch daemon, but the
  exit gate (FR-CLI-7) makes no requirement on watch mechanism —
  only that rebuild is triggered on change.

### Debounce strategy

A "quiet period" approach:
1. Poll for changes every 500 ms.
2. On first detected change, record a `last_change_time`.
3. Continue polling. Each new change updates `last_change_time`.
4. When `now - last_change_time >= DEBOUNCE_DELAY` (250 ms after the
   last event within a 500 ms poll cycle), trigger one rebuild.
5. After rebuild, reset `last_change_time = None` and re-snapshot
   the file state to avoid re-triggering on the same change.

Effective minimum debounce window is one poll interval (500 ms) because
the watcher only checks every 500 ms, so a flurry of changes within the
same 500 ms window always results in exactly one rebuild.

### Process management

Kill + restart (FR-CLI-7 explicitly scopes out live-reload of Python state).

Sequence on rebuild:
1. Send SIGTERM to the running process (if any).
2. Wait up to 3 seconds for graceful exit.
3. If still alive after 3 s: send SIGKILL.
4. Run `picolet build`.
5. Exec the built binary as a subprocess (non-blocking `Popen`).

The running binary is stored in a `subprocess.Popen` handle; on SIGINT
the dev loop sends SIGTERM to the child before exiting.

### Watch scope

Watched paths (resolved relative to app root, i.e. where picolet.toml is):
- `src/` (or `dirname(app.entry)`) — Python sources
- `ui/` (if `[ui]` section present and `ui.root` is set) — UI assets
- `picolet.toml` — config changes also trigger rebuild

Ignored unconditionally:
- `target/` — build outputs
- `.picolet-cache/` — runtime cache
- `__pycache__/` directories
- Hidden files and directories (name starts with `.`)
- `*.pyc`, `*.mpy` — compiled artefacts

### CTRL-C handling

`signal.SIGINT` is caught by a `try/finally` around the watch loop:
1. Print a clean shutdown message.
2. SIGTERM the running child process (if any); wait up to 3 s.
3. SIGKILL if still alive.
4. Exit 0.

Also registers `atexit` to kill the child in case of unexpected exit.

### run_cmd.py

FR-CLI-6 (`picolet run`) is unimplemented. PH16 implements a minimal
`run_cmd.py` alongside `dev_cmd.py` because `picolet dev` needs to invoke
the same build+exec logic. The run command:
1. Reads and validates picolet.toml (same as build).
2. Checks if the binary exists and is newer than all sources; if not,
   invokes `picolet build` first.
3. Executes the binary with `subprocess.run` (blocking, forwarding exit
   code).

`dev_cmd.py` reuses the build-then-exec logic from `run_cmd.py`'s
internals rather than shelling out to `picolet run` as a subprocess, to
avoid double-parsing toml and to keep process tree simple.

---

## Files to create / modify

| File | Action |
|------|--------|
| `packages/picolet-cli/picolet/run_cmd.py` | Create — `picolet run` subcommand |
| `packages/picolet-cli/picolet/dev_cmd.py` | Create — `picolet dev` subcommand |
| `packages/picolet-cli/picolet/__main__.py` | Modify — wire `run_cmd` + `dev_cmd` |
| `tests/phase-16/run.sh` | Create — PH16 test harness |
| `docs/phases/PHASE_16_picolet-dev.md` | This file |

---

## Developer Notes

_(appended during implementation)_
