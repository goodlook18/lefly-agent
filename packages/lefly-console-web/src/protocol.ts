import {
  isDeviceState,
  parseDeviceEvent,
  type DeviceCommand,
  type DeviceEvent,
  type DeviceState,
  type UnknownDeviceEvent,
} from "./deviceProtocol";

export type JsonObject = Record<string, unknown>;
export type { DeviceCommand, DeviceEvent, DeviceState } from "./deviceProtocol";

export type ConsoleLease =
  | { role: "readonly" }
  | { role: "controller"; expires_at: number };

export interface RouterError {
  code: string;
  message: string;
  recoverable: boolean;
}

export interface ConsoleHello {
  type: "console.hello";
  session_id: string;
  lease: ConsoleLease;
  target_id: string | null;
  target_epoch: number;
  state: DeviceState;
}

export interface ConsoleState {
  type: "console.state";
  target_id: string | null;
  target_epoch: number;
  state: DeviceState;
}

export interface ConsoleEvent {
  type: "console.event";
  target_id: string;
  target_epoch: number;
  event: DeviceEvent | UnknownDeviceEvent;
  error?: RouterError;
}

export interface ConsoleError {
  type: "console.error";
  code: string;
  message: string;
  recoverable: boolean;
  request_id?: string;
}

export interface ConsoleControl {
  type: "console.control";
  lease: ConsoleLease;
}

export type ConsoleIncoming =
  | ConsoleHello
  | ConsoleState
  | ConsoleEvent
  | ConsoleError
  | ConsoleControl;

export type ConsoleCommand = {
  type: "console.command";
  target_epoch: number;
  command: DeviceCommand;
};
export type ConsoleSelectTarget = {
  type: "console.select_target";
  target_id: string;
};
export type ConsoleInjectSensor = {
  type: "console.inject_sensor";
  sensor_type: string;
  payload: JsonObject;
};
export type ConsoleRenewControl = { type: "console.renew_control" };
export type ConsoleAcquireControl = { type: "console.acquire_control" };
export type ConsoleOutgoing =
  | ConsoleCommand
  | ConsoleSelectTarget
  | ConsoleInjectSensor
  | ConsoleRenewControl
  | ConsoleAcquireControl;

export type ParseResult =
  | { ok: true; value: ConsoleIncoming }
  | { ok: false; error: string };

export function parseConsoleIncoming(value: unknown): ParseResult {
  if (!isObject(value) || typeof value.type !== "string") {
    return invalid("message must be an object with a string type");
  }

  switch (value.type) {
    case "console.hello":
      if (!text(value.session_id) || !isLease(value.lease) || !targetId(value.target_id) ||
          !integer(value.target_epoch) || !isDeviceState(value.state)) {
        return invalid("invalid console.hello core fields");
      }
      return valid(value as unknown as ConsoleHello);
    case "console.state":
      if (!targetId(value.target_id) || !integer(value.target_epoch) || !isDeviceState(value.state)) {
        return invalid("invalid console.state core fields");
      }
      return valid(value as unknown as ConsoleState);
    case "console.event": {
      if (!text(value.target_id) || !integer(value.target_epoch) ||
          (value.error !== undefined && !isRouterError(value.error))) {
        return invalid("invalid console.event core fields");
      }
      const parsed = parseDeviceEvent(value.event);
      if (!parsed.ok) return invalid(parsed.error.message);
      return valid({
        type: "console.event",
        target_id: value.target_id,
        target_epoch: value.target_epoch,
        event: parsed.value,
        ...(value.error === undefined ? {} : { error: value.error }),
      });
    }
    case "console.error":
      if (!text(value.code) || !text(value.message) || typeof value.recoverable !== "boolean" ||
          (value.request_id !== undefined && !text(value.request_id))) {
        return invalid("invalid console.error core fields");
      }
      return valid(value as unknown as ConsoleError);
    case "console.control":
      return isLease(value.lease)
        ? valid(value as unknown as ConsoleControl)
        : invalid("invalid console.control lease");
    default:
      return invalid(`unsupported incoming message type: ${value.type}`);
  }
}

function isLease(value: unknown): value is ConsoleLease {
  if (!isObject(value)) return false;
  if (value.role === "readonly") return true;
  return value.role === "controller" && finite(value.expires_at);
}

function isRouterError(value: unknown): value is RouterError {
  return isObject(value) && text(value.code) && text(value.message) &&
    typeof value.recoverable === "boolean";
}

function targetId(value: unknown): value is string | null {
  return value === null || text(value);
}

function integer(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function valid(value: ConsoleIncoming): ParseResult {
  return { ok: true, value };
}

function invalid(error: string): ParseResult {
  return { ok: false, error };
}
