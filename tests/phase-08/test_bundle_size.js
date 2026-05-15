// tests/phase-08/test_bundle_size.js
//
// Verifies that the minified bundle is at most 5 120 bytes (5 KiB).
// A bundle growing beyond this threshold indicates accidental inclusion
// of third-party code or unminified output.
//
// Run: node tests/phase-08/test_bundle_size.js

"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const BUNDLE = path.resolve(
  __dirname,
  "../../packages/picolet-bridge-js/dist/picolet-bridge.js"
);
const MAX_BYTES = 5120; // 5 KiB

assert.ok(fs.existsSync(BUNDLE), "bundle file not found: " + BUNDLE);

const size = fs.statSync(BUNDLE).size;
assert.ok(
  size > 0,
  "bundle is empty"
);
assert.ok(
  size <= MAX_BYTES,
  "bundle size " + size + " bytes exceeds 5 KiB limit (" + MAX_BYTES + " bytes)"
);

console.log("PASS  bundle is " + size + " bytes (<= " + MAX_BYTES + " limit)");
