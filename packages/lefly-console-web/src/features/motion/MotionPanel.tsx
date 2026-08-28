import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Check, CircleOff, Gauge, MoveHorizontal, Play } from "lucide-react";

import { LEFLY_JOINT_NAMES } from "../../scene/createLeFlyModel";
import {
  ActionButton,
  completeJointTelemetry,
  reportedJointTelemetry,
  type WorkspaceProps,
} from "../../app/consoleUi";

const EMPTY_JOINTS: Record<string, never> = {};

const JOINT_LABELS: Record<string, string> = {
  base_yaw: "底座偏航",
  base_pitch: "底座俯仰",
  elbow_pitch: "肘部俯仰",
  wrist_pitch: "腕部俯仰",
  wrist_roll: "腕部滚转",
};

export function MotionPanel({ gateway }: WorkspaceProps) {
  const joints = gateway.state.deviceState?.motion.joints ?? EMPTY_JOINTS;
  const completeTelemetry = useMemo(() => completeJointTelemetry(joints), [joints]);
  const current = useMemo(() => completeTelemetry === null ? {} : Object.fromEntries(
    LEFLY_JOINT_NAMES.map((name) => [name, completeTelemetry[name].pos]),
  ), [completeTelemetry]);
  const [draft, setDraft] = useState<Record<string, number>>(current);
  const leftReason = gateway.absoluteMoveDisabledReason({ base_yaw: 60 });
  const centerReason = gateway.absoluteMoveDisabledReason({ base_yaw: 0 });
  const rightReason = gateway.absoluteMoveDisabledReason({ base_yaw: -60 });
  const playReason = gateway.disabledReason("motion.play", { motionStart: true });
  const restReason = gateway.disabledReason("device.rest", { motionStart: true });
  const presets = gateway.state.deviceState?.capabilities.motion.presets ?? [];

  useEffect(() => setDraft(current), [current]);

  const move = (baseYaw: number) => gateway.requestAbsoluteMove({ base_yaw: baseYaw });
  const draftIsValid = completeTelemetry !== null && LEFLY_JOINT_NAMES.every((name) => {
    const value = draft[name];
    const limit = completeTelemetry[name];
    return Number.isFinite(value) && limit.min <= value && value <= limit.max;
  });
  const applyReason = completeTelemetry === null
    ? gateway.disabledReason("motion.absolute_move", { motionStart: true }) ?? "遥测限制缺失"
    : gateway.absoluteMoveDisabledReason(draft) ?? (!draftIsValid ? "关节值超出目标限制" : null);
  const apply = () => gateway.requestAbsoluteMove(draft);

  return (
    <section className="workspace-panel" aria-labelledby="motion-title">
      <header className="workspace-heading bilingual-workspace-heading">
        <div><h1 id="motion-title">动作</h1><p className="eyebrow">MOTION CONTROL</p></div>
        <div className="inline-status"><span className="status-dot" />{gateway.state.deviceState?.motion.state ?? "unknown"}</div>
      </header>

      <div className="workspace-band motion-presets">
        <div className="section-heading"><Play size={18} /><div><h2>预设动作</h2></div></div>
        <div className="preset-cards">
          {presets.map((preset) => (
            <ActionButton
              key={preset.name}
              className="motion-card"
              ariaLabel={preset.label ?? preset.name}
              disabledReason={playReason}
              onClick={() => gateway.sendCommand("motion.play", { name: preset.name }, { motionStart: true })}
            >
              <span>{preset.label ?? preset.name}</span><code>{preset.name}</code>
            </ActionButton>
          ))}
        </div>
      </div>

      <div className="workspace-band">
        <div className="section-heading"><MoveHorizontal size={18} /><div><h2>快速姿态</h2></div></div>
        <div className="motion-shortcuts">
          <ActionButton ariaLabel="向左" disabledReason={leftReason} onClick={() => move(60)}>向左 <code>+60°</code></ActionButton>
          <ActionButton ariaLabel="回中" disabledReason={centerReason} onClick={() => move(0)}>回中 <code>0°</code></ActionButton>
          <ActionButton ariaLabel="向右" disabledReason={rightReason} onClick={() => move(-60)}>向右 <code>-60°</code></ActionButton>
        </div>
      </div>

      <div className="workspace-band lifecycle-control">
        <div className="section-heading"><CircleOff size={18} /><div><h2>设备状态</h2></div></div>
        <ActionButton className="rest-action" ariaLabel="进入休息状态" disabledReason={restReason} onClick={() => gateway.sendCommand("device.rest", {}, { motionStart: true })}>
          <CircleOff size={16} />进入休息状态
        </ActionButton>
      </div>

      <div className="workspace-band joint-lab">
          <div className="section-heading"><Gauge size={18} /><div><h2>五轴直接控制</h2></div></div>
          <div className="joint-editor">
            {LEFLY_JOINT_NAMES.map((name) => {
              const raw = joints[name];
              const telemetry = reportedJointTelemetry(raw);
              const min = telemetry?.min ?? 0;
              const max = telemetry?.max ?? 1;
              const value = telemetry === null ? 0 : (draft[name] ?? telemetry.pos);
              return (
                <label className="joint-control" key={name}>
                  <span><strong>{JOINT_LABELS[name]}</strong><code>{name}</code></span>
                  <input
                    aria-label={name}
                    type="range"
                    min={min}
                    max={max}
                    step="1"
                    value={value}
                    style={{
                      "--range-accent": "var(--color-primary)",
                      "--range-value": telemetry === null
                        ? "0%"
                        : `${Math.max(0, Math.min(100, ((value - min) / (max - min || 1)) * 100))}%`,
                    } as CSSProperties}
                    disabled={telemetry === null}
                    title={telemetry === null ? "遥测限制缺失" : `${min}° 至 ${max}°`}
                    onChange={(event) => setDraft((values) => ({ ...values, [name]: Number(event.target.value) }))}
                  />
                  <output>{telemetry === null ? "--" : `${value.toFixed(0)}°`}</output>
                  <small>{telemetry === null ? "-- · --" : `${min}° · ${max}°`}</small>
                </label>
              );
            })}
          </div>
          <div className="apply-row">
            <span>{applyReason ?? "关节草稿可发送"}</span>
            <ActionButton className="primary-action" ariaLabel="应用关节位置" disabledReason={applyReason} onClick={apply}><Check size={16} />应用</ActionButton>
          </div>
      </div>
    </section>
  );
}
