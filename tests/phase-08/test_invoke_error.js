// tests/phase-08/test_invoke_error.js — Gate 5.
//
// Verifies that an error reply from Python:
//   { id: N, ok: false, error: { type: "ValueError", message: "bad input" } }
// causes the promise to reject with an Error whose .name is "ValueError"
// and .message is "bad input".
//
// Run: node tests/phase-08/test_invoke_error.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

let capturedJson;
window.webkit.messageHandlers.picolet.postMessage = (json) => {
  capturedJson = json;
};

const p = window.picolet.invoke("boom");
const req = JSON.parse(capturedJson);

// Simulate error reply from Python.
window.__picolet_recv(JSON.stringify({
  id: req.id,
  ok: false,
  error: { type: "ValueError", message: "bad input" },
}));

p.then(() => {
  console.error("FAIL: promise should have rejected");
  process.exit(1);
}).catch((err) => {
  assert.ok(err instanceof Error, "rejection value should be an Error");
  assert.strictEqual(err.name, "ValueError", `err.name should be 'ValueError', got '${err.name}'`);
  assert.strictEqual(err.message, "bad input", `err.message should be 'bad input', got '${err.message}'`);
  console.log("PASS");
});
