import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import stateChanged from "../../../../contracts/examples/v1/events/device-state-changed.json";
import type { ConsoleOutgoing, DeviceState } from "../protocol";
import type { CapabilitySet } from "../deviceProtocol";
import type { ConsoleAction, TargetSummary } from "../state/consoleStore";
import { App, type ConsoleTransport } from "./App";

vi.mock("../scene/LeFlyScene", () => ({
  LeFlyScene: ({
    joints,
    "aria-label": ariaLabel,
  }: {
    joints?: Record<string, unknown>;
    "aria-label"?: string;
  }) => (
    <div data-testid="lefly-scene" data-joints={JSON.stringify(joints)} aria-label={ariaLabel} />
  ),
}));

const JOINTS = {
  base_yaw: { pos: 0, min: -90, max: 90 },
  base_pitch: { pos: -18, min: -45, max: 45 },
  elbow_pitch: { pos: 42, min: -15, max: 105 },
  wrist_pitch: { pos: -6, min: -45, max: 45 },
  wrist_roll: { pos: 0, min: -180, max: 180 },
};

const ALL_CAPABILITIES = structuredClone(stateChanged.payload.capabilities) as unknown as CapabilitySet;

function canonicalState(target: "simulator" | "remote", revision = 14): DeviceState {
  return {
    ...structuredClone(stateChanged.payload),
    device_id: target === "simulator" ? "device-sim" : "device-remote",
    revision,
    motion: { state: "idle", action: null, joints: structuredClone(JOINTS) },
  } as unknown as DeviceState;
}

function capabilitiesWithout(...commands: string[]): CapabilitySet {
  const capabilities = structuredClone(ALL_CAPABILITIES);
  for (const command of commands) delete capabilities.commands[command];
  return capabilities;
}

interface Harness {
  transport: ConsoleTransport;
  receive: (action: ConsoleAction) => void;
  sent: ConsoleOutgoing[];
  connect: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  fetcher: ReturnType<typeof vi.fn>;
  aborted: ReturnType<typeof vi.fn>;
}

function createHarness(targets: TargetSummary[] = [
  { id: "simulator", kind: "simulator", active: true, status: "ready", capabilities: ALL_CAPABILITIES },
  { id: "remote", kind: "remote", active: false, status: "connected", capabilities: ALL_CAPABILITIES },
]): Harness {
  let dispatch: ((action: ConsoleAction) => void) | undefined;
  const sent: ConsoleOutgoing[] = [];
  const connect = vi.fn();
  const close = vi.fn();
  const aborted = vi.fn();
  const transport: ConsoleTransport = {
    connect,
    close,
    send: (message) => {
      sent.push(message);
      return true;
    },
  };
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    init?.signal?.addEventListener("abort", aborted);
    return { ok: true, json: async () => ({ targets }) } as Response;
  });
  Object.assign(transport, {
    bind(nextDispatch: (action: ConsoleAction) => void) {
      dispatch = nextDispatch;
    },
  });
  return {
    transport,
    sent,
    connect,
    close,
    fetcher,
    aborted,
    receive: (action) => {
      if (!dispatch) throw new Error("transport is not bound");
      dispatch(action);
    },
  };
}

function renderConsole(harness: Harness, options: {
  target?: "simulator" | "remote";
  state?: Record<string, unknown>;
  leaseExpiresAt?: number;
  now?: () => Date;
  agentControl?: {
    available: boolean;
    voiceAvailable: boolean;
    phase: "idle" | "interpreting" | "executing" | "error";
    deviceConnected: boolean;
    messages: Array<{
      id: string;
      role: "user" | "agent" | "system";
      text: string;
      timestamp: string;
      streamState?: "streaming" | "interrupted";
      tools?: Array<{
        requestId: string;
        responseId: string;
        toolCallId: string;
        toolName: "play_motion";
        status: "running" | "completed" | "failed";
      }>;
    }>;
    error: string | null;
    submitText(text: string, callbacks?: { onAccepted?(): void; onRejected?(message: string): void }): boolean;
  };
} = {}) {
  let id = 0;
  const result = render(
    <App
      transportFactory={(dispatch) => {
        (harness.transport as ConsoleTransport & { bind(dispatch: (action: ConsoleAction) => void): void }).bind(dispatch);
        return harness.transport;
      }}
      fetcher={harness.fetcher}
      idFactory={() => `10000000-0000-4000-8000-${String(++id).padStart(12, "0")}`}
      now={options.now ?? (() => new Date("2026-08-14T09:30:00.000Z"))}
      agentControl={options.agentControl}
    />,
  );
  const target = options.target ?? "simulator";
  act(() => harness.receive({
    type: "incoming",
    errorId: "incoming-1",
    receivedAt: 1,
    message: {
      type: "console.hello",
      session_id: "browser-1",
      lease: {
        role: "controller",
        expires_at: options.leaseExpiresAt ?? Date.parse("2026-08-14T09:30:15.000Z") / 1000,
      },
      target_id: target,
      target_epoch: 3,
      state: { ...canonicalState(target), ...options.state },
    },
  }));
  return result;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("unified console", () => {
  it("keeps the target selector visible and loads real targets", async () => {
    const harness = createHarness();
    renderConsole(harness);

    expect(screen.getByRole("combobox", { name: "目标设备" })).toBeVisible();
    expect(await screen.findByRole("option", { name: "模拟器 · simulator" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "远程设备 · remote" })).toBeInTheDocument();
    expect(screen.queryByText("Simulator")).not.toBeInTheDocument();
    expect(harness.fetcher).toHaveBeenCalledWith("/api/targets", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(harness.connect).toHaveBeenCalledOnce();
  });

  it("separates the configured software version from the device state revision", () => {
    const harness = createHarness();
    renderConsole(harness);

    expect(document.querySelector(".revision-fact")).not.toBeInTheDocument();
    const navigationVersion = document.querySelector(".nav-version-mark");
    expect(navigationVersion).toHaveTextContent("VERSION");
    expect(navigationVersion).toHaveTextContent("v0.1.0");
    expect(navigationVersion?.querySelector(".status-dot")).not.toBeInTheDocument();
    expect(document.querySelector(".nav-target-mark")).not.toBeInTheDocument();
    expect(document.querySelector(".revision-block")).not.toBeInTheDocument();

    const footer = document.querySelector(".status-footer") as HTMLElement;
    const revision = footer.querySelector(".footer-revision");
    const session = footer.querySelector(".footer-session");
    expect(revision).toHaveTextContent("STATE REV 14");
    expect(session).toHaveTextContent("SESSION ID");
    if (revision === null || session === null) throw new Error("footer revision metadata is missing");
    expect(revision.compareDocumentPosition(session) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("renders bilingual navigation and overview control headings", () => {
    const harness = createHarness();
    renderConsole(harness);

    const overviewNav = screen.getByRole("button", { name: "概览" });
    expect(overviewNav).toHaveTextContent("概览");
    expect(overviewNav).toHaveTextContent("Overview");
    expect(screen.getByRole("button", { name: "语音交互" })).toHaveTextContent("Voice");
    expect(screen.getByRole("button", { name: "动作" })).toHaveTextContent("Motion");
    expect(screen.getByRole("button", { name: "灯光" })).toHaveTextContent("Lighting");
    expect(screen.getByRole("button", { name: "传感器" })).toHaveTextContent("Sensors");
    expect(screen.getByRole("button", { name: "诊断" })).toHaveTextContent("Diagnostics");

    const overviewHeading = document.querySelector(".overview-heading > div");
    const chineseTitle = screen.getByRole("heading", { name: "设备概览" });
    const englishTitle = screen.getByText("LIVE INSTRUMENT");
    expect(overviewHeading?.firstElementChild).toBe(chineseTitle);
    expect(chineseTitle.compareDocumentPosition(englishTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const railHeadings = Array.from(document.querySelectorAll(".rail-bilingual-label"));
    for (const label of [
      "预设动作 Preset Actions",
      "头部灯 Head Light",
      "头部灯预设 Light Presets",
      "五关节调节 Joint Control",
      "自定义动作 Custom Actions",
    ]) {
      expect(railHeadings.some((heading) => heading.textContent?.replace(/\s+/g, " ").trim() === label)).toBe(true);
    }
  });

  it("uses the overview title treatment on every secondary workspace", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    for (const [navigation, chinese, english] of [
      ["语音交互", "语音交互", "AGENT CHANNEL"],
      ["动作", "动作", "MOTION CONTROL"],
      ["灯光", "灯光", "LIGHT CHANNELS"],
      ["传感器", "传感器", "SENSOR BUS"],
      ["诊断", "诊断", "SYSTEM TRACE"],
    ] as const) {
      await user.click(screen.getByRole("button", { name: navigation }));
      const chineseTitle = screen.getByRole("heading", { name: chinese, level: 1 });
      const heading = chineseTitle.closest(".workspace-heading");
      const englishTitle = within(heading as HTMLElement).getByText(english);

      expect(heading).toHaveClass("bilingual-workspace-heading");
      expect(heading?.querySelector("div")?.firstElementChild).toBe(chineseTitle);
      expect(chineseTitle.compareDocumentPosition(englishTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("shows preset icons and combined bilingual joint labels", () => {
    const harness = createHarness();
    renderConsole(harness);

    for (const label of ["点头", "摇头", "休眠"]) {
      const action = screen.getByRole("button", { name: label });
      expect(action.querySelector("svg")).not.toBeNull();
    }

    const axis = document.querySelector(".telemetry-axis.axis-1 .axis-label");
    expect(axis).toHaveTextContent("底座偏航 base_yaw");

    const user = userEvent.setup();
    return user.click(screen.getByRole("button", { name: "展开五关节调节" })).then(() => {
      const jointControl = screen.getByLabelText("概览 base_yaw").closest("label");
      expect(jointControl?.querySelector(".joint-name")).toHaveTextContent("底座偏航 base_yaw");
    });
  });

  it("marks the reported quick color and uses the reference send affordance", () => {
    const harness = createHarness();
    renderConsole(harness, {
      state: {
        light: {
          brightness: 0.72,
          pixels: Array.from({ length: 16 }, () => "#F1A22E"),
        },
      },
    });

    expect(screen.getByRole("button", { name: "设为 #F1A22E" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "设为 #FFFFFF" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "发送文本指令" }).querySelector(".lucide-arrow-right")).not.toBeNull();
  });

  it("places the bilingual standby command below presets with a pause-state icon", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    expect(screen.queryByText("设备状态")).not.toBeInTheDocument();
    const presetSection = screen.getByText("预设动作").closest(".rail-section") as HTMLElement;
    const standby = within(presetSection).getByRole("button", { name: "进入待机状态" });
    expect(standby).toHaveTextContent("进入待机状态");
    expect(standby).toHaveTextContent("Enter Standby");
    expect(standby.querySelector(".lucide-circle-pause")).not.toBeNull();

    await user.click(standby);
    expect(harness.sent[0]).toMatchObject({
      type: "console.command",
      command: { type: "device.rest", payload: {} },
    });

    const headLightSection = screen.getByText("头部灯").closest(".rail-section") as HTMLElement;
    expect(within(headLightSection).getByRole("button", { name: "关闭头部灯" })).toHaveTextContent("关闭 Off");
  });

  it("keeps controls disabled after disconnect and restores them after full recovery", () => {
    const harness = createHarness();
    renderConsole(harness);
    act(() => harness.receive({ type: "closed" }));

    expect(screen.getAllByText("状态陈旧").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "点头" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "点头" })).toHaveAttribute("title", expect.stringContaining("状态陈旧"));

    act(() => harness.receive({
      type: "incoming",
      errorId: "recovered-state",
      receivedAt: 2,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: canonicalState("simulator", 15),
      },
    }));

    expect(screen.getAllByText("在线").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "点头" })).toBeDisabled();
    act(() => harness.receive({
      type: "incoming",
      errorId: "recovered-control",
      receivedAt: 3,
      message: { type: "console.control", lease: { role: "controller", expires_at: 20 } },
    }));
    expect(screen.getByRole("button", { name: "点头" })).toBeEnabled();
  });

  it("disables remote controls when the last state is stale", async () => {
    const harness = createHarness();
    renderConsole(harness, { target: "remote" });
    act(() => harness.receive({ type: "closed" }));

    const nod = screen.getByRole("button", { name: "点头" });
    expect(nod).toBeDisabled();
    expect(nod).toHaveAttribute("title", expect.stringMatching(/离线|陈旧/));
  });

  it("shows simulator injection without a global console mode", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    expect(screen.queryByRole("button", { name: "日常模式" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开发模式" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "传感器" }));
    expect(screen.getByRole("button", { name: "注入左侧触摸" })).toBeEnabled();
    expect(screen.getByLabelText("Gesture raw ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Face raw ID")).toBeInTheDocument();
  });

  it("shows remote readings without synthetic injection controls", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness, { target: "remote" });
    act(() => harness.receive({
      type: "incoming",
      errorId: "gesture-reading",
      receivedAt: 2,
      message: {
        type: "console.event",
        target_id: "remote",
        target_epoch: 3,
        event: {
          version: "1",
          id: "20000000-0000-4000-8000-000000000070",
          type: "sensor.vision.gesture",
          timestamp: "2026-08-17T08:00:00.100Z",
          device_id: "device-remote",
          payload: { id: 7, label: "wave", confidence: 0.9 },
        },
      },
    }));

    await user.click(screen.getByRole("button", { name: "传感器" }));
    expect(screen.getAllByText(/raw ID 7/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /注入.*触摸/ })).not.toBeInTheDocument();
  });

  it("reports voice as unavailable instead of presenting a working toggle", async () => {
    const user = userEvent.setup();
    renderConsole(createHarness());

    await user.click(screen.getByRole("button", { name: "语音交互" }));
    expect(screen.getByText("当前目标未提供语音控制能力")).toBeVisible();
    expect(screen.queryByRole("switch", { name: /语音/ })).not.toBeInTheDocument();
  });

  it("shows direct joint controls without a global console mode", async () => {
    const user = userEvent.setup();
    renderConsole(createHarness());

    await user.click(screen.getByRole("button", { name: "动作" }));
    expect(screen.getByLabelText("base_yaw")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "日常模式" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开发模式" })).not.toBeInTheDocument();
  });

  it("disables every motion start while motion is running", async () => {
    const harness = createHarness();
    renderConsole(harness, { state: { motion: { state: "moving", action: "nod", joints: JOINTS } } });

    expect(screen.getByRole("button", { name: "点头" })).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "动作" }));
    expect(screen.getByRole("button", { name: "点头" })).toBeDisabled();
  });

  it("exposes the complete preset library from the overview rail", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    for (const label of ["点头", "摇头", "休眠"]) {
      expect(screen.getByRole("button", { name: label })).toBeVisible();
    }
    expect(screen.queryByRole("button", { name: "复位" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "向左转" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "向右转" })).not.toBeInTheDocument();

    const presetSection = screen.getByText("预设动作").closest(".rail-section");
    const presetGrid = (presetSection as HTMLElement).querySelector(".rail-preset-grid") as HTMLElement;
    const presetButtons = within(presetGrid).getAllByRole("button");
    expect(presetButtons.at(-1)).toHaveAccessibleName("休眠");

    await user.click(within(presetSection as HTMLElement).getByRole("button", { name: "休眠" }));
    expect(harness.sent[0]).toMatchObject({
      type: "console.command",
      command: {
        type: "motion.play",
        payload: { name: "sleep" },
      },
    });

    await user.click(screen.getByRole("button", { name: "进入待机状态" }));
    expect(harness.sent[1]).toMatchObject({
      type: "console.command",
      command: {
        type: "device.rest",
        payload: {},
      },
    });

    await user.click(screen.getByRole("button", { name: "动作" }));
    const motionPresetSection = screen.getByText("预设动作").closest(".workspace-band");
    await user.click(within(motionPresetSection as HTMLElement).getByRole("button", { name: "休眠" }));
    expect(harness.sent[2]).toMatchObject({
      type: "console.command",
      command: {
        type: "motion.play",
        payload: { name: "sleep" },
      },
    });

    await user.click(screen.getByRole("button", { name: "进入休息状态" }));
    expect(harness.sent[3]).toMatchObject({
      type: "console.command",
      command: {
        type: "device.rest",
        payload: {},
      },
    });
  });

  it("keeps the scene and simulator sliders driven by telemetry while the joint editor is open", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await user.click(screen.getByRole("button", { name: "展开五关节调节" }));

    act(() => harness.receive({
      type: "incoming",
      errorId: "moving-telemetry",
      receivedAt: 2,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: {
          ...canonicalState("simulator", 15),
          motion: {
            state: "moving",
            action: "nod",
            joints: { ...JOINTS, base_yaw: { pos: 12, min: -90, max: 90 } },
          },
          command_queue: { size: 1, capacity: 8 },
        },
      },
    }));

    expect(JSON.parse(screen.getByTestId("lefly-scene").dataset.joints ?? "{}")).toMatchObject({ base_yaw: 12 });
    expect(screen.getByRole("slider", { name: "概览 base_yaw" })).toHaveValue("12");
  });

  it("previews simulator joint changes immediately and sends one sparse move on release", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await user.click(screen.getByRole("button", { name: "展开五关节调节" }));
    expect(within(screen.getByRole("button", { name: "收起五关节调节" })).queryByText("idle")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("slider", { name: "概览 base_yaw" }), { target: { value: "30" } });

    expect(JSON.parse(screen.getByTestId("lefly-scene").dataset.joints ?? "{}")).toMatchObject({ base_yaw: 30 });
    expect(screen.getByRole("slider", { name: "概览 base_yaw" })).toHaveValue("30");
    expect(harness.sent).toEqual([]);
    fireEvent.pointerUp(screen.getByRole("slider", { name: "概览 base_yaw" }));
    expect(harness.sent).toHaveLength(1);
    expect(harness.sent[0]).toMatchObject({
      type: "console.command",
      command: {
        type: "motion.absolute_move",
        payload: { joints: { base_yaw: 30 }, duration_ms: 100 },
      },
    });
    act(() => harness.receive({
      type: "incoming",
      errorId: "simulator-queued",
      receivedAt: 2,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: {
          ...canonicalState("simulator", 15),
          command_queue: { size: 1, capacity: 8 },
        },
      },
    }));
    expect(screen.getByRole("slider", { name: "概览 base_yaw" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "应用关节位置" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同步当前姿态" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重置关节草稿" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消关节修改" })).not.toBeInTheDocument();
  });

  it("collapses a simulator drag to its final target and holds the preview through telemetry", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await user.click(screen.getByRole("button", { name: "展开五关节调节" }));
    const baseYaw = screen.getByRole("slider", { name: "概览 base_yaw" });
    fireEvent.change(baseYaw, { target: { value: "10" } });
    fireEvent.change(baseYaw, { target: { value: "20" } });
    fireEvent.change(baseYaw, { target: { value: "35" } });
    expect(harness.sent).toEqual([]);
    expect(JSON.parse(screen.getByTestId("lefly-scene").dataset.joints ?? "{}")).toMatchObject({ base_yaw: 35 });
    fireEvent.pointerUp(baseYaw);
    expect(harness.sent).toHaveLength(1);
    expect(harness.sent[0]).toMatchObject({
      command: {
        type: "motion.absolute_move",
        payload: { joints: { base_yaw: 35 }, duration_ms: 100 },
      },
    });

    act(() => harness.receive({
      type: "incoming",
      errorId: "simulator-moving",
      receivedAt: 2,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: {
          ...canonicalState("simulator", 15),
          motion: { state: "moving", action: "absolute_move", joints: { ...JOINTS, base_yaw: { pos: 5, min: -90, max: 90 } } },
          command_queue: { size: 1, capacity: 8 },
        },
      },
    }));
    expect(harness.sent).toHaveLength(1);
    expect(JSON.parse(screen.getByTestId("lefly-scene").dataset.joints ?? "{}")).toMatchObject({ base_yaw: 35 });

    act(() => harness.receive({
      type: "incoming",
      errorId: "simulator-idle",
      receivedAt: 3,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: {
          ...canonicalState("simulator", 16),
          motion: { state: "idle", action: null, joints: { ...JOINTS, base_yaw: { pos: 35, min: -90, max: 90 } } },
          command_queue: { size: 0, capacity: 8 },
        },
      },
    }));

    await waitFor(() => expect(screen.getByRole("slider", { name: "概览 base_yaw" })).toHaveValue("35"));
    expect(harness.sent).toHaveLength(1);
  });

  it("keeps remote joint changes as a sparse draft until Apply", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness, { target: "remote" });

    await user.click(screen.getByRole("button", { name: "展开五关节调节" }));
    fireEvent.change(screen.getByRole("slider", { name: "概览 base_yaw" }), { target: { value: "30" } });

    expect(harness.sent).toEqual([]);
    expect(screen.getByText("当前 0° / 目标 30°")).toBeVisible();
    expect(screen.queryByRole("button", { name: "同步当前姿态" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重置关节草稿" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消关节修改" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "应用关节位置" }));
    expect(screen.getByRole("dialog", { name: "确认远程关节运动" })).toBeVisible();
    expect(harness.sent).toEqual([]);
    expect(screen.queryByText("当前 0° / 目标 30°")).not.toBeInTheDocument();
  });

  it("discards an open overview joint draft when the active target changes", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await user.click(screen.getByRole("button", { name: "展开五关节调节" }));
    fireEvent.change(screen.getByRole("slider", { name: "概览 base_yaw" }), { target: { value: "30" } });

    act(() => harness.receive({
      type: "incoming",
      errorId: "target-change",
      receivedAt: 2,
      message: {
        type: "console.state",
        target_id: "remote",
        target_epoch: 4,
        state: {
          ...canonicalState("remote", 1),
          motion: { state: "idle", action: null, joints: { ...JOINTS, base_yaw: { pos: 5, min: -90, max: 90 } } },
        },
      },
    }));

    expect(screen.getByRole("button", { name: "展开五关节调节" })).toBeVisible();
    expect(screen.queryByRole("slider", { name: "概览 base_yaw" })).not.toBeInTheDocument();
    expect(JSON.parse(screen.getByTestId("lefly-scene").dataset.joints ?? "{}")).toMatchObject({ base_yaw: 5 });
  });

  it("applies head-light presets and keeps custom action entry points honest", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await user.click(screen.getByRole("button", { name: "温暖黄光" }));
    expect(harness.sent).toHaveLength(2);
    expect(harness.sent[0]).toMatchObject({ command: { type: "light.solid", payload: { target: "head_matrix", color: "#F1A22E" } } });
    expect(harness.sent[1]).toMatchObject({ command: { type: "light.brightness", payload: { target: "head_matrix", brightness: 0.72 } } });
    expect(screen.getByRole("button", { name: "导入自定义动作" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "管理自定义动作" })).toBeDisabled();
  });

  it("keeps the base status strip read-only", async () => {
    const user = userEvent.setup();
    renderConsole(createHarness());

    await user.click(screen.getByRole("button", { name: "灯光" }));
    expect(screen.getByText("自动跟随设备状态")).toBeVisible();
    expect(screen.queryByRole("button", { name: /状态灯|恢复自动状态|关闭状态灯条/ })).not.toBeInTheDocument();
  });

  it("keeps an edited head-light draft across unrelated device revisions", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await user.click(screen.getByRole("button", { name: "灯光" }));
    await user.click(screen.getByRole("button", { name: "选择头部灯 #20A8B5" }));

    act(() => harness.receive({
      type: "incoming",
      errorId: "unrelated-light-revision",
      receivedAt: 2,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: canonicalState("simulator", 15),
      },
    }));

    const matchingState = canonicalState("simulator", 16);
    matchingState.light.pixels = matchingState.light.pixels.map(() => "#20A8B5");
    act(() => harness.receive({
      type: "incoming",
      errorId: "matching-light-revision",
      receivedAt: 3,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: matchingState,
      },
    }));
    act(() => harness.receive({
      type: "incoming",
      errorId: "late-light-revision",
      receivedAt: 4,
      message: {
        type: "console.state",
        target_id: "simulator",
        target_epoch: 3,
        state: canonicalState("simulator", 17),
      },
    }));

    expect(screen.getByText("#20A8B5")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "应用头部灯" }));
    expect(harness.sent.at(-2)).toMatchObject({ command: { type: "light.solid", payload: { color: "#20A8B5" } } });
  });

  it("sends a unique v1 envelope with the real axis payload and no lease token", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await user.click(screen.getByRole("button", { name: "展开五关节调节" }));
    const baseYaw = screen.getByRole("slider", { name: "概览 base_yaw" });
    fireEvent.change(baseYaw, { target: { value: "30" } });
    fireEvent.pointerUp(baseYaw);
    expect(harness.sent).toHaveLength(1);
    expect(harness.sent[0]).toEqual({
      type: "console.command",
      target_epoch: 3,
      command: {
        version: "1",
        id: "10000000-0000-4000-8000-000000000001",
        type: "motion.absolute_move",
        timestamp: "2026-08-14T09:30:00.000Z",
        payload: { joints: { base_yaw: 30 }, duration_ms: 100 },
        device_id: "device-sim",
      },
    });
    expect(JSON.stringify(harness.sent[0])).not.toContain("private-lease");
  });

  it("fails closed when a capability is missing", () => {
    const harness = createHarness();
    renderConsole(harness, { state: { capabilities: capabilitiesWithout("light.solid", "light.brightness") } });

    const lightOff = screen.getByRole("button", { name: "关闭头部灯" });
    expect(lightOff).toBeDisabled();
    expect(lightOff).toHaveAttribute("title", expect.stringMatching(/不支持/));
  });

  it("selects targets through the browser protocol", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);

    await waitFor(() => expect(within(screen.getByRole("combobox", { name: "目标设备" })).getAllByRole("option")).toHaveLength(2));
    await user.selectOptions(screen.getByRole("combobox", { name: "目标设备" }), "remote");
    expect(harness.sent).toContainEqual({ type: "console.select_target", target_id: "remote" });
  });

  it("offers explicit control takeover to readonly sessions", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness);
    act(() => harness.receive({
      type: "incoming",
      errorId: "control-1",
      receivedAt: 2,
      message: { type: "console.control", lease: { role: "readonly" } },
    }));

    const takeover = screen.getByRole("button", { name: "接管控制权" });
    expect(takeover).toHaveAttribute("title", "从其他 Console 页面接管控制权");
    await user.click(takeover);
    expect(harness.sent).toContainEqual({ type: "console.acquire_control" });
  });

  it("closes the socket and aborts target loading on unmount", () => {
    const harness = createHarness();
    const view = renderConsole(harness);
    view.unmount();

    expect(harness.close).toHaveBeenCalledOnce();
    expect(harness.aborted).toHaveBeenCalledOnce();
  });

  it("renews a controller lease before expiry and stops after losing control", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-14T09:30:00.000Z"));
    const harness = createHarness();
    renderConsole(harness, {
      leaseExpiresAt: Date.parse("2026-08-14T09:30:15.000Z") / 1000,
      now: () => new Date(Date.now()),
    });

    vi.advanceTimersByTime(7_499);
    expect(harness.sent).not.toContainEqual({ type: "console.renew_control" });
    vi.advanceTimersByTime(1);
    expect(harness.sent).toContainEqual({ type: "console.renew_control" });

    act(() => harness.receive({
      type: "incoming",
      errorId: "lost-lease",
      receivedAt: Date.now(),
      message: { type: "console.control", lease: { role: "readonly" } },
    }));
    harness.sent.length = 0;
    vi.advanceTimersByTime(60_000);
    expect(harness.sent).toEqual([]);
  });

  it("clears the lease renewal timer on unmount", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-14T09:30:00.000Z"));
    const harness = createHarness();
    const view = renderConsole(harness, {
      leaseExpiresAt: Date.parse("2026-08-14T09:30:15.000Z") / 1000,
      now: () => new Date(Date.now()),
    });

    view.unmount();
    vi.advanceTimersByTime(60_000);
    expect(harness.sent).not.toContainEqual({ type: "console.renew_control" });
  });

  it("reschedules renewal when a refreshed controller lease arrives", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-14T09:30:00.000Z"));
    const harness = createHarness();
    renderConsole(harness, {
      leaseExpiresAt: Date.parse("2026-08-14T09:30:15.000Z") / 1000,
      now: () => new Date(Date.now()),
    });

    vi.advanceTimersByTime(5_000);
    act(() => harness.receive({
      type: "incoming",
      errorId: "renewed-lease",
      receivedAt: Date.now(),
      message: {
        type: "console.control",
        lease: {
          role: "controller",
          expires_at: Date.parse("2026-08-14T09:30:25.000Z") / 1000,
        },
      },
    }));
    vi.advanceTimersByTime(9_999);
    expect(harness.sent).not.toContainEqual({ type: "console.renew_control" });
    vi.advanceTimersByTime(1);
    expect(harness.sent).toContainEqual({ type: "console.renew_control" });
  });

  it("uses the same remote confirmation for Motion shortcuts and direct five-joint Apply", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness, { target: "remote" });
    await user.click(screen.getByRole("button", { name: "动作" }));

    await user.click(screen.getByRole("button", { name: "向右" }));
    expect(screen.getByRole("dialog", { name: "确认远程关节运动" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "取消远程动作" }));

    await user.click(screen.getByRole("button", { name: "应用关节位置" }));
    expect(screen.getByRole("dialog", { name: "确认远程关节运动" })).toBeVisible();
    expect(harness.sent).toEqual([]);
  });

  it("fails closed when any direct joint limit is absent", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    renderConsole(harness, {
      target: "remote",
      state: { motion: { state: "idle", action: null, joints: { ...JOINTS, wrist_roll: { pos: 0, min: -180 } } } },
    });
    await user.click(screen.getByRole("button", { name: "动作" }));

    expect(screen.getByText("遥测限制缺失")).toBeVisible();
    expect(screen.getByRole("button", { name: "应用关节位置" })).toBeDisabled();
    expect(screen.getByLabelText("wrist_roll")).toBeDisabled();
    expect(screen.getAllByText("--").length).toBeGreaterThan(0);
  });

  it("keeps voice unavailable when only the device protocol advertises voice.control", async () => {
    const user = userEvent.setup();
    renderConsole(createHarness(), { state: { capabilities: { ...ALL_CAPABILITIES, "voice.control": true } } });
    await user.click(screen.getByRole("button", { name: "语音交互" }));

    expect(screen.getByText("当前目标未提供语音控制能力")).toBeVisible();
    expect(within(document.querySelector(".status-footer") as HTMLElement).getByText("不可用")).toBeVisible();
  });

  it("uses injected idFactory and now for target fetch errors", async () => {
    const harness = createHarness();
    harness.fetcher.mockRejectedValueOnce(new Error("offline"));
    const idFactory = vi.fn(() => "deterministic-error");
    const now = vi.fn(() => new Date("2026-08-14T10:00:00.000Z"));
    render(
      <App
        transportFactory={(dispatch) => {
          (harness.transport as ConsoleTransport & { bind(dispatch: (action: ConsoleAction) => void): void }).bind(dispatch);
          return harness.transport;
        }}
        fetcher={harness.fetcher}
        idFactory={idFactory}
        now={now}
      />,
    );

    await waitFor(() => expect(idFactory).toHaveBeenCalled());
    expect(now).toHaveBeenCalled();
  });

  it.each(["simulator", "remote"] as const)(
    "fails closed for %s quick moves outside reported base_yaw limits",
    async (target) => {
      const user = userEvent.setup();
      const harness = createHarness();
      renderConsole(harness, {
        target,
        state: {
          motion: {
            state: "idle",
            action: null,
            joints: { ...JOINTS, base_yaw: { pos: 0, min: -45, max: 45 } },
          },
        },
      });

      await user.click(screen.getByRole("button", { name: "动作" }));
      const motionLeft = screen.getByRole("button", { name: "向左" });
      const motionRight = screen.getByRole("button", { name: "向右" });
      expect(motionLeft).toBeDisabled();
      expect(motionRight).toBeDisabled();
      expect(motionLeft).toHaveAttribute("title", expect.stringMatching(/-45.*45/));
      expect(screen.getByRole("button", { name: "回中" })).toBeEnabled();
      await user.click(motionRight);
      expect(screen.queryByRole("dialog", { name: "确认远程关节运动" })).not.toBeInTheDocument();
      expect(harness.sent).toEqual([]);
    },
  );

  it("keeps voice unavailable when only the text Agent is connected", async () => {
    const user = userEvent.setup();
    renderConsole(createHarness(), {
      agentControl: {
        available: true,
        voiceAvailable: false,
        phase: "idle",
        deviceConnected: true,
        messages: [],
        error: null,
        submitText: () => true,
      },
    });
    await user.click(screen.getByRole("button", { name: "语音交互" }));

    expect(screen.getByText("当前目标未提供语音控制能力")).toBeVisible();
    const voiceGrid = document.querySelector(".voice-state-grid") as HTMLElement;
    expect(within(voiceGrid).getByText("未连接")).toBeVisible();
    const footer = document.querySelector(".status-footer") as HTMLElement;
    expect(within(footer).getByText("不可用")).toBeVisible();
    expect(within(footer).getByText("已接入")).toBeVisible();
  });

  it("shows scrollable Agent history and submits text from the overview", async () => {
    const user = userEvent.setup();
    const submitText = vi.fn((
      _text: string,
      _callbacks?: { onAccepted?(): void; onRejected?(message: string): void },
    ) => true);
    renderConsole(createHarness(), {
      agentControl: {
        available: true,
        voiceAvailable: false,
        phase: "idle",
        deviceConnected: true,
        messages: [
          { id: "u-1", role: "user", text: "看左边", timestamp: "2026-08-15T01:00:00Z" },
          { id: "a-1", role: "agent", text: "正在看向左边。", timestamp: "2026-08-15T01:00:01Z" },
        ],
        error: null,
        submitText,
      },
    });

    const history = screen.getByRole("log", { name: "最近聊天记录" });
    expect(history).toHaveClass("agent-message-list");
    expect(within(history).getByText("看左边")).toBeVisible();
    expect(within(history).getByText("正在看向左边。")).toBeVisible();
    const input = screen.getByRole("textbox", { name: "文本指令" });
    await user.type(input, "变成蓝色{Enter}");

    expect(submitText).toHaveBeenCalledWith("变成蓝色", expect.any(Object));
    expect(input).toHaveValue("变成蓝色");
    expect(input).toBeEnabled();
    expect(input).toHaveAttribute("readonly");
    expect(input).toHaveFocus();
    act(() => submitText.mock.calls[0][1]?.onAccepted?.());
    expect(input).toHaveValue("");
    expect(input).toBeEnabled();
    expect(input).not.toHaveAttribute("readonly");
    expect(input).toHaveFocus();
    expect(screen.queryByText("最近事件")).not.toBeInTheDocument();
  });

  it("renders one streamed assistant entry with separate tool progress", () => {
    renderConsole(createHarness(), {
      agentControl: {
        available: true,
        voiceAvailable: false,
        phase: "executing",
        deviceConnected: true,
        messages: [{
          id: "response-1",
          role: "agent",
          text: "正在点头",
          timestamp: "2026-08-21T08:00:00.000Z",
          streamState: "streaming",
          tools: [{
            requestId: "request-1",
            responseId: "response-1",
            toolCallId: "tool-1",
            toolName: "play_motion",
            status: "running",
          }],
        }],
        error: null,
        submitText: () => true,
      },
    });

    const history = screen.getByRole("log", { name: "最近聊天记录" });
    expect(within(history).getAllByText("正在点头")).toHaveLength(1);
    expect(within(history).getByRole("status", { name: "工具执行进度" })).toHaveTextContent("执行动作");
    expect(within(history).getByRole("status", { name: "工具执行进度" })).toHaveTextContent("进行中");
  });

  it("keeps direct controls off Agent Control and text off Device Protocol", async () => {
    const user = userEvent.setup();
    const harness = createHarness();
    const submitText = vi.fn((_text: string, callbacks?: { onAccepted?(): void }) => {
      callbacks?.onAccepted?.();
      return true;
    });
    renderConsole(harness, {
      agentControl: {
        available: true,
        voiceAvailable: false,
        phase: "idle",
        deviceConnected: true,
        messages: [],
        error: null,
        submitText,
      },
    });

    await user.click(screen.getByRole("button", { name: "点头" }));
    fireEvent.change(screen.getByRole("slider", { name: "快速亮度" }), { target: { value: "40" } });
    await user.click(screen.getByRole("button", { name: "设为 #20A8B5" }));
    await user.click(screen.getByRole("button", { name: "传感器" }));
    await user.click(screen.getByRole("button", { name: "注入左侧触摸" }));

    expect(submitText).not.toHaveBeenCalled();
    expect(harness.sent.map((message) => message.type)).toEqual([
      "console.command",
      "console.command",
      "console.command",
      "console.inject_sensor",
    ]);

    await user.click(screen.getByRole("button", { name: "概览" }));
    const beforeTextSubmission = harness.sent.length;
    await user.type(screen.getByRole("textbox", { name: "文本指令" }), "看左边{Enter}");

    expect(submitText).toHaveBeenCalledOnce();
    expect(harness.sent).toHaveLength(beforeTextSubmission);
    expect(screen.getByRole("textbox", { name: "文本指令" })).toHaveFocus();
  });

  it("keeps failed text in the composer and disables it while disconnected", async () => {
    const user = userEvent.setup();
    const submitText = vi.fn(() => false);
    const { rerender } = renderConsole(createHarness(), {
      agentControl: {
        available: true,
        voiceAvailable: false,
        phase: "idle",
        deviceConnected: true,
        messages: [],
        error: "发送失败",
        submitText,
      },
    });
    const input = screen.getByRole("textbox", { name: "文本指令" });
    await user.type(input, "点头{Enter}");
    expect(input).toHaveValue("点头");

    rerender(<App agentControl={{
      available: false,
      voiceAvailable: false,
      phase: "idle",
      deviceConnected: false,
      messages: [],
      error: null,
      submitText,
    }} />);
    expect(screen.getByRole("textbox", { name: "文本指令" })).toBeDisabled();
  });

  it("disables text control while the Agent cannot reach its device", () => {
    renderConsole(createHarness(), {
      agentControl: {
        available: true,
        voiceAvailable: false,
        phase: "idle",
        deviceConnected: false,
        messages: [],
        error: null,
        submitText: () => true,
      },
    });

    expect(screen.getByRole("textbox", { name: "文本指令" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "文本指令" })).toHaveAttribute("placeholder", "设备未连接");
  });

  it("shows the latest five device events as transparent scene text", () => {
    const harness = createHarness();
    renderConsole(harness);
    for (let index = 0; index < 6; index += 1) {
      const id = `event-${index}`;
      act(() => harness.receive({
        type: "incoming",
        errorId: id,
        receivedAt: 2,
        message: {
          type: "console.event",
          target_id: "simulator",
          target_epoch: 3,
          event: {
            version: "1",
            id: `20000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
            type: `device.event_${index}`,
            timestamp: `2026-08-15T01:00:0${index}.000Z`,
            device_id: "device-sim",
            payload: {},
          },
        },
      }));
    }

    const eventStack = screen.getByRole("log", { name: "最近设备事件" });
    expect(within(eventStack).getAllByTestId("scene-event-row")).toHaveLength(5);
    expect(eventStack).not.toHaveTextContent("device.event_0");
    expect(eventStack).toHaveTextContent("device.event_1");
    expect(eventStack).toHaveTextContent("device.event_5");
    expect(eventStack).toHaveClass("scene-event-stack");
  });
});
