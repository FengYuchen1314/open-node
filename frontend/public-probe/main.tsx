import "antd/dist/reset.css";
import "../src/react/styles.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import ProbeView from "../src/react/views/ProbeView";

const container = document.getElementById("app");
if (!container) throw new Error("Public probe root is missing");
createRoot(container).render(<StrictMode><ConfigProvider locale={zhCN}><App className="public-probe-root"><ProbeView publicOnly /></App></ConfigProvider></StrictMode>);
