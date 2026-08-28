import { StrictMode, useEffect, useMemo, useReducer, useRef } from "react";
import { createRoot } from "react-dom/client";

import { createAgentControl } from "./agent/agentControl";
import { agentReducer, createInitialAgentState } from "./agent/agentStore";
import { AgentSocket } from "./api/agentSocket";
import { App } from "./app/App";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("missing #root element");

function ConnectedApp() {
  const [agentState, dispatchAgent] = useReducer(agentReducer, undefined, createInitialAgentState);
  const agentSocketRef = useRef<AgentSocket | null>(null);
  if (agentSocketRef.current === null) agentSocketRef.current = new AgentSocket(dispatchAgent);
  const agentSocket = agentSocketRef.current;

  useEffect(() => {
    agentSocket.connect();
    return () => agentSocket.close();
  }, [agentSocket]);

  const agentControl = useMemo(
    () => createAgentControl(agentState, (text, callbacks) => agentSocket.sendText(text, callbacks)),
    [agentSocket, agentState],
  );

  return <App agentControl={agentControl} />;
}

createRoot(root).render(
  <StrictMode>
    <ConnectedApp />
  </StrictMode>,
);
