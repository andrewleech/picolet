# PH31 Linux CLI findings review

Reviewed: 2026-10-06, based on HEAD 47e4918 plus the scanner execution-status changes in this worktree. Scanner: Opengrep stable 1.30.0, `p/security-audit`, manifest-selected Linux x64 CLI and owned host/example/frontend sources. This run scanned 165 files and reported six findings, with one file partially analysed. Native compiler inputs were not supplied to this run; it does not review native findings or establish matrix-wide coverage.

| Finding | Source evidence | Disposition |
|---|---|---|
| Two deprecated `ssl.wrap_socket` findings | MicroPython `extmod/asyncio/stream.py:119,167`, frozen upstream library | Unresolved applicability. [INFERENCE] The rule targets CPython's deprecated API, whereas this is MicroPython's SSL API. Verify MicroPython certificate/hostname behaviour separately before dismissing either occurrence. |
| Dynamic urllib, library cache | `cli/_mpy_lib_cache.py:67-69,285,301,317-324` constructs an HTTPS GitHub archive URL from checked-in constants and checks the extracted tree against a pinned digest | The reported user-controlled scheme path is not present at this callsite. No suppression added; redirects and transport trust are outside this local-pattern disposition. |
| Dynamic urllib, runtime resolver | `cli/runtime_resolver.py:148-180,273-281,465-467` validates the configured base URL scheme before constructing download URLs; file URLs require explicit opt-in | Existing control addresses the rule's direct arbitrary-scheme concern. This is not a review of every URL authority/redirect or local filesystem boundary. No suppression added. |
| `exec`, test command | `cli/test_cmd.py:609-619` reads the local script explicitly selected by `--run-script`, then executes it in the test harness | Intentional local test-script execution, not an application IPC input. Do not run untrusted test scripts. No suppression added. |
| Dynamic urllib, inspector discovery | `testing/_webkit.py:147-156` constructs the URL with a literal HTTP scheme and loopback host plus the inspector port | No arbitrary URL scheme at this callsite. Returned debugger URLs remain a separate trust boundary. No suppression added. |

This is an initial source review, not a security baseline or an approval to gate on new findings. All findings remain in scanner reports. Frozen dependency ownership stays separate from Picolet-owned code, and the partial-analysis warning remains a coverage gap.

## Compiler-derived native follow-up

A second run used the captured Linux CLI compile database (226 commands / 226 source files). It reported one native finding and 35 syntax-error notifications. `micropython/ports/unix/unix_mphal.c:392-400` uses `getrandom` when `_HAVE_GETRANDOM` is defined, otherwise opens `/dev/random`, checks the read result with `RAISE_ERRNO`, then closes the descriptor. [INFERENCE] A read-error exception in the fallback branch could bypass `close`; whether that branch is active is target-dependent. The syntactic scan does not apply compile-command preprocessor flags, so the finding is unresolved across the matrix, not a confirmed Linux runtime leak. No dependency patch or suppression was made.
