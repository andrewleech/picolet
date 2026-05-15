// tests/phase-08/test_unsubscribe.js — Gate 7.
//
// Verifies that the unsubscribe function returned by window.picolet.on()
// correctly removes the handler so further events are not delivered.
//
// Run: node tests/phase-08/test_unsubscribe.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

let callCount = 0;
const unsub = window.picolet.on("tick", () => {
  callCount++;
});

// First event — should be delivered.
window.__picolet_recv(JSON.stringify({ event: "tick", data: {} }));
assert.strictEqual(callCount, 1, "handler should be called once before unsub");

// Unsubscribe.
unsub();

// Second event — should NOT be delivered.
window.__picolet_recv(JSON.stringify({ event: "tick", data: {} }));
assert.strictEqual(callCount, 1, "handler should not be called after unsub");

// Calling unsub() again should be a no-op (not throw).
unsub();
assert.strictEqual(callCount, 1, "double-unsub should be a no-op");

// A second handler on the same topic should still receive events.
let otherCount = 0;
window.picolet.on("tick", () => {
  otherCount++;
});
window.__picolet_recv(JSON.stringify({ event: "tick", data: {} }));
assert.strictEqual(callCount, 1, "unsubbed handler stays silent");
assert.strictEqual(otherCount, 1, "other handler still receives events");

console.log("PASS");
