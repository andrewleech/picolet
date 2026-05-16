import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  // base './' produces relative asset paths (./assets/main-xxxx.js) which
  // work with both the picolet:// custom scheme (WebKitGTK) and WebView2.
  base: "./",
  // root: directory containing index.html for this project.
  root: "ui",
  build: {
    // '../dist' puts output at app_root/dist/ which matches picolet.toml dist_dir.
    outDir: "../dist",
    emptyOutDir: true,
  },
});
