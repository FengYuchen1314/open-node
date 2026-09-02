import "./react/styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./react/App";

const container = document.getElementById("app");
if (!container) throw new Error("Application root is missing");
createRoot(container).render(<StrictMode><BrowserRouter><App /></BrowserRouter></StrictMode>);
