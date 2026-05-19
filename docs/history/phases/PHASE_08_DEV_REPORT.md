# picolet-bridge-js — Phase 08 Developer Report

**Feature:** picolet-bridge-js
**Phase:** 08 — JS bridge: window.picolet API + IPC wire-up
**Date:** 2026-05-15
**Attempt:** 1 (with post-SQE fix pass on transport encoding bug)
**Phase File:** /home/anl/picolet/docs/phases/PHASE_08_picolet-bridge-js.md

## Implementation Summary

PH08 delivers the `picolet-bridge-js` package: a TypeScript IIFE bundle that exposes `window.picolet.invoke()`, `window.picolet.on()`, and `window.picolet.emit()` to web-content JS, and replaces the PH07 no-op `window.__picolet_recv` stub with a functional inbound dispatcher that routes Python replies to pending `invoke()` promises and Python push events to `on()` subscribers.

The bridge is built with esbuild into a single minified IIFE (`dist/picolet-bridge.js`, 1083 bytes). The webview runtime build script copies this bundle into the romfs tree under `picolet-bridge/` and the PH07 `_webview.py` startup sequence injects it at `DOCUMENT_START` via `webkit_user_script_new` so it is present before any application HTML script runs.

The critical correctness issue discovered during testing: `WebviewTransport.send()` was constructing the eval_js call as `window.__picolet_recv(<json-object-literal>)` — a bare JS object, not a string. The bridge's `__picolet_recv` calls `JSON.parse(jsonString)`, and when the argument is an object, JS coerces it to `"[object Object]"` before parsing, causing a silent SyntaxError. The fix is to double-encode: `json.dumps(json.dumps(msg))` produces a properly quoted JS string literal. The binary was rebuilt to freeze the fix into the romfs. PH07 unit tests that parse `eval_js` output were updated to double-parse accordingly.

## Files Created

| File Path | Purpose | Lines |
|-----------|---------|-------|
| `packages/picolet-bridge-js/package.json` | npm package manifest for the bridge | 18 |
| `packages/picolet-bridge-js/tsconfig.json` | TypeScript configuration | 12 |
| `packages/picolet-bridge-js/build.mjs` | esbuild script (IIFE format, minified) | 28 |
| `packages/picolet-bridge-js/src/index.ts` | Bridge source: `window.picolet` API + `__picolet_recv` | 139 |
| `packages/picolet-bridge-js/dist/picolet-bridge.js` | Compiled, minified IIFE bundle | 1 (1083 bytes) |
| `tests/phase-08/run.sh` | Gate harness for all 15 PH08 gates | ~180 |
| `tests/phase-08/fixtures/bridge-inject-order/src/main.py` | Gate 12 fixture: injection ordering verification | ~60 |
| `tests/phase-08/fixtures/bridge-inject-order/src/picolet.toml` | Config for injection-order fixture | 4 |
| `tests/phase-08/fixtures/bridge-inject-order/ui/index.html` | JS probe for DOCUMENT_START timing | ~30 |
| `tests/phase-08/fixtures/event-push/src/main.py` | Gate 12 fixture: Python→JS push event | ~70 |
| `tests/phase-08/fixtures/event-push/src/picolet.toml` | Config for event-push fixture | 4 |
| `tests/phase-08/fixtures/event-push/ui/index.html` | JS event subscriber for push test | ~25 |
| `tests/phase-08/fixtures/invoke-roundtrip/src/main.py` | Gates 11+13 fixture: invoke + error propagation | 84 |
| `tests/phase-08/fixtures/invoke-roundtrip/src/picolet.toml` | Config for roundtrip fixture | 4 |
| `tests/phase-08/fixtures/invoke-roundtrip/ui/index.html` | JS that calls invoke() and emits results | ~35 |
| `tests/phase-08/js/picolet-bridge.test.js` | Jest unit tests for bridge JS logic | ~120 |
| `tests/phase-08/js/package.json` | npm manifest for jest test runner | 15 |

## Files Modified

| File Path | Changes Made | Reason |
|-----------|-------------|--------|
| `packages/picolet-runtime/python/picolet_ui/_webview.py` | `transport.send()`: changed `json.dumps(msg)` to `json.dumps(json.dumps(msg))` as the JS argument, plus comment explaining double-encoding | `__picolet_recv` expects a JSON string; bare object literal causes JSON.parse to silently fail |
| `packages/picolet-runtime/build/picolet-runtime-linux-x64-webview` | Rebuilt binary (665904 bytes) | `_webview.py` is frozen bytecode; fix requires rebuild |
| `packages/picolet-runtime/micropython` (submodule) | Bumped to tip of `windows-pyusb` branch after rebuild | Build script commits submodule on each rebuild |
| `tests/phase-07/test_transport_contract.py` | Double-parse in `test_send_invokes_eval_js_with_picolet_recv` and `test_dispatcher_consumes_webview_transport_messages` | transport.send now double-encodes; tests must unwrap outer string before asserting dict contents |
| `tests/phase-07/test_webview_additional.py` | Double-parse in `test_two_rapid_messages_dispatcher_replies_in_order` | Same encoding change |

## Build Status

- **TypeScript/bundle build command:** `cd packages/picolet-bridge-js && node build.mjs`
- **Result:** Pass — `dist/picolet-bridge.js` 1083 bytes
- **Runtime build command:** `bash packages/picolet-runtime/scripts/build-runtime.sh --target linux-x64 --variant webview`
- **Result:** Pass — `build/picolet-runtime-linux-x64-webview` 665904 bytes (31% of NFR-2 2 MiB ceiling)
- **Warnings:** None

## Deviations from Phase Plan

**Double-encoding in `transport.send()`**: The phase plan and initial implementation passed the JSON-serialised message as a bare JS object literal to `window.__picolet_recv`. The bridge spec requires `__picolet_recv(jsonString: string)` to receive a JSON string and call `JSON.parse()` on it. Passing an object instead of a string means `JSON.parse` receives `"[object Object]"` (the object's toString) and throws SyntaxError silently — promises never resolve. Fixed by double-encoding: `js = "window.__picolet_recv(" + json.dumps(json.dumps(msg)) + ")"`.

**Integration fixture pattern**: The `invoke-roundtrip/src/main.py` fixture uses `picolet.on("result"/"err")` + `asyncio.Event()` to receive JS-pushed results rather than reading directly from the transport. This is necessary because the dispatcher processes all inbound messages (including JS `emit()` calls) and routes them to `picolet.on()` subscribers. Attempting to `transport.recv()` from a watcher coroutine races with the dispatcher's own recv loop and starves it.

## Known Limitations

- The visual gate (bridge injection confirmed by rendered text) requires Xvfb + GTK; it passes in the full test environment. The Jest unit tests cover the pure JS logic without a browser. Integration fixtures exercise the full GTK/WebKit path.
- The PH07 `_webview.py` tests that validate `eval_js` output are sensitive to the double-encoding format. If the encoding changes again, those assertions must change with it. This coupling is noted but acceptable since the tests are specifically validating the wire format.

## Key Decisions Made

**IIFE format for the bundle**: esbuild's `format: 'iife'` wraps the module in an immediately-invoked function expression, giving it a clean private scope while assigning `window.picolet` and `window.__picolet_recv` on the global. This avoids CommonJS/ESM module loading machinery that is unavailable in the WebKit content process at DOCUMENT_START time.

**`dist/` checked into git**: The compiled bundle is committed so consumers (the runtime build script) do not need a Node.js toolchain at build time. The source and build script are also present for reproducibility.

**`json.dumps(json.dumps(msg))` for eval_js**: The outer `json.dumps` produces a Python string containing the inner JSON. The inner `json.dumps` was already the JSON-serialised message dict. The outer call then turns that string into a valid JS string literal (quoting it, escaping quotes inside). This is the minimal-code correct approach for passing a Python dict as a JS string argument via eval_js.

## Notes for SQE

- Gate 11 (invoke round-trip `greet`): confirm `PICOLET_WV_INVOKE_OK` appears in fixture stdout.
- Gate 13 (error propagation `boom`): confirm `PICOLET_WV_ERROR_OK` appears in fixture stdout. The error object sent to JS must have `name = "ValueError"` and `message = "bad input"`.
- Gate 15 (PH07 regression): `bash tests/phase-07/run.sh --skip-rebuild` must show 20 passed / 0 failed.
- The double-encoding boundary: if you add tests that inspect `eval_js` output from `WebviewTransport.send()`, remember the argument is a JSON string (outer quotes included), not a bare JSON object. You need two `json.loads` calls to get to the Python dict.
- `window.picolet.emit()` sends `{ event, data }` outbound via `postMessage`; the dispatcher routes this to Python `picolet.on()` subscribers. The `invoke-roundtrip` fixture relies on this path — if `picolet.on()` is broken, the fixture hangs rather than failing immediately.
- Concurrency: the dispatcher runs a single asyncio task processing one message at a time. If a command handler is slow, `invoke()` promises from JS will wait. The test fixtures do not exercise concurrent in-flight invocations; that is deferred to PH09+.

## Commit SHAs

| SHA | Description |
|-----|-------------|
| `53b287c` | Decision: TypeScript + esbuild IIFE; dist/ checked in; DOCUMENT_START inject |
| `0511fe6` | Add picolet-bridge-js package skeleton |
| `85f0829` | Add bridge TypeScript source and compiled IIFE bundle |
| `8afe61f` | Copy bridge bundle into webview-variant romfs at build time |
| `1678bc5` | Replace PH07 no-op stub with bridge-js injection at DOCUMENT_START |
| `63a8e7f` | Add JS unit tests, integration fixtures, and run.sh harness |
| `02cd183` | Caveat: _webview.py is frozen into the runtime binary; rebuild required |
| `7ca2128` | Fix transport.send JSON encoding and rebuild runtime |

---

```
STATUS: Complete
ARTIFACT: docs/phases/PHASE_08_DEV_REPORT.md
SUMMARY: PH08 delivers the picolet-bridge-js IIFE bundle (window.picolet.invoke/on/emit + __picolet_recv) injected at DOCUMENT_START in the webview runtime. The root cause of integration test failures was a JSON encoding bug in WebviewTransport.send() where the bridge received a JS object literal instead of a string, causing JSON.parse to silently fail; fixed by double-encoding and rebuilding the runtime binary. All 15 PH08 gates pass, including the PH07 regression gate.
```
