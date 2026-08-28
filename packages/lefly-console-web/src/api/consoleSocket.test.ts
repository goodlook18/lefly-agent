import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import stateChanged from "../../../../contracts/examples/v1/events/device-state-changed.json";
import {
  consoleReducer,
  createInitialConsoleState,
  type ConsoleAction,
} from "../state/consoleStore";
import {
  ConsoleSocket,
  SocketSendError,
  defaultConsoleSocketUrl,
  type WebSocketLike,
} from "./consoleSocket";

class FakeWebSocket implements WebSocketLike {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readonly sent: string[] = [];
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(data: unknown) {
    this.onmessage?.({ data } as MessageEvent);
  }

  disconnect() {
    this.readyState = 3;
    this.onclose?.();
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.disconnect();
  }
}

const COMMAND_1 = "10000000-0000-4000-8000-000000000001";
const COMMAND_2 = "10000000-0000-4000-8000-000000000002";

const helloMessage = () => ({
  type: "console.hello",
  session_id: "session-a",
  lease: { role: "readonly" },
  target_id: "simulator",
  target_epoch: 1,
  state: structuredClone(stateChanged.payload),
});
const hello = JSON.stringify(helloMessage());

describe("ConsoleSocket", () => {
  const actions: ConsoleAction[] = [];
  const factory = (url: string) => new FakeWebSocket(url);

  beforeEach(() => {
    vi.useFakeTimers();
    actions.length = 0;
    FakeWebSocket.instances.length = 0;
  });

  afterEach(() => vi.useRealTimers());

  it("builds default ws and wss URLs from an injected location-like value", () => {
    expect(defaultConsoleSocketUrl({
      origin: "http://console.test:4173",
      protocol: "http:",
    })).toBe("ws://console.test:4173/ws/console");
    expect(defaultConsoleSocketUrl({
      origin: "https://console.test",
      protocol: "https:",
    })).toBe("wss://console.test/ws/console");
  });

  it("connects, validates messages, and dispatches the hello", () => {
    const socket = new ConsoleSocket(actions.push.bind(actions), {
      url: "ws://example.test/ws/console",
      webSocketFactory: factory,
    });

    socket.connect();
    FakeWebSocket.instances[0].open();
    FakeWebSocket.instances[0].receive(hello);

    expect(FakeWebSocket.instances[0].url).toBe("ws://example.test/ws/console");
    expect(actions.map((action) => action.type)).toEqual(["connecting", "incoming"]);
  });

  it("reconnects with capped exponential backoff and injected jitter", () => {
    const socket = new ConsoleSocket(actions.push.bind(actions), {
      webSocketFactory: factory,
      baseDelayMs: 100,
      maxDelayMs: 250,
      jitter: () => 1,
    });
    socket.connect();

    FakeWebSocket.instances[0].disconnect();
    vi.advanceTimersByTime(99);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    FakeWebSocket.instances[1].disconnect();
    vi.advanceTimersByTime(199);
    expect(FakeWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    FakeWebSocket.instances[2].disconnect();
    vi.advanceTimersByTime(249);
    expect(FakeWebSocket.instances).toHaveLength(3);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(4);
  });

  it("reconnects on a revision gap and recovers from the next full hello", () => {
    let state = createInitialConsoleState();
    const socket = new ConsoleSocket(
      (action) => {
        state = consoleReducer(state, action);
      },
      {
        webSocketFactory: factory,
        baseDelayMs: 10,
        jitter: () => 1,
        now: () => 1234,
        idFactory: () => "gap-error",
      },
    );
    socket.connect();
    const first = FakeWebSocket.instances[0];
    first.open();
    first.receive(hello);
    const gapState = structuredClone(stateChanged.payload);
    gapState.revision = 3;
    gapState.light.brightness = 0.5;
    first.receive(JSON.stringify({
      type: "console.event",
      target_id: "simulator",
      target_epoch: 1,
      event: {
        version: "1",
        id: "20000000-0000-4000-8000-000000000090",
        type: "device.state_changed",
        timestamp: "2026-08-17T08:00:01.000Z",
        device_id: gapState.device_id,
        correlation_id: COMMAND_1,
        payload: gapState,
      },
    }));

    expect(first.readyState).toBe(3);
    expect(state.connection).toBe("stale");
    vi.advanceTimersByTime(10);
    expect(FakeWebSocket.instances).toHaveLength(2);
    const second = FakeWebSocket.instances[1];
    second.open();
    const recovered = helloMessage();
    recovered.state.revision = 5;
    second.receive(JSON.stringify(recovered));

    expect(state.connection).toBe("online");
    expect(state.deviceState?.revision).toBe(5);
    expect(second.sent).toEqual([]);
  });

  it("does not regress revision tracking for a delayed same-epoch full state", () => {
    let state = createInitialConsoleState();
    const socket = new ConsoleSocket(
      (action) => {
        state = consoleReducer(state, action);
      },
      {
        webSocketFactory: factory,
        baseDelayMs: 10,
        jitter: () => 1,
      },
    );
    socket.connect();
    const active = FakeWebSocket.instances[0];
    active.open();
    const initial = helloMessage();
    initial.state.revision = 5;
    active.receive(JSON.stringify(initial));
    active.receive(JSON.stringify({
      type: "console.state",
      target_id: "simulator",
      target_epoch: 1,
      state: { ...initial.state, revision: 3 },
    }));
    const revision6 = structuredClone(initial.state);
    revision6.revision = 6;
    revision6.light.brightness = 0.6;
    active.receive(JSON.stringify({
      type: "console.event",
      target_id: "simulator",
      target_epoch: 1,
      event: {
        version: "1",
        id: "20000000-0000-4000-8000-000000000091",
        type: "device.state_changed",
        timestamp: "2026-08-17T08:00:02.000Z",
        device_id: revision6.device_id,
        payload: revision6,
      },
    }));
    vi.runAllTimers();

    expect(active.readyState).toBe(FakeWebSocket.OPEN);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(state.deviceState?.revision).toBe(6);
    expect(state.connection).toBe("online");
  });

  it("detaches handlers and ignores retained callbacks from retired generations", () => {
    const socket = new ConsoleSocket(actions.push.bind(actions), {
      webSocketFactory: factory,
    });
    socket.connect();
    const first = FakeWebSocket.instances[0];
    const retiredMessage = first.onmessage;

    socket.connect();
    const second = FakeWebSocket.instances[1];
    expect(first.onopen).toBeNull();
    expect(first.onmessage).toBeNull();
    expect(first.onerror).toBeNull();
    expect(first.onclose).toBeNull();
    retiredMessage?.({ data: hello } as MessageEvent);
    expect(actions.filter((action) => action.type === "incoming")).toHaveLength(0);

    const closedMessage = second.onmessage;
    socket.close();
    expect(second.onopen).toBeNull();
    expect(second.onmessage).toBeNull();
    expect(second.onerror).toBeNull();
    expect(second.onclose).toBeNull();
    closedMessage?.({ data: hello } as MessageEvent);
    expect(actions.filter((action) => action.type === "incoming")).toHaveLength(0);
  });

  it("cancels reconnect on close", () => {
    const socket = new ConsoleSocket(actions.push.bind(actions), {
      webSocketFactory: factory,
      baseDelayMs: 100,
      jitter: () => 1,
    });
    socket.connect();
    FakeWebSocket.instances[0].disconnect();

    socket.close();
    vi.runAllTimers();

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not queue or replay messages across reconnects", () => {
    const socket = new ConsoleSocket(actions.push.bind(actions), {
      webSocketFactory: factory,
      baseDelayMs: 10,
      jitter: () => 1,
    });
    socket.connect();
    expect(socket.send({ type: "console.acquire_control" })).toBe(false);
    expect(() => socket.sendOrThrow({ type: "console.acquire_control" })).toThrow(SocketSendError);
    FakeWebSocket.instances[0].open();
    expect(socket.send({ type: "console.acquire_control" })).toBe(true);
    FakeWebSocket.instances[0].disconnect();
    expect(socket.send({ type: "console.renew_control" })).toBe(false);
    vi.advanceTimersByTime(10);
    FakeWebSocket.instances[1].open();

    expect(FakeWebSocket.instances[0].sent).toHaveLength(1);
    expect(FakeWebSocket.instances[1].sent).toEqual([]);
  });

  it("records a local command only after it is sent on an open socket", () => {
    const socket = new ConsoleSocket(actions.push.bind(actions), {
      webSocketFactory: factory,
    });
    const outgoing = {
      type: "console.command" as const,
      target_epoch: 1,
      command: {
        version: "1" as const,
        id: COMMAND_1,
        type: "motion.absolute_move" as const,
        timestamp: "2026-08-17T08:00:00.000Z",
        device_id: "lefly-sim-01",
        payload: { joints: { base_yaw: 20 }, duration_ms: 300 },
      },
    };
    socket.connect();

    expect(socket.send(outgoing)).toBe(false);
    FakeWebSocket.instances[0].open();
    expect(socket.send(outgoing)).toBe(true);

    const sentActions = actions.filter((action) => action.type === "local_command_sent");
    expect(sentActions).toHaveLength(1);
  });

  it("reports malformed JSON, invalid protocol messages, and binary data without throwing", () => {
    const socket = new ConsoleSocket(actions.push.bind(actions), {
      webSocketFactory: factory,
    });
    socket.connect();
    FakeWebSocket.instances[0].open();

    expect(() => FakeWebSocket.instances[0].receive("{" )).not.toThrow();
    expect(() => FakeWebSocket.instances[0].receive(JSON.stringify({ type: "console.hello" }))).not.toThrow();
    expect(() => FakeWebSocket.instances[0].receive(new ArrayBuffer(2))).not.toThrow();

    const errors = actions.filter((action) => action.type === "protocol_error");
    expect(errors).toHaveLength(3);
  });

  it("writes malformed messages and local send failures into store error history", () => {
    let state = createInitialConsoleState();
    let sequence = 0;
    const socket = new ConsoleSocket(
      (action) => {
        state = consoleReducer(state, action);
      },
      {
        webSocketFactory: factory,
        now: () => 1234,
        idFactory: () => `socket-error-${++sequence}`,
      },
    );
    socket.connect();
    expect(socket.send({
      type: "console.command",
      target_epoch: 1,
      command: {
        version: "1",
        id: COMMAND_2,
        type: "device.get_state",
        timestamp: "2026-08-17T08:00:00.000Z",
        device_id: "lefly-sim-01",
        payload: {},
      },
    })).toBe(false);
    FakeWebSocket.instances[0].open();
    FakeWebSocket.instances[0].receive("{");

    expect(state.errors).toEqual([
      expect.objectContaining({
        id: "socket-error-1",
        time: 1234,
        code: "socket_not_open",
        requestId: COMMAND_2,
      }),
      expect.objectContaining({
        id: "socket-error-2",
        time: 1234,
        code: "invalid_json",
      }),
    ]);
  });
});
