// tests/phase-10/test_bridge_channel_legacy.js — PH10 gate 13.
//
// Verifies: the rebuilt picolet-bridge.js bundle still dispatches
// outbound messages to window.webkit.messageHandlers.picolet on Linux
// (WebKitGTK).  Regression guard for the AD5 feature-detect change.
//
// Run: node tests/phase-10/test_bridge_channel_legacy.js

"use strict";
const assert = require("assert");

// Provide only the WebKitGTK channel — no window.chrome.
let captured = null;
global.window = {
  webkit: {
    messageHandlers: {
      picolet: {
        postMessage: (json) => { captured = json; },
      },
    },
  },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

window.picolet.emit("x", null);

assert.strictEqual(typeof captured, "string",
  "expected outbound JSON to be captured via webkit.messageHandlers.picolet");
const msg = JSON.parse(captured);
assert.strictEqual(msg.event, "x", "outbound event mismatch");
assert.strictEqual(msg.data, null, "outbound data mismatch");

console.log("PASS");
