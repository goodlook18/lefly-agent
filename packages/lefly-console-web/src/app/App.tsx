import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import packageMetadata from "../../package.json";
import {
  Activity,
  AlertTriangle,
  AudioLines,
  ChevronDown,
  CircleGauge,
  LayoutDashboard,
  Lightbulb,
  LockKeyhole,
  Move3D,
  RadioTower,
  ShieldCheck,
  Wifi,
  WifiOff,
} from "lucide-react";

import { ConsoleSocket } from "../api/consoleSocket";
import { unavailableAgentControl, type AgentControl } from "../agent/agentControl";
import type { ConsoleOutgoing, DeviceCommand, JsonObject } from "../protocol";
import type { CommandType } from "../deviceProtocol";
import {
  consoleReducer,
  createInitialConsoleState,
  type ConsoleAction,
  type TargetSummary,
} from "../state/consoleStore";
import { Diagnostics } from "../features/diagnostics/Diagnostics";
import { LightingPanel } from "../features/lighting/LightingPanel";
import { MotionPanel } from "../features/motion/MotionPanel";
import { Overview } from "../features/overview/Overview";
import { SensorsPanel } from "../features/sensors/SensorsPanel";
import {
  connectionLabel,
  motionIsBusy,
  reportedJointTelemetry,
  type CommandGateway,
  type WorkspaceId,
} from "./consoleUi";

export interface ConsoleTransport {
  connect(): void;
  close(): void;
  send(message: ConsoleOutgoing): boolean;
}

export interface AppProps {
  transportFactory?: (dispatch: (action: ConsoleAction) => void) => ConsoleTransport;
  fetcher?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  idFactory?: () => string;
  now?: () => Date;
  agentControl?: AgentControl;
}

interface PendingRemoteMove {
  targetId: string;
  targetEpoch: number;
  joints: Record<string, number>;
}

const CONSOLE_VERSION = `v${packageMetadata.version}`;

const WORKSPACES: Array<{ id: WorkspaceId; label: string; englishLabel: string; icon: typeof LayoutDashboard }> = [
  { id: "overview", label: "概览", englishLabel: "Overview", icon: LayoutDashboard },
  { id: "voice", label: "语音交互", englishLabel: "Voice", icon: AudioLines },
  { id: "motion", label: "动作", englishLabel: "Motion", icon: Move3D },
  { id: "lighting", label: "灯光", englishLabel: "Lighting", icon: Lightbulb },
  { id: "sensors", label: "传感器", englishLabel: "Sensors", icon: RadioTower },
  { id: "diagnostics", label: "诊断", englishLabel: "Diagnostics", icon: Activity },
];

const defaultTransportFactory = (dispatch: (action: ConsoleAction) => void): ConsoleTransport =>
  new ConsoleSocket(dispatch);
const defaultFetcher = (input: RequestInfo | URL, init?: RequestInit) =>
  globalThis.fetch(input, init);
const defaultNow = () => new Date();
export function App({
  transportFactory = defaultTransportFactory,
  fetcher = defaultFetcher,
  idFactory = defaultId,
  now = defaultNow,
  agentControl = unavailableAgentControl,
}: AppProps = {}) {
  const [state, dispatch] = useReducer(consoleReducer, undefined, createInitialConsoleState);
  const [workspace, setWorkspace] = useState<WorkspaceId>("overview");
  const [pendingRemoteMove, setPendingRemoteMove] = useState<PendingRemoteMove | null>(null);
  const transportRef = useRef<ConsoleTransport | null>(null);
  if (transportRef.current === null) transportRef.current = transportFactory(dispatch);
  const transport = transportRef.current;

  useEffect(() => {
    const controller = new AbortController();
    transport.connect();
    void fetcher("/api/targets", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`targets request failed: ${response.status}`);
        const value = await response.json() as { targets?: unknown };
        if (!Array.isArray(value.targets)) throw new Error("targets response is invalid");
        dispatch({ type: "targets_loaded", targets: value.targets.filter(isTargetSummary) });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        dispatch({
          type: "protocol_error",
          errorId: idFactory(),
          receivedAt: now().getTime(),
          error: {
            type: "console.error",
            code: "targets_load_failed",
            message: error instanceof Error ? error.message : "targets request failed",
            recoverable: true,
          },
        });
      });
    return () => {
      controller.abort();
      transport.close();
    };
  }, [fetcher, idFactory, now, transport]);

  useEffect(() => {
    if (state.lease.role !== "controller") return;
    const delay = leaseRenewDelayMs(state.lease.expiresAt, now().getTime());
    if (delay === null) return;
    const timer = globalThis.setTimeout(() => {
      transport.send({ type: "console.renew_control" });
    }, delay);
    return () => globalThis.clearTimeout(timer);
  }, [now, state.lease, transport]);

  useEffect(() => {
    setPendingRemoteMove(null);
  }, [state.activeTargetId, state.targetEpoch]);

  const target = useMemo(() => {
    const found = state.targets.find((item) => item.id === state.activeTargetId)
      ?? state.targets.find((item) => item.active);
    if (found) return found;
    return state.activeTargetId ? { id: state.activeTargetId, active: true } : null;
  }, [state.activeTargetId, state.targets]);

  const controlUnavailableReason = (): string | null => {
    if (!state.activeTargetId || !state.deviceState) return "未选择可用目标";
    if (state.connection === "stale") return "状态陈旧，等待重新同步";
    if (state.connection !== "online") return "目标离线或仍在连接";
    if (state.deviceState.connection === "offline") return "目标离线";
    if (state.lease.role !== "controller") return "控制权由其他 Console 页面持有";
    return null;
  };

  const disabledReason = (capability: CommandType, options: { motionStart?: boolean } = {}): string | null => {
    const unavailable = controlUnavailableReason();
    if (unavailable !== null || !state.deviceState) return unavailable;
    if (state.deviceState.capabilities.commands[capability]?.scope !== "control") return `当前目标不支持 ${capability}`;
    if (options.motionStart && motionIsBusy(state)) return "动作运行中或命令队列忙";
    return null;
  };

  const sendCommand = (capability: CommandType, payload: JsonObject, options: { motionStart?: boolean } = {}): boolean => {
    if (disabledReason(capability, options) !== null || !state.deviceState) return false;
    const command: DeviceCommand = {
      version: "1",
      id: idFactory(),
      type: capability,
      timestamp: now().toISOString(),
      payload,
      device_id: state.deviceState.device_id,
    } as DeviceCommand;
    return transport.send({ type: "console.command", target_epoch: state.targetEpoch, command });
  };

  const sendSensor = (sensorType: string, payload: JsonObject): boolean => {
    if (target?.kind !== "simulator" || controlUnavailableReason() !== null) return false;
    return transport.send({ type: "console.inject_sensor", sensor_type: sensorType, payload });
  };

  const absoluteMoveDisabledReason = (joints: Record<string, number>): string | null => {
    const commandReason = disabledReason("motion.absolute_move", { motionStart: true });
    if (commandReason !== null) return commandReason;
    const entries = Object.entries(joints);
    if (entries.length === 0) return "未提供关节目标值";
    const reportedJoints = state.deviceState?.motion.joints;
    for (const [name, value] of entries) {
      const telemetry = reportedJointTelemetry(reportedJoints?.[name]);
      if (telemetry === null) return `${name} 遥测限制缺失`;
      if (!Number.isFinite(value)) return `${name} 目标值无效`;
      if (value < telemetry.min || value > telemetry.max) {
        return `${name} 目标 ${value}° 超出范围 ${telemetry.min}° 至 ${telemetry.max}°`;
      }
    }
    return null;
  };

  const requestAbsoluteMove = (joints: Record<string, number>): boolean => {
    if (absoluteMoveDisabledReason(joints) !== null) return false;
    if (target?.kind === "remote" && state.activeTargetId !== null) {
      setPendingRemoteMove({
        targetId: state.activeTargetId,
        targetEpoch: state.targetEpoch,
        joints: { ...joints },
      });
      return true;
    }
    return sendCommand("motion.absolute_move", { joints, duration_ms: 300 }, { motionStart: true });
  };

  const gateway: CommandGateway = {
    state,
    target,
    disabledReason,
    sensorInjectionDisabledReason: () => target?.kind === "simulator"
      ? controlUnavailableReason()
      : "仅模拟器支持事件注入",
    absoluteMoveDisabledReason,
    sendCommand,
    requestAbsoluteMove,
    sendSensor,
  };
  const switchReason = state.lease.role !== "controller"
    ? "接管控制权后可切换目标"
    : motionIsBusy(state) ? "动作运行或队列忙时不能切换目标" : null;

  return (
    <div className="console-shell">
      <header className="global-header">
        <div className="brand-block"><CircleGauge size={24} /><div><strong>LeFly</strong><span>统一控制台</span></div></div>
        <div className="target-control">
          <label htmlFor="target-select">目标设备 <small>Target Device</small></label>
          <div className="select-wrap">
            <select
              id="target-select"
              aria-label="目标设备"
              value={state.activeTargetId ?? ""}
              disabled={Boolean(switchReason)}
              title={switchReason ?? "选择控制目标"}
              onChange={(event) => transport.send({ type: "console.select_target", target_id: event.target.value })}
            >
              {!state.activeTargetId && <option value="">未选择</option>}
              {state.targets.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.kind === "simulator" ? "模拟器" : item.kind === "remote" ? "远程设备" : item.id} · {item.id}
                </option>
              ))}
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </div>
        </div>

        <div className="global-facts">
          <div className={`connection-fact ${state.connection}`}>
            {state.connection === "online" ? <Wifi size={16} /> : <WifiOff size={16} />}
            <span>{connectionLabel(state.connection)}</span>
          </div>
          {state.lease.role === "controller" ? (
            <div className="lease-fact controller"><ShieldCheck size={16} /><span>controller</span></div>
          ) : (
            <button
              className="lease-action"
              aria-label="接管控制权"
              title="从其他 Console 页面接管控制权"
              onClick={() => transport.send({ type: "console.acquire_control" })}
            >
              <LockKeyhole size={16} />接管控制权
            </button>
          )}
        </div>

      </header>

      <div className="console-body">
        <nav className="workspace-nav" aria-label="工作区">
          {WORKSPACES.map(({ id, label, englishLabel, icon: Icon }) => (
            <button key={id} className={workspace === id ? "active" : ""} aria-label={label} aria-current={workspace === id ? "page" : undefined} onClick={() => setWorkspace(id)} title={label}>
              <Icon size={19} />
              <span className="nav-bilingual-label"><strong>{label}</strong><small>{englishLabel}</small></span>
            </button>
          ))}
          <div className="nav-version-mark"><small>VERSION</small><strong>{CONSOLE_VERSION}</strong></div>
        </nav>

        <main className={`workspace-content workspace-${workspace}`}>
          {workspace === "overview" && <Overview gateway={gateway} agentControl={agentControl} />}
          {workspace === "voice" && <VoiceWorkspace agentControl={agentControl} />}
          {workspace === "motion" && (
            <MotionPanel
              key={`${state.activeTargetId}:${state.targetEpoch}`}
              gateway={gateway}
            />
          )}
          {workspace === "lighting" && <LightingPanel gateway={gateway} />}
          {workspace === "sensors" && <SensorsPanel gateway={gateway} />}
          {workspace === "diagnostics" && <Diagnostics gateway={gateway} />}
        </main>
      </div>

      <footer className="status-footer">
        <FooterFact label="设备" value={connectionLabel(state.connection)} tone={state.connection === "online" ? "good" : "warn"} />
        <FooterFact label="Agent" value={agentControl.available ? "已接入" : "未接入"} />
        <FooterFact label="语音" value={agentControl.voiceAvailable ? "可用" : "不可用"} />
        <FooterFact label="命令队列" value={`${state.deviceState?.command_queue?.size ?? 0}/${state.deviceState?.command_queue?.capacity ?? "--"}`} tone={motionIsBusy(state) ? "motion" : "good"} />
        <span className="footer-spacer" />
        <span className="footer-revision">STATE REV <code>{state.deviceState?.revision ?? "--"}</code></span>
        <span className="footer-session">SESSION ID <code>{state.sessionId ?? "--"}</code></span>
      </footer>

      {pendingRemoteMove && (
        <div className="dialog-backdrop">
          <section className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="remote-motion-title">
            <div className="dialog-icon"><AlertTriangle size={20} /></div>
            <div className="dialog-copy">
              <p className="eyebrow">PHYSICAL MOTION</p>
              <h2 id="remote-motion-title">确认远程关节运动</h2>
              <dl>
                <div><dt>目标</dt><dd><code>{pendingRemoteMove.targetId}</code></dd></div>
                {Object.entries(pendingRemoteMove.joints).map(([name, value]) => (
                  <div key={name}><dt>关节</dt><dd><code>{name}: {value}°</code></dd></div>
                ))}
              </dl>
              <p className="safety-state">确认物理设备周围安全。</p>
            </div>
            <div className="dialog-actions">
              <button aria-label="取消远程动作" onClick={() => setPendingRemoteMove(null)}>取消</button>
              <button
                className="danger-action"
                aria-label="确认发送远程动作"
                disabled={Boolean(absoluteMoveDisabledReason(pendingRemoteMove.joints))}
                title={absoluteMoveDisabledReason(pendingRemoteMove.joints) ?? "发送到物理目标"}
                onClick={() => {
                  if (
                    pendingRemoteMove.targetId === state.activeTargetId
                    && pendingRemoteMove.targetEpoch === state.targetEpoch
                  ) {
                    if (absoluteMoveDisabledReason(pendingRemoteMove.joints) === null) {
                      sendCommand("motion.absolute_move", { joints: pendingRemoteMove.joints, duration_ms: 300 }, { motionStart: true });
                    }
                  }
                  setPendingRemoteMove(null);
                }}
              >确认发送</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function VoiceWorkspace({ agentControl }: { agentControl: AgentControl }) {
  const available = agentControl.voiceAvailable;
  return (
    <section className="workspace-panel voice-workspace" aria-labelledby="voice-title">
      <header className="workspace-heading bilingual-workspace-heading"><div><h1 id="voice-title">语音交互</h1><p className="eyebrow">AGENT CHANNEL</p></div></header>
      <div className={`voice-availability ${available ? "available" : ""}`}>
        <AudioLines size={28} />
        <div><span>{available ? "VOICE READY" : "VOICE UNAVAILABLE"}</span><h2>{available ? "Agent Control 已连接" : "当前目标未提供语音控制能力"}</h2><p>{available ? "语音通道可用" : "Agent Control connector 未连接"}</p></div>
      </div>
      <div className="workspace-band voice-state-grid">
        <div><span>Agent Control API</span><strong>{available ? "已连接" : "未连接"}</strong></div>
        <div><span>麦克风活动</span><strong>{available ? "待机" : "--"}</strong></div>
        <div><span>播放状态</span><strong>{available ? "待机" : "--"}</strong></div>
        <div><span>最近转写</span><strong>无</strong></div>
      </div>
    </section>
  );
}

function FooterFact({ label, value, tone = "neutral" }: { label: string; value: string; tone?: string }) {
  return <span className={`footer-fact ${tone}`}><i />{label}<strong>{value}</strong></span>;
}

function isTargetSummary(value: unknown): value is TargetSummary {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === "string" && typeof item.active === "boolean";
}

function defaultId(): string {
  return typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `cmd-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function leaseRenewDelayMs(expiresAtSeconds: number, nowMs: number): number | null {
  if (!Number.isFinite(expiresAtSeconds) || !Number.isFinite(nowMs)) return null;
  const remaining = expiresAtSeconds * 1000 - nowMs;
  if (remaining <= 0) return 0;
  const preferred = Math.min(remaining / 2, Math.max(0, remaining - 5_000));
  return Math.min(30_000, Math.max(250, preferred));
}
