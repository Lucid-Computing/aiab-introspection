import "@xyflow/react/dist/style.css";
// The agreement UI's design system, shared rather than copied.
import "../../assets/swiss.css";
import "../../assets/aiab.css";
import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
