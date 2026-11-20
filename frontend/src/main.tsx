import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import { App } from "./App";
import { applyTheme, readThemePref } from "./theme";
import "./styles.css";

applyTheme(readThemePref());   // o index.html já aplicou antes da 1ª pintura; aqui só sincroniza (e emite "agf-theme")
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
