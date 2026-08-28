import { createAgentSubmission, parseAgentIncoming, type AgentIncoming } from "../agent/agentProtocol";
import type { AgentSubmissionCallbacks } from "../agent/agentControl";
import type { AgentAction } from "../agent/agentStore";

export interface WebSocketLike {
  readyState: number;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface AgentLocationLike {
  hostname: string;
  protocol: string;
}

export interface AgentSocketOptions {
  url?: string;
  location?: AgentLocationLike;
  webSocketFactory?: (url: string) => WebSocketLike;
  timers?: { setTimeout(callback: () => void, delayMs: number): unknown; clearTimeout(handle: unknown): void };
  jitter?: () => number;
  now?: () => Date;
  idFactory?: () => string;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

const OPEN = 1;

export class AgentSocket {
  private socket: WebSocketLike | null = null;
  private reconnectTimer: unknown = null;
  private reconnectAttempt = 0;
  private stopped = true;
  private readonly pending = new Map<string, AgentSubmissionCallbacks>();
  private generation = 0;
  private readonly url: string;
  private readonly factory: (url: string) => WebSocketLike;
  private readonly timers: NonNullable<AgentSocketOptions["timers"]>;
  private readonly jitter: () => number;
  private readonly now: () => Date;
  private readonly idFactory?: () => string;
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;

  constructor(private readonly dispatch: (action: AgentAction) => void, options: AgentSocketOptions = {}) {
    this.url = options.url ?? defaultAgentSocketUrl(options.location);
    this.factory = options.webSocketFactory ?? ((url) => new WebSocket(url));
    this.timers = options.timers ?? {
      setTimeout: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
      clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
    };
    this.jitter = options.jitter ?? Math.random;
    this.now = options.now ?? (() => new Date());
    this.idFactory = options.idFactory;
    this.baseDelayMs = positive(options.baseDelayMs, 500);
    this.maxDelayMs = positive(options.maxDelayMs, 10_000);
  }

  connect(): void {
    this.stopped = false;
    this.clearReconnect();
    this.retireSocket();
    this.openSocket();
  }

  close(): void {
    this.stopped = true;
    this.clearReconnect();
    this.retireSocket();
  }

  sendText(text: string, callbacks: AgentSubmissionCallbacks = {}): boolean {
    let submission;
    try {
      submission = createAgentSubmission(text, { idFactory: this.idFactory, now: this.now });
    } catch (error) {
      this.dispatch({ type: "send_failed", message: errorMessage(error) });
      return false;
    }
    if (this.socket?.readyState !== OPEN) {
      this.dispatch({ type: "send_failed", message: "Agent Control 未连接" });
      return false;
    }
    try {
      this.socket.send(JSON.stringify(submission));
      this.pending.set(submission.id, callbacks);
      return true;
    } catch (error) {
      this.dispatch({ type: "send_failed", message: errorMessage(error) });
      return false;
    }
  }

  private openSocket(): void {
    if (this.stopped || this.socket !== null) return;
    this.dispatch({ type: "connecting" });
    const generation = ++this.generation;
    let socket: WebSocketLike;
    try {
      socket = this.factory(this.url);
    } catch (error) {
      this.dispatch({ type: "protocol_error", message: errorMessage(error) });
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => undefined;
    socket.onmessage = (event) => {
      if (!this.isCurrent(socket, generation)) return;
      if (typeof event.data !== "string") {
        this.dispatch({ type: "protocol_error", message: "Agent Control 不支持二进制消息" });
        return;
      }
      try {
        const parsed = parseAgentIncoming(JSON.parse(event.data));
        if (!parsed.ok) this.dispatch({ type: "protocol_error", message: parsed.error });
        else {
          if (parsed.value.type === "agent.hello") this.reconnectAttempt = 0;
          this.settleSubmission(parsed.value);
          this.dispatch({ type: "incoming", message: parsed.value, receivedAt: this.now().toISOString() });
        }
      } catch (error) {
        this.dispatch({ type: "protocol_error", message: errorMessage(error) });
      }
    };
    socket.onerror = () => undefined;
    socket.onclose = () => {
      if (!this.isCurrent(socket, generation)) return;
      this.detach(socket);
      this.socket = null;
      this.generation += 1;
      this.rejectPending("Agent Control 连接已断开");
      this.dispatch({ type: "closed" });
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) return;
    const exponential = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    const jitter = Math.min(1, Math.max(0, this.jitter()));
    const delay = Math.round(exponential * (0.5 + jitter * 0.5));
    this.reconnectTimer = this.timers.setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer === null) return;
    this.timers.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private retireSocket(): void {
    const socket = this.socket;
    if (socket === null) return;
    this.socket = null;
    this.generation += 1;
    this.detach(socket);
    this.rejectPending("Agent Control 连接已关闭");
    socket.close();
  }

  private detach(socket: WebSocketLike): void {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
  }

  private isCurrent(socket: WebSocketLike, generation: number): boolean {
    return !this.stopped && this.socket === socket && this.generation === generation;
  }

  private settleSubmission(message: AgentIncoming): void {
    if (message.type === "agent.accepted") {
      const callbacks = this.pending.get(message.request_id);
      this.pending.delete(message.request_id);
      callbacks?.onAccepted?.();
    } else if (message.type === "agent.error" && message.request_id !== undefined) {
      const callbacks = this.pending.get(message.request_id);
      this.pending.delete(message.request_id);
      callbacks?.onRejected?.(message.message);
    }
  }

  private rejectPending(message: string): void {
    for (const callbacks of this.pending.values()) callbacks.onRejected?.(message);
    this.pending.clear();
  }
}

export function defaultAgentSocketUrl(locationLike?: AgentLocationLike): string {
  const location = locationLike ?? globalThis.location;
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.hostname}:8767/ws/agent`;
}

function positive(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
