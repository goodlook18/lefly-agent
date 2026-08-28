import { defineConfig } from "@playwright/test";

import { chromiumLaunchOptions, withLocalNoProxy } from "./playwright.environment";

const launchOptions = chromiumLaunchOptions(process.env.LEFLY_CHROMIUM_EXECUTABLE);
const environment = withLocalNoProxy(process.env);
process.env.NO_PROXY = environment.NO_PROXY;
process.env.no_proxy = environment.no_proxy;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "overview-visual.spec.ts",
  outputDir: "./test-results/playwright-visual",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    ...(launchOptions ? { launchOptions } : {}),
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    timeout: 15_000,
  },
  projects: [
    { name: "desktop-1440x900", use: { viewport: { width: 1440, height: 900 } } },
    { name: "mobile-390x844", use: { viewport: { width: 390, height: 844 } } },
  ],
});
