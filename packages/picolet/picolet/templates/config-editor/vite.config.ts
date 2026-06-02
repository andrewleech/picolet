import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  // base './' produces relative asset paths which work with the picolet:// custom
  // scheme (WebKitGTK) and WebView2.
  base: "./",
  // root: the directory containing index.html for this project.
  root: "ui",
  build: {
    // outDir relative to root (ui/); '../dist' puts output at app_root/dist/
    // which matches picolet.toml [ui.frontend] dist_dir = "dist".
    outDir: "../dist",
    emptyOutDir: true,
  },
});
