import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // 同域提供网页与 /api，与部署形态一致（Nginx 同域转发）。
    // 这样开发期不需要 CORS，Cookie 行为也和生产一致。
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/shared/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
