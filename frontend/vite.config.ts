import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  define: {
    __OPEN_NODE_PUBLIC_PROBE__: JSON.stringify(false),
  },
  // Bound concurrent DOM suites; visual/responsive behavior is tested in Chromium.
  test: {
    maxWorkers: 2,
    testTimeout: 30000,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.OPEN_NODE_DEV_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
