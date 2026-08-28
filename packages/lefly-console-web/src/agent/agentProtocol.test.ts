import { describe, expect, it } from "vitest";

import { createAgentSubmission, parseAgentIncoming } from "./agentProtocol";

const state = {
  phase: "idle",
  device_connected: true,
  queue: { size: 0, capacity: 8 },
};

describe("Agent Control protocol", () => {
  it.each([
    { version: "1", type: "agent.response.started", request_id: "r-1", response_id: "p-1" },
    { version: "1", type: "agent.response.delta", request_id: "r-1", response_id: "p-1", text: "你" },
    { version: "1", type: "agent.response.completed", request_id: "r-1", response_id: "p-1" },
    {
      version: "1", type: "agent.response.failed", request_id: "r-1", response_id: "p-1",
      code: "response_failed", message: "处理请求失败。", recoverable: true,
    },
    {
      version: "1", type: "agent.tool.started", request_id: "r-1", response_id: "p-1",
      tool_call_id: "c-1", tool_name: "play_motion",
    },
    {
      version: "1", type: "agent.tool.completed", request_id: "r-1", response_id: "p-1",
      tool_call_id: "c-1", tool_name: "play_motion", protocol_correlation_id: "cmd-1", disposition: "queued",
    },
    {
      version: "1", type: "agent.tool.failed", request_id: "r-1", response_id: "p-1",
      tool_call_id: "c-1", tool_name: "get_weather", code: "tool_failed", message: "工具执行失败。", recoverable: true,
    },
  ])("parses strict lifecycle message $type", (message) => {
    expect(parseAgentIncoming(message)).toEqual({ ok: true, value: message });
  });

  it.each([
    { version: "1", type: "agent.response.started", request_id: "r-1", response_id: "" },
    { version: "1", type: "agent.response.started", request_id: " r-1 ", response_id: "p-1" },
    { version: "1", type: "agent.response.started", request_id: "r-1", response_id: "p".repeat(129) },
    { version: "1", type: "agent.response.delta", request_id: "r-1", response_id: "p-1", text: "" },
    { version: "1", type: "agent.response.completed", request_id: "r-1", response_id: "p-1", extra: true },
    { version: "1", type: "agent.response.failed", request_id: "r-1", response_id: "p-1", code: "x", message: "x" },
    { version: "1", type: "agent.tool.started", request_id: "r-1", response_id: "p-1", tool_name: "play_motion" },
    {
      version: "1", type: "agent.tool.completed", request_id: "r-1", response_id: "p-1",
      tool_call_id: "c-1", tool_name: "shell", protocol_correlation_id: "cmd-1",
    },
    {
      version: "1", type: "agent.tool.failed", request_id: "r-1", response_id: "p-1",
      tool_call_id: "c-1", tool_name: "get_weather", code: "x", message: "x", recoverable: true, secret: "x",
    },
  ])("rejects malformed lifecycle %#", (message) => {
    expect(parseAgentIncoming(message).ok).toBe(false);
  });

  it("parses a hello snapshot with chat history", () => {
    const parsed = parseAgentIncoming({
      version: "1",
      type: "agent.hello",
      session_id: "agent-session",
      state: {
        ...state,
        messages: [{ id: "m-1", role: "agent", text: "你好", timestamp: "2026-08-15T01:00:00Z" }],
      },
    });

    expect(parsed).toEqual({
      ok: true,
      value: expect.objectContaining({ type: "agent.hello", session_id: "agent-session" }),
    });
  });

  it("rejects invalid phases and chat roles", () => {
    expect(parseAgentIncoming({ version: "1", type: "agent.state", state: { ...state, phase: "busy" } }).ok).toBe(false);
    expect(parseAgentIncoming({
      version: "1",
      type: "agent.message",
      message: { id: "m-1", role: "tool", text: "x", timestamp: "now" },
    }).ok).toBe(false);
    expect(parseAgentIncoming({
      version: "1",
      type: "agent.message",
      message: { id: "m-1", role: "agent", text: "x", timestamp: "now", extra: true },
    }).ok).toBe(false);
    expect(parseAgentIncoming({
      version: "1",
      type: "agent.accepted",
      request_id: "r-1",
      unexpected: true,
    }).ok).toBe(false);
  });

  it("creates a bounded versioned text submission", () => {
    expect(createAgentSubmission(" 看左边 ", {
      idFactory: () => "request-1",
      now: () => new Date("2026-08-15T01:02:03.000Z"),
    })).toEqual({
      version: "1",
      id: "request-1",
      type: "agent.submit_text",
      timestamp: "2026-08-15T01:02:03.000Z",
      text: "看左边",
    });
    expect(() => createAgentSubmission(" ")).toThrow(/empty/i);
    expect(() => createAgentSubmission("x".repeat(501))).toThrow(/500/);
  });
});
