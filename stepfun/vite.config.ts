import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = "http://127.0.0.1:8788";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    headers: {
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Embedder-Policy": "require-corp",
    },
    proxy: {
      "/api": backend,
      "/models": backend,
      "/data": backend,
    },
  },
  worker: { format: "es" },
  optimizeDeps: { exclude: ["@huggingface/transformers"] },
  build: { target: "esnext", sourcemap: false },
});
