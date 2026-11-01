import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const target = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";
const proxy = { "/api": { target, ws: true, rewrite: (p: string) => p.replace(/^\/api/, "") } };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  build: { chunkSizeWarningLimit: 1200 },
  test: { environment: "jsdom" },
});
