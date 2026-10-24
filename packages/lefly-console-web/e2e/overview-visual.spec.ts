import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const JOINTS = {
  base_yaw: { pos: 0, min: -90, max: 90 },
  base_pitch: { pos: -45, min: -45, max: 45 },
  elbow_pitch: { pos: 105, min: -15, max: 105 },
  wrist_pitch: { pos: 45, min: -45, max: 45 },
  wrist_roll: { pos: 0, min: -180, max: 180 },
};

async function mockDevice(page: Page) {
  await page.route("**/api/targets", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      targets: [{ id: "simulator", kind: "simulator", active: true, status: "ready" }],
    }),
  }));
  await page.routeWebSocket("**/ws/console", (socket) => {
    socket.onMessage(() => {});
    socket.send(JSON.stringify({
      type: "console.hello",
      session_id: "visual-review",
      lease: { role: "controller", expires_at: Date.now() / 1000 + 3_600 },
      target_id: "simulator",
      target_epoch: 1,
      state: {
        device_id: "lefly-visual",
        revision: 14,
        capabilities: {
          commands: {
            "motion.play": { scope: "control" },
            "motion.absolute_move": { scope: "control" },
            "device.rest": { scope: "control" },
            "light.solid": { scope: "control" },
            "light.brightness": { scope: "control" },
            "status.set": { scope: "system" },
          },
          events: ["device.state_changed", "motion.started", "motion.progress", "motion.finished"],
          motion: {
            joints: Object.keys(JOINTS),
            presets: [
              { name: "wake", label: "唤醒" },
              { name: "nod", label: "点头" },
              { name: "shake", label: "摇头" },
              { name: "happy_wiggle", label: "开心摇摆" },
              { name: "look_up", label: "向上看" },
              { name: "look_down", label: "向下看" },
              { name: "look_left", label: "向左看" },
              { name: "look_right", label: "向右看" },
              { name: "dance_demo", label: "舞蹈演示" },
              { name: "sleep", label: "休眠" },
            ],
          },
          lights: [{ target: "head_matrix", kind: "rgb_matrix", width: 8, height: 8 }],
        },
        connection: "ready",
        motion: { state: "idle", action: null, joints: JOINTS },
        light: {
          brightness: 0.72,
          matrix: { width: 8, height: 8 },
          pixels: Array(64).fill("#F1A22E"),
        },
        status: { mode: "active" },
        status_strip: { effect: "solid", color: "#FFF0D0" },
        command_queue: { size: 0, capacity: 8 },
      },
    }));
    ["device.visual_event_0", "device.visual_event_1", "device.visual_event_2", "device.visual_event_3", "device.visual_event_4"].forEach((type, index) => {
      socket.send(JSON.stringify({
        type: "console.event",
        target_id: "simulator",
        target_epoch: 1,
        event: {
          version: "1",
          id: `20000000-0000-4000-8001-${String(index).padStart(12, "0")}`,
          type,
          timestamp: `2026-08-15T01:02:0${index + 3}.000Z`,
          device_id: "lefly-visual",
          payload: {},
        },
      }));
    });
  });
  await page.routeWebSocket("**/ws/agent", (socket) => {
    socket.onMessage(() => {});
    socket.send(JSON.stringify({
      version: "1",
      type: "agent.hello",
      session_id: "visual-agent",
      state: {
        phase: "idle",
        device_connected: true,
        queue: { size: 0, capacity: 8 },
        model: { name: "deepseek-chat", status: "unverified" },
        messages: [
          { id: "visual-user", role: "user", text: "看左边，然后变成蓝色", timestamp: "2026-08-15T01:02:01Z" },
          { id: "visual-agent", role: "agent", text: "正在看向左边，并把灯光调成蓝色。", timestamp: "2026-08-15T01:02:02Z" },
        ],
      },
    }));
  });
}

test("renders the complete overview without clipping or a blank model", async ({ page }, testInfo) => {
  await mockDevice(page);
  await page.goto("/?renderDiagnostics=1");
  await expect(page.locator(".connection-fact")).toHaveClass(/online/);
  await expect(page.getByRole("button", { name: "复位" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "向左转" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "向右转" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "舞蹈演示" })).toBeVisible();
  await expect(page.getByRole("button", { name: "休眠" })).toBeVisible();
  await expect(page.getByRole("button", { name: "进入待机状态" })).toBeVisible();
  await expect(page.getByRole("button", { name: "温暖黄光" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "文本指令" })).toBeEnabled();
  await expect(page.getByTestId("agent-model-status")).toHaveText("deepseek-chat");
  await expect(page.getByTestId("agent-model-status")).toHaveClass(/model-unverified/);
  await page.getByTestId("agent-model-status").focus();
  await expect(page.getByTestId("agent-model-status")).toBeFocused();
  expect(await page.getByTestId("agent-model-status").evaluate((element) =>
    getComputedStyle(element, "::after").content,
  )).toContain("尚未通过实际请求验证");
  await expect(page.locator(".brand-block strong")).toHaveText("乐飞 LeFly");
  await expect(page.locator(".brand-block span")).toHaveText("具身智能桌面机器人虚拟仿真平台");
  await expect(page.getByRole("log", { name: "最近聊天记录" })).toContainText("正在看向左边");
  await expect(page.getByRole("log", { name: "最近设备事件" }).locator(".scene-event-row").last()).toContainText("device.visual_event_4");

  const canvas = page.locator("canvas[aria-label='LeFly 三维实时姿态']");
  await expect(canvas).toBeVisible();
  await expect(canvas).toHaveAttribute("data-model-bounds", /.+/);
  await expect(canvas).toHaveAttribute("data-model-mask", /.+/);
  const mask = JSON.parse(await canvas.getAttribute("data-model-mask") ?? "{}") as { pixelCount?: number };
  const bounds = JSON.parse(await canvas.getAttribute("data-model-bounds") ?? "{}") as {
    left?: number;
    top?: number;
    right?: number;
    bottom?: number;
  };
  const canvasPixels = await canvas.evaluate((element: HTMLCanvasElement) => ({
    width: element.width,
    height: element.height,
  }));
  expect(mask.pixelCount).toBeGreaterThan(100);
  const modelMargin = Math.max(8, Math.min(canvasPixels.width, canvasPixels.height) * 0.07);
  expect(bounds.left).toBeGreaterThanOrEqual(modelMargin);
  expect(bounds.top).toBeGreaterThanOrEqual(modelMargin);
  expect(bounds.right).toBeLessThanOrEqual(canvasPixels.width - modelMargin);
  expect(bounds.bottom).toBeLessThanOrEqual(canvasPixels.height - modelMargin);

  await page.getByRole("button", { name: "展开五关节调节" }).click();
  await expect(page.getByRole("slider", { name: "概览 base_yaw" })).toBeVisible();
  const agentInput = page.getByRole("textbox", { name: "文本指令" });
  await agentInput.fill("视觉状态检查");
  const sendButton = page.getByRole("button", { name: "发送文本指令" });
  await expect(sendButton).toBeEnabled();
  await expect(sendButton).toHaveCSS("background-color", "rgb(29, 36, 48)");

  const visualContract = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const style = (selector: string) => {
      const element = document.querySelector(selector);
      if (!(element instanceof HTMLElement || element instanceof SVGElement)) {
        throw new Error(`missing visual contract element: ${selector}`);
      }
      return getComputedStyle(element);
    };
    return {
      tokens: {
        primary: root.getPropertyValue("--color-primary").trim().toLowerCase(),
        primaryHover: root.getPropertyValue("--color-primary-hover").trim().toLowerCase(),
        hardware: root.getPropertyValue("--color-hardware").trim().toLowerCase(),
        canvas: root.getPropertyValue("--color-canvas").trim().toLowerCase(),
        panel: root.getPropertyValue("--color-panel").trim().toLowerCase(),
        error: root.getPropertyValue("--color-error").trim().toLowerCase(),
        errorText: root.getPropertyValue("--color-error-text").trim().toLowerCase(),
      },
      roles: {
        activeNavColor: style(".workspace-nav > button.active svg").color,
        activeNavBackground: style(".workspace-nav > button.active svg").backgroundColor,
        activeChineseColor: style(".workspace-nav > button.active strong").color,
        activeEnglishColor: style(".workspace-nav > button.active small").color,
        sendBackground: style(".agent-composer button").backgroundColor,
        motionAccent: style(".quick-rail > .rail-section:first-child .rail-title svg").color,
        lightingAccent: style(".light-quick .rail-title svg").color,
        jointAccent: style(".rail-joint-control input").accentColor,
        lightAccent: style(".light-quick input").accentColor,
        sceneBackground: style(".scene-stage").backgroundColor,
        dockBackground: style(".overview-chat-dock").backgroundColor,
      },
      refinement: {
        navEnglishSize: style(".nav-bilingual-label small").fontSize,
        railEnglishSize: style(".rail-bilingual-label small").fontSize,
        jointCodeSize: style(".axis-label code").fontSize,
        composerHeight: style(".agent-composer input").height,
        sendSize: [style(".agent-composer button").width, style(".agent-composer button").height],
        selectedSwatchShadow: style(".swatch-row .color-swatch.selected").boxShadow,
        lightRangeAppearance: style(".light-quick input[type=range]").appearance,
        lightRangeBackground: style(".light-quick input[type=range]").backgroundImage,
        actionCorner: getComputedStyle(
          document.querySelector(".rail-preset-grid .preset-action") as HTMLElement,
          "::after",
        ).content,
        targetLabelAlignment: style(".target-control > label").alignItems,
        footerDivider: [style(".footer-session").borderLeftStyle, style(".footer-session").borderLeftColor],
        overviewEnglishColor: style(".overview-heading .eyebrow").color,
      },
    };
  });
  expect(visualContract.tokens).toEqual({
    primary: "#2583f5",
    primaryHover: "#1472e4",
    hardware: "#f2a13b",
    canvas: "#ffffff",
    panel: "#fafbfc",
    error: "#c85850",
    errorText: "#a94741",
  });
  expect(visualContract.roles).toEqual({
    activeNavColor: "rgb(255, 255, 255)",
    activeNavBackground: "rgb(37, 131, 245)",
    activeChineseColor: "rgb(37, 131, 245)",
    activeEnglishColor: "rgb(37, 131, 245)",
    sendBackground: "rgb(29, 36, 48)",
    motionAccent: "rgb(37, 131, 245)",
    lightingAccent: "rgb(242, 161, 59)",
    jointAccent: "rgb(37, 131, 245)",
    lightAccent: "rgb(242, 161, 59)",
    sceneBackground: "rgb(255, 255, 255)",
    dockBackground: "rgb(245, 246, 248)",
  });
  expect(visualContract.refinement.navEnglishSize).toBe(
    testInfo.project.name.startsWith("mobile") ? "9px" : testInfo.project.name.startsWith("tablet") ? "10px" : "11px",
  );
  expect(visualContract.refinement.railEnglishSize).toBe("12px");
  expect(visualContract.refinement.jointCodeSize).toBe(testInfo.project.name.startsWith("mobile") ? "10px" : "12px");
  expect(visualContract.refinement.composerHeight).toBe("35px");
  expect(visualContract.refinement.sendSize).toEqual(["35px", "35px"]);
  expect(visualContract.refinement.selectedSwatchShadow).toContain("rgb(29, 36, 48)");
  expect(visualContract.refinement.lightRangeAppearance).toBe("none");
  expect(visualContract.refinement.lightRangeBackground).toContain("linear-gradient");
  expect(visualContract.refinement.actionCorner).not.toBe("none");
  expect(visualContract.refinement.targetLabelAlignment).toBe("center");
  expect(visualContract.refinement.footerDivider).toEqual(["solid", "rgb(223, 228, 236)"]);
  expect(visualContract.refinement.overviewEnglishColor).toBe("rgb(143, 153, 168)");
  await page.emulateMedia({ reducedMotion: "reduce" });
  const reducedMotionDuration = await sendButton.evaluate((element) =>
    Number.parseFloat(getComputedStyle(element).transitionDuration),
  );
  expect(reducedMotionDuration).toBeLessThanOrEqual(0.001);
  await agentInput.fill("");
  await expect(sendButton).toBeDisabled();
  await expect(sendButton).toHaveCSS("opacity", "0.58");

  const contrastRatios = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const rgb = (name: string) => {
      const hex = root.getPropertyValue(name).trim().replace("#", "");
      return [0, 2, 4].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16));
    };
    const luminance = (channels: number[]) => {
      const [red, green, blue] = channels.map((value) => {
        const normalized = value / 255;
        return normalized <= 0.03928
          ? normalized / 12.92
          : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    };
    const contrast = (foreground: string, background: string) => {
      const first = luminance(rgb(foreground));
      const second = luminance(rgb(background));
      return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    };
    return {
      primaryOnSurface: contrast("--color-surface", "--color-primary"),
      mutedOnSurface: contrast("--color-muted", "--color-surface"),
      inkOnHardware: contrast("--color-ink", "--color-hardware"),
      errorTextOnSurface: contrast("--color-error-text", "--color-surface"),
    };
  });
  expect(contrastRatios.primaryOnSurface).toBeGreaterThanOrEqual(3);
  for (const [role, ratio] of Object.entries(contrastRatios)) {
    if (role !== "primaryOnSurface") expect(ratio).toBeGreaterThanOrEqual(4.5);
  }

  const connectionFact = page.locator(".connection-fact");
  await connectionFact.evaluate((element) => {
    element.classList.remove("online");
    element.classList.add("offline");
  });
  await expect(connectionFact).toHaveCSS("color", "rgb(169, 71, 65)");
  await connectionFact.evaluate((element) => {
    element.classList.remove("offline");
    element.classList.add("online");
  });

  const layout = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth;
    const controls = Array.from(document.querySelectorAll("button, input, select"));
    return {
      horizontalOverflow: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - viewportWidth,
      overflowingControls: controls
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && element.scrollWidth > element.clientWidth + 2;
        })
        .map((element) => element.getAttribute("aria-label") ?? element.tagName),
      railSections: document.querySelectorAll(".quick-rail > .rail-section").length,
      scroll: {
        window: window.scrollY,
        workspace: document.querySelector(".workspace-content")?.scrollTop ?? -1,
        rail: document.querySelector(".quick-rail")?.scrollTop ?? -1,
      },
      sceneGeometry: (() => {
        const stageRect = document.querySelector(".scene-stage")?.getBoundingClientRect();
        const sceneRect = document.querySelector(".lefly-scene")?.getBoundingClientRect();
        const canvasRect = document.querySelector("canvas")?.getBoundingClientRect();
        return stageRect && sceneRect && canvasRect ? {
          stage: { top: stageRect.top, bottom: stageRect.bottom, width: stageRect.width, height: stageRect.height },
          scene: { top: sceneRect.top, bottom: sceneRect.bottom, width: sceneRect.width, height: sceneRect.height },
          canvas: { top: canvasRect.top, bottom: canvasRect.bottom, width: canvasRect.width, height: canvasRect.height },
        } : null;
      })(),
    };
  });

  expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
  expect(layout.overflowingControls).toEqual([]);
  expect(layout.railSections).toBe(5);
  if (testInfo.project.name.startsWith("desktop")) {
    expect(layout.scroll.window).toBe(0);
    expect(layout.scroll.workspace).toBe(0);
    expect(layout.scroll.rail).toBeGreaterThan(0);
  }
  expect(layout.sceneGeometry?.canvas.width).toBeGreaterThan(200);
  expect(layout.sceneGeometry?.canvas.height).toBeGreaterThan(250);
  expect(layout.sceneGeometry?.scene).toEqual(layout.sceneGeometry?.stage);
  expect(layout.sceneGeometry?.canvas).toEqual(layout.sceneGeometry?.stage);

  const screenshotPath = path.join(process.cwd(), "test-results", "visual", `overview-${testInfo.project.name}.png`);
  const canvasScreenshotPath = path.join(process.cwd(), "test-results", "visual", `model-${testInfo.project.name}.png`);
  await canvas.screenshot({ path: canvasScreenshotPath });
  await page.getByRole("button", { name: "收起五关节调节" }).click();
  await agentInput.fill("");
  await page.evaluate(() => {
    (document.activeElement as HTMLElement)?.blur();
    window.scrollTo(0, 0);
    document.querySelector(".quick-rail")?.scrollTo(0, 0);
  });
  await page.screenshot({
    path: screenshotPath,
    fullPage: testInfo.project.name.startsWith("mobile"),
  });
});

test("applies stable semantic roles across every workspace", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("desktop"), "workspace review screenshots are desktop-only");
  await mockDevice(page);
  await page.goto("/");
  await expect(page.locator(".connection-fact")).toHaveClass(/online/);

  const workspaceCases = [
    {
      name: "动作",
      selector: ".motion-presets .section-heading > svg",
      property: "color",
      value: "rgb(37, 131, 245)",
      screenshot: "motion",
    },
    {
      name: "灯光",
      selector: ".light-output-action",
      property: "backgroundColor",
      value: "rgb(242, 161, 59)",
      screenshot: "lighting",
    },
    {
      name: "传感器",
      selector: ".injection-lab .section-heading > svg",
      property: "color",
      value: "rgb(37, 131, 245)",
      screenshot: "sensors",
    },
    {
      name: "诊断",
      selector: ".diagnostics-error-band .section-heading > svg",
      property: "color",
      value: "rgb(200, 88, 80)",
      screenshot: "diagnostics",
    },
  ] as const;

  for (const item of workspaceCases) {
    await page.getByRole("button", { name: item.name, exact: true }).click();
    await expect(page.locator(item.selector)).toBeVisible();
    const value = await page.locator(item.selector).evaluate((element, property) =>
      getComputedStyle(element)[property as "color" | "backgroundColor"], item.property,
    );
    expect(value).toBe(item.value);
    const overflowingControls = await page.evaluate(() =>
      Array.from(document.querySelectorAll("button, input, select"))
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && element.scrollWidth > element.clientWidth + 2;
        })
        .map((element) => element.getAttribute("aria-label") ?? element.tagName),
    );
    expect(overflowingControls).toEqual([]);
    await page.screenshot({
      path: path.join(process.cwd(), "test-results", "visual", `workspace-${item.screenshot}-desktop-1440x900.png`),
      fullPage: true,
    });
  }

  await page.getByRole("button", { name: "灯光", exact: true }).click();
  await expect(page.locator(".status-state")).toContainText("自动跟随设备状态");
  await expect(page.getByRole("button", { name: /状态灯/ })).toHaveCount(0);
});
