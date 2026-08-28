import { describe, expect, it } from "vitest";

import stateChanged from "../../../../contracts/examples/v1/events/device-state-changed.json";
import type {
  ConsoleEvent,
  ConsoleHello,
  ConsoleIncoming,
  ConsoleState,
  DeviceCommand,
  DeviceEvent,
  DeviceState,
} from "../protocol";
import {
  COMMAND_RECORD_LIMIT,
  CONSOLE_EVENT_LIMIT,
  ERROR_RECORD_LIMIT,
  type ConsoleAction,
  consoleReducer,
  createInitialConsoleState,
} from "./consoleStore";

const COMMAND_1 = "10000000-0000-4000-8000-000000000001";
const COMMAND_2 = "10000000-0000-4000-8000-000000000002";
let eventSequence = 20;

const deviceState = (revision = 1): DeviceState => ({
  ...structuredClone(stateChanged.payload),
  device_id: "simulator",
  revision,
} as unknown as DeviceState);

const hello = (revision = 1): ConsoleHello => ({
  type: "console.hello",
  session_id: "session-a",
  lease: { role: "controller", expires_at: 10 },
  target_id: "simulator",
  target_epoch: 1,
  state: deviceState(revision),
});

const eventEnvelope = (
  type: string,
  payload: object,
  correlationId?: string,
): DeviceEvent => {
  eventSequence += 1;
  return {
    version: "1",
    id: `20000000-0000-4000-8000-${String(eventSequence).padStart(12, "0")}`,
    type,
    timestamp: "2026-08-17T08:00:00.100Z",
    payload,
    device_id: "simulator",
    ...(correlationId ? { correlation_id: correlationId } : {}),
  } as DeviceEvent;
};

const routedEvent = (event: DeviceEvent, epoch = 1): ConsoleEvent => ({
  type: "console.event",
  target_id: "simulator",
  target_epoch: epoch,
  event,
});

let actionSequence = 0;
const incoming = (message: ConsoleIncoming): ConsoleAction => ({
  type: "incoming",
  message,
  errorId: `error-${++actionSequence}`,
  receivedAt: 1_700_000_000_000 + actionSequence,
});

const initialized = () => consoleReducer(createInitialConsoleState(), incoming(hello()));

const command = (id: string, type: "device.get_state" | "light.solid" | "motion.absolute_move"): DeviceCommand => {
  if (type === "motion.absolute_move") {
    return {
      version: "1", id, type, timestamp: "2026-08-17T08:00:00.000Z", device_id: "simulator",
      payload: { joints: { base_yaw: 20 }, duration_ms: 300 },
    };
  }
  if (type === "light.solid") {
    return {
      version: "1", id, type, timestamp: "2026-08-17T08:00:00.000Z", device_id: "simulator",
      payload: { target: "head_matrix", color: "#FF0000" },
    };
  }
  return {
    version: "1", id, type, timestamp: "2026-08-17T08:00:00.000Z", device_id: "simulator", payload: {},
  };
};

describe("consoleReducer", () => {
  it("initializes a detached online session from a complete hello", () => {
    const message = hello();
    const state = consoleReducer(createInitialConsoleState(), incoming(message));
    message.state.motion.joints.base_yaw.pos = 55;

    expect(state.connection).toBe("online");
    expect(state.lease).toEqual({ role: "controller", expiresAt: 10 });
    expect(state.deviceState?.motion.joints.base_yaw.pos).toBe(0);
  });

  it("replaces a continuous full snapshot without retaining old fields", () => {
    const initial = hello();
    initial.state.extensions = { "vendor.old": true };
    const before = consoleReducer(createInitialConsoleState(), incoming(initial));
    const replacement = deviceState(2);
    replacement.light.brightness = 0.4;
    replacement.motion.joints.base_yaw.pos = 12;

    const state = consoleReducer(before, incoming(routedEvent(
      eventEnvelope("device.state_changed", replacement, COMMAND_1),
    )));

    expect(state.deviceState?.revision).toBe(2);
    expect(state.deviceState?.light.brightness).toBe(0.4);
    expect(state.deviceState?.motion.joints.base_yaw.pos).toBe(12);
    expect(state.deviceState).not.toHaveProperty("extensions");
  });

  it("marks a revision gap stale and waits for an authoritative full state", () => {
    const gap = deviceState(3);
    gap.light.brightness = 0.2;
    const stale = consoleReducer(initialized(), incoming(routedEvent(
      eventEnvelope("device.state_changed", gap),
    )));
    expect(stale.connection).toBe("stale");
    expect(stale.deviceState?.revision).toBe(1);
    expect(stale.lastError?.code).toBe("revision_gap");

    const recovered = consoleReducer(stale, incoming({
      type: "console.state",
      target_id: "simulator",
      target_epoch: 1,
      state: deviceState(3),
    }));
    expect(recovered.connection).toBe("online");
    expect(recovered.deviceState?.revision).toBe(3);
    expect(recovered.lastError).toBeNull();
  });

  it("ignores old epochs and lower or duplicate revisions", () => {
    const current = initialized();
    for (const [revision, epoch] of [[2, 0], [0, 1], [1, 1]] as const) {
      expect(consoleReducer(current, incoming(routedEvent(
        eventEnvelope("device.state_changed", deviceState(revision)), epoch,
      )))).toBe(current);
    }
  });

  it("accepts a complete state replacement for a newer target epoch", () => {
    const message: ConsoleState = {
      type: "console.state",
      target_id: "remote",
      target_epoch: 2,
      state: { ...deviceState(9), device_id: "remote-device" },
    };
    const state = consoleReducer(initialized(), incoming(message));
    expect(state.targetEpoch).toBe(2);
    expect(state.deviceState?.device_id).toBe("remote-device");
  });

  it("records an additive unknown event without mutating state", () => {
    const before = initialized();
    const state = consoleReducer(before, incoming({
      type: "console.event",
      target_id: "simulator",
      target_epoch: 1,
      event: {
        version: "1",
        id: "20000000-0000-4000-8000-000000000099",
        type: "sensor.audio.beat",
        timestamp: "2026-08-17T08:00:00.099Z",
        device_id: "simulator",
        payload: { bpm: 120 },
      },
    }));
    expect(state.deviceState).toEqual(before.deviceState);
    expect(state.events.at(-1)?.event.type).toBe("sensor.audio.beat");
  });

  it("keeps the last target snapshot stale on disconnect", () => {
    const before = initialized();
    const state = consoleReducer(before, { type: "closed" });
    expect(state.connection).toBe("stale");
    expect(state.deviceState).toEqual(before.deviceState);
  });

  it("moves to readonly when control is lost", () => {
    const state = consoleReducer(initialized(), incoming({
      type: "console.error", code: "invalid_control_lease", message: "expired", recoverable: true,
    }));
    expect(state.lease).toEqual({ role: "readonly" });
    expect(state.lastError?.code).toBe("invalid_control_lease");
  });

  it("correlates a complete motion lifecycle", () => {
    let state = consoleReducer(initialized(), { type: "local_command_sent", command: command(COMMAND_1, "motion.absolute_move") });
    const lifecycle = [
      ["command.accepted", { command_type: "motion.absolute_move", disposition: "queued" }],
      ["motion.started", { command_type: "motion.absolute_move", action: "absolute_move", duration_ms: 300 }],
      ["motion.progress", { action: "absolute_move", progress: 0.5, elapsed_ms: 150, joints: { base_yaw: 10 } }],
      ["motion.finished", { action: "absolute_move", status: "completed", elapsed_ms: 300, joints: { base_yaw: 20 }, reason: null, error: null }],
    ] as const;
    for (const [type, payload] of lifecycle) {
      state = consoleReducer(state, incoming(routedEvent(eventEnvelope(type, payload, COMMAND_1))));
    }
    expect(state.commandTimeline[0]).toMatchObject({ status: "finished", progress: 0.5 });
    expect(state.pendingCommands).toEqual([]);
  });

  it("correlates structured device errors and console request errors", () => {
    let state = consoleReducer(initialized(), { type: "local_command_sent", command: command(COMMAND_1, "light.solid") });
    state = consoleReducer(state, { type: "local_command_sent", command: command(COMMAND_2, "device.get_state") });
    state = consoleReducer(state, incoming(routedEvent(eventEnvelope("device.error", {
      code: "light_failed", message: "blocked", recoverable: true, details: null,
    }, COMMAND_1))));
    state = consoleReducer(state, incoming({
      type: "console.error", code: "stale_target_epoch", message: "stale", recoverable: true, request_id: COMMAND_2,
    }));
    expect(state.commandTimeline.map((item) => item.status)).toEqual(["failed", "failed"]);
    expect(state.pendingCommands).toEqual([]);
  });

  it("bounds event, command, and error histories", () => {
    let state = initialized();
    for (let index = 0; index < CONSOLE_EVENT_LIMIT + 5; index += 1) {
      state = consoleReducer(state, incoming({
        type: "console.event", target_id: "simulator", target_epoch: 1,
        event: {
          version: "1", id: `20000000-0000-4000-8001-${String(index).padStart(12, "0")}`,
          type: "sensor.audio.beat", timestamp: "2026-08-17T08:00:00.099Z",
          device_id: "simulator", payload: { index },
        },
      }));
    }
    for (let index = 0; index < COMMAND_RECORD_LIMIT + 5; index += 1) {
      const id = `10000000-0000-4000-8001-${String(index).padStart(12, "0")}`;
      state = consoleReducer(state, { type: "local_command_sent", command: command(id, "device.get_state") });
    }
    for (let index = 0; index < ERROR_RECORD_LIMIT + 5; index += 1) {
      state = consoleReducer(state, {
        type: "protocol_error",
        error: { type: "console.error", code: "invalid_message", message: `bad-${index}`, recoverable: true },
        errorId: `protocol-${index}`,
        receivedAt: index,
      });
    }
    expect(state.events).toHaveLength(CONSOLE_EVENT_LIMIT);
    expect(state.pendingCommands).toHaveLength(COMMAND_RECORD_LIMIT);
    expect(state.commandTimeline).toHaveLength(COMMAND_RECORD_LIMIT);
    expect(state.errors).toHaveLength(ERROR_RECORD_LIMIT);
  });
});
