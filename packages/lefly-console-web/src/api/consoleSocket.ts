import {
  parseConsoleIncoming,
  type ConsoleError,
  type ConsoleIncoming,
  type ConsoleOutgoing,
} from "../protocol";
import type { ConsoleAction } from "../state/consoleStore";

export interface WebSocketLike {
  readyState: number;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface TimerApi {
  setTimeout(callback: () => void, delayMs: number): unknown;
  clearTimeout(handle: unknown): void;
}

export interface ConsoleSocketOptions {
  url?: string;
  location?: LocationLike;
  webSocketFactory?: (url: string) => WebSocketLike;
  timers?: TimerApi;
  jitter?: () => number;
  now?: () => number;
  idFactory?: () => string;
  baseDelayMs?: number;
  maxDelayMs?: number;
}

export interface LocationLike {
  origin: string;
  protocol: string;
}

export class SocketSendError extends Error {
  readonly code = "socket_not_open";

  constructor() {
    super("console socket is not open");
    this.name = "SocketSendError";
  }
}

const OPEN = 1;

export class ConsoleSocket {
  private readonly dispatch: (action: ConsoleAction) => void;
  private readonly url: string;
  private readonly factory: (url: string) => WebSocketLike;
  private readonly timers: TimerApi;
  private readonly jitter: () => number;
  private readonly now: () => number;
  private readonly idFactory: () => string;
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;
  private socket: WebSocketLike | null = null;
  private reconnectTimer: unknown = null;
  private reconnectAttempt = 0;
  private generation = 0;
  private observedEpoch: number | null = null;
  private observedRevision: number | null = null;
  private stopped = true;

  constructor(dispatch: (action: ConsoleAction) => void, options: ConsoleSocketOptions = {}) {
    this.dispatch = dispatch;
    this.url = options.url ?? defaultConsoleSocketUrl(options.location);
    this.factory = options.webSocketFactory ?? ((url) => new WebSocket(url));
    this.timers = options.timers ?? {
      setTimeout: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
      clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
    };
    this.jitter = options.jitter ?? Math.random;
    this.now = options.now ?? Date.now;
    this.idFactory = options.idFactory ?? defaultErrorId;
    this.baseDelayMs = positive(options.baseDelayMs, 500);
    this.maxDelayMs = positive(options.maxDelayMs, 10_000);
  }

  connect(): void {
    this.stopped = false;
    this.clearReconnect();
    this.retireSocket();
    this.resetObservation();
    this.openSocket();
  }

  close(): void {
    this.stopped = true;
    this.clearReconnect();
    this.retireSocket();
    this.resetObservation();
  }

  send(message: ConsoleOutgoing): boolean {
    if (this.socket?.readyState !== OPEN) {
      this.dispatchSendFailure("socket_not_open", "console socket is not open", message);
      return false;
    }
    try {
      this.socket.send(JSON.stringify(message));
    } catch (error) {
      this.dispatchSendFailure("socket_send_failed", errorMessage(error), message);
      return false;
    }
    if (message.type === "console.command") {
      this.dispatch({ type: "local_command_sent", command: message.command });
    }
    return true;
  }

  sendOrThrow(message: ConsoleOutgoing): void {
    if (!this.send(message)) throw new SocketSendError();
  }

  private openSocket(): void {
    if (this.stopped || this.socket !== null) return;
    this.dispatch({ type: "connecting" });
    const generation = ++this.generation;
    let socket: WebSocketLike;
    try {
      socket = this.factory(this.url);
    } catch (error) {
      this.dispatchProtocolError("socket_open_failed", errorMessage(error));
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      if (!this.isCurrent(socket, generation)) return;
    };
    socket.onmessage = (event) => {
      if (!this.isCurrent(socket, generation)) return;
      this.handleMessage(event.data, socket, generation);
    };
    socket.onerror = () => {
      if (!this.isCurrent(socket, generation)) return;
    };
    socket.onclose = () => {
      if (!this.isCurrent(socket, generation)) return;
      this.detach(socket);
      this.socket = null;
      this.generation += 1;
      this.resetObservation();
      this.dispatch({ type: "closed" });
      this.scheduleReconnect();
    };
  }

  private handleMessage(
    data: unknown,
    socket: WebSocketLike,
    generation: number,
  ): void {
    if (!this.isCurrent(socket, generation)) return;
    if (typeof data !== "string") {
      this.dispatchProtocolError("invalid_message", "binary console messages are not supported");
      return;
    }
    let decoded: unknown;
    try {
      decoded = JSON.parse(data);
    } catch (error) {
      this.dispatchProtocolError("invalid_json", errorMessage(error));
      return;
    }
    const parsed = parseConsoleIncoming(decoded);
    if (!parsed.ok) {
      this.dispatchProtocolError("invalid_message", parsed.error);
      return;
    }
    if (parsed.value.type === "console.hello") this.reconnectAttempt = 0;
    this.dispatch({ type: "incoming", message: parsed.value, ...this.errorMetadata() });
    if (this.observeRevision(parsed.value)) {
      this.retireSocket();
      this.resetObservation();
      this.scheduleReconnect();
    }
  }

  private observeRevision(message: ConsoleIncoming): boolean {
    if (message.type === "console.hello") {
      this.observedEpoch = message.target_epoch;
      this.observedRevision = message.state.revision;
      return false;
    }
    if (message.type === "console.state") {
      if (this.observedEpoch === null || message.target_epoch > this.observedEpoch) {
        this.observedEpoch = message.target_epoch;
        this.observedRevision = message.state.revision;
      } else if (
        message.target_epoch === this.observedEpoch &&
        (this.observedRevision === null || message.state.revision >= this.observedRevision)
      ) {
        this.observedRevision = message.state.revision;
      }
      return false;
    }
    if (
      message.type !== "console.event" ||
      message.event.type !== "device.state_changed" ||
      message.target_epoch !== this.observedEpoch
    ) {
      return false;
    }
    const revision = message.event.payload.revision;
    if (typeof revision !== "number" || this.observedRevision === null) return false;
    if (revision <= this.observedRevision) return false;
    if (revision > this.observedRevision + 1) return true;
    this.observedRevision = revision;
    return false;
  }

  private retireSocket(): void {
    const socket = this.socket;
    if (socket === null) return;
    this.socket = null;
    this.generation += 1;
    this.detach(socket);
    socket.close();
  }

  private detach(socket: WebSocketLike): void {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
  }

  private isCurrent(socket: WebSocketLike, generation: number): boolean {
    return !this.stopped && this.socket === socket && this.generation === generation;
  }

  private resetObservation(): void {
    this.observedEpoch = null;
    this.observedRevision = null;
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) return;
    const exponential = Math.min(
      this.maxDelayMs,
      this.baseDelayMs * 2 ** this.reconnectAttempt,
    );
    this.reconnectAttempt += 1;
    const jitter = Math.min(1, Math.max(0, this.jitter()));
    const delay = Math.round(exponential * (0.5 + jitter * 0.5));
    this.reconnectTimer = this.timers.setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) {
      this.timers.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private dispatchProtocolError(code: string, message: string): void {
    const error: ConsoleError = {
      type: "console.error",
      code,
      message,
      recoverable: true,
    };
    this.dispatch({ type: "protocol_error", error, ...this.errorMetadata() });
  }

  private dispatchSendFailure(
    code: string,
    messageText: string,
    message: ConsoleOutgoing,
  ): void {
    const error: ConsoleError = {
      type: "console.error",
      code,
      message: messageText,
      recoverable: true,
    };
    this.dispatch({
      type: "local_send_failed",
      error,
      ...(message.type === "console.command" ? { requestId: message.command.id } : {}),
      ...this.errorMetadata(),
    });
  }

  private errorMetadata() {
    return { errorId: this.idFactory(), receivedAt: this.now() };
  }
}

export function defaultConsoleSocketUrl(
  locationValue: LocationLike = globalThis.location,
): string {
  const url = new URL("/ws/console", locationValue.origin);
  url.protocol = locationValue.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

let errorSequence = 0;
function defaultErrorId(): string {
  errorSequence += 1;
  return `console-error-${Date.now()}-${errorSequence}`;
}

function positive(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
