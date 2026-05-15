// tests/phase-10/test_bridge_channel_detect.js — PH10 gate 12.
//
// Verifies: the rebuilt picolet-bridge.js bundle dispatches outbound
// messages to window.chrome.webview.postMessage when only the WebView2
// channel is present on window.
//
// Run: node tests/phase-10/test_bridge_channel_detect.js

"use strict";
const assert = require("assert");

// Provide only the WebView2 channel — no window.webkit.
let captured = null;
global.window = {
  chrome: {
    webview: {
      postMessage: (json) => { captured = json; },
    },
  },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

window.picolet.emit("x", null);

assert.strictEqual(typeof captured, "string",
  "expected outbound JSON to be captured via chrome.webview.postMessage");
const msg = JSON.parse(captured);
assert.strictEqual(msg.event, "x", "outbound event mismatch");
assert.strictEqual(msg.data, null, "outbound data mismatch");

console.log("PASS");
