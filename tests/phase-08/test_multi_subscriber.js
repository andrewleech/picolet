// tests/phase-08/test_multi_subscriber.js
//
// Verifies that multiple handlers registered with window.picolet.on() for the
// same topic all fire when an event arrives (FR-WV-5 multi-subscriber path).
//
// Also verifies that after unsub(), only the unsubbed handler stops firing
// while the remaining handler(s) continue to receive subsequent events.
//
// Run: node tests/phase-08/test_multi_subscriber.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

// --- Part 1: two handlers on the same topic both fire ---
let h1Count = 0;
let h2Count = 0;
let h1LastData = null;
let h2LastData = null;

const unsub1 = window.picolet.on("topic", (data) => {
  h1Count++;
  h1LastData = data;
});

const unsub2 = window.picolet.on("topic", (data) => {
  h2Count++;
  h2LastData = data;
});

window.__picolet_recv(JSON.stringify({ event: "topic", data: { x: 7 } }));

assert.strictEqual(h1Count, 1, "h1 should have been called once");
assert.strictEqual(h2Count, 1, "h2 should have been called once");
assert.deepStrictEqual(h1LastData, { x: 7 }, "h1 received wrong data");
assert.deepStrictEqual(h2LastData, { x: 7 }, "h2 received wrong data");

// --- Part 2: unsub h1; only h2 fires on the next event ---
unsub1();

window.__picolet_recv(JSON.stringify({ event: "topic", data: { x: 8 } }));

assert.strictEqual(h1Count, 1, "h1 should not be called after unsub");
assert.strictEqual(h2Count, 2, "h2 should have been called again");
assert.deepStrictEqual(h2LastData, { x: 8 }, "h2 received wrong data on second event");

// --- Part 3: unsub h2; no handlers remain, event is silently discarded ---
unsub2();

window.__picolet_recv(JSON.stringify({ event: "topic", data: { x: 9 } }));

assert.strictEqual(h1Count, 1, "h1 still silent after double-unsub scenario");
assert.strictEqual(h2Count, 2, "h2 silent after its unsub");

console.log("PASS");
