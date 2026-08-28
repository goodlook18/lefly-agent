import { describe, expect, it } from "vitest";

import manifest from "../../../../contracts/fixtures/v1/manifest.json";
import {
  parseDeviceCommand,
  parseDeviceEvent,
  type UnknownDeviceEvent,
} from "../deviceProtocol";

const validCommands = import.meta.glob(
  "../../../../contracts/examples/v1/commands/*.json",
  { eager: true, import: "default" },
);
const validEvents = import.meta.glob(
  "../../../../contracts/examples/v1/events/*.json",
  { eager: true, import: "default" },
);
const invalidFixtures = import.meta.glob(
  "../../../../contracts/fixtures/v1/invalid/*.json",
  { eager: true, import: "default" },
);

describe("Protocol v1 shared fixtures", () => {
  it("accepts every canonical command and event example", () => {
    for (const [path, value] of Object.entries(validCommands)) {
      expect(parseDeviceCommand(value), path).toMatchObject({ ok: true, known: true });
    }
    for (const [path, value] of Object.entries(validEvents)) {
      expect(parseDeviceEvent(value), path).toMatchObject({ ok: true, known: true });
    }
  });

  it("rejects every invalid manifest case", () => {
    for (const fixture of manifest.cases) {
      const suffix = `/contracts/fixtures/v1/${fixture.path}`;
      const entry = Object.entries(invalidFixtures).find(([path]) => path.endsWith(suffix));
      expect(entry, suffix).toBeDefined();
      const result = fixture.kind === "command"
        ? parseDeviceCommand(entry?.[1])
        : parseDeviceEvent(entry?.[1]);
      expect(result.ok, fixture.path).toBe(fixture.valid);
    }
  });

  it("preserves an additive unknown event without treating it as known", () => {
    const result = parseDeviceEvent({
      version: "1",
      id: "20000000-0000-4000-8000-000000000099",
      type: "sensor.audio.beat",
      timestamp: "2026-08-17T08:00:00.099Z",
      device_id: "lefly-sim-01",
      payload: { bpm: 120 },
    });

    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.error.message);
    expect(result.known).toBe(false);
    if (result.ok && !result.known) {
      const event: UnknownDeviceEvent = result.value;
      expect(event.type).toBe("sensor.audio.beat");
      expect(event.payload).toEqual({ bpm: 120 });
    }
  });

  it("does not expose unknown commands as constructible Console commands", () => {
    const result = parseDeviceCommand({
      version: "1",
      id: "10000000-0000-4000-8000-000000000099",
      type: "motion.future_move",
      timestamp: "2026-08-17T08:00:00.099Z",
      device_id: "lefly-sim-01",
      payload: {},
    });

    expect(result).toMatchObject({ ok: true, known: false });
  });
});
