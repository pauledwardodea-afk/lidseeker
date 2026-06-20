import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built SPA is served by the FastAPI backend at "/". During dev, proxy
// /api to a locally running backend (default port 5056).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/api": "http://localhost:5056",
    },
  },
});
