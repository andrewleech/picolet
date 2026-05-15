// tests/phase-08/test_api_surface.js — Gate 3: window.picolet API surface.
//
// Verifies that after sourcing the bundle, window.picolet.invoke, .on, and
// .emit are all present and callable.  Also verifies window.__picolet_recv
// is installed as an internal handler.
//
// Run: node tests/phase-08/test_api_surface.js

"use strict";
const assert = require("assert");

// Set up a minimal window mock before requiring the bundle.
global.window = {
  webkit: { messageHandlers: { picolet: { postMessage: () => {} } } },
};

require("../../packages/picolet-bridge-js/dist/picolet-bridge.js");

assert.strictEqual(typeof window.picolet, "object", "window.picolet should be object");
assert.strictEqual(typeof window.picolet.invoke, "function", "window.picolet.invoke should be function");
assert.strictEqual(typeof window.picolet.on, "function", "window.picolet.on should be function");
assert.strictEqual(typeof window.picolet.emit, "function", "window.picolet.emit should be function");
assert.strictEqual(typeof window.__picolet_recv, "function", "window.__picolet_recv should be function");

console.log("PASS");
