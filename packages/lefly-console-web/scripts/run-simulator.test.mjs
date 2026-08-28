import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { runSimulator } from "./run-simulator.mjs";

function harness(python = "python") {
  const child = new EventEmitter();
  child.kill = (signal) => child.killedWith.push(signal);
  child.killedWith = [];
  const calls = [];
  const host = new EventEmitter();
  host.env = { LEFLY_PYTHON: python, SAFE_VALUE: "preserved" };
  host.pid = 42;
  host.exitCode = undefined;
  host.kill = (pid, signal) => host.killedWith = [pid, signal];
  const spawnProcess = (...args) => {
    calls.push(args);
    return child;
  };
  runSimulator({ host, spawnProcess });
  return { calls, child, host };
}

test("passes dangerous Python paths as an executable without a shell", () => {
  const dangerous = 'python"; touch /tmp/should-not-run; #';
  const { calls } = harness(dangerous);

  assert.deepEqual(calls, [[
    dangerous,
    ["-m", "lefly_simulator", "--host", "127.0.0.1", "--port", "18766"],
    { stdio: "inherit", env: { LEFLY_PYTHON: dangerous, SAFE_VALUE: "preserved" }, shell: false },
  ]]);
});

test("forwards termination signals and the child exit code", () => {
  const { child, host } = harness();

  host.emit("SIGTERM");
  host.emit("SIGINT");
  assert.deepEqual(child.killedWith, ["SIGTERM", "SIGINT"]);

  child.emit("exit", 7, null);
  assert.equal(host.exitCode, 7);
  assert.equal(host.listenerCount("SIGTERM"), 0);
  assert.equal(host.listenerCount("SIGINT"), 0);
});
