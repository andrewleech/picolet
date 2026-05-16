// tests/phase-08/test_invoke_timeout.js
//
// Verifies that invoke() with { timeout: N } rejects with
// Error("invoke timeout") when the Python side never replies,
// and that it does so within a reasonable wall-clock window.
//
// Run: node tests/phase-08/test_invoke_timeout.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

// Swallow outbound messages — no reply will ever come.
window.webkit.messageHandlers.picolet.postMessage = () => {};

const TIMEOUT_MS = 100;
const GRACE_MS   = 150; // wall-clock window: timeout + 50 ms jitter

const start = Date.now();

window.picolet.invoke("never_replies", null, { timeout: TIMEOUT_MS })
  .then(() => {
    console.error("FAIL: promise resolved instead of rejecting");
    process.exit(1);
  })
  .catch((err) => {
    const elapsed = Date.now() - start;

    assert.ok(
      err instanceof Error,
      "rejection value should be an Error, got: " + err
    );
    assert.strictEqual(
      err.message,
      "invoke timeout",
      "expected message 'invoke timeout', got: " + err.message
    );
    assert.ok(
      elapsed <= GRACE_MS,
      "rejection took " + elapsed + " ms, expected <= " + GRACE_MS + " ms"
    );

    console.log(
      "PASS  invoke timeout rejected in " + elapsed + " ms " +
      "(<= " + GRACE_MS + " ms grace)"
    );
  });
