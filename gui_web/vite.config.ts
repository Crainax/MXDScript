import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const apiTarget = resolveApiTarget();

export default defineConfig({
  base: "./",
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: "127.0.0.1",
    proxy: apiTarget
      ? {
          "/api": {
            target: apiTarget,
            changeOrigin: true,
          },
        }
      : undefined,
  },
});

function resolveApiTarget(): string | undefined {
  const urlFile = resolve(__dirname, "..", ".mxd_http_url");
  if (!existsSync(urlFile)) {
    return undefined;
  }
  const raw = readFileSync(urlFile, "utf-8").trim();
  return raw ? raw.replace(/\/$/, "") : undefined;
}
