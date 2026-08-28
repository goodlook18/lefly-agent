import { describe, expect, it } from "vitest";

import stateChanged from "../../../contracts/examples/v1/events/device-state-changed.json";
import { parseConsoleIncoming } from "./protocol";

const state = () => structuredClone(stateChanged.payload);

const hello = () => ({
  type: "console.hello",
  session_id: "session-a",
  lease: { role: "readonly" },
  target_id: "simulator",
  target_epoch: 1,
  state: state(),
});

describe("parseConsoleIncoming", () => {
  it("accepts complete canonical hello and state messages", () => {
    expect(parseConsoleIncoming(hello()).ok).toBe(true);
    expect(parseConsoleIncoming({
      type: "console.state",
      target_id: "simulator",
      target_epoch: 2,
      state: { ...state(), revision: 2 },
    }).ok).toBe(true);
  });

  it("rejects partial, legacy, and stale-decorated device states", () => {
    const canonical = state();
    for (const invalidState of [
      { revision: 2, light: { brightness: 0.4 } },
      { ...canonical, connection: "connected" },
      { ...canonical, stale: true },
    ]) {
      expect(parseConsoleIncoming({ ...hello(), state: invalidState }).ok).toBe(false);
    }
  });

  it("accepts resting and rejects the removed sleeping status", () => {
    const canonical = state();
    expect(parseConsoleIncoming({
      ...hello(),
      state: { ...canonical, status: { mode: "resting" } },
    }).ok).toBe(true);
    expect(parseConsoleIncoming({
      ...hello(),
      state: { ...canonical, status: { mode: "sleeping" } },
    }).ok).toBe(false);
  });

  it("accepts strict known events and valid additive unknown events", () => {
    expect(parseConsoleIncoming({
      type: "console.event",
      target_id: "simulator",
      target_epoch: 1,
      event: stateChanged,
    }).ok).toBe(true);
    expect(parseConsoleIncoming({
      type: "console.event",
      target_id: "simulator",
      target_epoch: 1,
      event: {
        version: "1",
        id: "20000000-0000-4000-8000-000000000099",
        type: "sensor.audio.beat",
        timestamp: "2026-08-17T08:00:00.099Z",
        device_id: "lefly-sim-01",
        payload: { bpm: 120 },
      },
    }).ok).toBe(true);
  });

  it("rejects partial state events and malformed envelopes", () => {
    expect(parseConsoleIncoming({
      type: "console.event",
      target_id: "simulator",
      target_epoch: 1,
      event: {
        ...stateChanged,
        payload: { revision: 2, light: { brightness: 0.4 } },
      },
    }).ok).toBe(false);
    expect(parseConsoleIncoming({
      type: "console.event",
      target_id: "simulator",
      target_epoch: 1,
      event: { ...stateChanged, id: "event-1" },
    }).ok).toBe(false);
  });

  it("validates leases, target epochs, and optional error request IDs", () => {
    expect(parseConsoleIncoming({
      ...hello(),
      lease: { role: "controller", expires_at: 2_000_000_015 },
    }).ok).toBe(true);
    expect(parseConsoleIncoming({
      type: "console.error",
      code: "stale_target_epoch",
      message: "stale",
      recoverable: true,
      request_id: "10000000-0000-4000-8000-000000000001",
    }).ok).toBe(true);
    expect(parseConsoleIncoming({ ...hello(), target_epoch: true }).ok).toBe(false);
    expect(parseConsoleIncoming({
      type: "console.error",
      code: "stale_target_epoch",
      message: "stale",
      recoverable: true,
      request_id: "",
    }).ok).toBe(false);
  });
});
