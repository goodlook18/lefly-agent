import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromiumLaunchOptions, withLocalNoProxy } from "./playwright.environment";

const consoleDir = fileURLToPath(new URL(".", import.meta.url));
const repositoryRoot = path.resolve(consoleDir, "../..");
const launchOptions = chromiumLaunchOptions(process.env.LEFLY_CHROMIUM_EXECUTABLE);
const environment = withLocalNoProxy(process.env);
const simulatorPort = process.env.LEFLY_E2E_SIMULATOR_PORT ?? "18766";
process.env.NO_PROXY = environment.NO_PROXY;
process.env.no_proxy = environment.no_proxy;
const packageSources = [
  "packages/lefly-protocol/src",
  "packages/lefly-sdk-python/src",
  "packages/lefly-simulator/src",
  "packages/lefly-agent/src",
].map((source) => path.join(repositoryRoot, source));
const runM3Stack = process.env.LEFLY_E2E_M3 === "1"
  || process.argv.some((argument) => argument.includes("M3 Agent"));
const useInstalledPackages = process.env.LEFLY_E2E_USE_INSTALLED === "1";
const pythonPath = useInstalledPackages
  ? process.env.PYTHONPATH
  : [
      ...packageSources,
      ...(process.env.PYTHONPATH ? [process.env.PYTHONPATH] : []),
    ].join(path.delimiter);

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results/playwright",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: `http://127.0.0.1:${simulatorPort}`,
    browserName: "chromium",
    ...(launchOptions ? { launchOptions } : {}),
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: runM3Stack ? "node scripts/run-m3-stack.mjs" : "node scripts/run-simulator.mjs",
    cwd: consoleDir,
    env: {
      ...environment,
      LEFLY_E2E_SIMULATOR_PORT: simulatorPort,
      ...(pythonPath
        ? { PYTHONPATH: [repositoryRoot, pythonPath].join(path.delimiter) }
        : { PYTHONPATH: repositoryRoot }),
    },
    url: runM3Stack
      ? "http://127.0.0.1:8767/health"
      : `http://127.0.0.1:${simulatorPort}/health`,
    reuseExistingServer: false,
    timeout: 15_000,
  },
  projects: [
    { name: "desktop-1440x900", use: { viewport: { width: 1440, height: 900 } } },
    { name: "tablet-820x1180", use: { viewport: { width: 820, height: 1180 } } },
    { name: "mobile-390x844", use: { viewport: { width: 390, height: 844 } } },
  ],
});
