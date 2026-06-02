# 04 — Per-platform terminal handling for a TUI variant

Scope: what a picolet TUI variant needs from the host OS to put the
controlling terminal into raw mode, read keystrokes and mouse events
as escape sequences, react to window resize, restore the terminal on
exit, and emit colour cleanly. Sized for two target platforms:
Linux/x86_64 (glibc) and Windows/x86_64 (conhost or Windows Terminal,
Windows 10 1809+).

The TUI variant is a separate `mpconfigvariant` parallel to
`picolet-cli`. The natural Python surfaces — CPython's `termios` and
`msvcrt` — are not portable across the two platforms, and MicroPython
does not ship `msvcrt` at all. The cleanest cross-platform shape is
therefore a thin C shim plus a Python-level state machine over the
byte stream. Where possible the C shim reuses what MicroPython
already ships rather than going through `ffi`.

What is already in tree:

- `ports/unix/modtermios.c` (177 LOC) exposes `tcgetattr`,
  `tcsetattr`, `setraw` and the `B*` baud-rate constants. The c_lflag
  / c_iflag / c_oflag bit constants (`ICANON`, `ECHO`, `ISIG`,
  `IEXTEN`, `IXON`, `OPOST`, `BRKINT`, `INPCK`, `ISTRIP`, `ICRNL`,
  `VMIN`, `VTIME`) are **not** exposed. The current `setraw` helper
  works but does not return enough information for a full TUI (no way
  to flip individual flags from Python).
- `ports/unix/modffi.c` is built and libffi is linked. FFI is
  therefore available as a fallback / prototyping path on Unix.
- Windows builds are produced via dockcross MinGW. There is no
  Windows equivalent of `modtermios.c` in tree today, and no
  `modmsvcrt.c`. Anything Windows-side has to come from a new C
  module.

Recommendation summary (detail per platform below): ship a single
`tuiterm` C module that provides the raw-mode toggle, signal-safe
resize plumbing, a non-blocking `read_input` returning `bytes`, and
colour-capability probing. Keep the escape-sequence parser, key
table, and mouse decoder in frozen Python; they are pure byte-stream
state machines and there is no reason to spend C size budget on
them.

---

## 1. Unix (Linux) raw mode and input

Header: `<termios.h>`, `<unistd.h>`, `<sys/ioctl.h>`, `<signal.h>`.

### Symbols and shape

| Symbol | Use | Notes |
|---|---|---|
| `tcgetattr(int fd, struct termios *)` | snapshot current attrs | save once at startup |
| `tcsetattr(int fd, int when, struct termios *)` | apply attrs | use `TCSAFLUSH` on enter, `TCSANOW` on restore |
| `cfmakeraw(struct termios *)` | shortcut to raw | BSD extension, present in glibc + macOS |
| `ioctl(fd, TIOCGWINSZ, struct winsize *)` | query rows/cols | called from main loop after SIGWINCH fires |
| `sigaction(SIGWINCH, ...)` | install resize handler | `signal(2)` is non-portable; use `sigaction` |
| `sigaction(SIGINT, ...)` | trap Ctrl+C without `ISIG` losing it | only needed if `ISIG` left on |
| `sigaction(SIGTERM, ...)` | restore terminal before death | flag-and-defer pattern |
| `atexit(restore_fn)` | belt-and-braces restore | runs on normal `exit()` only |
| `isatty(fd)` | gate raw-mode entry | bail out cleanly on pipes |

### Raw-mode incantation

Two equivalent forms. `cfmakeraw` is one call but masks individual
intent; the explicit form is what every serious TUI uses because
each flag has a reason. Both are ~30 LOC of C wrapping.

```c
struct termios orig;
tcgetattr(fd, &orig);
struct termios raw = orig;
raw.c_iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON);
raw.c_oflag &= ~OPOST;            /* keep output processing off only while drawing */
raw.c_lflag &= ~(ECHO | ECHONL | ICANON | IEXTEN | ISIG);
raw.c_cflag &= ~(CSIZE | PARENB);
raw.c_cflag |= CS8;
raw.c_cc[VMIN]  = 0;              /* non-blocking read */
raw.c_cc[VTIME] = 0;
tcsetattr(fd, TCSAFLUSH, &raw);
```

Leaving `ISIG` on (so Ctrl+C still raises SIGINT) is the usual
choice; the C shim then needs `sigaction(SIGINT, ...)` so the signal
restores the terminal instead of killing the process mid-frame.

### SIGWINCH

Canonical pattern, exactly what every TUI framework uses:

```c
static volatile sig_atomic_t resize_pending = 0;
static void on_winch(int sig) { resize_pending = 1; }
```

The Python loop polls a `tuiterm.poll_resize() -> (rows, cols) | None`
that atomically clears the flag and runs `ioctl(TIOCGWINSZ)`. The
ioctl is **not** async-signal-safe so it must not run inside the
handler.

### Restore on exit

Two-layered:

1. `atexit(restore_fn)` for normal interpreter shutdown. `restore_fn`
   simply calls `tcsetattr(fd, TCSANOW, &orig)`.
2. `sigaction(SIGTERM | SIGINT | SIGHUP, ...)` handlers that set a
   "die after restore" flag the main loop sees, or — simpler — that
   call `tcsetattr` (which is technically not on the official
   async-signal-safe list, but is universally treated as safe in
   practice; the alternative is dying with a wrecked terminal).

### Input-stream byte sources

`read(STDIN_FILENO, buf, 64)` with `VMIN=0/VTIME=0` returns
0..N bytes immediately. This is the only call the parser needs.
For a select-driven event loop, `poll(POLLIN)` on stdin is fine.
`epoll` is overkill for one fd.

### Colour-capability detection

Pure-Python, runs once at startup. Precedence order:

1. `getenv("NO_COLOR")` non-empty → mono. (Per https://no-color.org —
   presence with any non-empty value disables colour.)
2. `getenv("FORCE_COLOR")` non-empty → 24-bit (CPython colorama
   convention).
3. `getenv("COLORTERM")` ∈ {"truecolor", "24bit"} → 24-bit.
4. `getenv("TERM")` contains "256color" → 256.
5. `getenv("TERM")` ∈ known-colour set → 16.
6. Stdout is not a tty (`isatty(1) == 0`) → mono.
7. Fallback → 16.

### Implementation size on Unix

- C shim covering raw-mode toggle, SIGWINCH install + poll,
  WINSZ ioctl, signal-safe restore, non-blocking read: **~250 LOC C**
  (i.e. ~50% more than the existing `modtermios.c`, mostly the new
  flag constants and signal plumbing).
- Python: escape-sequence state machine + key table + colour
  detection: **~500 LOC Python**, frozen.

---

## 2. Windows raw mode and input

Header: `<windows.h>` (`kernel32.h` is pulled in transitively).
Minimum target: Windows 10 1809 (October 2018 Update) for
`ENABLE_VIRTUAL_TERMINAL_INPUT`. Output VT
(`ENABLE_VIRTUAL_TERMINAL_PROCESSING`) has been present since
Windows 10 1511 (TH2). Pre-1809 input is a regression case — see
"out of scope" below.

### Symbols and shape

| Symbol | Use |
|---|---|
| `GetStdHandle(STD_INPUT_HANDLE)` | obtain console handle |
| `GetStdHandle(STD_OUTPUT_HANDLE)` | obtain output handle |
| `GetConsoleMode(h, &mode)` | snapshot original mode |
| `SetConsoleMode(h, mode)` | apply new mode (in/out separately) |
| `ReadFile(hIn, buf, n, &got, NULL)` | read VT bytes once VTI is on |
| `WaitForSingleObject(hIn, ms)` | timed wait for input |
| `SetConsoleCtrlHandler(handler, TRUE)` | Ctrl+C / Ctrl+Break trap |
| `GetConsoleScreenBufferInfo(hOut, &csbi)` | rows/cols on demand |
| `_isatty(_fileno(stdout))` | gate VT entry (msvcrt CRT) |

### Mode flags

Output handle: set
```
ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING | DISABLE_NEWLINE_AUTO_RETURN
```
`DISABLE_NEWLINE_AUTO_RETURN` matters: without it, writes that hit
the right margin auto-wrap and the cursor's column position becomes
unpredictable, which the TUI redraw logic can't tolerate.

Input handle: set
```
ENABLE_VIRTUAL_TERMINAL_INPUT | ENABLE_WINDOW_INPUT | ENABLE_EXTENDED_FLAGS
```
**Clear**
```
ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_PROCESSED_INPUT | ENABLE_MOUSE_INPUT | ENABLE_QUICK_EDIT_MODE
```
Notes:

- `ENABLE_EXTENDED_FLAGS` is required so that disabling
  `ENABLE_QUICK_EDIT_MODE` actually takes effect (Windows historic
  quirk).
- Disabling `ENABLE_PROCESSED_INPUT` is what releases Ctrl+C to the
  byte stream instead of raising the console control event. Pick
  one: either let Ctrl+C come through as `0x03` in the read buffer
  (simpler, matches Unix behaviour with `ISIG` off), or leave
  `ENABLE_PROCESSED_INPUT` on and install `SetConsoleCtrlHandler` to
  trap it. The TUI variant should choose the former and let the
  Python layer dispatch Ctrl+C the same way it would on Unix.
- With `ENABLE_VIRTUAL_TERMINAL_INPUT` on, the console writes xterm
  byte sequences to stdin: arrows = `ESC [ A/B/C/D`, function keys
  per the standard table (see Microsoft Learn input table — Microsoft
  documents exactly the sequences we already parse on Unix).

### Resize events

There is no SIGWINCH on Windows. Two options:

- **`ENABLE_MOUSE_INPUT | ENABLE_WINDOW_INPUT` + `ReadConsoleInput`**
  on a *separate* handle/buffer. This breaks the "everything comes
  in as VT bytes" model — `ReadConsoleInput` returns
  `INPUT_RECORD` structs, including `WINDOW_BUFFER_SIZE_RECORD`.
  Mixing `ReadFile` and `ReadConsoleInput` on the same handle is
  not supported.
- **Poll `GetConsoleScreenBufferInfo` once per frame** and diff the
  reported `dwSize` against last frame. Cheap (single syscall),
  no extra threads, no `INPUT_RECORD` parsing.

Polling is the better fit for the TUI variant. The same `poll_resize()`
function the Unix path exposes returns `(rows, cols) | None` on
Windows by calling `GetConsoleScreenBufferInfo` and comparing.

### Ctrl handler

`SetConsoleCtrlHandler(handler, TRUE)` installs a routine that fires
for `CTRL_C_EVENT`, `CTRL_BREAK_EVENT`, `CTRL_CLOSE_EVENT`,
`CTRL_LOGOFF_EVENT`, `CTRL_SHUTDOWN_EVENT`. The
`CLOSE/LOGOFF/SHUTDOWN` cases are the Windows equivalent of SIGTERM
on Unix — there is no other way to know the window is being killed.
The handler must do its work synchronously (it runs on a separate
thread Windows spins up) and return TRUE to swallow the event or
FALSE to let the default kill happen. For terminal restoration this
means: do `SetConsoleMode(hOut, original)` and
`SetConsoleMode(hIn, original)` inside the handler, then return
FALSE so the process still dies.

### Restore on exit

Same two-layer pattern as Unix:

1. `atexit` registered at runtime startup → calls a restore function
   that re-applies the saved input/output modes.
2. `SetConsoleCtrlHandler` covering CLOSE/LOGOFF/SHUTDOWN so the
   window-X path also restores.

### Colour-capability detection

The Windows code path runs after VT enable succeeds; if
`SetConsoleMode(hOut, ENABLE_VIRTUAL_TERMINAL_PROCESSING)` fails
(returns 0, `GetLastError() == ERROR_INVALID_PARAMETER`) the host
is pre-1809 conhost without VT — fall back to mono and write plain
ASCII. Otherwise reuse the same env-var ladder as Unix:
NO_COLOR → FORCE_COLOR → COLORTERM → TERM (rare on Windows but
Windows Terminal sets WT_SESSION and COLORTERM=truecolor; conhost
sets neither so default to 256 once VT is enabled).

### Implementation size on Windows

- C shim covering VT enable for in+out with graceful failure, mode
  save/restore, ctrl-handler install, `ReadFile` non-blocking read
  via `WaitForSingleObject(0)`, `GetConsoleScreenBufferInfo` poll:
  **~300 LOC C**. The MinGW headers expose every needed symbol; no
  extra link libraries beyond `kernel32.lib` which dockcross's MinGW
  already links by default.
- Python: shares the same escape parser, key table, and colour
  detection module as Unix. **0 extra LOC.**

---

## 3. ANSI escape parser (shared, Python)

Pure byte-stream state machine. Fed bytes from
`tuiterm.read_input(timeout_ms)` regardless of platform. Emits
high-level events:

```
KeyEvent(key, mods)           # KEY_UP, 'a', KEY_F5, ...
MouseEvent(kind, x, y, btn, mods)
PasteEvent(text)              # bracketed paste payload
ResizeEvent(rows, cols)       # injected by main loop after poll_resize()
TextEvent(str)                # raw printable UTF-8
```

### State machine

Following the canonical xterm/vt500 model (Paul Williams). States:

```
GROUND
ESCAPE                  after a bare ESC
ESCAPE_INTERMEDIATE     ESC then 0x20..0x2F
CSI_ENTRY               after ESC [
CSI_PARAM               0x30..0x3F (digits, ; : < > ?)
CSI_INTERMEDIATE        0x20..0x2F after params
CSI_IGNORE              malformed, swallow until final
SS3                     after ESC O (function-key shorthand)
OSC_STRING              after ESC ], terminated by BEL or ST
DCS_*                   skipped — picolet does not consume DCS
```

Transitions follow the standard byte-range table. A finalising byte
in `0x40..0x7E` ends CSI/SS3 and dispatches. OSC is ended by either
`BEL` (0x07) or `ST` (`ESC \` = `0x1B 0x5C`).

### Mouse SGR decoder

When `ENABLE_MOUSE_INPUT` is requested, picolet emits
`CSI ? 1006 h` on startup and `CSI ? 1006 l` on shutdown. Input
arrives as `CSI < Cb ; Cx ; Cy M` (press / motion) or `CSI < Cb ; Cx ; Cy m`
(release), 1-indexed coordinates. Decoding:

```
Cb & 3  -> 0 left, 1 middle, 2 right, 3 release (legacy; SGR uses M/m)
Cb & 4  -> Shift
Cb & 8  -> Meta/Alt
Cb & 16 -> Ctrl
Cb & 32 -> motion-while-pressed
Cb & 64 -> wheel (then 0/1 = up/down)
```

### Bracketed paste

Enable on startup with `CSI ? 2004 h`, disable on shutdown with
`CSI ? 2004 l`. Paste arrives wrapped in `CSI 2 0 0 ~` and
`CSI 2 0 1 ~`. The parser, on seeing the open marker, transitions
into a PASTE state that collects bytes verbatim until the close
marker and emits a single `PasteEvent`. Critical: bytes inside
the paste *must not* be interpreted as keys, otherwise pasted
content containing ESC will trigger random commands.

### Key table

A 50-entry dict keyed on the canonicalised sequence (e.g.
`b"\x1b[A"` → `KEY_UP`, `b"\x1bOP"` → `KEY_F1`). Covers the xterm
modifier encoding (final-parameter `N = 1 + Shift*1 + Alt*2 + Ctrl*4`)
so e.g. `CSI 1 ; 5 A` → `KEY_UP` with `Ctrl` modifier.

### Implementation size

~400 LOC Python for the parser + key table, ~100 LOC for the mouse
decoder. Frozen as `.mpy` it costs ~6 KB romfs.

---

## 4. Cross-platform `tuiterm` C module shape

Single C source file per port that exposes the same Python API:

```python
import tuiterm
tuiterm.enable()                 # raw mode on, save originals
tuiterm.disable()                # restore (idempotent, also runs via atexit)
buf = tuiterm.read_input(timeout_ms=10)  # bytes, possibly empty
size = tuiterm.poll_resize()     # (rows, cols) or None
tuiterm.colour_caps()            # 'mono' | '16' | '256' | 'truecolor'
tuiterm.write(b"...")            # straight passthrough, just for symmetry
```

Behind the API:

- Unix backend lives next to `modtermios.c` as
  `ports/unix/modtuiterm.c`. Reuses `<termios.h>` already pulled in
  by the existing module. ~250 LOC.
- Windows backend lives in the picolet windows variant tree as a
  new C source compiled by dockcross MinGW. Uses only `windows.h`
  and the standard CRT. ~300 LOC.
- Build-system: a `srcs-tuiterm = ...` line in each variant's
  `mpconfigvariant.mk` selects the right backend, mirroring how the
  existing renderer modules are wired.

Total picolet TUI variant overhead vs picolet-cli: **~550 LOC C +
~500 LOC frozen Python ≈ 10 KB binary + 8 KB romfs**.

---

## 5. What does **not** work without rework

- **macOS**: out of scope per repo CLAUDE.md. The Unix path would
  work as-is (cfmakeraw is BSD, `<termios.h>` is identical) but
  nothing should be merged with `__APPLE__` ifdefs while v1 forbids
  the platform.
- **Pre-1809 Windows (no `ENABLE_VIRTUAL_TERMINAL_INPUT`)**: the
  output side still works (1511+), but input arrives as
  `INPUT_RECORD` structs and a separate code path would be needed
  to translate `KEY_EVENT_RECORD` into the same byte stream the
  parser expects. Cost: ~200 LOC additional Windows C. Worth
  feature-flagging off and refusing to start on pre-1809 hosts with
  a clear error message rather than carrying the conversion code
  forever.
- **mintty / Cygwin pty under Windows**: their stdin is a named pipe,
  not a console, so `GetConsoleMode` fails. Detection is via
  `GetFileType(hIn) == FILE_TYPE_PIPE`. The fix is the same code
  path Unix uses (treat the pipe as a tty if `MSYSCON` or similar
  env-var is set), but again this is feature-flag territory, not
  v1.
- **DCS sequences (Sixel images, Kitty graphics, terminal queries
  returning DCS payloads)**: skip. The parser swallows DCS state
  to ST without dispatching. Adding Sixel output later would be
  a separate work item; reading DCS replies (e.g. cursor-position
  reports) is in scope because they come back as CSI, not DCS.
- **Kitty keyboard protocol (CSI u)**: out of scope for v1. The
  parser can ignore the extra trailing `u` for now; supporting it
  later is purely a parser table extension.
- **Windows Console API drawing**: deliberately not used. Everything
  is VT bytes, even on Windows. This is what makes the TUI variant
  one renderer with one event model.
