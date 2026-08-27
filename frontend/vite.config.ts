import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [vue()],
  // Transform Vuetify's CSS imports for server-rendered component tests.
  test: {
    server: { deps: { inline: ["vuetify"] } },
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
