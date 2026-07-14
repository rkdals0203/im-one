import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./tests/setup.ts",
    exclude: ["tests/e2e/**", "node_modules/**", "dist/**"],
    pool: "threads",
    maxWorkers: 1,
    fileParallelism: false,
  },
});
