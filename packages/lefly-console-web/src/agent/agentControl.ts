import type { AgentChatMessage, AgentPhase } from "./agentProtocol";
import type { AgentResponseDraftStatus, AgentStoreState, AgentToolProgress } from "./agentStore";

export interface AgentConversationMessage extends AgentChatMessage {
  streamState?: AgentResponseDraftStatus;
  streamError?: string;
  tools?: readonly AgentToolProgress[];
}

export interface AgentControl {
  available: boolean;
  voiceAvailable: boolean;
  phase: AgentPhase;
  deviceConnected: boolean;
  messages: readonly AgentConversationMessage[];
  error: string | null;
  submitText(text: string, callbacks?: AgentSubmissionCallbacks): boolean;
}

export interface AgentSubmissionCallbacks {
  onAccepted?(): void;
  onRejected?(message: string): void;
}

export function createAgentControl(
  state: AgentStoreState,
  sendText: (text: string, callbacks?: AgentSubmissionCallbacks) => boolean,
): AgentControl {
  const toolsFor = (responseId: string) => state.toolProgress.filter((tool) => tool.responseId === responseId);
  const committed: AgentConversationMessage[] = state.messages.map((message) => {
    const tools = toolsFor(message.id);
    return tools.length === 0 ? message : { ...message, tools };
  });
  const committedIds = new Set(committed.map((message) => message.id));
  const drafts: AgentConversationMessage[] = state.responseDrafts
    .filter((draft) => !committedIds.has(draft.responseId))
    .map((draft) => {
      const tools = toolsFor(draft.responseId);
      return {
        id: draft.responseId,
        role: "agent",
        text: draft.text,
        timestamp: draft.startedAt,
        streamState: draft.status,
        ...(draft.error === undefined ? {} : { streamError: draft.error }),
        ...(tools.length === 0 ? {} : { tools }),
      };
    });
  return {
    available: state.connection === "online",
    voiceAvailable: false,
    phase: state.phase,
    deviceConnected: state.deviceConnected,
    messages: [...committed, ...drafts],
    error: state.error,
    submitText: (text, callbacks) => sendText(text, callbacks),
  };
}

export const unavailableAgentControl: AgentControl = {
  available: false,
  voiceAvailable: false,
  phase: "idle",
  deviceConnected: false,
  messages: [],
  error: null,
  submitText: () => false,
};
