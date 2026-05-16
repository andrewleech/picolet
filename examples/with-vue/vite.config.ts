import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  // base './' produces relative asset paths (./assets/main-xxxx.js) which
  // work with both the picolet:// custom scheme (WebKitGTK) and WebView2.
  // An absolute base '/' would break the picolet:// scheme on Windows (F10).
  base: "./",
  // root: the directory containing index.html for this project.
  root: "ui",
  build: {
    // outDir is relative to root (ui/); '../dist' puts output at app_root/dist/
    // which matches picolet.toml [ui.frontend] dist_dir = "dist".
    outDir: "../dist",
    emptyOutDir: true,
  },
});
