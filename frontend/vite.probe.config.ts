import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  root: fileURLToPath(new URL("./public-probe", import.meta.url)),
  publicDir: false,
  plugins: [react()],
  define: {
    __OPEN_NODE_PUBLIC_PROBE__: JSON.stringify(true),
    "import.meta.env.VITE_API_BASE_URL": JSON.stringify(""),
  },
  build: {
    outDir: fileURLToPath(new URL("./dist-probe", import.meta.url)),
    emptyOutDir: true,
  },
});
