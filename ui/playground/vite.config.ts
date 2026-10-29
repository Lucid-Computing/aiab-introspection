import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Served at /audit-plan-playground/ both by `pnpm dev` and, after `pnpm build`, by the
// agreement UI (ui/server/app.py serves dist/ behind its login when it exists).
export default defineConfig({
  base: "/audit-plan-playground/",
  plugins: [react()],
  // In agreement mode (?agreement=<id>) the builder reads and saves through the UI server;
  // `pnpm dev` forwards those calls to a local `make dev` on 8090.
  server: {
    port: 5174,
    fs: { allow: ["../assets", "../../plan"] },
    proxy: { "/a/": "http://127.0.0.1:8090", "/login": "http://127.0.0.1:8090" },
  },
  // CodeMirror and React Flow in one bundle; fine for a prototype served locally.
  build: { chunkSizeWarningLimit: 1500 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
