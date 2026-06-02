/*
 * picolet_tuiterm.h — Unix terminal-handling shim for the picolet tui variant.
 *
 * Surface
 * =======
 *
 *  * enable()/disable() snapshot and restore termios, switch to alt-screen,
 *    hide the cursor, and turn on SGR mouse + bracketed paste.  Both are
 *    idempotent.  disable() also fires from atexit + SIGINT/SIGTERM/SIGHUP
 *    so an unhandled signal does not leave the user's terminal wedged.
 *
 *  * read_input() is non-blocking: poll(POLLIN) gates a single read() into
 *    the caller's buffer with VMIN=0/VTIME=0, so 0 on timeout costs one
 *    syscall and never blocks past timeout_ms.  The C side does not parse
 *    escape sequences — that lives in frozen Python (driver/unix.py).
 *
 *  * size() pulls window geometry via TIOCGWINSZ on demand.  resize_pending()
 *    consumes the sig_atomic_t flag the SIGWINCH handler sets.  The ioctl
 *    is not async-signal-safe so the handler must not call it directly.
 *
 *  * capabilities() reports a colour-system bitfield derived from env vars
 *    at the first enable() call.  Per NFR-TUI-7 the precedence is
 *    NO_COLOR > COLORTERM(=truecolor|24bit) > TERM(=*-256color).  The
 *    PICOLET_TUI_COLOR override (FR-TUI-39) is applied on the Python side,
 *    not here — this layer reports what the host advertises, not what the
 *    user wants.
 *
 * Resolution: this file is compiled into the runtime binary.  Python
 * reaches each symbol via `ffi.open(None).func("picolet_tuiterm_*")`; the
 * variant Makefile retains them with -Wl,--export-dynamic and per-symbol
 * --undefined= so --gc-sections cannot strip them.
 *
 * Threading: the picolet tui variant disables _thread (NFR-TUI-11).  The
 * signal handler is the only concurrent writer; it only ever sets a
 * volatile sig_atomic_t — no locks needed.
 *
 * License: MIT (picolet code).
 */

#ifndef PICOLET_TUITERM_H
#define PICOLET_TUITERM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Export-visibility shim --------------------------------------------
 *
 * Mirrors picolet_winevents.h.  -fvisibility=default keeps the symbol
 * reachable from ffi.open(None) on ELF; the dllexport branch is here so
 * the same source compiles unchanged when a Windows port is added under
 * variants/tui/windows/. */
#if defined(_WIN32) || defined(__CYGWIN__)
#  define PICOLET_TUITERM_API __declspec(dllexport)
#else
#  define PICOLET_TUITERM_API __attribute__((visibility("default")))
#endif

/* ---- Colour-capability bitfield ---------------------------------------- */

/* Reported by picolet_tuiterm_capabilities().  Bits are independent: a
 * truecolor-capable terminal also advertises 256-colour support.  The
 * NO_COLOR bit overrides the others and is set when $NO_COLOR is non-empty
 * (https://no-color.org). */
#define PICOLET_TUITERM_CAP_256COLOR   (1u << 0)
#define PICOLET_TUITERM_CAP_TRUECOLOR  (1u << 1)
#define PICOLET_TUITERM_CAP_NO_COLOR   (1u << 2)

/* ---- Lifecycle ---------------------------------------------------------- */

/* Enter raw mode on STDIN_FILENO, switch the output side to the alternate
 * screen, hide the cursor, and arm SGR mouse + bracketed paste.  The
 * original termios is snapshotted on first call.  atexit() + SIGINT,
 * SIGTERM, SIGHUP handlers are installed once and re-fire disable() on
 * any path out.
 *
 * Returns 0 on success.  On failure returns negative and sets last_error
 * to errno; the terminal is left untouched. */
PICOLET_TUITERM_API int32_t picolet_tuiterm_enable(void);

/* Restore the snapshot, leave the alt-screen, show the cursor, and turn
 * off SGR mouse + bracketed paste.  Idempotent.  Safe to call from a
 * signal handler (tcsetattr is universally treated as async-signal-safe
 * even though POSIX does not list it). */
PICOLET_TUITERM_API int32_t picolet_tuiterm_disable(void);

/* ---- Geometry ----------------------------------------------------------- */

/* Write the current terminal size into *cols and *rows via TIOCGWINSZ.
 * Returns 0 on success, negative on failure with errno in last_error. */
PICOLET_TUITERM_API int32_t picolet_tuiterm_size(int *cols, int *rows);

/* Returns 1 if the SIGWINCH handler has fired since the last call to
 * this function, 0 otherwise.  Reading clears the flag.  The Python
 * _pump_resize task calls this once per tick; the actual TIOCGWINSZ
 * runs from size() on the main thread (the ioctl is not
 * async-signal-safe). */
PICOLET_TUITERM_API int32_t picolet_tuiterm_resize_pending(void);

/* Returns 1 if fd refers to a tty, 0 otherwise.  Used by the App to
 * refuse to start when stdin or stdout has been redirected to a pipe. */
PICOLET_TUITERM_API int32_t picolet_tuiterm_is_tty(int fd);

/* ---- IO ---------------------------------------------------------------- */

/* Non-blocking read of up to cap bytes from STDIN_FILENO.  When
 * timeout_ms > 0, blocks in poll(POLLIN) for at most timeout_ms before
 * the read attempt; timeout_ms == 0 polls once and returns immediately.
 *
 * Returns the number of bytes written into buf (0 on timeout / no data
 * pending), or negative on failure with errno in last_error.  EAGAIN /
 * EWOULDBLOCK / EINTR are treated as "no data" and return 0. */
PICOLET_TUITERM_API
int32_t picolet_tuiterm_read_input(char *buf, int32_t cap, int32_t timeout_ms);

/* Write exactly n bytes from bytes to STDOUT_FILENO.  Loops over partial
 * writes; EINTR retries.  Returns n on success, negative on failure with
 * errno in last_error.  No buffering — the caller (compositor) decides
 * when to flush. */
PICOLET_TUITERM_API int32_t picolet_tuiterm_write(const char *bytes, int32_t n);

/* ---- Capabilities ------------------------------------------------------ */

/* Write the host-advertised colour-capability bitfield (PICOLET_TUITERM_CAP_*)
 * into *flags.  Probed once on first enable() and cached for the life of
 * the process; subsequent calls are O(1).  Returns 0 on success. */
PICOLET_TUITERM_API int32_t picolet_tuiterm_capabilities(uint32_t *flags);

/* ---- Errors ------------------------------------------------------------ */

/* errno from the most recent failed call.  Cleared on any successful call. */
PICOLET_TUITERM_API int32_t picolet_tuiterm_last_error(void);

#ifdef __cplusplus
}
#endif

#endif /* PICOLET_TUITERM_H */
