import type {
  ConsoleError,
  ConsoleEvent,
  ConsoleIncoming,
  ConsoleLease,
  DeviceState,
  DeviceCommand,
  JsonObject,
} from "../protocol";
import type { CapabilitySet } from "../deviceProtocol";

export const CONSOLE_EVENT_LIMIT = 200;
export const ERROR_RECORD_LIMIT = 200;
export const COMMAND_RECORD_LIMIT = 200;

export type ConnectionStatus = "connecting" | "online" | "stale" | "offline";
export type LeaseState =
  | { role: "readonly" }
  | { role: "controller"; expiresAt: number };

export interface TargetSummary {
  id: string;
  kind?: string;
  active: boolean;
  status?: string;
  capabilities?: CapabilitySet;
}

export type NormalizedDeviceState = DeviceState;

export type CommandStatus =
  | "sent"
  | "accepted"
  | "started"
  | "progress"
  | "finished"
  | "failed";

export interface CommandRecord {
  requestId: string;
  deviceCommandId: string;
  commandType: string;
  status: CommandStatus;
  progress?: number;
  eventIds: string[];
  error?: { code?: string; message?: string };
}

export interface ConsoleErrorRecord {
  id: string;
  time: number;
  code: string;
  message: string;
  recoverable: boolean;
  requestId?: string;
}

export interface ErrorMetadata {
  errorId: string;
  receivedAt: number;
}

export interface ConsoleStoreState {
  connection: ConnectionStatus;
  sessionId: string | null;
  lease: LeaseState;
  targets: TargetSummary[];
  activeTargetId: string | null;
  targetEpoch: number;
  deviceState: NormalizedDeviceState | null;
  events: ConsoleEvent[];
  errors: ConsoleErrorRecord[];
  lastError: ConsoleErrorRecord | null;
  pendingCommands: CommandRecord[];
  commandTimeline: CommandRecord[];
}

export type ConsoleAction =
  | { type: "connecting" }
  | { type: "closed" }
  | ({ type: "incoming"; message: ConsoleIncoming } & ErrorMetadata)
  | ({ type: "protocol_error"; error: ConsoleError; requestId?: string } & ErrorMetadata)
  | ({ type: "local_send_failed"; error: ConsoleError; requestId?: string } & ErrorMetadata)
  | { type: "local_command_sent"; command: DeviceCommand }
  | { type: "targets_loaded"; targets: TargetSummary[] };

export function createInitialConsoleState(): ConsoleStoreState {
  return {
    connection: "offline",
    sessionId: null,
    lease: { role: "readonly" },
    targets: [],
    activeTargetId: null,
    targetEpoch: 0,
    deviceState: null,
    events: [],
    errors: [],
    lastError: null,
    pendingCommands: [],
    commandTimeline: [],
  };
}

export function consoleReducer(
  state: ConsoleStoreState,
  action: ConsoleAction,
): ConsoleStoreState {
  switch (action.type) {
    case "connecting":
      return { ...state, connection: "connecting" };
    case "closed":
      return {
        ...state,
        connection: state.deviceState === null ? "offline" : "stale",
        lease: { role: "readonly" },
      };
    case "protocol_error":
    case "local_send_failed":
      return appendError(state, action.error, action, action.requestId);
    case "targets_loaded":
      return { ...state, targets: detached(action.targets) };
    case "local_command_sent":
      return addLocalCommand(state, action.command);
    case "incoming":
      return reduceIncoming(state, action.message, action);
  }
}

function reduceIncoming(
  state: ConsoleStoreState,
  message: ConsoleIncoming,
  metadata: ErrorMetadata,
): ConsoleStoreState {
  switch (message.type) {
    case "console.hello":
      return {
        ...state,
        connection: "online",
        sessionId: message.session_id,
        lease: leaseState(message.lease),
        targets: state.targets.map((target) => ({
          ...target,
          active: target.id === message.target_id,
        })),
        activeTargetId: message.target_id,
        targetEpoch: message.target_epoch,
        deviceState: detached(message.state),
        lastError: null,
      };
    case "console.state":
      return reduceFullState(state, message, metadata);
    case "console.event":
      return reduceEvent(state, message, metadata);
    case "console.error": {
      const next = {
        ...state,
        lease: losesControl(message.code) ? ({ role: "readonly" } as const) : state.lease,
      };
      const withError = appendError(next, message, metadata, message.request_id);
      return message.request_id
        ? failCommand(withError, message.request_id, message)
        : withError;
    }
    case "console.control":
      return { ...state, lease: leaseState(message.lease), lastError: null };
  }
}

function reduceFullState(
  state: ConsoleStoreState,
  message: Extract<ConsoleIncoming, { type: "console.state" }>,
  metadata: ErrorMetadata,
): ConsoleStoreState {
  if (message.target_epoch < state.targetEpoch) return state;
  if (message.target_epoch === state.targetEpoch && state.deviceState !== null) {
    const currentRevision = state.deviceState.revision;
    const nextRevision = message.state.revision;
    if (nextRevision <= currentRevision) return state;
  }
  return {
    ...state,
    connection: "online",
    activeTargetId: message.target_id,
    targetEpoch: message.target_epoch,
    deviceState: detached(message.state),
    lastError: null,
    targets: state.targets.map((target) => ({
      ...target,
      active: target.id === message.target_id,
    })),
  };
}

function reduceEvent(
  state: ConsoleStoreState,
  message: ConsoleEvent,
  metadata: ErrorMetadata,
): ConsoleStoreState {
  if (message.target_epoch !== state.targetEpoch) return state;
  if (message.event.type === "device.state_changed") {
    if (state.deviceState === null) return state;
    const revision = message.event.payload.revision;
    if (!isRevision(revision)) return state;
    if (revision <= state.deviceState.revision) {
      return correlateCommand(state, message);
    }
    if (revision > state.deviceState.revision + 1) {
      return correlateCommand(revisionGap(state, revision, metadata), message);
    }
    let next = appendEvent(
      {
        ...state,
        connection: "online" as const,
        deviceState: detached(message.event.payload as DeviceState),
        lastError: null,
      },
      message,
    );
    if (message.error) next = appendError(next, message.error, metadata);
    return correlateCommand(next, message);
  }

  let next = appendEvent(state, message);
  next = correlateCommand(next, message);
  if (message.event.type === "device.error") {
    const payload = message.event.payload;
    next = appendError(
      next,
      {
        code: typeof payload.code === "string" ? payload.code : "device_error",
        message: typeof payload.message === "string" ? payload.message : "device command failed",
        recoverable: typeof payload.recoverable === "boolean" ? payload.recoverable : true,
      },
      metadata,
      correlationIdOf(message.event),
    );
  } else if (message.error) {
    next = appendError(next, message.error, metadata, correlationIdOf(message.event));
  }
  return next;
}

function addLocalCommand(state: ConsoleStoreState, command: DeviceCommand): ConsoleStoreState {
  const next: CommandRecord = {
    requestId: command.id,
    deviceCommandId: command.id,
    commandType: command.type,
    status: "sent",
    eventIds: [],
  };
  const withoutDuplicate = (items: CommandRecord[]) =>
    items.filter((item) => item.requestId !== command.id);
  return {
    ...state,
    pendingCommands: bounded([...withoutDuplicate(state.pendingCommands), next], COMMAND_RECORD_LIMIT),
    commandTimeline: bounded([...withoutDuplicate(state.commandTimeline), next], COMMAND_RECORD_LIMIT),
  };
}

function correlateCommand(state: ConsoleStoreState, routed: ConsoleEvent): ConsoleStoreState {
  const correlationId = correlationIdOf(routed.event);
  if (!correlationId) return state;
  const update = (records: CommandRecord[]) => records.map((record) => {
    if (record.requestId !== correlationId && record.deviceCommandId !== correlationId) return record;
    const payload = routed.event.payload as unknown as JsonObject;
    const status = commandStatus(record, routed.event.type, payload);
    if (status === null) return record;
    const errorCode = typeof payload.code === "string" ? payload.code : undefined;
    const errorMessage = typeof payload.message === "string" ? payload.message : undefined;
    return {
      ...record,
      status,
      eventIds: [...record.eventIds, routed.event.id],
      ...(typeof payload.progress === "number" && Number.isFinite(payload.progress)
        ? { progress: payload.progress }
        : {}),
      ...(status === "failed" && (errorCode !== undefined || errorMessage !== undefined)
        ? {
            error: {
              ...record.error,
              ...(errorCode !== undefined ? { code: errorCode } : {}),
              ...(errorMessage !== undefined ? { message: errorMessage } : {}),
            },
          }
        : {}),
    };
  });
  const pending = update(state.pendingCommands);
  return {
    ...state,
    pendingCommands: pending.filter((record) => !isTerminal(record.status)),
    commandTimeline: update(state.commandTimeline),
  };
}

function commandStatus(
  record: CommandRecord,
  eventType: string,
  payload: JsonObject,
): CommandStatus | null {
  if (isTerminal(record.status)) return record.status;
  if (eventType === "device.state_changed") {
    return isMotionCommand(record.commandType) ? record.status : "finished";
  }
  if (eventType === "command.accepted") return "accepted";
  if (eventType.endsWith(".started")) return "started";
  if (eventType.endsWith(".progress")) return "progress";
  if (eventType.endsWith(".finished")) {
    return payload.status === "failed" ? "failed" : "finished";
  }
  if (eventType === "device.error" || eventType.endsWith(".error")) return "failed";
  return null;
}

function correlationIdOf(event: ConsoleEvent["event"]): string | undefined {
  return "correlation_id" in event && typeof event.correlation_id === "string"
    ? event.correlation_id
    : undefined;
}

function failCommand(
  state: ConsoleStoreState,
  requestId: string,
  error: { code: string; message: string },
): ConsoleStoreState {
  const update = (record: CommandRecord): CommandRecord =>
    record.requestId === requestId || record.deviceCommandId === requestId
      ? {
          ...record,
          status: "failed",
          error: { code: error.code, message: error.message },
        }
      : record;
  return {
    ...state,
    pendingCommands: state.pendingCommands.map(update).filter((record) => !isTerminal(record.status)),
    commandTimeline: state.commandTimeline.map(update),
  };
}

function isMotionCommand(commandType: string): boolean {
  return commandType.startsWith("motion.") || commandType === "device.rest";
}

function isTerminal(status: CommandStatus): boolean {
  return status === "finished" || status === "failed";
}

function appendEvent(state: ConsoleStoreState, message: ConsoleEvent): ConsoleStoreState {
  return {
    ...state,
    events: bounded([...state.events, detached(message)], CONSOLE_EVENT_LIMIT),
  };
}

function appendError(
  state: ConsoleStoreState,
  error: { code: string; message: string; recoverable: boolean },
  metadata: ErrorMetadata,
  requestId?: string,
): ConsoleStoreState {
  const record: ConsoleErrorRecord = {
    id: metadata.errorId,
    time: metadata.receivedAt,
    code: error.code,
    message: error.message,
    recoverable: error.recoverable,
    ...(requestId ? { requestId } : {}),
  };
  return {
    ...state,
    lastError: record,
    errors: bounded([...state.errors, record], ERROR_RECORD_LIMIT),
  };
}

function revisionGap(
  state: ConsoleStoreState,
  receivedRevision: number,
  metadata: ErrorMetadata,
): ConsoleStoreState {
  const expected = (state.deviceState?.revision ?? -1) + 1;
  return appendError(
    { ...state, connection: "stale" },
    {
      code: "revision_gap",
      message: `state revision gap: expected ${expected}, received ${receivedRevision}`,
      recoverable: true,
    },
    metadata,
  );
}

function leaseState(lease: ConsoleLease): LeaseState {
  return lease.role === "readonly"
    ? { role: "readonly" }
    : { role: "controller", expiresAt: lease.expires_at };
}

function losesControl(code: string): boolean {
  return code === "invalid_control_lease" || code === "read_only";
}

function isRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isPlainObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function detached<T>(value: T): T {
  if (Array.isArray(value)) return value.map((item) => detached(item)) as T;
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, detached(item)]),
    ) as T;
  }
  return value;
}

function bounded<T>(items: T[], limit: number): T[] {
  return items.length <= limit ? items : items.slice(items.length - limit);
}
