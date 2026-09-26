import React from "react";
import ReactDOM from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "./styles.css";
import App from "./App";
import { AuthGate } from "./auth";
import { AppProvider } from "./context";
import { LiveProvider } from "./live";

// Browser storage from before the rename to StrataSense: move "nwis.*" keys over once, so a rig-site
// outbox with unsent entries (and the cached user, meta and snapshot) survives the upgrade.
try {
  for (const k of ["user", "meta", "actor", "rigSnapshot", "outbox", "streamMode"]) {
    const old = localStorage.getItem(`nwis.${k}`);
    if (old !== null && localStorage.getItem(`stratasense.${k}`) === null) localStorage.setItem(`stratasense.${k}`, old);
    localStorage.removeItem(`nwis.${k}`);
  }
} catch { /* storage blocked: nothing to migrate */ }

// Offline support for the rig-site app (production builds only; the dev server must stay uncached)
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate>
      <AppProvider>
        <LiveProvider>
          <App />
        </LiveProvider>
      </AppProvider>
    </AuthGate>
  </React.StrictMode>,
);
