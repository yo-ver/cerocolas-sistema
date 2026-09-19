import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // El backend acepta este origen por defecto (CORS_ORIGENES).
    proxy: {
      "/v1": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
