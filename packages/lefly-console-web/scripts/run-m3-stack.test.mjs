import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { runM3Stack } from "./run-m3-stack.mjs";

function harness(python = "python") {
  const child = new EventEmitter();
  child.killedWith = [];
  child.kill = (signal) => child.killedWith.push(signal);
  const calls = [];
  const host = new EventEmitter();
  host.env = { LEFLY_PYTHON: python, SAFE_VALUE: "preserved" };
  host.exitCode = undefined;
  const spawnProcess = (...args) => {
    calls.push(args);
    return child;
  };
  runM3Stack({ host, spawnProcess });
  return { calls, child, host };
}

test("starts the M3 stack without shell interpretation", () => {
  const dangerous = 'python"; touch /tmp/should-not-run; #';
  const { calls } = harness(dangerous);

  assert.deepEqual(calls, [[
    dangerous,
    ["-m", "tests.support.m3_e2e_stack"],
    {
      stdio: "inherit",
      env: { LEFLY_PYTHON: dangerous, SAFE_VALUE: "preserved" },
      shell: false,
    },
  ]]);
});

test("forwards shutdown and exits cleanly after an expected signal", () => {
  const { child, host } = harness();

  host.emit("SIGTERM");
  assert.deepEqual(child.killedWith, ["SIGTERM"]);
  child.emit("exit", null, "SIGTERM");

  assert.equal(host.exitCode, 0);
  assert.equal(host.listenerCount("SIGTERM"), 0);
  assert.equal(host.listenerCount("SIGINT"), 0);
});
