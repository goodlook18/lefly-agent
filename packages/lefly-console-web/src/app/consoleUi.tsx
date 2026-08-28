import type { ReactNode } from "react";

import type { JsonObject } from "../protocol";
import type { CommandType } from "../deviceProtocol";
import type { ConsoleStoreState, TargetSummary } from "../state/consoleStore";
import { LEFLY_JOINT_NAMES, type LeFlyJointName } from "../scene/createLeFlyModel";

export type WorkspaceId = "overview" | "voice" | "motion" | "lighting" | "sensors" | "diagnostics";

export interface CommandGateway {
  state: ConsoleStoreState;
  target: TargetSummary | null;
  disabledReason: (capability: CommandType, options?: { motionStart?: boolean }) => string | null;
  sensorInjectionDisabledReason: () => string | null;
  absoluteMoveDisabledReason: (joints: Record<string, number>) => string | null;
  sendCommand: (capability: CommandType, payload: JsonObject, options?: { motionStart?: boolean }) => boolean;
  requestAbsoluteMove: (joints: Record<string, number>) => boolean;
  sendSensor: (sensorType: string, payload: JsonObject) => boolean;
}

export interface ReportedJointTelemetry {
  pos: number;
  min: number;
  max: number;
}

export interface WorkspaceProps {
  gateway: CommandGateway;
}

export interface ActionButtonProps {
  children: ReactNode;
  className?: string;
  disabledReason?: string | null;
  onClick: () => void;
  ariaLabel?: string;
  title?: string;
  type?: "button" | "submit";
}

export function ActionButton({
  children,
  className = "",
  disabledReason,
  onClick,
  ariaLabel,
  title,
  type = "button",
}: ActionButtonProps) {
  return (
    <button
      type={type}
      className={className}
      onClick={onClick}
      disabled={Boolean(disabledReason)}
      aria-label={ariaLabel}
      title={disabledReason ?? title}
    >
      {children}
    </button>
  );
}

export function capabilityLabel(capability: string): string {
  const labels: Record<string, string> = {
    "motion.play": "预设动作",
    "motion.absolute_move": "绝对关节运动",
    "device.rest": "进入休息状态",
    "light.solid": "头部灯颜色",
    "light.brightness": "头部灯亮度",
    "status.set": "状态灯条控制",
    "sensor.inject": "传感器注入",
    "voice.control": "语音控制",
  };
  return labels[capability] ?? capability;
}

export function connectionLabel(connection: ConsoleStoreState["connection"]): string {
  return ({ connecting: "连接中", online: "在线", stale: "状态陈旧", offline: "离线" })[connection];
}

export function motionIsBusy(state: ConsoleStoreState): boolean {
  const motionState = state.deviceState?.motion.state;
  const queueSize = state.deviceState?.command_queue?.size ?? 0;
  const pendingMotion = state.pendingCommands.some((command) =>
    command.commandType.startsWith("motion.") || command.commandType === "device.rest"
  );
  return motionState === "moving" || queueSize > 0 || pendingMotion;
}

export function jointPosition(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "object" && value !== null && "pos" in value) {
    const position = (value as { pos?: unknown }).pos;
    return typeof position === "number" && Number.isFinite(position) ? position : null;
  }
  return null;
}

export function reportedJointTelemetry(value: unknown): ReportedJointTelemetry | null {
  if (typeof value !== "object" || value === null) return null;
  const joint = value as { pos?: unknown; min?: unknown; max?: unknown };
  if (![joint.pos, joint.min, joint.max].every((item) => typeof item === "number" && Number.isFinite(item))) {
    return null;
  }
  const { pos, min, max } = joint as ReportedJointTelemetry;
  return min <= pos && pos <= max ? { pos, min, max } : null;
}

export function completeJointTelemetry(joints: unknown): Record<LeFlyJointName, ReportedJointTelemetry> | null {
  if (typeof joints !== "object" || joints === null) return null;
  const values = joints as Record<string, unknown>;
  const entries = LEFLY_JOINT_NAMES.map((name) => [name, reportedJointTelemetry(values[name])] as const);
  if (entries.some(([, telemetry]) => telemetry === null)) return null;
  return Object.fromEntries(entries) as Record<LeFlyJointName, ReportedJointTelemetry>;
}

export function completeJointPositions(joints: unknown): Record<LeFlyJointName, number> | null {
  if (typeof joints !== "object" || joints === null) return null;
  const values = joints as Record<string, unknown>;
  const entries = LEFLY_JOINT_NAMES.map((name) => [name, jointPosition(values[name])] as const);
  if (entries.some(([, position]) => position === null)) return null;
  return Object.fromEntries(entries) as Record<LeFlyJointName, number>;
}

export function numericField(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function displayTime(timestamp: string): string {
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? "--:--:--" : parsed.toLocaleTimeString("zh-CN", { hour12: false });
}
