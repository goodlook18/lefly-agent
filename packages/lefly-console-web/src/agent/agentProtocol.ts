export type AgentPhase = "idle" | "interpreting" | "executing" | "error";
export type AgentChatRole = "user" | "agent" | "system";
export type AgentToolName =
  | "play_motion"
  | "set_head_light"
  | "set_head_light_brightness"
  | "enter_rest_state"
  | "get_current_datetime"
  | "get_weather"
  | "web_search";

export interface AgentChatMessage {
  id: string;
  role: AgentChatRole;
  text: string;
  timestamp: string;
}

export interface AgentRuntimeState {
  phase: AgentPhase;
  device_connected: boolean;
  queue: { size: number; capacity: number };
}

export type AgentIncoming =
  | { version: "1"; type: "agent.hello"; session_id: string; state: AgentRuntimeState & { messages: AgentChatMessage[] } }
  | { version: "1"; type: "agent.state"; state: AgentRuntimeState }
  | { version: "1"; type: "agent.message"; message: AgentChatMessage }
  | { version: "1"; type: "agent.accepted"; request_id: string }
  | { version: "1"; type: "agent.error"; code: string; message: string; recoverable: boolean; request_id?: string }
  | { version: "1"; type: "agent.response.started"; request_id: string; response_id: string }
  | { version: "1"; type: "agent.response.delta"; request_id: string; response_id: string; text: string }
  | { version: "1"; type: "agent.response.completed"; request_id: string; response_id: string }
  | { version: "1"; type: "agent.response.failed"; request_id: string; response_id: string; code: string; message: string; recoverable: boolean }
  | { version: "1"; type: "agent.tool.started"; request_id: string; response_id: string; tool_call_id: string; tool_name: AgentToolName }
  | { version: "1"; type: "agent.tool.completed"; request_id: string; response_id: string; tool_call_id: string; tool_name: AgentToolName; protocol_correlation_id?: string; disposition?: "applied" | "queued" }
  | { version: "1"; type: "agent.tool.failed"; request_id: string; response_id: string; tool_call_id: string; tool_name: AgentToolName; code: string; message: string; recoverable: boolean };

export interface AgentSubmission {
  version: "1";
  id: string;
  type: "agent.submit_text";
  timestamp: string;
  text: string;
}

export type ParseAgentResult =
  | { ok: true; value: AgentIncoming }
  | { ok: false; error: string };

export function parseAgentIncoming(value: unknown): ParseAgentResult {
  if (!isRecord(value) || value.version !== "1" || typeof value.type !== "string") {
    return invalid("message must be a version 1 Agent Control object");
  }
  switch (value.type) {
    case "agent.hello": {
      if (!hasOnlyKeys(value, ["version", "type", "session_id", "state"])) return invalid("unknown agent.hello field");
      const state = parseRuntimeState(value.state, true);
      if (!nonEmpty(value.session_id) || !state.ok) return invalid("invalid agent.hello");
      return { ok: true, value: { version: "1", type: value.type, session_id: value.session_id, state: state.value } };
    }
    case "agent.state": {
      if (!hasOnlyKeys(value, ["version", "type", "state"])) return invalid("unknown agent.state field");
      const state = parseRuntimeState(value.state, false);
      return state.ok
        ? { ok: true, value: { version: "1", type: value.type, state: state.value } }
        : invalid("invalid agent.state");
    }
    case "agent.message": {
      if (!hasOnlyKeys(value, ["version", "type", "message"])) return invalid("unknown agent.message field");
      const message = parseChatMessage(value.message);
      return message === null
        ? invalid("invalid agent.message")
        : { ok: true, value: { version: "1", type: value.type, message } };
    }
    case "agent.accepted":
      if (!hasOnlyKeys(value, ["version", "type", "request_id"])) return invalid("unknown agent.accepted field");
      return correlationId(value.request_id)
        ? { ok: true, value: { version: "1", type: value.type, request_id: value.request_id } }
        : invalid("invalid agent.accepted");
    case "agent.error":
      if (!hasOnlyKeys(value, ["version", "type", "code", "message", "recoverable", "request_id"])) return invalid("unknown agent.error field");
      if (!nonEmpty(value.code) || !nonEmpty(value.message) || typeof value.recoverable !== "boolean") {
        return invalid("invalid agent.error");
      }
      if (value.request_id !== undefined && !correlationId(value.request_id)) return invalid("invalid agent.error request_id");
      return {
        ok: true,
        value: {
          version: "1",
          type: value.type,
          code: value.code,
          message: value.message,
          recoverable: value.recoverable,
          ...(value.request_id === undefined ? {} : { request_id: value.request_id }),
        },
      };
    case "agent.response.started":
    case "agent.response.completed":
      if (!hasOnlyKeys(value, ["version", "type", "request_id", "response_id"])) {
        return invalid(`unknown ${value.type} field`);
      }
      return validCorrelation(value)
        ? { ok: true, value: { version: "1", type: value.type, request_id: value.request_id, response_id: value.response_id } }
        : invalid(`invalid ${value.type}`);
    case "agent.response.delta":
      if (!hasOnlyKeys(value, ["version", "type", "request_id", "response_id", "text"])) {
        return invalid("unknown agent.response.delta field");
      }
      if (!validCorrelation(value) || typeof value.text !== "string" || value.text.length === 0 || value.text.length > 4000) {
        return invalid("invalid agent.response.delta");
      }
      return { ok: true, value: { version: "1", type: value.type, request_id: value.request_id, response_id: value.response_id, text: value.text } };
    case "agent.response.failed":
      if (!hasOnlyKeys(value, ["version", "type", "request_id", "response_id", "code", "message", "recoverable"])) {
        return invalid("unknown agent.response.failed field");
      }
      if (!validCorrelation(value) || !validFailure(value)) return invalid("invalid agent.response.failed");
      return {
        ok: true,
        value: {
          version: "1", type: value.type, request_id: value.request_id, response_id: value.response_id,
          code: value.code, message: value.message, recoverable: value.recoverable,
        },
      };
    case "agent.tool.started":
      if (!hasOnlyKeys(value, ["version", "type", "request_id", "response_id", "tool_call_id", "tool_name"])) {
        return invalid("unknown agent.tool.started field");
      }
      if (!validToolCorrelation(value)) return invalid("invalid agent.tool.started");
      return {
        ok: true,
        value: {
          version: "1", type: value.type, request_id: value.request_id, response_id: value.response_id,
          tool_call_id: value.tool_call_id, tool_name: value.tool_name,
        },
      };
    case "agent.tool.completed": {
      if (!hasOnlyKeys(value, [
        "version", "type", "request_id", "response_id", "tool_call_id", "tool_name",
        "protocol_correlation_id", "disposition",
      ])) return invalid("unknown agent.tool.completed field");
      if (!validToolCorrelation(value)) return invalid("invalid agent.tool.completed");
      if (value.protocol_correlation_id !== undefined && !correlationId(value.protocol_correlation_id)) {
        return invalid("invalid agent.tool.completed protocol correlation");
      }
      if (value.disposition !== undefined && value.disposition !== "applied" && value.disposition !== "queued") {
        return invalid("invalid agent.tool.completed disposition");
      }
      return {
        ok: true,
        value: {
          version: "1", type: value.type, request_id: value.request_id, response_id: value.response_id,
          tool_call_id: value.tool_call_id, tool_name: value.tool_name,
          ...(value.protocol_correlation_id === undefined ? {} : { protocol_correlation_id: value.protocol_correlation_id }),
          ...(value.disposition === undefined ? {} : { disposition: value.disposition }),
        },
      };
    }
    case "agent.tool.failed":
      if (!hasOnlyKeys(value, [
        "version", "type", "request_id", "response_id", "tool_call_id", "tool_name", "code", "message", "recoverable",
      ])) return invalid("unknown agent.tool.failed field");
      if (!validToolCorrelation(value) || !validFailure(value)) return invalid("invalid agent.tool.failed");
      return {
        ok: true,
        value: {
          version: "1", type: value.type, request_id: value.request_id, response_id: value.response_id,
          tool_call_id: value.tool_call_id, tool_name: value.tool_name,
          code: value.code, message: value.message, recoverable: value.recoverable,
        },
      };
    default:
      return invalid(`unsupported Agent Control message: ${value.type}`);
  }
}

export function createAgentSubmission(
  text: string,
  options: { idFactory?: () => string; now?: () => Date } = {},
): AgentSubmission {
  if (typeof text !== "string" || text.trim().length === 0) throw new Error("Agent text must be non-empty");
  const normalized = text.trim();
  if (normalized.length > 500) throw new Error("Agent text must not exceed 500 characters");
  const id = (options.idFactory ?? defaultId)();
  if (!correlationId(id)) throw new Error("Agent request ID must contain 1 to 128 characters without surrounding whitespace");
  return {
    version: "1",
    id,
    type: "agent.submit_text",
    timestamp: (options.now ?? (() => new Date()))().toISOString(),
    text: normalized,
  };
}

function parseRuntimeState(value: unknown, requireMessages: true): { ok: true; value: AgentRuntimeState & { messages: AgentChatMessage[] } } | { ok: false };
function parseRuntimeState(value: unknown, requireMessages: false): { ok: true; value: AgentRuntimeState } | { ok: false };
function parseRuntimeState(value: unknown, requireMessages: boolean) {
  if (!isRecord(value) || !isPhase(value.phase) || typeof value.device_connected !== "boolean" || !isRecord(value.queue)) return { ok: false as const };
  const allowedStateKeys = requireMessages
    ? ["phase", "device_connected", "queue", "messages"]
    : ["phase", "device_connected", "queue"];
  if (!hasOnlyKeys(value, allowedStateKeys) || !hasOnlyKeys(value.queue, ["size", "capacity"])) return { ok: false as const };
  const { size, capacity } = value.queue;
  if (!isCount(size) || !isCount(capacity) || capacity <= 0 || size > capacity) return { ok: false as const };
  const runtime: AgentRuntimeState = {
    phase: value.phase,
    device_connected: value.device_connected,
    queue: { size, capacity },
  };
  if (!requireMessages) return { ok: true as const, value: runtime };
  if (!Array.isArray(value.messages)) return { ok: false as const };
  const messages = value.messages.map(parseChatMessage);
  if (messages.some((message) => message === null)) return { ok: false as const };
  return { ok: true as const, value: { ...runtime, messages: messages as AgentChatMessage[] } };
}

function parseChatMessage(value: unknown): AgentChatMessage | null {
  if (!isRecord(value) || !hasOnlyKeys(value, ["id", "role", "text", "timestamp"]) || !nonEmpty(value.id) || !isRole(value.role) || !nonEmpty(value.text) || !nonEmpty(value.timestamp)) return null;
  return { id: value.id, role: value.role, text: value.text, timestamp: value.timestamp };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  const keys = new Set(allowed);
  return Object.keys(value).every((key) => keys.has(key));
}

function nonEmpty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPhase(value: unknown): value is AgentPhase {
  return value === "idle" || value === "interpreting" || value === "executing" || value === "error";
}

function isRole(value: unknown): value is AgentChatRole {
  return value === "user" || value === "agent" || value === "system";
}

function validCorrelation(value: Record<string, unknown>): value is Record<string, unknown> & { request_id: string; response_id: string } {
  return correlationId(value.request_id) && correlationId(value.response_id);
}

function validToolCorrelation(value: Record<string, unknown>): value is Record<string, unknown> & {
  request_id: string; response_id: string; tool_call_id: string; tool_name: AgentToolName;
} {
  return validCorrelation(value) && correlationId(value.tool_call_id) && isToolName(value.tool_name);
}

function validFailure(value: Record<string, unknown>): value is Record<string, unknown> & {
  code: string; message: string; recoverable: boolean;
} {
  return correlationId(value.code) && nonEmpty(value.message) && value.message.length <= 500 && typeof value.recoverable === "boolean";
}

function correlationId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 128 && value === value.trim();
}

function isToolName(value: unknown): value is AgentToolName {
  return value === "play_motion"
    || value === "set_head_light"
    || value === "set_head_light_brightness"
    || value === "enter_rest_state"
    || value === "get_current_datetime"
    || value === "get_weather"
    || value === "web_search";
}

function invalid(error: string): ParseAgentResult {
  return { ok: false, error };
}

function defaultId(): string {
  return typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `agent-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
