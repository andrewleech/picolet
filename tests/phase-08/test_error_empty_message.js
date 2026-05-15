// tests/phase-08/test_error_empty_message.js
//
// Verifies the error path when Python raises with an empty message string.
// The bridge must produce an Error with:
//   err.name  === "ValueError" (the type is preserved)
//   err.message === ""         (empty, not undefined or null)
//
// Run: node tests/phase-08/test_error_empty_message.js

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

const p = window.picolet.invoke("will_fail");
const req = JSON.parse(capturedJson);

// Simulate error reply with an empty message.
window.__picolet_recv(JSON.stringify({
  id: req.id,
  ok: false,
  error: { type: "ValueError", message: "" },
}));

p.then(() => {
  console.error("FAIL: promise should have rejected");
  process.exit(1);
}).catch((err) => {
  assert.ok(err instanceof Error, "rejection should be an Error");
  assert.strictEqual(err.name, "ValueError",
    "err.name should be 'ValueError', got '" + err.name + "'");
  assert.strictEqual(err.message, "",
    "err.message should be empty string, got '" + err.message + "'");
  console.log("PASS");
});
