import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://nidin-menu-parser:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/health": {
        target: "http://nidin-menu-parser:8000",
        changeOrigin: true,
      },
      "/menus": {
        target: "http://nidin-menu-parser:8000",
        changeOrigin: true,
      },
      "/guest": {
        target: "http://nidin-menu-parser:8000",
        changeOrigin: true,
      },
      "/resolve-store": {
        target: "http://nidin-menu-parser:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
