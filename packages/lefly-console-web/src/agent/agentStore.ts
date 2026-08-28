import type { AgentChatMessage, AgentIncoming, AgentPhase, AgentToolName } from "./agentProtocol";

export const AGENT_MESSAGE_LIMIT = 100;
export const AGENT_TOOL_PROGRESS_LIMIT = 100;
export type AgentConnection = "connecting" | "online" | "offline";
export type AgentResponseDraftStatus = "streaming" | "interrupted";
export type AgentToolProgressStatus = "running" | "completed" | "failed";

export interface AgentResponseDraft {
  requestId: string;
  responseId: string;
  text: string;
  status: AgentResponseDraftStatus;
  startedAt: string;
  error?: string;
}

export interface AgentToolProgress {
  requestId: string;
  responseId: string;
  toolCallId: string;
  toolName: AgentToolName;
  status: AgentToolProgressStatus;
  protocolCorrelationId?: string;
  disposition?: "applied" | "queued";
  error?: string;
}

export interface AgentStoreState {
  connection: AgentConnection;
  sessionId: string | null;
  phase: AgentPhase;
  deviceConnected: boolean;
  queue: { size: number; capacity: number };
  messages: AgentChatMessage[];
  responseDrafts: AgentResponseDraft[];
  toolProgress: AgentToolProgress[];
  error: string | null;
}

export type AgentAction =
  | { type: "connecting" }
  | { type: "closed" }
  | { type: "incoming"; message: AgentIncoming; receivedAt?: string }
  | { type: "protocol_error"; message: string }
  | { type: "send_failed"; message: string };

export function createInitialAgentState(): AgentStoreState {
  return {
    connection: "offline",
    sessionId: null,
    phase: "idle",
    deviceConnected: false,
    queue: { size: 0, capacity: 8 },
    messages: [],
    responseDrafts: [],
    toolProgress: [],
    error: null,
  };
}

export function agentReducer(state: AgentStoreState, action: AgentAction): AgentStoreState {
  if (action.type === "connecting") return { ...state, connection: "connecting", error: null };
  if (action.type === "closed") return { ...state, connection: "offline", sessionId: null, deviceConnected: false };
  if (action.type === "protocol_error" || action.type === "send_failed") return { ...state, error: action.message };
  const message = action.message;
  switch (message.type) {
    case "agent.hello":
      return {
        ...state,
        connection: "online",
        sessionId: message.session_id,
        phase: message.state.phase,
        deviceConnected: message.state.device_connected,
        queue: message.state.queue,
        messages: uniqueMessages(message.state.messages).slice(-AGENT_MESSAGE_LIMIT),
        responseDrafts: [],
        toolProgress: [],
        error: null,
      };
    case "agent.state":
      return {
        ...state,
        phase: message.state.phase,
        deviceConnected: message.state.device_connected,
        queue: message.state.queue,
      };
    case "agent.message":
      return {
        ...state,
        messages: appendMessage(state.messages, message.message),
        responseDrafts: state.responseDrafts.filter((draft) => draft.responseId !== message.message.id),
      };
    case "agent.error":
      return { ...state, error: message.message };
    case "agent.accepted":
      return { ...state, error: null };
    case "agent.response.started": {
      if (state.messages.some((item) => item.id === message.response_id)) return state;
      if (state.responseDrafts.some((draft) => draft.responseId === message.response_id)) return state;
      const draft: AgentResponseDraft = {
        requestId: message.request_id,
        responseId: message.response_id,
        text: "",
        status: "streaming",
        startedAt: action.receivedAt ?? "",
      };
      return {
        ...state,
        responseDrafts: [...state.responseDrafts, draft].slice(-AGENT_MESSAGE_LIMIT),
      };
    }
    case "agent.response.delta": {
      if (state.messages.some((item) => item.id === message.response_id)) return state;
      const existing = state.responseDrafts.find((draft) => draft.responseId === message.response_id);
      const draft: AgentResponseDraft = existing ?? {
        requestId: message.request_id,
        responseId: message.response_id,
        text: "",
        status: "streaming",
        startedAt: action.receivedAt ?? "",
      };
      if (draft.status !== "streaming") return state;
      return {
        ...state,
        responseDrafts: upsertDraft(state.responseDrafts, { ...draft, text: draft.text + message.text }),
      };
    }
    case "agent.response.completed": {
      const draft = state.responseDrafts.find((item) => item.responseId === message.response_id);
      if (draft === undefined) return state;
      return {
        ...state,
        messages: draft.text.length === 0
          ? state.messages
          : appendMessage(state.messages, {
            id: message.response_id,
            role: "agent",
            text: draft.text,
            timestamp: action.receivedAt ?? draft.startedAt,
          }),
        responseDrafts: state.responseDrafts.filter((item) => item.responseId !== message.response_id),
      };
    }
    case "agent.response.failed": {
      const existing = state.responseDrafts.find((draft) => draft.responseId === message.response_id);
      const draft: AgentResponseDraft = existing ?? {
        requestId: message.request_id,
        responseId: message.response_id,
        text: "",
        status: "streaming",
        startedAt: action.receivedAt ?? "",
      };
      return {
        ...state,
        responseDrafts: upsertDraft(state.responseDrafts, {
          ...draft,
          status: "interrupted",
          error: message.message,
        }),
        error: message.message,
      };
    }
    case "agent.tool.started":
      return {
        ...state,
        toolProgress: upsertToolProgress(state.toolProgress, {
          requestId: message.request_id,
          responseId: message.response_id,
          toolCallId: message.tool_call_id,
          toolName: message.tool_name,
          status: "running",
        }),
      };
    case "agent.tool.completed":
      return {
        ...state,
        toolProgress: upsertToolProgress(state.toolProgress, {
          requestId: message.request_id,
          responseId: message.response_id,
          toolCallId: message.tool_call_id,
          toolName: message.tool_name,
          status: "completed",
          ...(message.protocol_correlation_id === undefined ? {} : { protocolCorrelationId: message.protocol_correlation_id }),
          ...(message.disposition === undefined ? {} : { disposition: message.disposition }),
        }),
      };
    case "agent.tool.failed":
      return {
        ...state,
        toolProgress: upsertToolProgress(state.toolProgress, {
          requestId: message.request_id,
          responseId: message.response_id,
          toolCallId: message.tool_call_id,
          toolName: message.tool_name,
          status: "failed",
          error: message.message,
        }),
      };
  }
}

function upsertDraft(drafts: AgentResponseDraft[], next: AgentResponseDraft): AgentResponseDraft[] {
  const index = drafts.findIndex((draft) => draft.responseId === next.responseId);
  if (index < 0) return [...drafts, next].slice(-AGENT_MESSAGE_LIMIT);
  return drafts.map((draft, current) => current === index ? next : draft);
}

function upsertToolProgress(tools: AgentToolProgress[], next: AgentToolProgress): AgentToolProgress[] {
  const index = tools.findIndex((tool) => tool.toolCallId === next.toolCallId);
  if (index < 0) return [...tools, next].slice(-AGENT_TOOL_PROGRESS_LIMIT);
  return tools.map((tool, current) => current === index ? next : tool);
}

function appendMessage(messages: AgentChatMessage[], message: AgentChatMessage): AgentChatMessage[] {
  if (messages.some((item) => item.id === message.id)) return messages;
  return [...messages, message].slice(-AGENT_MESSAGE_LIMIT);
}

function uniqueMessages(messages: AgentChatMessage[]): AgentChatMessage[] {
  return messages.filter((message, index) => messages.findIndex((item) => item.id === message.id) === index);
}
