import { expect, test, type Page } from "@playwright/test";

async function expectOnline(page: Page) {
  await expect(page.locator(".connection-fact")).toHaveClass(/online/);
  await expect(page.getByLabel("目标设备")).toHaveValue("simulator");
}

async function sendDeviceCommand(page: Page, type: string, payload: Record<string, unknown>) {
  await page.evaluate(({ commandType, commandPayload }) => new Promise<void>((resolve, reject) => {
    const id = crypto.randomUUID();
    const socket = new WebSocket(`ws://${location.host}/ws/device/simulator`);
    const timer = window.setTimeout(() => {
      socket.close();
      reject(new Error(`timed out waiting for ${commandType}`));
    }, 5_000);
    socket.addEventListener("open", () => socket.send(JSON.stringify({
      version: "1",
      id,
      type: commandType,
      timestamp: new Date().toISOString(),
      device_id: "lefly-sim-01",
      payload: commandPayload,
    })));
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data)) as { type?: string; correlation_id?: string };
      if (message.type === "device.error" && message.correlation_id === id) {
        window.clearTimeout(timer);
        socket.close();
        reject(new Error(`device rejected ${commandType}`));
      }
      if (message.type === "device.state_changed" && message.correlation_id === id) {
        window.clearTimeout(timer);
        socket.close();
        resolve();
      }
    });
    socket.addEventListener("error", () => {
      window.clearTimeout(timer);
      reject(new Error(`device socket failed for ${commandType}`));
    });
  }), { commandType: type, commandPayload: payload });
}

test("injects touch gesture and face without a global mode", async ({ page }) => {
  await page.goto("/?renderDiagnostics=1");
  await expectOnline(page);
  await expect(page.getByLabel("日常模式")).toHaveCount(0);
  await expect(page.getByLabel("开发模式")).toHaveCount(0);

  await page.getByRole("button", { name: "传感器", exact: true }).click();
  await page.getByLabel("注入左侧触摸").click();
  await page.getByLabel("Gesture raw ID").fill("7");
  await page.getByLabel("Gesture label").fill("wave");
  await page.getByLabel("注入 Gesture").click();
  await page.getByLabel("Face raw ID").fill("3");
  await page.getByLabel("Face label").fill("known");
  await page.getByLabel("注入 Face").click();

  await expect(page.locator(".event-list")).toContainText("sensor.touch");
  await expect(page.locator(".event-list")).toContainText("sensor.vision.gesture");
  await expect(page.locator(".event-list")).toContainText("sensor.vision.face");
  await expect(page.locator(".sensor-readings")).toContainText("raw ID 7 · wave");
  await expect(page.locator(".sensor-readings")).toContainText("raw ID 3 · known");
});

test("accepts api-first paint without corrupting the console", async ({ page }) => {
  await page.goto("/?renderDiagnostics=1");
  await expectOnline(page);
  const pixels = Array.from({ length: 64 }, (_, index) => index % 2 === 0 ? "#2F9D68" : "#438CFF");

  await sendDeviceCommand(page, "light.paint", { target: "head_matrix", pixels });
  await page.getByRole("button", { name: "灯光", exact: true }).click();
  await expect(page.locator(".light-channel-panel").first().locator(".light-readout strong")).toHaveText("#2F9D68");
  await expect(page.getByLabel("头部灯亮度")).toBeEnabled();
  await page.getByRole("button", { name: "概览", exact: true }).click();
  await expect(page.locator("canvas[aria-label='LeFly 三维实时姿态']")).toBeVisible();

  await sendDeviceCommand(page, "light.solid", { target: "head_matrix", color: "#FFFFFF" });
});

test("runs the rest and wake lifecycle from distinct overview controls", async ({ page }) => {
  await page.goto("/?renderDiagnostics=1");
  await expectOnline(page);

  await page.getByRole("button", { name: "进入待机状态" }).click();
  const canvas = page.locator("canvas[aria-label='LeFly 三维实时姿态']");
  await expect(canvas).toHaveAttribute("data-status-effect", "breath");

  await page.getByRole("button", { name: "唤醒" }).click();
  await expect(canvas).toHaveAttribute("data-status-effect", "fade");
  await expect(canvas).toHaveAttribute("data-status-effect", "solid");
});

test("renders every robot-owned status mapping", async ({ page }, testInfo) => {
  await page.goto("/?renderDiagnostics=1");
  await expectOnline(page);
  const expected = [
    ["starting", "fade"],
    ["resting", "breath"],
    ["active", "solid"],
    ["listening", "breath"],
    ["thinking", "marquee"],
    ["speaking", "level_sweep"],
  ] as const;

  for (const [mode, effect] of expected) {
    await sendDeviceCommand(page, "status.set", { mode });
    await expect(page.locator("canvas[aria-label='LeFly 三维实时姿态']")).toHaveAttribute("data-status-effect", effect);
  }

  await sendDeviceCommand(page, "status.set", { mode: "active" });
  await expect(page.locator("canvas[aria-label='LeFly 三维实时姿态']")).toHaveAttribute("data-status-effect", "solid");
  await page.getByRole("button", { name: "灯光", exact: true }).click();
  const statusPanel = page.locator(".status-channel-panel");
  await expect(statusPanel.locator(".light-readout output")).toHaveText("active");
  await expect(statusPanel.locator(".light-readout > span")).toHaveCSS("background-color", "rgb(255, 240, 208)");
  await page.getByRole("button", { name: "概览", exact: true }).click();

  if (testInfo.project.name === "mobile-390x844") {
    await sendDeviceCommand(page, "status.set", { mode: "error" });
    await expect(page.locator("canvas[aria-label='LeFly 三维实时姿态']")).toHaveAttribute("data-status-effect", "blink");
  } else {
    await expect(page.locator("canvas[aria-label='LeFly 三维实时姿态']")).toHaveAttribute("data-status-effect", "solid");
  }
  await expect(page.getByLabel(/状态灯/)).toHaveCount(0);
});
