import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const PLAYWRIGHT_CLI = fileURLToPath(
  new URL("../node_modules/@playwright/test/cli.js", import.meta.url),
);

export function runE2e({ host = process, spawnProcess = spawn, args = host.argv.slice(2) } = {}) {
  const hasProject = args.some(
    (argument) => argument === "--project" || argument.startsWith("--project="),
  );
  const m3Args = hasProject
    ? args
    : [...args, "--project", "desktop-1440x900"];
  const suites = [
    {
      args: [PLAYWRIGHT_CLI, "test", "--grep-invert", "M3 Agent", ...args],
      environment: host.env,
    },
    {
      args: [PLAYWRIGHT_CLI, "test", "e2e/agent-m3.spec.ts", ...m3Args],
      environment: { ...host.env, LEFLY_E2E_M3: "1" },
    },
  ];
  let child = null;
  let stopping = false;

  const forward = (signal) => {
    stopping = true;
    child?.kill(signal);
  };
  const onSigterm = () => forward("SIGTERM");
  const onSigint = () => forward("SIGINT");
  const cleanup = () => {
    host.off("SIGTERM", onSigterm);
    host.off("SIGINT", onSigint);
  };
  const start = (index) => {
    const suite = suites[index];
    child = spawnProcess(host.execPath, suite.args, {
      stdio: "inherit",
      env: suite.environment,
      shell: false,
    });
    child.once("error", (error) => {
      cleanup();
      console.error(`Unable to start Playwright E2E suite: ${error.message}`);
      host.exitCode = 1;
    });
    child.once("exit", (code, signal) => {
      if (!stopping && code === 0 && index + 1 < suites.length) {
        start(index + 1);
        return;
      }
      cleanup();
      host.exitCode = stopping ? 0 : code ?? (signal ? 1 : 0);
    });
  };

  host.on("SIGTERM", onSigterm);
  host.on("SIGINT", onSigint);
  start(0);
  return () => child;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runE2e();
}
