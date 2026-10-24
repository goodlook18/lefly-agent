import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import packageMetadata from "./package.json";
import { resolveDisplayVersionFromConfig } from "./scripts/resolve-display-version.mjs";

const displayVersion = resolveDisplayVersionFromConfig(import.meta.url, packageMetadata.version);

export default defineConfig({
  plugins: [react()],
  define: {
    __LEFLY_DISPLAY_VERSION__: JSON.stringify(displayVersion),
  },
  build: {
    outDir: "../lefly-simulator/src/lefly_simulator/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 550,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          three: ["three"],
          lucide: ["lucide-react"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8766",
      "/ws": {
        target: "ws://127.0.0.1:8766",
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
