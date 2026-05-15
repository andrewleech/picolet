// tests/phase-08/test_args_edge_cases.js
//
// Verifies args edge cases for window.picolet.invoke():
//
//   invoke('cmd', undefined)  — JSON.stringify converts undefined to null
//   invoke('cmd', null)       — null is preserved as null
//   invoke('cmd', '')         — empty string is preserved
//
// In all cases the outbound wire message must be valid JSON and the id
// field must be a unique integer.
//
// Run: node tests/phase-08/test_args_edge_cases.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

const captured = [];
window.webkit.messageHandlers.picolet.postMessage = (json) => {
  captured.push(json);
};

// Case 1: invoke with undefined args (JS default-param path makes it null
// before reaching JSON.stringify, per the TS source: args: unknown = null).
const p1 = window.picolet.invoke("cmd", undefined);
// Case 2: invoke with null.
const p2 = window.picolet.invoke("cmd", null);
// Case 3: invoke with empty string.
const p3 = window.picolet.invoke("cmd", "");

assert.strictEqual(captured.length, 3, "expected 3 outbound messages");

const req1 = JSON.parse(captured[0]);
const req2 = JSON.parse(captured[1]);
const req3 = JSON.parse(captured[2]);

// undefined arg: bridge default-param converts to null.
assert.strictEqual(req1.args, null,
  "undefined args should be sent as null; got " + JSON.stringify(req1.args));

// null arg: passes through.
assert.strictEqual(req2.args, null,
  "null args should be sent as null; got " + JSON.stringify(req2.args));

// empty string arg: passes through.
assert.strictEqual(req3.args, "",
  "empty-string args should be sent as ''; got " + JSON.stringify(req3.args));

// All IDs must be unique integers.
const ids = [req1.id, req2.id, req3.id];
ids.forEach((id) => {
  assert.strictEqual(typeof id, "number", "id should be a number");
});
assert.strictEqual(new Set(ids).size, 3, "all three IDs should be unique");

// Resolve all promises so they do not dangle.
[req1, req2, req3].forEach((req) => {
  window.__picolet_recv(JSON.stringify({ id: req.id, ok: true, result: null }));
});

Promise.all([p1, p2, p3]).then(() => {
  console.log("PASS");
}).catch((e) => {
  console.error("FAIL", e);
  process.exit(1);
});
