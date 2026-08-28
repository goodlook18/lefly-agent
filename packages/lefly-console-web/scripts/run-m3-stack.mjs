import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

const STACK_ARGS = ["-m", "tests.support.m3_e2e_stack"];

export function runM3Stack({ host = process, spawnProcess = spawn } = {}) {
  const executable = host.env.LEFLY_PYTHON ?? "python";
  const child = spawnProcess(executable, STACK_ARGS, {
    stdio: "inherit",
    env: host.env,
    shell: false,
  });
  let stopping = false;
  const onSigterm = () => {
    stopping = true;
    child.kill("SIGTERM");
  };
  const onSigint = () => {
    stopping = true;
    child.kill("SIGINT");
  };
  const cleanup = () => {
    host.off("SIGTERM", onSigterm);
    host.off("SIGINT", onSigint);
  };

  host.on("SIGTERM", onSigterm);
  host.on("SIGINT", onSigint);
  child.once("error", (error) => {
    cleanup();
    console.error(`Unable to start LeFly M3 E2E stack: ${error.message}`);
    host.exitCode = 1;
  });
  child.once("exit", (code, signal) => {
    cleanup();
    host.exitCode = stopping ? 0 : code ?? (signal ? 1 : 0);
  });
  return child;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runM3Stack();
}
