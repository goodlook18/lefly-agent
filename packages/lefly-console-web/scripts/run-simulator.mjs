import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

export function runSimulator({ host = process, spawnProcess = spawn } = {}) {
  const executable = host.env.LEFLY_PYTHON ?? "python";
  const port = host.env.LEFLY_E2E_SIMULATOR_PORT ?? "18766";
  const child = spawnProcess(executable, [
    "-m",
    "lefly_simulator",
    "--host",
    "127.0.0.1",
    "--port",
    port,
  ], {
    stdio: "inherit",
    env: host.env,
    shell: false,
  });
  const forward = (signal) => child.kill(signal);
  const onSigterm = () => forward("SIGTERM");
  const onSigint = () => forward("SIGINT");
  const cleanup = () => {
    host.off("SIGTERM", onSigterm);
    host.off("SIGINT", onSigint);
  };

  host.on("SIGTERM", onSigterm);
  host.on("SIGINT", onSigint);
  child.once("error", (error) => {
    cleanup();
    console.error(`Unable to start LeFly simulator: ${error.message}`);
    host.exitCode = 1;
  });
  child.once("exit", (code, signal) => {
    cleanup();
    if (signal) host.kill(host.pid, signal);
    else host.exitCode = code ?? 1;
  });
  return child;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  runSimulator();
}
