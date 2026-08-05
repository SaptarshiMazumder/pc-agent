// electron.vite.config.ts
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";
var __electron_vite_injected_import_meta_url = "file:///C:/Users/googler/OneDrive/Desktop/Projects/pc-agent/v2/clients/desktop/electron.vite.config.ts";
var electron_vite_config_default = defineConfig({
  main: { plugins: [externalizeDepsPlugin()] },
  preload: { plugins: [externalizeDepsPlugin()] },
  renderer: {
    root: fileURLToPath(new URL("../ui", __electron_vite_injected_import_meta_url)),
    plugins: [react()],
    build: {
      rollupOptions: { input: fileURLToPath(new URL("../ui/index.html", __electron_vite_injected_import_meta_url)) }
    }
  }
});
export {
  electron_vite_config_default as default
};
