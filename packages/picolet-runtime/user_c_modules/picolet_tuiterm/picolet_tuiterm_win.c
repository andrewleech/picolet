/*
 * picolet_tuiterm_win.c — Windows terminal-handling shim for the picolet
 * tui variant.  Implements the public C surface declared in
 * picolet_tuiterm.h on top of the conhost / Windows Terminal VT plumbing
 * documented in docs/tui/research/04-terminal-handling.md §2.
 *
 * Pre-1809 conhost (no ENABLE_VIRTUAL_TERMINAL_PROCESSING) is a hard
 * refusal at enable() — R5 / FR-TUI-10.  We do NOT carry a parallel
 * INPUT_RECORD-to-VT translation path; the spec explicitly forbids it.
 *
 * Threading: SetConsoleCtrlHandler routines run on a Windows-spawned
 * thread; the rest of the surface runs on the picolet asyncio thread.
 * disable() is therefore safe to invoke from the ctrl handler — it only
 * touches the cached HANDLE / DWORD state and calls SetConsoleMode +
 * WriteConsoleW, neither of which mutates a Python-visible structure.
 *
 * License: MIT (picolet code).
 */

#if !defined(_WIN32) && !defined(__CYGWIN__)
/* Build-system sanity guard.  Including this file in a non-Windows build
 * would silently produce zero exported symbols and the picolet_tuiterm
 * link would fail with mysterious undefined references on the Unix
 * counterpart — fail loudly instead. */
#error "picolet_tuiterm_win.c is the Windows-only backend; build the Unix sibling on other platforms"
#endif

#include "picolet_tuiterm.h"

#include <windows.h>
#include <io.h>          /* _isatty / _fileno */
#include <stdio.h>       /* stdin/stdout/stderr FILE* for _fileno */

#include <stdint.h>
#include <string.h>

/* ---- Cached console state ---------------------------------------------- */

/* All Win32 handles + saved-mode words live in process-global state.
 * enable()/disable() are the only mutators; both run on the picolet main
 * thread.  The ctrl handler reads g_enabled + the cached restore words
 * without locks: a torn DWORD read still produces a value Windows
 * accepts (modes that have been cleared since boot are a no-op). */
static HANDLE  g_hin            = INVALID_HANDLE_VALUE;
static HANDLE  g_hout           = INVALID_HANDLE_VALUE;
static DWORD   g_orig_in_mode   = 0;
static DWORD   g_orig_out_mode  = 0;
static UINT    g_orig_out_cp    = 0;
static UINT    g_orig_in_cp     = 0;
static int     g_enabled        = 0;
static int     g_ctrl_installed = 0;
static int     g_atexit_armed   = 0;
static uint32_t g_caps          = 0;
static int     g_caps_probed    = 0;

static int32_t g_last_error = 0;

static inline void set_last(int32_t err) { g_last_error = err; }

int32_t picolet_tuiterm_last_error(void) { return g_last_error; }

/* ---- Output prologue / epilogue ---------------------------------------- */

/* alt-screen + cursor-hide + SGR mouse + bracketed paste, in the order the
 * spec mandates (FR-TUI-11).  The disable epilogue runs the inverse in
 * reverse.  Emitted via WriteConsoleA against the cached output handle so
 * the bytes go to the real console even when Python's stdout has been
 * redirected to the per-app capture ring (FR-TUI-78). */
static const char PROLOGUE[]  = "\x1b[?1049h\x1b[?25l\x1b[?1006h\x1b[?1000h\x1b[?2004h";
static const char EPILOGUE[]  = "\x1b[?2004l\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l";

static void write_console_raw(const char *bytes, DWORD len) {
    if (g_hout == INVALID_HANDLE_VALUE || len == 0) {
        return;
    }
    DWORD written = 0;
    /* WriteFile, not WriteConsoleA: the latter goes through CRT-style
     * conversion under some shells.  WriteFile on a console handle emits
     * the bytes verbatim once VT processing is on. */
    WriteFile(g_hout, bytes, len, &written, NULL);
}

/* ---- Ctrl handler / atexit --------------------------------------------- */

/* Quiet wrapper so the function pointer types line up.  Idempotent. */
static void NTAPI picolet_tuiterm_atexit(void) {
    picolet_tuiterm_disable();
}

static BOOL WINAPI ctrl_handler(DWORD type) {
    /* CTRL_C / CTRL_BREAK / CTRL_CLOSE / CTRL_LOGOFF / CTRL_SHUTDOWN —
     * restore the terminal in every case, then let the default handler
     * run.  Returning FALSE lets Windows continue propagating the event,
     * which on CTRL_C produces the normal ExitProcess path (Python's own
     * SIGINT-equivalent handling has already been displaced by picolet
     * pumping its own loop). */
    (void)type;
    picolet_tuiterm_disable();
    return FALSE;
}

/* ---- Colour-capability probe ------------------------------------------- */

/* Env-var ladder per NFR-TUI-7 / research doc 04 §1.  PICOLET_TUI_COLOR is
 * applied on the Python side (FR-TUI-39); this layer reports what the
 * host advertises.  Windows defaults to neither COLORTERM nor TERM under
 * conhost; Windows Terminal sets COLORTERM=truecolor + WT_SESSION.  Once
 * VT processing has been negotiated successfully the runtime can always
 * advertise at least 256-colour. */
static void probe_caps(void) {
    if (g_caps_probed) return;
    g_caps_probed = 1;
    g_caps = 0;

    char buf[64];
    DWORD n;

    n = GetEnvironmentVariableA("NO_COLOR", buf, sizeof(buf));
    if (n > 0 && n < sizeof(buf)) {
        g_caps |= PICOLET_TUITERM_CAP_NO_COLOR;
        return;
    }

    n = GetEnvironmentVariableA("COLORTERM", buf, sizeof(buf));
    if (n > 0 && n < sizeof(buf)) {
        if (strcmp(buf, "truecolor") == 0 || strcmp(buf, "24bit") == 0) {
            g_caps |= PICOLET_TUITERM_CAP_TRUECOLOR | PICOLET_TUITERM_CAP_256COLOR;
            return;
        }
    }

    n = GetEnvironmentVariableA("TERM", buf, sizeof(buf));
    if (n > 0 && n < sizeof(buf)) {
        if (strstr(buf, "256color") != NULL) {
            g_caps |= PICOLET_TUITERM_CAP_256COLOR;
            return;
        }
    }

    /* VT processing on but no env hints — conhost on Windows 10 1809+
     * paints the 256-cube correctly even when neither COLORTERM nor TERM
     * is set.  Research doc 04 §2 ("default to 256 once VT is enabled"). */
    g_caps |= PICOLET_TUITERM_CAP_256COLOR;
}

/* ---- Lifecycle --------------------------------------------------------- */

int32_t picolet_tuiterm_enable(void) {
    if (g_enabled) {
        set_last(0);
        return 0;
    }

    g_hin  = GetStdHandle(STD_INPUT_HANDLE);
    g_hout = GetStdHandle(STD_OUTPUT_HANDLE);
    if (g_hin == INVALID_HANDLE_VALUE || g_hout == INVALID_HANDLE_VALUE) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -1;
    }

    /* Both handles must be real consoles — GetConsoleMode fails on
     * pipes / file redirections.  is_tty() is the public probe; this
     * is the internal version. */
    if (!GetConsoleMode(g_hin, &g_orig_in_mode)) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -2;
    }
    if (!GetConsoleMode(g_hout, &g_orig_out_mode)) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -3;
    }

    /* Output: turn on VT processing + suppress newline auto-wrap.  Auto-
     * wrap puts the cursor in an unpredictable column when a write hits
     * the right margin, which the compositor's diff cannot tolerate
     * (research doc 04 §2). */
    DWORD new_out = (g_orig_out_mode
                     | ENABLE_PROCESSED_OUTPUT
                     | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                     | DISABLE_NEWLINE_AUTO_RETURN);
    if (!SetConsoleMode(g_hout, new_out)) {
        /* R5 / FR-TUI-10: pre-1809 conhost has no VT output support; the
         * App is required to refuse to start.  Leave the terminal as we
         * found it. */
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -4;
    }

    /* Input: VT input + window-buffer-size events on; line/echo/processed
     * input off so the parser receives raw bytes.  ENABLE_EXTENDED_FLAGS
     * is the historic quirk: without it, clearing ENABLE_QUICK_EDIT_MODE
     * is silently ignored and Windows still selects text on mouse-down
     * (R5 catch from spec §). */
    DWORD new_in = g_orig_in_mode;
    new_in &= ~(ENABLE_LINE_INPUT
                | ENABLE_ECHO_INPUT
                | ENABLE_PROCESSED_INPUT
                | ENABLE_MOUSE_INPUT
                | ENABLE_QUICK_EDIT_MODE);
    new_in |=  (ENABLE_VIRTUAL_TERMINAL_INPUT
                | ENABLE_WINDOW_INPUT
                | ENABLE_EXTENDED_FLAGS);
    if (!SetConsoleMode(g_hin, new_in)) {
        /* Roll back the output side so the terminal is consistent. */
        SetConsoleMode(g_hout, g_orig_out_mode);
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -5;
    }

    /* The compositor emits UTF-8.  Force both code pages to CP_UTF8 so
     * WriteFile bytes are passed straight to the renderer without ANSI
     * code-page conversion that would mangle box-drawing glyphs. */
    g_orig_out_cp = GetConsoleOutputCP();
    g_orig_in_cp  = GetConsoleCP();
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);

    if (!g_ctrl_installed) {
        if (SetConsoleCtrlHandler(ctrl_handler, TRUE)) {
            g_ctrl_installed = 1;
        }
        /* Failure to install is non-fatal — without it, CTRL_CLOSE skips
         * restore but the user can still recover (next process re-enables
         * VT).  Don't burn the enable on it. */
    }

    if (!g_atexit_armed) {
        /* atexit fires on normal CRT shutdown; the ctrl handler covers the
         * abnormal paths.  Both ultimately call picolet_tuiterm_disable
         * which is idempotent. */
        atexit((void(*)(void))picolet_tuiterm_atexit);
        g_atexit_armed = 1;
    }

    write_console_raw(PROLOGUE, (DWORD)(sizeof(PROLOGUE) - 1));

    probe_caps();
    g_enabled = 1;
    set_last(0);
    return 0;
}

int32_t picolet_tuiterm_disable(void) {
    if (!g_enabled) {
        set_last(0);
        return 0;
    }
    /* Flip the flag first so a re-entrant call from the ctrl handler
     * fired mid-disable is a no-op. */
    g_enabled = 0;

    write_console_raw(EPILOGUE, (DWORD)(sizeof(EPILOGUE) - 1));

    if (g_hin  != INVALID_HANDLE_VALUE) SetConsoleMode(g_hin,  g_orig_in_mode);
    if (g_hout != INVALID_HANDLE_VALUE) SetConsoleMode(g_hout, g_orig_out_mode);

    if (g_orig_out_cp != 0) SetConsoleOutputCP(g_orig_out_cp);
    if (g_orig_in_cp  != 0) SetConsoleCP(g_orig_in_cp);

    set_last(0);
    return 0;
}

/* ---- Geometry ---------------------------------------------------------- */

int32_t picolet_tuiterm_size(int *cols, int *rows) {
    if (cols == NULL || rows == NULL) { set_last(E_POINTER); return -1; }
    HANDLE h = (g_hout != INVALID_HANDLE_VALUE) ? g_hout
                                                : GetStdHandle(STD_OUTPUT_HANDLE);
    CONSOLE_SCREEN_BUFFER_INFO csbi;
    if (!GetConsoleScreenBufferInfo(h, &csbi)) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -2;
    }
    /* srWindow is the visible viewport — dwSize is the back-scroll buffer
     * (often 9000 rows tall).  The compositor must size against the
     * viewport, not the buffer. */
    *cols = (int)(csbi.srWindow.Right  - csbi.srWindow.Left + 1);
    *rows = (int)(csbi.srWindow.Bottom - csbi.srWindow.Top  + 1);
    set_last(0);
    return 0;
}

int32_t picolet_tuiterm_resize_pending(void) {
    /* No SIGWINCH on Windows — the spec's _pump_resize task calls size()
     * once per tick and diffs.  resize_pending() is therefore a constant
     * 0 on this port; the Python driver knows not to rely on it.  Kept
     * in the surface so frozen Python's import-from layer can blindly
     * read the symbol on both platforms. */
    set_last(0);
    return 0;
}

int32_t picolet_tuiterm_is_tty(int fd) {
    /* CRT _isatty returns non-zero for both console handles and character-
     * special devices (NUL).  GetConsoleMode is the stricter test the spec
     * actually wants: only a real console qualifies.  Apply both so a
     * redirect-to-NUL doesn't fool the App into entering raw mode. */
    if (!_isatty(fd)) {
        set_last(0);
        return 0;
    }
    HANDLE h = INVALID_HANDLE_VALUE;
    switch (fd) {
        case 0:  h = GetStdHandle(STD_INPUT_HANDLE);  break;
        case 1:  h = GetStdHandle(STD_OUTPUT_HANDLE); break;
        case 2:  h = GetStdHandle(STD_ERROR_HANDLE);  break;
        default: set_last(E_INVALIDARG);              return 0;
    }
    if (h == INVALID_HANDLE_VALUE || h == NULL) {
        set_last(0);
        return 0;
    }
    DWORD mode;
    if (!GetConsoleMode(h, &mode)) {
        set_last(0);
        return 0;
    }
    set_last(0);
    return 1;
}

/* ---- IO ---------------------------------------------------------------- */

/* The KEY_EVENT_RECORD → VT translation table is intentionally tiny: with
 * ENABLE_VIRTUAL_TERMINAL_INPUT on, conhost / Windows Terminal already
 * inject the xterm byte sequences for arrows, function keys, navigation,
 * and modifier-keyed forms (research doc 04 §2).  The records we still
 * see are the plain printable / control characters that the host did NOT
 * pre-encode — emit those via record->uChar.AsciiChar. */
static int append_key_event(const KEY_EVENT_RECORD *ke,
                            char *buf, int32_t cap, int32_t pos) {
    if (!ke->bKeyDown) return pos;
    char c = ke->uChar.AsciiChar;
    if (c == 0) return pos;
    if (pos >= cap) return pos;
    buf[pos] = c;
    return pos + 1;
}

int32_t picolet_tuiterm_read_input(char *buf, int32_t cap, int32_t timeout_ms) {
    if (buf == NULL || cap <= 0) { set_last(E_INVALIDARG); return -1; }
    HANDLE h = (g_hin != INVALID_HANDLE_VALUE) ? g_hin
                                              : GetStdHandle(STD_INPUT_HANDLE);
    if (h == INVALID_HANDLE_VALUE) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -2;
    }

    DWORD wait_ms = (timeout_ms < 0) ? INFINITE : (DWORD)timeout_ms;
    DWORD wr = WaitForSingleObject(h, wait_ms);
    if (wr == WAIT_TIMEOUT) {
        set_last(0);
        return 0;
    }
    if (wr != WAIT_OBJECT_0) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -3;
    }

    /* GetNumberOfConsoleInputEvents tells us how many records to drain in
     * one ReadConsoleInputW call — bounded by both cap (bytes we can
     * emit) and a small local INPUT_RECORD array to avoid stack
     * pressure. */
    DWORD avail = 0;
    if (!GetNumberOfConsoleInputEvents(h, &avail) || avail == 0) {
        set_last(0);
        return 0;
    }

    INPUT_RECORD recs[64];
    DWORD want = avail;
    if (want > (DWORD)(sizeof(recs) / sizeof(recs[0]))) {
        want = (DWORD)(sizeof(recs) / sizeof(recs[0]));
    }

    DWORD got = 0;
    if (!ReadConsoleInputW(h, recs, want, &got)) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -4;
    }

    int32_t pos = 0;
    for (DWORD i = 0; i < got; i++) {
        switch (recs[i].EventType) {
            case KEY_EVENT:
                pos = append_key_event(&recs[i].Event.KeyEvent, buf, cap, pos);
                break;
            case WINDOW_BUFFER_SIZE_EVENT:
                /* Resize is observed via size()-polling per FR-TUI-9; there
                 * is no flag to set.  Drop the record. */
                break;
            default:
                /* MOUSE_EVENT / FOCUS_EVENT / MENU_EVENT — disabled at
                 * SetConsoleMode time or filtered as harmless noise. */
                break;
        }
    }
    set_last(0);
    return pos;
}

int32_t picolet_tuiterm_write(const char *bytes, int32_t n) {
    if (bytes == NULL || n < 0) { set_last(E_INVALIDARG); return -1; }
    HANDLE h = (g_hout != INVALID_HANDLE_VALUE) ? g_hout
                                                : GetStdHandle(STD_OUTPUT_HANDLE);
    if (h == INVALID_HANDLE_VALUE) {
        set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
        return -2;
    }
    int32_t total = 0;
    while (total < n) {
        DWORD written = 0;
        if (!WriteFile(h, bytes + total, (DWORD)(n - total), &written, NULL)) {
            set_last((int32_t)HRESULT_FROM_WIN32(GetLastError()));
            return -3;
        }
        if (written == 0) {
            /* Zero-byte success on a console handle is the equivalent of
             * a closed pipe — bail out rather than spin forever. */
            set_last((int32_t)HRESULT_FROM_WIN32(ERROR_BROKEN_PIPE));
            return -4;
        }
        total += (int32_t)written;
    }
    set_last(0);
    return total;
}

/* ---- Capabilities ------------------------------------------------------ */

int32_t picolet_tuiterm_capabilities(uint32_t *flags) {
    if (flags == NULL) { set_last(E_POINTER); return -1; }
    probe_caps();
    *flags = g_caps;
    set_last(0);
    return 0;
}
