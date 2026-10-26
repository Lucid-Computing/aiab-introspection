import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Served at /audit-plan-playground/ both by `pnpm dev` and, after `pnpm build`, by the
// agreement UI (ui/server/app.py serves dist/ behind its login when it exists).
export default defineConfig({
  base: "/audit-plan-playground/",
  plugins: [react()],
  server: { port: 5174, fs: { allow: [".."] } },
  // CodeMirror and React Flow in one bundle; fine for a prototype served locally.
  build: { chunkSizeWarningLimit: 1500 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
