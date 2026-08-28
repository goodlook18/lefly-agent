import { describe, expect, it } from "vitest";

import { agentReducer, createInitialAgentState } from "./agentStore";

const runtimeState = {
  phase: "idle" as const,
  device_connected: true,
  queue: { size: 0, capacity: 8 },
};

describe("agentReducer", () => {
  it("hydrates from hello and deduplicates streamed messages", () => {
    const message = { id: "m-1", role: "agent" as const, text: "收到", timestamp: "2026-08-15T01:00:00Z" };
    let state = agentReducer(createInitialAgentState(), {
      type: "incoming",
      message: {
        version: "1",
        type: "agent.hello",
        session_id: "session-1",
        state: { ...runtimeState, messages: [message] },
      },
    });
    state = agentReducer(state, {
      type: "incoming",
      message: { version: "1", type: "agent.message", message },
    });

    expect(state.connection).toBe("online");
    expect(state.messages).toEqual([message]);
    expect(state.sessionId).toBe("session-1");
  });

  it("keeps history bounded and records Agent errors", () => {
    let state = createInitialAgentState();
    for (let index = 0; index < 105; index += 1) {
      state = agentReducer(state, {
        type: "incoming",
        message: {
          version: "1",
          type: "agent.message",
          message: { id: `m-${index}`, role: "user", text: `${index}`, timestamp: "now" },
        },
      });
    }
    state = agentReducer(state, {
      type: "incoming",
      message: { version: "1", type: "agent.error", code: "queue_full", message: "full", recoverable: true },
    });

    expect(state.messages).toHaveLength(100);
    expect(state.messages[0].id).toBe("m-5");
    expect(state.error).toBe("full");
  });

  it("preserves history but marks the connection offline when closed", () => {
    const message = { id: "m-1", role: "system" as const, text: "触摸", timestamp: "now" };
    let state = agentReducer(createInitialAgentState(), {
      type: "incoming",
      message: { version: "1", type: "agent.message", message },
    });
    state = agentReducer(state, { type: "closed" });

    expect(state.connection).toBe("offline");
    expect(state.messages).toEqual([message]);
  });

  it("clears an old error after a later submission is accepted", () => {
    let state = agentReducer(createInitialAgentState(), {
      type: "incoming",
      message: { version: "1", type: "agent.error", code: "queue_full", message: "full", recoverable: true, request_id: "r-1" },
    });
    state = agentReducer(state, {
      type: "incoming",
      message: { version: "1", type: "agent.accepted", request_id: "r-2" },
    });

    expect(state.error).toBeNull();
  });

  it("appends ordered deltas to one draft and commits completion exactly once", () => {
    let state = agentReducer(createInitialAgentState(), {
      type: "incoming",
      receivedAt: "2026-08-21T08:00:00.000Z",
      message: { version: "1", type: "agent.response.started", request_id: "r-1", response_id: "p-1" },
    });
    for (const text of ["正在", "点头"] as const) {
      state = agentReducer(state, {
        type: "incoming",
        message: { version: "1", type: "agent.response.delta", request_id: "r-1", response_id: "p-1", text },
      });
    }

    expect(state.responseDrafts).toEqual([expect.objectContaining({
      requestId: "r-1",
      responseId: "p-1",
      text: "正在点头",
      status: "streaming",
    })]);
    expect(state.messages).toEqual([]);

    const completed = { version: "1" as const, type: "agent.response.completed" as const, request_id: "r-1", response_id: "p-1" };
    state = agentReducer(state, { type: "incoming", receivedAt: "2026-08-21T08:00:01.000Z", message: completed });
    state = agentReducer(state, { type: "incoming", receivedAt: "2026-08-21T08:00:02.000Z", message: completed });

    expect(state.responseDrafts).toEqual([]);
    expect(state.messages).toEqual([{
      id: "p-1",
      role: "agent",
      text: "正在点头",
      timestamp: "2026-08-21T08:00:01.000Z",
    }]);
  });

  it("keeps a visible interrupted draft after a failed response", () => {
    let state = agentReducer(createInitialAgentState(), {
      type: "incoming",
      receivedAt: "2026-08-21T08:01:00.000Z",
      message: { version: "1", type: "agent.response.started", request_id: "r-2", response_id: "p-2" },
    });
    state = agentReducer(state, {
      type: "incoming",
      message: { version: "1", type: "agent.response.delta", request_id: "r-2", response_id: "p-2", text: "我正在" },
    });
    state = agentReducer(state, {
      type: "incoming",
      message: {
        version: "1",
        type: "agent.response.failed",
        request_id: "r-2",
        response_id: "p-2",
        code: "model_stream_failed",
        message: "回复中断",
        recoverable: true,
      },
    });

    expect(state.responseDrafts).toEqual([expect.objectContaining({
      responseId: "p-2",
      text: "我正在",
      status: "interrupted",
      error: "回复中断",
    })]);
    expect(state.error).toBe("回复中断");
  });

  it("correlates tool progress without adding assistant prose", () => {
    let state = agentReducer(createInitialAgentState(), {
      type: "incoming",
      message: { version: "1", type: "agent.response.started", request_id: "r-3", response_id: "p-3" },
    });
    state = agentReducer(state, {
      type: "incoming",
      message: {
        version: "1",
        type: "agent.tool.started",
        request_id: "r-3",
        response_id: "p-3",
        tool_call_id: "t-1",
        tool_name: "play_motion",
      },
    });
    state = agentReducer(state, {
      type: "incoming",
      message: {
        version: "1",
        type: "agent.tool.completed",
        request_id: "r-3",
        response_id: "p-3",
        tool_call_id: "t-1",
        tool_name: "play_motion",
        protocol_correlation_id: "command-1",
        disposition: "applied",
      },
    });

    expect(state.messages).toEqual([]);
    expect(state.toolProgress).toEqual([{
      requestId: "r-3",
      responseId: "p-3",
      toolCallId: "t-1",
      toolName: "play_motion",
      status: "completed",
      protocolCorrelationId: "command-1",
      disposition: "applied",
    }]);
  });

  it("replaces local drafts with the committed reconnect snapshot", () => {
    let state = agentReducer(createInitialAgentState(), {
      type: "incoming",
      message: { version: "1", type: "agent.response.started", request_id: "r-4", response_id: "p-4" },
    });
    state = agentReducer(state, {
      type: "incoming",
      message: { version: "1", type: "agent.response.delta", request_id: "r-4", response_id: "p-4", text: "临时" },
    });
    const committed = { id: "p-4", role: "agent" as const, text: "最终", timestamp: "2026-08-21T08:02:00.000Z" };
    state = agentReducer(state, {
      type: "incoming",
      message: {
        version: "1",
        type: "agent.hello",
        session_id: "session-2",
        state: { ...runtimeState, messages: [committed, committed] },
      },
    });

    expect(state.messages).toEqual([committed]);
    expect(state.responseDrafts).toEqual([]);
    expect(state.toolProgress).toEqual([]);
  });
});
