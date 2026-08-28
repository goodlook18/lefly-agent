export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export const COMMAND_TYPES = [
  "motion.play",
  "motion.relative_move",
  "motion.absolute_move",
  "light.solid",
  "light.paint",
  "light.brightness",
  "status.set",
  "device.rest",
  "device.get_state",
] as const;

export const EVENT_TYPES = [
  "command.accepted",
  "sensor.touch",
  "sensor.vision.gesture",
  "sensor.vision.face",
  "motion.started",
  "motion.progress",
  "motion.finished",
  "device.state_changed",
  "device.error",
] as const;

export const STATUS_MODES = [
  "starting",
  "resting",
  "active",
  "listening",
  "thinking",
  "speaking",
  "error",
] as const;

export const STATUS_EFFECTS = [
  "fade",
  "breath",
  "solid",
  "marquee",
  "level_sweep",
  "blink",
] as const;

export type CommandType = (typeof COMMAND_TYPES)[number];
export type EventType = (typeof EVENT_TYPES)[number];
export type StatusMode = (typeof STATUS_MODES)[number];
export type StatusEffect = (typeof STATUS_EFFECTS)[number];
export type DeviceConnection = "connecting" | "ready" | "degraded" | "offline";
export type MotionState = "idle" | "moving" | "error";
export type CommandScope = "control" | "system";
export type Extensions = Record<string, JsonValue>;

export interface ProtocolError {
  code: string;
  message: string;
  recoverable: boolean;
  details: JsonObject | null;
}

export interface JointState {
  pos: number;
  min: number;
  max: number;
}

export interface CapabilitySet {
  commands: Record<string, { scope: CommandScope }>;
  events: string[];
  motion: {
    joints: string[];
    presets: Array<{ name: string; label: string | null }>;
  };
  lights: Array<{
    target: string;
    kind: "rgb_matrix";
    width: number;
    height: number;
  }>;
}

export interface DeviceState {
  device_id: string;
  revision: number;
  connection: DeviceConnection;
  capabilities: CapabilitySet;
  motion: {
    state: MotionState;
    action: string | null;
    joints: Record<string, JointState>;
  };
  light: {
    brightness: number;
    matrix: { width: number; height: number };
    pixels: string[];
  };
  status: { mode: StatusMode };
  status_strip: { color: string; effect: StatusEffect };
  command_queue: { size: number; capacity: number };
  extensions?: Extensions;
}

interface Envelope<TType extends string, TPayload extends object> {
  version: "1";
  id: string;
  type: TType;
  timestamp: string;
  device_id: string;
  payload: TPayload;
}

type WithExtensions<T extends object> = T & { extensions?: Extensions };
type JointCommandMap = Record<string, number>;

export type DeviceCommand =
  | Envelope<"motion.play", WithExtensions<{ name: string }>>
  | Envelope<"motion.relative_move", WithExtensions<{ joints: JointCommandMap; duration_ms: number }>>
  | Envelope<"motion.absolute_move", WithExtensions<{ joints: JointCommandMap; duration_ms: number }>>
  | Envelope<"light.solid", WithExtensions<{ target: "head_matrix"; color: string }>>
  | Envelope<"light.paint", WithExtensions<{ target: "head_matrix"; pixels: string[] }>>
  | Envelope<"light.brightness", WithExtensions<{ target: "head_matrix"; brightness: number }>>
  | Envelope<"status.set", WithExtensions<{ mode: StatusMode }>>
  | Envelope<"device.rest", { extensions?: Extensions }>
  | Envelope<"device.get_state", { extensions?: Extensions }>;

export type UnknownDeviceCommand = Envelope<string, JsonObject>;

type CorrelatedEnvelope<TType extends string, TPayload extends object> =
  Envelope<TType, TPayload> & { correlation_id: string };
type OptionalCorrelationEnvelope<TType extends string, TPayload extends object> =
  Envelope<TType, TPayload> & { correlation_id?: string };

export type DeviceEvent =
  | CorrelatedEnvelope<"command.accepted", WithExtensions<{
      command_type: CommandType;
      disposition: "applied" | "queued";
    }>>
  | Envelope<"sensor.touch", WithExtensions<{
      position: "left" | "middle" | "right";
      pressed: boolean;
    }>>
  | Envelope<"sensor.vision.gesture" | "sensor.vision.face", WithExtensions<{
      id: number;
      label: string | null;
      confidence: number | null;
    }>>
  | CorrelatedEnvelope<"motion.started", WithExtensions<{
      command_type: "motion.play" | "motion.relative_move" | "motion.absolute_move" | "device.rest";
      action: string;
      duration_ms: number;
    }>>
  | CorrelatedEnvelope<"motion.progress", WithExtensions<{
      action: string;
      progress: number;
      elapsed_ms: number;
      joints: JointCommandMap;
    }>>
  | CorrelatedEnvelope<"motion.finished", WithExtensions<{
      action: string;
      status: "completed" | "cancelled" | "failed";
      elapsed_ms: number;
      joints: JointCommandMap;
      reason: string | null;
      error: ProtocolError | null;
    }>>
  | OptionalCorrelationEnvelope<"device.state_changed", DeviceState>
  | OptionalCorrelationEnvelope<"device.error", ProtocolError>;

export type UnknownDeviceEvent = OptionalCorrelationEnvelope<string, JsonObject>;

export type DeviceParseResult<TKnown, TUnknown> =
  | { ok: true; known: true; value: TKnown }
  | { ok: true; known: false; value: TUnknown }
  | { ok: false; error: ProtocolError };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const DEVICE_ID = /^[a-z][a-z0-9_-]{0,63}$/;
const MESSAGE_TYPE = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/;
const ACTION_ID = /^[a-z][a-z0-9_]{0,63}$/;
const COLOR = /^#[0-9A-F]{6}$/;
const EXTENSION = /^[a-z][a-z0-9-]*(\.[a-z][a-z0-9_-]*)+$/;
const COMMAND_SET = new Set<string>(COMMAND_TYPES);
const EVENT_SET = new Set<string>(EVENT_TYPES);
const STATUS_MODE_SET = new Set<string>(STATUS_MODES);
const STATUS_EFFECT_SET = new Set<string>(STATUS_EFFECTS);

export function parseDeviceCommand(
  value: unknown,
): DeviceParseResult<DeviceCommand, UnknownDeviceCommand> {
  const envelope = validateEnvelope(value, false);
  if (!envelope.ok) return envelope;
  if (!COMMAND_SET.has(envelope.value.type)) {
    return { ok: true, known: false, value: envelope.value as UnknownDeviceCommand };
  }
  const error = validateCommandPayload(envelope.value.type as CommandType, envelope.value.payload);
  return error
    ? { ok: false, error }
    : { ok: true, known: true, value: envelope.value as DeviceCommand };
}

export function parseDeviceEvent(
  value: unknown,
): DeviceParseResult<DeviceEvent, UnknownDeviceEvent> {
  const envelope = validateEnvelope(value, true);
  if (!envelope.ok) return envelope;
  if (!EVENT_SET.has(envelope.value.type)) {
    return { ok: true, known: false, value: envelope.value as UnknownDeviceEvent };
  }
  const error = validateEventPayload(
    envelope.value.type as EventType,
    envelope.value.payload,
    envelope.value.correlation_id,
    envelope.value.device_id,
  );
  return error
    ? { ok: false, error }
    : { ok: true, known: true, value: envelope.value as DeviceEvent };
}

export function isDeviceState(value: unknown, envelopeDeviceId?: string): value is DeviceState {
  if (!isObject(value) || !keys(value, [
    "device_id", "revision", "connection", "capabilities", "motion", "light",
    "status", "status_strip", "command_queue",
  ], ["extensions"])) return false;
  if (!matches(value.device_id, DEVICE_ID) ||
      (envelopeDeviceId !== undefined && value.device_id !== envelopeDeviceId) ||
      !integer(value.revision, 0) ||
      !oneOf(value.connection, ["connecting", "ready", "degraded", "offline"]) ||
      !isCapabilities(value.capabilities) ||
      !isMotionState(value.motion, value.capabilities.motion.joints) ||
      !isLightState(value.light, value.capabilities) ||
      !isObject(value.status) || !keys(value.status, ["mode"]) ||
      !oneOf(value.status.mode, STATUS_MODES) ||
      !isObject(value.status_strip) || !keys(value.status_strip, ["color", "effect"]) ||
      !matches(value.status_strip.color, COLOR) ||
      !oneOf(value.status_strip.effect, STATUS_EFFECTS) ||
      !isObject(value.command_queue) || !keys(value.command_queue, ["size", "capacity"]) ||
      !integer(value.command_queue.size, 0) || !integer(value.command_queue.capacity, 0) ||
      value.command_queue.size > value.command_queue.capacity ||
      !validExtensions(value.extensions)) return false;
  return true;
}

function validateEnvelope(
  value: unknown,
  allowCorrelation: boolean,
): { ok: true; value: UnknownDeviceEvent } | { ok: false; error: ProtocolError } {
  const allowed = allowCorrelation
    ? ["version", "id", "type", "timestamp", "device_id", "payload", "correlation_id"]
    : ["version", "id", "type", "timestamp", "device_id", "payload"];
  if (!isObject(value) || !keys(value, ["version", "id", "type", "timestamp", "device_id", "payload"], allowed.slice(6))) {
    return failure("invalid_envelope", "message envelope fields are invalid");
  }
  if (value.version !== "1" || !matches(value.id, UUID) || !matches(value.type, MESSAGE_TYPE) ||
      !validTimestamp(value.timestamp) || !matches(value.device_id, DEVICE_ID) ||
      !isObject(value.payload) ||
      (value.correlation_id !== undefined && !matches(value.correlation_id, UUID))) {
    return failure("invalid_envelope", "message envelope values are invalid");
  }
  return { ok: true, value: value as unknown as UnknownDeviceEvent };
}

function validateCommandPayload(type: CommandType, payload: JsonObject): ProtocolError | null {
  let valid = false;
  switch (type) {
    case "motion.play":
      valid = keys(payload, ["name"], ["extensions"]) && matches(payload.name, ACTION_ID);
      break;
    case "motion.relative_move":
    case "motion.absolute_move":
      valid = keys(payload, ["joints", "duration_ms"], ["extensions"]) &&
        jointNumbers(payload.joints, true) && integer(payload.duration_ms, 1, 60_000);
      break;
    case "light.solid":
      valid = keys(payload, ["target", "color"], ["extensions"]) &&
        payload.target === "head_matrix" && matches(payload.color, COLOR);
      break;
    case "light.paint":
      valid = keys(payload, ["target", "pixels"], ["extensions"]) &&
        payload.target === "head_matrix" && colors(payload.pixels);
      break;
    case "light.brightness":
      valid = keys(payload, ["target", "brightness"], ["extensions"]) &&
        payload.target === "head_matrix" && number(payload.brightness, 0, 1);
      break;
    case "status.set":
      valid = keys(payload, ["mode"], ["extensions"]) && oneOf(payload.mode, STATUS_MODES);
      break;
    case "device.rest":
    case "device.get_state":
      valid = keys(payload, [], ["extensions"]);
      break;
  }
  return valid && validExtensions(payload.extensions)
    ? null
    : protocolError("invalid_command", `invalid payload for ${type}`);
}

function validateEventPayload(
  type: EventType,
  payload: JsonObject,
  correlationId: JsonValue | undefined,
  deviceId: string,
): ProtocolError | null {
  const requiresCorrelation = ["command.accepted", "motion.started", "motion.progress", "motion.finished"].includes(type);
  if (requiresCorrelation && typeof correlationId !== "string") return protocolError("invalid_event", `${type} requires correlation_id`);
  if (type.startsWith("sensor.") && correlationId !== undefined) return protocolError("invalid_event", `${type} prohibits correlation_id`);
  let valid = false;
  switch (type) {
    case "command.accepted":
      valid = keys(payload, ["command_type", "disposition"], ["extensions"]) &&
        oneOf(payload.command_type, COMMAND_TYPES) && oneOf(payload.disposition, ["applied", "queued"]);
      break;
    case "sensor.touch":
      valid = keys(payload, ["position", "pressed"], ["extensions"]) &&
        oneOf(payload.position, ["left", "middle", "right"]) && typeof payload.pressed === "boolean";
      break;
    case "sensor.vision.gesture":
    case "sensor.vision.face":
      valid = keys(payload, ["id", "label", "confidence"], ["extensions"]) &&
        integer(payload.id, 0) && (payload.label === null || text(payload.label)) &&
        (payload.confidence === null || number(payload.confidence, 0, 1));
      break;
    case "motion.started": {
      valid = keys(payload, ["command_type", "action", "duration_ms"], ["extensions"]) &&
        oneOf(payload.command_type, ["motion.play", "motion.relative_move", "motion.absolute_move", "device.rest"]) &&
        matches(payload.action, ACTION_ID) && integer(payload.duration_ms, 1, 60_000);
      const expected: Record<string, string> = {
        "motion.relative_move": "relative_move",
        "motion.absolute_move": "absolute_move",
        "device.rest": "rest",
      };
      valid = valid && (expected[String(payload.command_type)] === undefined || expected[String(payload.command_type)] === payload.action);
      break;
    }
    case "motion.progress":
      valid = keys(payload, ["action", "progress", "elapsed_ms", "joints"], ["extensions"]) &&
        matches(payload.action, ACTION_ID) && number(payload.progress, 0, 1) &&
        integer(payload.elapsed_ms, 0) && jointNumbers(payload.joints);
      break;
    case "motion.finished":
      valid = isMotionFinished(payload);
      break;
    case "device.state_changed":
      valid = isDeviceState(payload, deviceId);
      break;
    case "device.error":
      valid = isProtocolError(payload);
      break;
  }
  return valid && validExtensions(payload.extensions)
    ? null
    : protocolError("invalid_event", `invalid payload for ${type}`);
}

function isCapabilities(value: unknown): value is CapabilitySet {
  if (!isObject(value) || !keys(value, ["commands", "events", "motion", "lights"]) ||
      !isObject(value.commands) || !Array.isArray(value.events) ||
      !isObject(value.motion) || !keys(value.motion, ["joints", "presets"]) ||
      !Array.isArray(value.motion.joints) || !Array.isArray(value.motion.presets) ||
      !Array.isArray(value.lights)) return false;
  if (!Object.entries(value.commands).every(([name, metadata]) =>
    MESSAGE_TYPE.test(name) && isObject(metadata) && keys(metadata, ["scope"]) && oneOf(metadata.scope, ["control", "system"]))) return false;
  if (!uniqueMatches(value.events, MESSAGE_TYPE) || !uniqueMatches(value.motion.joints, ACTION_ID)) return false;
  const presetNames: string[] = [];
  for (const preset of value.motion.presets) {
    if (!isObject(preset) || !keys(preset, ["name", "label"]) || !matches(preset.name, ACTION_ID) ||
        (preset.label !== null && !text(preset.label))) return false;
    presetNames.push(preset.name as string);
  }
  if (new Set(presetNames).size !== presetNames.length) return false;
  return value.lights.every((light) => isObject(light) && keys(light, ["target", "kind", "width", "height"]) &&
    matches(light.target, ACTION_ID) && light.kind === "rgb_matrix" && integer(light.width, 1) && integer(light.height, 1));
}

function isMotionState(value: unknown, jointNames: string[]): boolean {
  if (!isObject(value) || !keys(value, ["state", "action", "joints"]) ||
      !oneOf(value.state, ["idle", "moving", "error"]) || !isObject(value.joints)) return false;
  if (value.state === "moving" ? !matches(value.action, ACTION_ID) : value.action !== null) return false;
  const names = Object.keys(value.joints);
  if (names.length !== jointNames.length || !names.every((name) => jointNames.includes(name))) return false;
  return Object.entries(value.joints).every(([name, joint]) =>
    ACTION_ID.test(name) && isObject(joint) && keys(joint, ["pos", "min", "max"]) &&
    number(joint.pos) && number(joint.min) && number(joint.max) && joint.min <= joint.pos && joint.pos <= joint.max);
}

function isLightState(value: unknown, capabilities: CapabilitySet): boolean {
  if (!isObject(value) || !keys(value, ["brightness", "matrix", "pixels"]) ||
      !number(value.brightness, 0, 1) || !isObject(value.matrix) ||
      !keys(value.matrix, ["width", "height"]) || !integer(value.matrix.width, 1) ||
      !integer(value.matrix.height, 1) || !colors(value.pixels) ||
      value.pixels.length !== value.matrix.width * value.matrix.height) return false;
  const head = capabilities.lights.filter((light) => light.target === "head_matrix");
  return head.length === 1 && head[0].width === value.matrix.width && head[0].height === value.matrix.height;
}

function isMotionFinished(payload: JsonObject): boolean {
  if (!keys(payload, ["action", "status", "elapsed_ms", "joints", "reason", "error"], ["extensions"]) ||
      !matches(payload.action, ACTION_ID) || !oneOf(payload.status, ["completed", "cancelled", "failed"]) ||
      !integer(payload.elapsed_ms, 0) || !jointNumbers(payload.joints)) return false;
  if (payload.status === "completed") return payload.reason === null && payload.error === null;
  if (payload.status === "cancelled") return matches(payload.reason, ACTION_ID) && payload.error === null;
  return payload.reason === null && isProtocolError(payload.error);
}

function isProtocolError(value: unknown): value is ProtocolError {
  return isObject(value) && keys(value, ["code", "message", "recoverable", "details"]) &&
    matches(value.code, ACTION_ID) && text(value.message) && typeof value.recoverable === "boolean" &&
    (value.details === null || isObject(value.details));
}

function validExtensions(value: unknown): boolean {
  return value === undefined || (isObject(value) && Object.keys(value).every((key) => EXTENSION.test(key)));
}

function jointNumbers(value: unknown, requireNonempty = false): value is JointCommandMap {
  return isObject(value) && (!requireNonempty || Object.keys(value).length > 0) &&
    Object.entries(value).every(([name, position]) => ACTION_ID.test(name) && number(position));
}

function colors(value: unknown): value is string[] {
  return Array.isArray(value) && value.length > 0 && value.every((item) => matches(item, COLOR));
}

function uniqueMatches(value: unknown[], pattern: RegExp): value is string[] {
  return value.every((item) => matches(item, pattern)) && new Set(value).size === value.length;
}

function keys(value: Record<string, unknown>, required: string[], optional: string[] = []): boolean {
  const actual = Object.keys(value);
  return required.every((key) => Object.hasOwn(value, key)) &&
    actual.every((key) => required.includes(key) || optional.includes(key));
}

function validTimestamp(value: unknown): value is string {
  return typeof value === "string" && TIMESTAMP.test(value) &&
    !Number.isNaN(Date.parse(value)) && new Date(value).toISOString() === value;
}

function matches(value: unknown, pattern: RegExp): value is string {
  return typeof value === "string" && pattern.test(value);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function number(value: unknown, minimum?: number, maximum?: number): value is number {
  return typeof value === "number" && Number.isFinite(value) &&
    (minimum === undefined || value >= minimum) && (maximum === undefined || value <= maximum);
}

function integer(value: unknown, minimum: number, maximum?: number): value is number {
  return number(value, minimum, maximum) && Number.isInteger(value);
}

function oneOf<T extends string>(value: unknown, choices: readonly T[]): value is T {
  return typeof value === "string" && choices.includes(value as T);
}

function isObject(value: unknown): value is Record<string, any> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function protocolError(code: string, message: string): ProtocolError {
  return { code, message, recoverable: true, details: null };
}

function failure(code: string, message: string): { ok: false; error: ProtocolError } {
  return { ok: false, error: protocolError(code, message) };
}
