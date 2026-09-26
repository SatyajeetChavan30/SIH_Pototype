import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

/** Writes /precache.json: the app-shell file list the service worker caches for offline rig-site use. */
function precacheManifest(): Plugin {
  return {
    name: "stratasense-precache",
    apply: "build",
    generateBundle(_opts, bundle) {
      const built = Object.keys(bundle).filter((f) => !f.endsWith(".map")).map((f) => `/${f}`);
      const statics = ["/", "/index.html", "/manifest.webmanifest", "/favicon.svg", "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"];
      this.emitFile({ type: "asset", fileName: "precache.json",
        source: JSON.stringify({ version: Date.now(), files: [...new Set([...statics, ...built])] }) });
    },
  };
}

// Dev server proxies API + WebSocket to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react(), precacheManifest()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
