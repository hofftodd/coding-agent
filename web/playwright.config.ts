import { defineConfig } from "@playwright/test";

// Runs against an already-running foreman (see scripts/e2e.sh).
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  use: {
    baseURL: process.env.FOREMAN_URL ?? "http://127.0.0.1:8700",
    screenshot: "only-on-failure",
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
  },
  outputDir: "e2e-results",
});
