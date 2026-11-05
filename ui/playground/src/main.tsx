import "@xyflow/react/dist/style.css";
// The agreement UI's design system, shared rather than copied.
import "../../assets/swiss.css";
import "../../assets/aiab.css";
import "./styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import WorkbenchApp from "./workbench/WorkbenchApp";

const RootApp =
  new URLSearchParams(window.location.search).get("workbench") === "1"
    ? WorkbenchApp
    : App;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RootApp />
  </StrictMode>,
);
