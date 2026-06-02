/*
 * picolet_tuiterm.c — Unix terminal-handling implementation.
 *
 * Design overview
 * ===============
 *
 *  * One process-global g_state holds the original termios snapshot, the
 *    install-once flag for atexit and signal handlers, the cached colour
 *    capability bitfield, and the SIGWINCH flag.  No per-fd state — the
 *    picolet tui variant always drives STDIN_FILENO / STDOUT_FILENO and
 *    refuses to start on anything else.
 *
 *  * enable() is the one-shot bootstrap.  First call snapshots termios via
 *    tcgetattr, installs the SIGWINCH/SIGINT/SIGTERM/SIGHUP handlers and
 *    the atexit hook, probes the env for colour capability, applies the
 *    raw-mode mask (the explicit form from research doc 04 §1), then emits
 *    the alt-screen + hide-cursor + bracketed-paste + SGR-mouse prologue.
 *    Subsequent calls return 0 without touching the terminal — the
 *    surface is idempotent so the framework's tear-down can be wired
 *    symmetrically without bookkeeping.
 *
 *  * disable() emits the inverse epilogue in reverse order before
 *    tcsetattr-restoring the snapshot.  Idempotent.  Re-entered from the
 *    signal handler via tcsetattr (POSIX does not list it as
 *    async-signal-safe but every TUI in the wild relies on it being so;
 *    the alternative is dying with a wrecked terminal).
 *
 *  * Colour capability is probed per NFR-TUI-7 in this order:
 *
 *      1. $NO_COLOR present + non-empty       -> NO_COLOR bit
 *      2. $COLORTERM in {truecolor, 24bit}    -> TRUECOLOR + 256COLOR bits
 *      3. $TERM contains "256color"           -> 256COLOR bit
 *
 *    PICOLET_TUI_COLOR (the test/override hook from FR-TUI-39) and
 *    FORCE_COLOR are applied on the Python side; the C layer reports
 *    only what the host actually advertises so the override path stays
 *    auditable.
 *
 *  * read_input() pairs poll(POLLIN) with VMIN=0/VTIME=0 — termios timing
 *    is bypassed so timeout_ms is the actual upper bound on wait.  EINTR
 *    is folded into the "no data" return so signal arrival mid-poll does
 *    not surface to the Python loop as a spurious error.
 *
 * License: MIT (picolet code).
 */

#include "picolet_tuiterm.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

/* ---- Prologue / epilogue byte strings ----------------------------------
 *
 * Listed here so the inverse pairs stay adjacent and the reversal in
 * disable() can be eyeballed against the enable() order.  The bracketed-
 * paste and SGR-mouse pair is the cross-resolution that picolet-tui
 * specifies (FR-TUI-11): enable order is alt-screen, hide cursor, mouse,
 * paste; disable is the exact reverse. */
#define TUI_ENTER_ALTSCREEN   "\x1b[?1049h"
#define TUI_EXIT_ALTSCREEN    "\x1b[?1049l"
#define TUI_HIDE_CURSOR       "\x1b[?25l"
#define TUI_SHOW_CURSOR       "\x1b[?25h"
#define TUI_MOUSE_ON          "\x1b[?1003h\x1b[?1006h"
#define TUI_MOUSE_OFF         "\x1b[?1006l\x1b[?1003l"
#define TUI_PASTE_ON          "\x1b[?2004h"
#define TUI_PASTE_OFF         "\x1b[?2004l"

/* ---- State -------------------------------------------------------------- */

typedef struct {
    struct termios       orig;       /* snapshotted on first enable() */
    int                  snapshotted; /* nonzero once orig is valid */
    int                  enabled;     /* nonzero between enable/disable pairs */
    int                  hooks_installed; /* atexit + signal handlers wired once */
    uint32_t             capabilities;    /* PICOLET_TUITERM_CAP_* bitfield */
    int                  capabilities_probed;
    volatile sig_atomic_t resize_pending; /* set by SIGWINCH, cleared by Python */
} state_t;

static state_t g_state;

static int32_t g_last_error;

static inline void set_last(int32_t err) { g_last_error = err; }

int32_t picolet_tuiterm_last_error(void) { return g_last_error; }

/* ---- Best-effort raw write --------------------------------------------- */

/* Loop over partial writes; EINTR retries.  Returns 0 on success, -1 on
 * unrecoverable error (and leaves errno alone for the caller). */
static int write_all(int fd, const char *p, size_t n) {
    while (n > 0) {
        ssize_t r = write(fd, p, n);
        if (r < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (r == 0) return -1;
        p += (size_t)r;
        n -= (size_t)r;
    }
    return 0;
}

/* ---- Colour-capability probe ------------------------------------------- */

static int env_nonempty(const char *name) {
    const char *v = getenv(name);
    return v != NULL && v[0] != '\0';
}

static int env_contains(const char *name, const char *needle) {
    const char *v = getenv(name);
    return v != NULL && strstr(v, needle) != NULL;
}

static int env_equals(const char *name, const char *expect) {
    const char *v = getenv(name);
    return v != NULL && strcmp(v, expect) == 0;
}

static void probe_capabilities(void) {
    if (g_state.capabilities_probed) return;
    uint32_t flags = 0;
    if (env_nonempty("NO_COLOR")) {
        flags |= PICOLET_TUITERM_CAP_NO_COLOR;
    } else if (env_equals("COLORTERM", "truecolor") ||
               env_equals("COLORTERM", "24bit")) {
        flags |= PICOLET_TUITERM_CAP_TRUECOLOR | PICOLET_TUITERM_CAP_256COLOR;
    } else if (env_contains("TERM", "256color")) {
        flags |= PICOLET_TUITERM_CAP_256COLOR;
    }
    g_state.capabilities = flags;
    g_state.capabilities_probed = 1;
}

int32_t picolet_tuiterm_capabilities(uint32_t *flags) {
    if (flags == NULL) { set_last(EINVAL); return -1; }
    probe_capabilities();
    *flags = g_state.capabilities;
    set_last(0);
    return 0;
}

/* ---- Signal + atexit plumbing ------------------------------------------ */

static void on_winch(int sig) {
    (void)sig;
    g_state.resize_pending = 1;
}

/* Restore-then-die handler.  Re-raises after restoring so the parent sees
 * the original signal disposition (so e.g. shell job control reports
 * "Killed by signal 15" instead of "exit 0"). */
static void on_termsig(int sig) {
    picolet_tuiterm_disable();
    /* Reset to default and re-raise.  Any further signal of the same kind
     * during this window kills with the default disposition. */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = SIG_DFL;
    sigaction(sig, &sa, NULL);
    raise(sig);
}

static void atexit_restore(void) {
    picolet_tuiterm_disable();
}

static int install_hooks(void) {
    if (g_state.hooks_installed) return 0;

    struct sigaction sa_winch;
    memset(&sa_winch, 0, sizeof(sa_winch));
    sa_winch.sa_handler = on_winch;
    sigemptyset(&sa_winch.sa_mask);
    /* SA_RESTART so the SIGWINCH does not surface as EINTR to the Python
     * I/O loop — it is purely a wake hint for the resize pump. */
    sa_winch.sa_flags = SA_RESTART;
    if (sigaction(SIGWINCH, &sa_winch, NULL) < 0) return -1;

    struct sigaction sa_term;
    memset(&sa_term, 0, sizeof(sa_term));
    sa_term.sa_handler = on_termsig;
    sigemptyset(&sa_term.sa_mask);
    sa_term.sa_flags = 0;
    /* SIGINT only meaningful when ISIG is left on; the raw-mode mask we
     * apply turns ISIG off so Ctrl+C arrives as 0x03 in the byte stream.
     * Install anyway — a SIGINT delivered by `kill` rather than the tty
     * still needs the restore path. */
    if (sigaction(SIGINT,  &sa_term, NULL) < 0) return -1;
    if (sigaction(SIGTERM, &sa_term, NULL) < 0) return -1;
    if (sigaction(SIGHUP,  &sa_term, NULL) < 0) return -1;

    if (atexit(atexit_restore) != 0) return -1;

    g_state.hooks_installed = 1;
    return 0;
}

/* ---- Lifecycle --------------------------------------------------------- */

int32_t picolet_tuiterm_enable(void) {
    if (g_state.enabled) { set_last(0); return 0; }

    if (!isatty(STDIN_FILENO) || !isatty(STDOUT_FILENO)) {
        set_last(ENOTTY);
        return -1;
    }

    if (!g_state.snapshotted) {
        if (tcgetattr(STDIN_FILENO, &g_state.orig) < 0) {
            set_last(errno);
            return -2;
        }
        g_state.snapshotted = 1;
    }

    if (install_hooks() < 0) {
        set_last(errno);
        return -3;
    }

    probe_capabilities();

    /* Raw-mode mask: explicit form from research doc 04 §1.  Clear every
     * input-translation, output-processing, and line-discipline flag the
     * compositor needs to own; pin to 8-bit characters. */
    struct termios raw = g_state.orig;
    raw.c_iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL | IXON | INPCK);
    raw.c_oflag &= ~OPOST;
    raw.c_lflag &= ~(ECHO | ECHONL | ICANON | IEXTEN | ISIG);
    raw.c_cflag &= ~(CSIZE | PARENB);
    raw.c_cflag |= CS8;
    /* VMIN/VTIME zeroed so termios never blocks; poll() in read_input()
     * is the actual timeout source. */
    raw.c_cc[VMIN]  = 0;
    raw.c_cc[VTIME] = 0;

    if (tcsetattr(STDIN_FILENO, TCSAFLUSH, &raw) < 0) {
        set_last(errno);
        return -4;
    }

    /* Prologue: alt-screen, hide cursor, mouse SGR, bracketed paste.
     * Order matches FR-TUI-11.  Each sequence is independent; failure of
     * the write is reported but we do NOT undo the termios change because
     * the caller's next move on any error is disable() anyway. */
    if (write_all(STDOUT_FILENO, TUI_ENTER_ALTSCREEN, strlen(TUI_ENTER_ALTSCREEN)) < 0 ||
        write_all(STDOUT_FILENO, TUI_HIDE_CURSOR,     strlen(TUI_HIDE_CURSOR))     < 0 ||
        write_all(STDOUT_FILENO, TUI_MOUSE_ON,        strlen(TUI_MOUSE_ON))        < 0 ||
        write_all(STDOUT_FILENO, TUI_PASTE_ON,        strlen(TUI_PASTE_ON))        < 0) {
        set_last(errno);
        return -5;
    }

    g_state.enabled = 1;
    set_last(0);
    return 0;
}

int32_t picolet_tuiterm_disable(void) {
    if (!g_state.enabled) { set_last(0); return 0; }
    /* Clear first so a signal-driven re-entry sees enabled=0 and bails
     * out without double-restoring. */
    g_state.enabled = 0;

    /* Epilogue in reverse order of the prologue (FR-TUI-11). */
    write_all(STDOUT_FILENO, TUI_PASTE_OFF,    strlen(TUI_PASTE_OFF));
    write_all(STDOUT_FILENO, TUI_MOUSE_OFF,    strlen(TUI_MOUSE_OFF));
    write_all(STDOUT_FILENO, TUI_SHOW_CURSOR,  strlen(TUI_SHOW_CURSOR));
    write_all(STDOUT_FILENO, TUI_EXIT_ALTSCREEN, strlen(TUI_EXIT_ALTSCREEN));

    if (g_state.snapshotted) {
        /* TCSANOW on restore: we want the saved attrs back even if there
         * are bytes buffered from the user's last keystroke. */
        tcsetattr(STDIN_FILENO, TCSANOW, &g_state.orig);
    }

    set_last(0);
    return 0;
}

/* ---- Geometry ---------------------------------------------------------- */

int32_t picolet_tuiterm_size(int *cols, int *rows) {
    if (cols == NULL || rows == NULL) { set_last(EINVAL); return -1; }
    struct winsize ws;
    if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &ws) < 0) {
        set_last(errno);
        return -2;
    }
    *cols = ws.ws_col;
    *rows = ws.ws_row;
    set_last(0);
    return 0;
}

int32_t picolet_tuiterm_resize_pending(void) {
    /* Read-and-clear.  The signal handler is the only other writer and it
     * only ever stores 1, so a torn read here is impossible on any sane
     * sig_atomic_t implementation. */
    if (g_state.resize_pending) {
        g_state.resize_pending = 0;
        return 1;
    }
    return 0;
}

int32_t picolet_tuiterm_is_tty(int fd) {
    return isatty(fd) ? 1 : 0;
}

/* ---- IO ---------------------------------------------------------------- */

int32_t picolet_tuiterm_read_input(char *buf, int32_t cap, int32_t timeout_ms) {
    if (buf == NULL || cap <= 0) { set_last(EINVAL); return -1; }

    struct pollfd pfd = { .fd = STDIN_FILENO, .events = POLLIN, .revents = 0 };
    int pr = poll(&pfd, 1, timeout_ms);
    if (pr < 0) {
        if (errno == EINTR) { set_last(0); return 0; }
        set_last(errno);
        return -2;
    }
    if (pr == 0) { set_last(0); return 0; }

    ssize_t n = read(STDIN_FILENO, buf, (size_t)cap);
    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
            set_last(0);
            return 0;
        }
        set_last(errno);
        return -3;
    }
    set_last(0);
    return (int32_t)n;
}

int32_t picolet_tuiterm_write(const char *bytes, int32_t n) {
    if (bytes == NULL || n < 0) { set_last(EINVAL); return -1; }
    if (n == 0) { set_last(0); return 0; }
    if (write_all(STDOUT_FILENO, bytes, (size_t)n) < 0) {
        set_last(errno);
        return -2;
    }
    set_last(0);
    return n;
}
