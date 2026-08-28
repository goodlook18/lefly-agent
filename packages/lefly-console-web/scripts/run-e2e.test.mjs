import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { runE2e } from "./run-e2e.mjs";


function fakeHost() {
  const host = new EventEmitter();
  host.argv = ["node", "run-e2e.mjs"];
  host.env = { LEFLY_PYTHON: "/safe/python" };
  host.execPath = "/safe/node";
  host.exitCode = undefined;
  return host;
}


test("runs Console and M3 suites in separate process stacks", () => {
  const host = fakeHost();
  const calls = [];
  const children = [];
  const spawnProcess = (executable, args, options) => {
    const child = new EventEmitter();
    child.kill = () => true;
    calls.push({ executable, args, options });
    children.push(child);
    return child;
  };

  runE2e({ host, spawnProcess, args: ["--project", "desktop-1440x900"] });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].executable, "/safe/node");
  assert.deepEqual(calls[0].args.slice(1, 4), ["test", "--grep-invert", "M3 Agent"]);
  assert.deepEqual(calls[0].args.slice(-2), ["--project", "desktop-1440x900"]);
  assert.equal(calls[0].options.shell, false);

  children[0].emit("exit", 0, null);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].args.slice(1, 3), ["test", "e2e/agent-m3.spec.ts"]);
  assert.equal(calls[1].options.env.LEFLY_E2E_M3, "1");
  assert.deepEqual(calls[1].args.slice(-2), ["--project", "desktop-1440x900"]);

  children[1].emit("exit", 0, null);
  assert.equal(host.exitCode, 0);
});


test("stops after a failed Console suite", () => {
  const host = fakeHost();
  const calls = [];
  const spawnProcess = (_executable, _args, _options) => {
    const child = new EventEmitter();
    child.kill = () => true;
    calls.push(child);
    return child;
  };

  runE2e({ host, spawnProcess });
  calls[0].emit("exit", 2, null);

  assert.equal(calls.length, 1);
  assert.equal(host.exitCode, 2);
});


test("defaults the stateful M3 suite to one desktop project", () => {
  const host = fakeHost();
  const calls = [];
  const children = [];
  const spawnProcess = (_executable, args, _options) => {
    const child = new EventEmitter();
    child.kill = () => true;
    calls.push(args);
    children.push(child);
    return child;
  };

  runE2e({ host, spawnProcess });
  children[0].emit("exit", 0, null);

  assert.deepEqual(calls[1].slice(-2), ["--project", "desktop-1440x900"]);
});
