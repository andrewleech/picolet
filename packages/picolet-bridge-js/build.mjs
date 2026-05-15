#!/usr/bin/env node
/**
 * build.mjs — esbuild driver for picolet-bridge-js.
 *
 * Usage:
 *   node build.mjs            # minified production bundle
 *   node build.mjs --no-minify  # readable output (debugging)
 *
 * Output: dist/picolet-bridge.js (IIFE, browser target, no external deps)
 */

import { build } from "esbuild";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const minify = !process.argv.includes("--no-minify");

const result = await build({
  entryPoints: [resolve(__dirname, "src/index.ts")],
  bundle: true,
  format: "iife",
  // The IIFE global-name is discarded; window.picolet is set from inside
  // the IIFE body.  We need a non-empty name to satisfy esbuild's
  // requirement, but nothing reads __picolet_iife__ from outside.
  globalName: "__picolet_iife__",
  outfile: resolve(__dirname, "dist/picolet-bridge.js"),
  platform: "browser",
  target: "es2019",
  minify,
  metafile: true,
});

const outputs = result.metafile?.outputs ?? {};
for (const [file, info] of Object.entries(outputs)) {
  const kb = (info.bytes / 1024).toFixed(1);
  console.log(`  built ${file} (${kb} kB)`);
}
