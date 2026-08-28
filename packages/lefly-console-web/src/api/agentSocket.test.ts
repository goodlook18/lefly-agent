import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentAction } from "../agent/agentStore";
import { AgentSocket, defaultAgentSocketUrl, type WebSocketLike } from "./agentSocket";

class FakeWebSocket implements WebSocketLike {
  static instances: FakeWebSocket[] = [];
  readonly sent: string[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) { FakeWebSocket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  receive(value: unknown) { this.onmessage?.({ data: value } as MessageEvent); }
  disconnect() { this.readyState = 3; this.onclose?.(); }
  send(value: string) { this.sent.push(value); }
  close() { this.disconnect(); }
}

describe("AgentSocket", () => {
  const actions: AgentAction[] = [];

  beforeEach(() => {
    vi.useFakeTimers();
    actions.length = 0;
    FakeWebSocket.instances.length = 0;
  });
  afterEach(() => vi.useRealTimers());

  it("uses a dedicated port on the current host", () => {
    expect(defaultAgentSocketUrl({ hostname: "console.test", protocol: "http:" })).toBe("ws://console.test:8767/ws/agent");
    expect(defaultAgentSocketUrl({ hostname: "pi.local", protocol: "https:" })).toBe("wss://pi.local:8767/ws/agent");
  });

  it("parses incoming messages and reconnects after close", () => {
    const socket = new AgentSocket(actions.push.bind(actions), {
      webSocketFactory: (url) => new FakeWebSocket(url),
      baseDelayMs: 50,
      jitter: () => 1,
    });
    socket.connect();
    const first = FakeWebSocket.instances[0];
    first.open();
    first.receive(JSON.stringify({
      version: "1",
      type: "agent.hello",
      session_id: "s-1",
      state: { phase: "idle", device_connected: true, queue: { size: 0, capacity: 8 }, messages: [] },
    }));
    first.disconnect();
    vi.advanceTimersByTime(50);

    expect(actions.map((action) => action.type)).toEqual(["connecting", "incoming", "closed", "connecting"]);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("submits trimmed text only while open and never queues it for replay", () => {
    const socket = new AgentSocket(actions.push.bind(actions), {
      webSocketFactory: (url) => new FakeWebSocket(url),
      idFactory: () => "request-1",
      now: () => new Date("2026-08-15T01:02:03.000Z"),
    });
    socket.connect();
    expect(socket.sendText("看左边")).toBe(false);
    FakeWebSocket.instances[0].open();
    expect(socket.sendText(" 看左边 ")).toBe(true);

    expect(JSON.parse(FakeWebSocket.instances[0].sent[0])).toEqual(expect.objectContaining({
      type: "agent.submit_text",
      id: "request-1",
      text: "看左边",
    }));
  });

  it("confirms or rejects submissions by request id", () => {
    const accepted = vi.fn();
    const rejected = vi.fn();
    const socket = new AgentSocket(actions.push.bind(actions), {
      webSocketFactory: (url) => new FakeWebSocket(url),
      idFactory: () => "request-ack",
    });
    socket.connect();
    const active = FakeWebSocket.instances[0];
    active.open();
    expect(socket.sendText("点头", { onAccepted: accepted, onRejected: rejected })).toBe(true);
    expect(accepted).not.toHaveBeenCalled();

    active.receive(JSON.stringify({ version: "1", type: "agent.accepted", request_id: "request-ack" }));
    expect(accepted).toHaveBeenCalledOnce();
    expect(rejected).not.toHaveBeenCalled();
  });

  it("rejects pending submissions when the socket closes", () => {
    const rejected = vi.fn();
    const socket = new AgentSocket(actions.push.bind(actions), {
      webSocketFactory: (url) => new FakeWebSocket(url),
      idFactory: () => "request-close",
    });
    socket.connect();
    const active = FakeWebSocket.instances[0];
    active.open();
    socket.sendText("点头", { onRejected: rejected });
    active.disconnect();

    expect(rejected).toHaveBeenCalledWith("Agent Control 连接已断开");
  });

  it("dispatches lifecycle messages in wire order with a receipt timestamp", () => {
    const socket = new AgentSocket(actions.push.bind(actions), {
      webSocketFactory: (url) => new FakeWebSocket(url),
      now: () => new Date("2026-08-21T08:03:00.000Z"),
    });
    socket.connect();
    const active = FakeWebSocket.instances[0];
    active.open();
    active.receive(JSON.stringify({ version: "1", type: "agent.response.started", request_id: "r-1", response_id: "p-1" }));
    active.receive(JSON.stringify({ version: "1", type: "agent.response.delta", request_id: "r-1", response_id: "p-1", text: "你好" }));
    active.receive(JSON.stringify({ version: "1", type: "agent.response.completed", request_id: "r-1", response_id: "p-1" }));

    expect(actions.slice(1).map((action) => action.type === "incoming" ? action.message.type : action.type)).toEqual([
      "agent.response.started",
      "agent.response.delta",
      "agent.response.completed",
    ]);
    expect(actions.slice(1)).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "incoming", receivedAt: "2026-08-21T08:03:00.000Z" }),
    ]));
  });
});
