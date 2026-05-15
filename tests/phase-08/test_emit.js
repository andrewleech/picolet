// tests/phase-08/test_emit.js — Gate 8.
//
// Verifies that window.picolet.emit("click", {x:10}) sends the correct
// wire-format JSON to window.webkit.messageHandlers.picolet.postMessage.
//
// Expected outbound shape: { "event": "click", "data": { "x": 10 } }
//
// Run: node tests/phase-08/test_emit.js

"use strict";
const assert = require("assert");

global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

let captured = null;
window.webkit.messageHandlers.picolet.postMessage = (json) => {
  captured = json;
};

window.picolet.emit("click", { x: 10 });

assert.ok(captured !== null, "postMessage should have been called");
const msg = JSON.parse(captured);
assert.strictEqual(msg.event, "click", `event should be 'click', got '${msg.event}'`);
assert.deepStrictEqual(msg.data, { x: 10 }, "data mismatch");
assert.strictEqual(msg.cmd, undefined, "emit should not include 'cmd'");
assert.strictEqual(msg.id, undefined, "emit should not include 'id'");

// Also verify emit with no data sends null.
window.picolet.emit("noop");
const msg2 = JSON.parse(captured);
assert.strictEqual(msg2.event, "noop");
assert.strictEqual(msg2.data, null, "omitted data should be sent as null");

console.log("PASS");
