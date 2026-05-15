// tests/phase-08/test_concurrent_invokes.js
//
// Verifies that 50 concurrent invokes all complete, each receives its own
// result, and invoke IDs are unique across the batch.
//
// Run: node tests/phase-08/test_concurrent_invokes.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

const CONCURRENCY = 50;
const outboundMessages = [];

window.webkit.messageHandlers.picolet.postMessage = (json) => {
  outboundMessages.push(json);
};

// Fire 50 concurrent invokes.
const promises = [];
for (let i = 0; i < CONCURRENCY; i++) {
  promises.push(window.picolet.invoke("cmd_" + i, { index: i }));
}

// Parse all outbound requests.
const requests = outboundMessages.map((json) => JSON.parse(json));

// All IDs must be numbers.
requests.forEach((req, idx) => {
  assert.strictEqual(typeof req.id, "number",
    "request " + idx + " id should be a number, got " + typeof req.id);
});

// All IDs must be unique.
const idSet = new Set(requests.map((r) => r.id));
assert.strictEqual(idSet.size, CONCURRENCY,
  "expected " + CONCURRENCY + " unique IDs, got " + idSet.size);

// Now replay success replies for each outstanding invoke.
requests.forEach((req) => {
  window.__picolet_recv(JSON.stringify({
    id: req.id,
    ok: true,
    result: "result_" + req.id,
  }));
});

// Wait for all promises to settle.
Promise.all(
  promises.map((p, i) =>
    p.then((val) => {
      // Each promise should resolve with its own result string.
      const req = requests[i];
      assert.strictEqual(val, "result_" + req.id,
        "promise " + i + " resolved with wrong value: " + val);
    })
  )
).then(() => {
  console.log("PASS");
}).catch((e) => {
  console.error("FAIL", e);
  process.exit(1);
});
