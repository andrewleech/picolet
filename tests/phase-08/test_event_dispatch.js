// tests/phase-08/test_event_dispatch.js — Gate 6.
//
// Verifies that an inbound push event:
//   { event: "progress", data: { pct: 42 } }
// dispatches to a handler registered with window.picolet.on("progress", ...).
//
// Run: node tests/phase-08/test_event_dispatch.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

let received = null;
window.picolet.on("progress", (data) => {
  received = data;
});

window.__picolet_recv(JSON.stringify({ event: "progress", data: { pct: 42 } }));

assert.ok(received !== null, "handler should have been called");
assert.deepStrictEqual(received, { pct: 42 }, "handler received wrong data");

console.log("PASS");
