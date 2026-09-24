import React from "react";
import ReactDOM from "react-dom/client";
import "leaflet/dist/leaflet.css";
import "./styles.css";
import App from "./App";
import { AppProvider } from "./context";
import { LiveProvider } from "./live";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppProvider>
      <LiveProvider>
        <App />
      </LiveProvider>
    </AppProvider>
  </React.StrictMode>,
);
