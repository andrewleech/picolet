# MCP stdio-to-HTTP bridge

This Picolet CLI example bridges newline-delimited MCP JSON-RPC over stdin and
stdout to an already-running MCP server's loopback streamable-HTTP endpoint.
It is a standalone Windows x64 executable: it does not start, install or stop
the server, and needs no Python, uv or WSL at runtime. Run one process per client
connection:

```text
mcp-stdio-http.exe URL [--connect-timeout SECONDS]
```

For example, use `http://127.0.0.1:28411/mcp` for a server listening on that
port. URLs must use HTTP on `127.0.0.1` with an explicit port, printable ASCII
and no credentials or fragment. Diagnostics go to stderr.

The adapter forwards client lines verbatim, propagates MCP session and protocol
headers, holds other messages until initialization completes, and handles JSON
and SSE responses, an optional reconnecting GET SSE stream, reverse requests,
progress, cancellation and concurrent calls. It retries initialization on
connection refusal or HTTP 503 within the connect timeout (default five
seconds); it does not replay an in-flight call after a failure. On stdin EOF it
sends DELETE for its session.

## Implementation

`src/main.py` uses MicroPython `asyncio` for concurrent stdio and HTTP work;
`src/hconn.py` implements the asynchronous HTTP/1.1 transport, including
chunked and streaming responses. `src/shallow.py` inspects only top-level
fields of messages larger than 64 KB to avoid the cost of fully decoding large
JSON payloads; those bodies are not fully validated. The adapter grows its heap
on demand in 64 MB increments, up to a 1 GB cap.

The stock runtime-v0.0.4 Windows CLI runtime is insufficient. This example
requires a locally built Windows x64 CLI runtime with native sockets,
socket-aware `select.poll` and wake signalling, pollable stdin, non-blocking
stdout and corresponding `asyncio` support. The Picolet CLI and `mpy-cross`
must match the runtime's MicroPython build. Follow the Picolet runtime build
documentation and incorporate the required runtime integration before building;
the runtime build uses Docker for cross-compilation, Python with `mpremote`,
Git, and libffi bootstrap tools (autoconf, automake and libtool 2.4.7 or newer).

## Build

From this directory, with `picolet` and a matching `mpy-cross` on PATH:

```bash
bash scripts/build.sh /path/to/picolet-runtime-windows-x64-cli.exe
```

The output is `target/windows-x64/mcp-stdio-http.exe`. The script
checks the runtime against its pinned SHA-256 and never downloads one. For a
runtime under development, pass its SHA-256 as the second argument to override
the pin; doing so does not establish compatibility or reproducibility:

```bash
bash scripts/build.sh /path/to/development-runtime.exe "$RUNTIME_SHA256"
```

## Tests

Build the executable first, then from this directory run:

```bash
uv run --no-project --with pytest python -m pytest -q tests
```

The tests launch the real executable against the controllable fake MCP server in
`tests/hub_peer.py`. They require Windows, WSL interop, or a separately
validated Wine setup; Linux alone cannot execute the Windows binary. Set
`ADAPTER_EXE` to test a different executable and `ADAPTER_TEST_PORT` to change
the fake server port (default 28411). Tests are skipped if the executable is absent.
A clean-machine run and integration with a real MCP server remain unverified.
