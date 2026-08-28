import { describe, expect, it, vi } from "vitest";

import { createAgentControl } from "./agentControl";
import { createInitialAgentState } from "./agentStore";

describe("createAgentControl", () => {
  it("forwards acknowledgement callbacks to the Agent transport", () => {
    const sender = vi.fn(() => true);
    const callbacks = { onAccepted: vi.fn(), onRejected: vi.fn() };
    const control = createAgentControl(
      { ...createInitialAgentState(), connection: "online", deviceConnected: true },
      sender,
    );

    expect(control.submitText("点头", callbacks)).toBe(true);
    expect(sender).toHaveBeenCalledWith("点头", callbacks);
  });

  it("projects a draft and its tools as one conversation entry", () => {
    const state = {
      ...createInitialAgentState(),
      responseDrafts: [{
        requestId: "request-1",
        responseId: "response-1",
        text: "正在执行",
        status: "streaming" as const,
        startedAt: "2026-08-21T08:00:00.000Z",
      }],
      toolProgress: [{
        requestId: "request-1",
        responseId: "response-1",
        toolCallId: "tool-1",
        toolName: "play_motion" as const,
        status: "running" as const,
      }],
    };

    const control = createAgentControl(state, () => true);

    expect(control.messages).toEqual([expect.objectContaining({
      id: "response-1",
      role: "agent",
      text: "正在执行",
      streamState: "streaming",
      tools: [expect.objectContaining({ toolCallId: "tool-1", status: "running" })],
    })]);
  });
});
