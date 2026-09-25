import React from "react";
import ReactDOM from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "./styles.css";
import App from "./App";
import { AppProvider } from "./context";
import { LiveProvider } from "./live";

// Offline support for the rig-site app (production builds only; the dev server must stay uncached)
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppProvider>
      <LiveProvider>
        <App />
      </LiveProvider>
    </AppProvider>
  </React.StrictMode>,
);
