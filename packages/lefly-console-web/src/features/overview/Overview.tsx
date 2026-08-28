import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  ArrowDown,
  ArrowDownUp,
  ArrowLeft,
  ArrowLeftRight,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronUp,
  CircleOff,
  CirclePause,
  Gauge,
  Lightbulb,
  Moon,
  Music2,
  Play,
  Power,
  Settings2,
  Sparkles,
  Upload,
} from "lucide-react";

import { LEFLY_JOINT_NAMES, type JointPositions, type LeFlyJointName } from "../../scene/createLeFlyModel";
import { LeFlyScene } from "../../scene/LeFlyScene";
import {
  ActionButton,
  completeJointTelemetry,
  displayTime,
  jointPosition,
  numericField,
  type WorkspaceProps,
} from "../../app/consoleUi";
import type { AgentControl } from "../../agent/agentControl";
import type { AgentToolProgress } from "../../agent/agentStore";

const QUICK_COLORS = ["#FFFFFF", "#F1A22E", "#20A8B5", "#2F9D68", "#F05D5E"];
const JOINT_LABELS: Record<string, string> = {
  base_yaw: "底座偏航",
  base_pitch: "底座俯仰",
  elbow_pitch: "肘部俯仰",
  wrist_pitch: "腕部俯仰",
  wrist_roll: "腕部滚转",
};

const HEAD_LIGHT_PRESETS = [
  { label: "明亮白光", color: "#FFFFFF", brightness: 1 },
  { label: "温暖黄光", color: "#F1A22E", brightness: 0.72 },
  { label: "柔和蓝光", color: "#20A8B5", brightness: 0.58 },
  { label: "夜间微光", color: "#F6D7A8", brightness: 0.18 },
  { label: "灯光预设：关闭", color: null, brightness: 0 },
] as const;

const CUSTOM_ACTION_REASON = "动作库导入接口尚未接入";

const PRESET_ICONS: Record<string, typeof Play> = {
  wake: Power,
  wake_up: Power,
  nod: ArrowDownUp,
  shake: ArrowLeftRight,
  headshake: ArrowLeftRight,
  happy_wiggle: Sparkles,
  look_up: ArrowUp,
  look_down: ArrowDown,
  look_left: ArrowLeft,
  look_right: ArrowRight,
  dance_demo: Music2,
  sleep: Moon,
};

function BilingualRailLabel({ chinese, english }: { chinese: string; english: string }) {
  return <span className="rail-bilingual-label"><strong>{chinese}</strong>{" "}<small>{english}</small></span>;
}

function jointDraftFromTelemetry(
  telemetry: ReturnType<typeof completeJointTelemetry>,
): Record<string, number> {
  if (telemetry === null) return {};
  return Object.fromEntries(LEFLY_JOINT_NAMES.map((name) => [name, telemetry[name].pos]));
}

interface OverviewProps extends WorkspaceProps {
  agentControl: AgentControl;
}

export function Overview({ gateway, agentControl }: OverviewProps) {
  const device = gateway.state.deviceState;
  const reportedJoints = (device?.motion.joints ?? {}) as JointPositions;
  const completeTelemetry = useMemo(() => completeJointTelemetry(reportedJoints), [reportedJoints]);
  const currentPositions = useMemo(() => jointDraftFromTelemetry(completeTelemetry), [completeTelemetry]);
  const telemetryAvailable = completeTelemetry !== null;
  const isSimulator = gateway.target?.kind === "simulator";
  const isRemote = gateway.target?.kind === "remote";
  const [jointEditorOpen, setJointEditorOpen] = useState(false);
  const [remoteJointDraft, setRemoteJointDraft] = useState<Record<string, number>>({});
  const [simulatorPreview, setSimulatorPreview] = useState<Record<string, number>>({});
  const pendingSimulatorJoint = useRef<{ name: LeFlyJointName; value: number } | null>(null);
  const simulatorCommitPending = useRef(false);
  const simulatorMoveInFlight = useRef(false);
  const [agentText, setAgentText] = useState("");
  const [agentPending, setAgentPending] = useState(false);
  const agentInputRef = useRef<HTMLInputElement | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const isComposingRef = useRef(false);
  const brightness = numericField(device?.light.brightness, 0);
  const lightColor = device?.light.pixels[0] ?? "#FFFFFF";
  const statusStrip = device?.status_strip ?? undefined;
  const playReason = gateway.disabledReason("motion.play", { motionStart: true });
  const restReason = gateway.disabledReason("device.rest", { motionStart: true });
  const colorReason = gateway.disabledReason("light.solid");
  const brightnessReason = gateway.disabledReason("light.brightness");
  const recentEvents = gateway.state.events.slice(-5).map(({ event }) => event);
  const motionPresets = device?.capabilities.motion.presets ?? [];

  useEffect(() => {
    const list = messageListRef.current;
    if (list !== null) list.scrollTop = list.scrollHeight;
  }, [agentControl.messages]);

  useEffect(() => {
    setJointEditorOpen(false);
    setRemoteJointDraft({});
    setSimulatorPreview({});
    pendingSimulatorJoint.current = null;
    simulatorCommitPending.current = false;
    simulatorMoveInFlight.current = false;
  }, [gateway.state.activeTargetId, gateway.state.targetEpoch]);

  const joints = telemetryAvailable
    ? { ...currentPositions, ...(isSimulator ? simulatorPreview : {}) }
    : {};
  const remoteDraftEntries = Object.entries(remoteJointDraft);
  const remoteDraftIsValid = completeTelemetry !== null && remoteDraftEntries.every(([name, value]) => {
    const limits = completeTelemetry[name as LeFlyJointName];
    return Number.isFinite(value) && limits.min <= value && value <= limits.max;
  });
  const applyReason = remoteDraftEntries.length === 0
    ? "尚未修改关节目标"
    : completeTelemetry === null
    ? gateway.disabledReason("motion.absolute_move", { motionStart: true }) ?? "遥测限制缺失"
    : gateway.absoluteMoveDisabledReason(remoteJointDraft) ?? (!remoteDraftIsValid ? "关节值超出目标限制" : null);

  const toggleJointEditor = () => {
    setJointEditorOpen((open) => !open);
  };
  const sendSimulatorJoint = (name: LeFlyJointName, value: number) => {
    const sent = gateway.sendCommand(
      "motion.absolute_move",
      { joints: { [name]: value }, duration_ms: 100 },
      { motionStart: true },
    );
    if (sent) simulatorMoveInFlight.current = true;
    return sent;
  };
  const changeJointTarget = (name: LeFlyJointName, value: number) => {
    if (isRemote) {
      setRemoteJointDraft((draft) => ({ ...draft, [name]: value }));
      return;
    }
    if (!isSimulator) return;
    setSimulatorPreview((preview) => ({ ...preview, [name]: value }));
    pendingSimulatorJoint.current = { name, value };
  };
  const flushSimulatorJoint = () => {
    const pending = pendingSimulatorJoint.current;
    if (!simulatorCommitPending.current || pending === null) return;
    if (simulatorMoveInFlight.current || device?.motion.state !== "idle" || (device?.command_queue?.size ?? 0) > 0) return;
    pendingSimulatorJoint.current = null;
    simulatorCommitPending.current = false;
    if (!sendSimulatorJoint(pending.name, pending.value)) {
      pendingSimulatorJoint.current = pending;
      simulatorCommitPending.current = true;
    }
  };
  const commitSimulatorJoint = () => {
    if (!isSimulator || pendingSimulatorJoint.current === null) return;
    simulatorCommitPending.current = true;
    flushSimulatorJoint();
  };
  const applyRemoteJointDraft = () => {
    if (gateway.requestAbsoluteMove(remoteJointDraft)) setRemoteJointDraft({});
  };

  useEffect(() => {
    if (!isSimulator) {
      pendingSimulatorJoint.current = null;
      simulatorCommitPending.current = false;
      simulatorMoveInFlight.current = false;
      setSimulatorPreview({});
      return;
    }
    if (device?.motion.action !== null && device?.motion.action !== "absolute_move") {
      pendingSimulatorJoint.current = null;
      simulatorCommitPending.current = false;
      simulatorMoveInFlight.current = false;
      setSimulatorPreview({});
      return;
    }
    if (device?.motion.state !== "idle" || (device?.command_queue?.size ?? 0) > 0) return;
    simulatorMoveInFlight.current = false;
    if (simulatorCommitPending.current && pendingSimulatorJoint.current !== null) {
      flushSimulatorJoint();
    } else if (pendingSimulatorJoint.current === null) {
      setSimulatorPreview({});
    }
  }, [device?.motion.state, device?.command_queue?.size, device?.revision, isSimulator]);
  const applyHeadLightPreset = (preset: (typeof HEAD_LIGHT_PRESETS)[number]) => {
    if (preset.color !== null) {
      const colorSent = gateway.sendCommand("light.solid", { target: "head_matrix", color: preset.color });
      if (!colorSent) return;
    }
    gateway.sendCommand("light.brightness", { target: "head_matrix", brightness: preset.brightness });
  };
  const submitAgentText = (event: FormEvent) => {
    event.preventDefault();
    const text = agentText.trim();
    if (!text || isComposingRef.current || !agentControl.available || !agentControl.deviceConnected) return;
    setAgentPending(true);
    const sent = agentControl.submitText(text, {
      onAccepted: () => {
        setAgentText("");
        setAgentPending(false);
      },
      onRejected: () => setAgentPending(false),
    });
    if (!sent) setAgentPending(false);
    agentInputRef.current?.focus();
  };
  const handleAgentKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && (event.nativeEvent.isComposing || isComposingRef.current)) {
      event.preventDefault();
    }
  };

  return (
    <section className="overview" aria-labelledby="overview-title">
      <header className="workspace-heading overview-heading bilingual-workspace-heading">
        <div>
          <h1 id="overview-title">设备概览</h1>
          <p className="eyebrow">LIVE INSTRUMENT</p>
        </div>
      </header>

      <div className="overview-grid">
        <div className="overview-core">
          <div className="scene-stage">
            {telemetryAvailable ? (
              <LeFlyScene
                key={`${gateway.state.activeTargetId}:${gateway.state.targetEpoch}`}
                joints={joints}
                headLight={{ color: lightColor, brightness }}
                statusStrip={statusStrip}
                aria-label="LeFly 三维实时姿态"
              />
            ) : (
              <div className="telemetry-unavailable" role="status">
                <strong>关节遥测不可用</strong>
                <span>{gateway.state.activeTargetId ?? "未选择目标"}</span>
              </div>
            )}
            <div className="scene-reticle" aria-hidden="true"><span /><span /></div>
            <div className="telemetry-rack" aria-label="五轴遥测标尺">
              {LEFLY_JOINT_NAMES.map((name, index) => {
                const raw = joints[name];
                const value = jointPosition(raw);
                const rawMin = typeof raw === "object" && raw !== null && "min" in raw ? (raw as { min?: unknown }).min : null;
                const rawMax = typeof raw === "object" && raw !== null && "max" in raw ? (raw as { max?: unknown }).max : null;
                const min = typeof rawMin === "number" && Number.isFinite(rawMin) ? rawMin : null;
                const max = typeof rawMax === "number" && Number.isFinite(rawMax) ? rawMax : null;
                const percent = value === null || min === null || max === null
                  ? 50
                  : Math.max(0, Math.min(100, ((value - min) / (max - min || 1)) * 100));
                return (
                  <div className={`telemetry-axis axis-${index + 1}`} key={name}>
                    <div className="axis-label"><span className="joint-name">{JOINT_LABELS[name]} <code>{name}</code></span></div>
                    <div className="axis-value"><strong>{value === null ? "--" : value.toFixed(1)}°</strong><small>{min ?? "--"} / {max ?? "--"}</small></div>
                    <div className="axis-track"><span style={{ left: `${percent}%` }} /></div>
                  </div>
                );
              })}
            </div>
            <div className="scene-state-badge">
              <span className="status-dot" />
              {!telemetryAvailable ? "姿态未知" : device?.motion.state === "moving" ? "运动中" : "姿态同步"}
            </div>
            {recentEvents.length > 0 && (
              <div className="scene-event-stack" role="log" aria-label="最近设备事件">
                {recentEvents.map((event, index) => (
                  <div
                    className="scene-event-row"
                    data-testid="scene-event-row"
                    data-latest={index === recentEvents.length - 1 ? "true" : undefined}
                    key={event.id}
                  >
                    <span>{displayTime(event.timestamp)}</span>
                    <code>{event.type}</code>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="overview-chat-dock">
            <div className="agent-chat-heading">
              <strong>对话</strong>
              <span className={`agent-phase phase-${agentControl.phase}`}>
                {agentControl.available ? agentPhaseLabel(agentControl.phase) : "未连接"}
              </span>
            </div>
            <div className="agent-message-list" role="log" aria-label="最近聊天记录" ref={messageListRef}>
              {agentControl.messages.length === 0 ? (
                <p className="empty-copy">暂无对话</p>
              ) : agentControl.messages.map((message) => (
                <div className={`agent-message role-${message.role}${message.streamState ? ` stream-${message.streamState}` : ""}`} key={message.id}>
                  <span>{message.role === "user" ? "你" : message.role === "agent" ? "乐飞" : "系统"}</span>
                  <div className="agent-message-content">
                    <p>{message.text || (message.streamState === "interrupted" ? "回复中断" : "…")}</p>
                    {message.tools && message.tools.length > 0 && (
                      <div className="agent-tool-progress" role="status" aria-label="工具执行进度">
                        {message.tools.map((tool) => (
                          <span className={`tool-${tool.status}`} key={tool.toolCallId}>
                            {agentToolLabel(tool.toolName)} · {agentToolStatus(tool.status)}
                          </span>
                        ))}
                      </div>
                    )}
                    {message.streamState === "interrupted" && message.streamError && (
                      <small className="agent-stream-error">{message.streamError}</small>
                    )}
                  </div>
                  <time dateTime={message.timestamp}>{displayTime(message.timestamp)}</time>
                </div>
              ))}
            </div>
            <form className="agent-composer" onSubmit={submitAgentText}>
              <input
                ref={agentInputRef}
                type="text"
                aria-label="文本指令"
                value={agentText}
                maxLength={500}
                disabled={!agentControl.available || !agentControl.deviceConnected}
                readOnly={agentPending}
                placeholder={!agentControl.available ? "Text Agent 未连接" : !agentControl.deviceConnected ? "设备未连接" : "输入指令"}
                onChange={(event) => setAgentText(event.target.value)}
                onKeyDown={handleAgentKeyDown}
                onCompositionStart={() => { isComposingRef.current = true; }}
                onCompositionEnd={() => { isComposingRef.current = false; }}
              />
              <button type="submit" aria-label="发送文本指令" title="发送" disabled={!agentControl.available || !agentControl.deviceConnected || agentPending || agentText.trim().length === 0}>
                <ArrowRight size={16} />
              </button>
            </form>
            {agentControl.error !== null && <p className="agent-inline-error" role="alert">{agentControl.error}</p>}
          </div>
        </div>

        <aside className="quick-rail" aria-label="快速控制">
          <div className="rail-section motion-quick">
            <div className="rail-title"><Play size={16} /><BilingualRailLabel chinese="预设动作" english="Preset Actions" /><output>{motionPresets.length}</output></div>
            <div className="rail-preset-grid">
              {motionPresets.map((preset) => {
                const PresetIcon = PRESET_ICONS[preset.name] ?? Play;
                return (
                  <ActionButton
                    key={preset.name}
                    className="preset-action"
                    ariaLabel={preset.label ?? preset.name}
                    disabledReason={playReason}
                    onClick={() => gateway.sendCommand("motion.play", { name: preset.name }, { motionStart: true })}
                  >
                    <PresetIcon size={17} aria-hidden="true" />
                    <span className="preset-action-copy"><span>{preset.label ?? preset.name}</span><code>{preset.name}</code></span>
                  </ActionButton>
                );
              })}
            </div>
            <ActionButton
              className="standby-action"
              ariaLabel="进入待机状态"
              disabledReason={restReason}
              onClick={() => gateway.sendCommand("device.rest", {}, { motionStart: true })}
            >
              <CirclePause size={17} />
              <span className="standby-action-copy"><strong>进入待机状态</strong><small>Enter Standby</small></span>
            </ActionButton>
          </div>

          <div className="rail-section light-quick">
            <div className="rail-title"><Lightbulb size={16} /><BilingualRailLabel chinese="头部灯" english="Head Light" /><output>{Math.round(brightness * 100)}%</output></div>
            <input
              aria-label="快速亮度"
              type="range"
              min="0"
              max="100"
              value={Math.round(brightness * 100)}
              style={{
                "--range-accent": "var(--color-hardware)",
                "--range-value": `${Math.round(brightness * 100)}%`,
              } as React.CSSProperties}
              disabled={Boolean(brightnessReason)}
              title={brightnessReason ?? "头部灯亮度"}
              onChange={(event) => gateway.sendCommand("light.brightness", { target: "head_matrix", brightness: Number(event.target.value) / 100 })}
            />
            <div className="swatch-row" aria-label="快速颜色">
              {QUICK_COLORS.map((color) => (
                <button
                  key={color}
                  className={`color-swatch ${lightColor.toUpperCase() === color ? "selected" : ""}`}
                  style={{ "--swatch": color } as React.CSSProperties}
                  aria-label={`设为 ${color}`}
                  aria-pressed={lightColor.toUpperCase() === color}
                  title={colorReason ?? color}
                  disabled={Boolean(colorReason)}
                  onClick={() => gateway.sendCommand("light.solid", { target: "head_matrix", color })}
                />
              ))}
            </div>
            <ActionButton className="icon-text-action" ariaLabel="关闭头部灯" disabledReason={brightnessReason} onClick={() => gateway.sendCommand("light.brightness", { target: "head_matrix", brightness: 0 })}>
              <CircleOff size={16} /><span className="action-bilingual-copy"><strong>关闭</strong>{" "}<small>Off</small></span>
            </ActionButton>
          </div>

          <div className="rail-section light-presets-quick">
            <div className="rail-title"><Lightbulb size={16} /><BilingualRailLabel chinese="头部灯预设" english="Light Presets" /></div>
            <div className="light-preset-grid">
              {HEAD_LIGHT_PRESETS.map((preset) => {
                const disabledReason = preset.color === null ? brightnessReason : colorReason ?? brightnessReason;
                return (
                  <ActionButton
                    key={preset.label}
                    className="light-preset-action"
                    ariaLabel={preset.label}
                    disabledReason={disabledReason}
                    onClick={() => applyHeadLightPreset(preset)}
                  >
                    <span className="preset-light-chip" style={{ "--preset-color": preset.color ?? "#111820" } as React.CSSProperties} />
                    <span>{preset.label.replace("灯光预设：", "")}</span>
                    <small>{Math.round(preset.brightness * 100)}%</small>
                  </ActionButton>
                );
              })}
            </div>
          </div>

          <div className={`rail-section joint-disclosure ${jointEditorOpen ? "open" : ""}`}>
            <button
              type="button"
              className="rail-disclosure-button"
              aria-label={jointEditorOpen ? "收起五关节调节" : "展开五关节调节"}
              aria-expanded={jointEditorOpen}
              onClick={toggleJointEditor}
            >
              <Gauge size={16} />
              <BilingualRailLabel chinese="五关节调节" english="Joint Control" />
              {jointEditorOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
            {jointEditorOpen && (
              <div className="rail-joint-editor">
                {LEFLY_JOINT_NAMES.map((name: LeFlyJointName) => {
                  const telemetry = completeTelemetry?.[name] ?? null;
                  const remoteTarget = remoteJointDraft[name];
                  const simulatorTarget = simulatorPreview[name];
                  const hasRemoteTarget = isRemote && remoteTarget !== undefined;
                  const hasSimulatorPreview = isSimulator && simulatorTarget !== undefined;
                  const value = telemetry === null
                    ? 0
                    : hasRemoteTarget
                      ? remoteTarget
                      : hasSimulatorPreview
                        ? simulatorTarget
                        : telemetry.pos;
                  const motionBusy = device?.motion.state !== "idle" || (device?.command_queue?.size ?? 0) > 0;
                  const controlDisabled = telemetry === null
                    || (!isSimulator && !isRemote)
                    || (isRemote && motionBusy)
                    || (isSimulator
                      && motionBusy
                      && device?.motion.action !== "absolute_move"
                      && !simulatorMoveInFlight.current);
                  return (
                    <label className="rail-joint-control" key={name}>
                      <span className="joint-name"><strong>{JOINT_LABELS[name]}</strong> <code>{name}</code></span>
                      <output>
                        {telemetry === null
                          ? "--"
                          : hasRemoteTarget
                            ? `当前 ${telemetry.pos.toFixed(0)}° / 目标 ${value.toFixed(0)}°`
                            : `${value.toFixed(0)}°`}
                      </output>
                      <input
                        aria-label={`概览 ${name}`}
                        type="range"
                        min={telemetry?.min ?? 0}
                        max={telemetry?.max ?? 1}
                        step="1"
                        value={value}
                        style={{
                          "--range-accent": "var(--color-primary)",
                          "--range-value": telemetry === null
                            ? "0%"
                            : `${Math.max(0, Math.min(100, ((value - telemetry.min) / (telemetry.max - telemetry.min || 1)) * 100))}%`,
                        } as React.CSSProperties}
                        disabled={controlDisabled}
                        title={telemetry === null ? "遥测限制缺失" : `${telemetry.min}° 至 ${telemetry.max}°`}
                        onChange={(event) => changeJointTarget(name, Number(event.target.value))}
                        onPointerUp={commitSimulatorJoint}
                        onPointerCancel={commitSimulatorJoint}
                        onKeyUp={commitSimulatorJoint}
                        onBlur={commitSimulatorJoint}
                      />
                      <small>{telemetry === null ? "遥测限制缺失" : `${telemetry.min}° 至 ${telemetry.max}°`}</small>
                    </label>
                  );
                })}
                {isRemote && (
                  <div className="joint-draft-actions">
                    <ActionButton className="compact-action" ariaLabel="取消关节修改" disabledReason={remoteDraftEntries.length === 0 ? "尚未修改关节目标" : null} onClick={() => setRemoteJointDraft({})}>
                      取消修改
                    </ActionButton>
                    <ActionButton className="compact-action primary-action" ariaLabel="应用关节位置" disabledReason={applyReason} onClick={applyRemoteJointDraft}>
                      <Check size={14} />应用
                    </ActionButton>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="rail-section custom-actions-section">
            <div className="rail-title"><Settings2 size={16} /><BilingualRailLabel chinese="自定义动作" english="Custom Actions" /></div>
            <div className="custom-action-grid">
              <ActionButton className="compact-action" ariaLabel="导入自定义动作" disabledReason={CUSTOM_ACTION_REASON} onClick={() => {}}><Upload size={14} />导入</ActionButton>
              <ActionButton className="compact-action" ariaLabel="管理自定义动作" disabledReason={CUSTOM_ACTION_REASON} onClick={() => {}}><Settings2 size={14} />管理</ActionButton>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}

function agentPhaseLabel(phase: AgentControl["phase"]): string {
  if (phase === "interpreting") return "理解中";
  if (phase === "executing") return "执行中";
  if (phase === "error") return "异常";
  return "在线";
}

function agentToolLabel(tool: AgentToolProgress["toolName"]): string {
  const labels: Record<AgentToolProgress["toolName"], string> = {
    play_motion: "执行动作",
    set_head_light: "设置头灯",
    set_head_light_brightness: "调节亮度",
    enter_rest_state: "进入休息",
    get_current_datetime: "查询时间",
    get_weather: "查询天气",
    web_search: "搜索信息",
  };
  return labels[tool];
}

function agentToolStatus(status: AgentToolProgress["status"]): string {
  if (status === "running") return "进行中";
  if (status === "completed") return "已完成";
  return "失败";
}
