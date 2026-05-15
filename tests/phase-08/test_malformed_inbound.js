// tests/phase-08/test_malformed_inbound.js
//
// Verifies that __picolet_recv handles malformed or unrecognised inbound
// messages without throwing:
//
//   1. Invalid JSON string — must not throw; logs a console.warn.
//   2. Valid JSON but unrecognised shape (no "id"/"ok", no "event") — no
//      throw; a console.warn is emitted.
//   3. A reply for an unknown id — no throw; console.warn emitted; any
//      registered pending promises are unaffected.
//
// Run: node tests/phase-08/test_malformed_inbound.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

// Suppress console.warn for this test to keep output clean; count calls.
let warnCount = 0;
const _origWarn = console.warn;
console.warn = (...args) => { warnCount++; };

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

// Case 1: invalid JSON.
assert.doesNotThrow(() => {
  window.__picolet_recv("not valid json {{{");
}, "invalid JSON must not throw");

// Case 2: valid JSON but unrecognised shape.
assert.doesNotThrow(() => {
  window.__picolet_recv(JSON.stringify({ totally: "unrelated" }));
}, "unrecognised shape must not throw");

// Case 3: reply for an unknown id — should warn, not throw, and not affect
// a real pending invoke.
let capturedJson;
window.webkit.messageHandlers.picolet.postMessage = (json) => {
  capturedJson = json;
};

const p = window.picolet.invoke("real_cmd");
const req = JSON.parse(capturedJson);

assert.doesNotThrow(() => {
  window.__picolet_recv(JSON.stringify({ id: req.id + 9999, ok: true, result: "ghost" }));
}, "reply for unknown id must not throw");

// The real invoke must still resolve correctly.
window.__picolet_recv(JSON.stringify({ id: req.id, ok: true, result: "expected" }));

p.then((val) => {
  assert.strictEqual(val, "expected",
    "real invoke should resolve with 'expected'; got " + val);
  assert.ok(warnCount >= 3,
    "expected at least 3 console.warn calls, got " + warnCount);
  console.warn = _origWarn;
  console.log("PASS");
}).catch((e) => {
  console.warn = _origWarn;
  console.error("FAIL", e);
  process.exit(1);
});
