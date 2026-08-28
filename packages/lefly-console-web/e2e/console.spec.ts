import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

async function expectOnline(page: Page) {
  await expect(page.locator(".connection-fact")).toHaveClass(/online/);
  await expect(page.getByLabel("目标设备")).toHaveValue("simulator");
}

async function revision(page: Page) {
  return Number(await page.locator(".footer-revision code").textContent());
}

async function expectRevisionAbove(page: Page, previous: number) {
  await expect.poll(() => revision(page)).toBeGreaterThan(previous);
}

async function assertResponsiveLayout(page: Page) {
  const result = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth;
    const visible = (element: Element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const rects = [".global-header", ".workspace-nav", ".workspace-content", ".status-footer"]
      .map((selector) => document.querySelector(selector))
      .filter((element): element is Element => element !== null && visible(element))
      .map((element) => ({ selector: element.className, rect: element.getBoundingClientRect() }));
    const overlaps: string[] = [];
    for (let left = 0; left < rects.length; left += 1) {
      for (let right = left + 1; right < rects.length; right += 1) {
        const a = rects[left];
        const b = rects[right];
        const width = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
        const height = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
        if (width > 1 && height > 1) overlaps.push(`${a.selector} overlaps ${b.selector}`);
      }
    }
    const outsideViewport = rects
      .filter(({ rect }) => rect.left < -1 || rect.right > viewportWidth + 1)
      .map(({ selector }) => String(selector));
    const overflowingControls = Array.from(document.querySelectorAll("button, select"))
      .filter(visible)
      .filter((element) => element.scrollWidth > element.clientWidth + 2)
      .map((element) => element.getAttribute("aria-label") ?? element.textContent?.trim() ?? element.tagName);
    const stage = document.querySelector(".scene-stage");
    const badge = document.querySelector(".scene-state-badge");
    const axes = Array.from(document.querySelectorAll(".telemetry-axis"));
    const sceneMissing = [
      ...(!stage || !visible(stage) ? ["scene-stage"] : []),
      ...(!badge || !visible(badge) ? ["scene-state-badge"] : []),
      ...(axes.length !== 5 ? [`telemetry-axis count ${axes.length}`] : []),
      ...axes.filter((axis) => !visible(axis)).map((axis) => String(axis.className)),
    ];
    const sceneOutside: string[] = [];
    const sceneOverlaps: string[] = [];
    if (stage && badge && visible(stage) && visible(badge)) {
      const stageRect = stage.getBoundingClientRect();
      const sceneElements = [badge, ...axes].filter(visible);
      for (const element of sceneElements) {
        const rect = element.getBoundingClientRect();
        if (
          rect.left < stageRect.left - 1
          || rect.top < stageRect.top - 1
          || rect.right > stageRect.right + 1
          || rect.bottom > stageRect.bottom + 1
        ) {
          sceneOutside.push(String(element.className));
        }
      }
      const badgeRect = badge.getBoundingClientRect();
      for (const axis of axes.filter(visible)) {
        const axisRect = axis.getBoundingClientRect();
        const overlapWidth = Math.min(badgeRect.right, axisRect.right) - Math.max(badgeRect.left, axisRect.left);
        const overlapHeight = Math.min(badgeRect.bottom, axisRect.bottom) - Math.max(badgeRect.top, axisRect.top);
        if (overlapWidth > 1 && overlapHeight > 1) {
          sceneOverlaps.push(`scene-state-badge overlaps ${axis.className}`);
        }
      }
    }
    return {
      bodyOverflow: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - viewportWidth,
      overlaps,
      outsideViewport,
      overflowingControls,
      sceneMissing,
      sceneOutside,
      sceneOverlaps,
    };
  });

  expect(result.bodyOverflow).toBeLessThanOrEqual(1);
  expect(result.overlaps).toEqual([]);
  expect(result.outsideViewport).toEqual([]);
  expect(result.overflowingControls).toEqual([]);
  expect(result.sceneMissing).toEqual([]);
  expect(result.sceneOutside).toEqual([]);
  expect(result.sceneOverlaps).toEqual([]);
}

async function assertCanvasPixels(page: Page) {
  const canvasLocator = page.locator("canvas[aria-label='LeFly 三维实时姿态']");
  await expect(canvasLocator).toHaveAttribute("data-model-bounds", /.+/);
  await expect(canvasLocator).toHaveAttribute("data-model-mask", /.+/);
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  const pixels = await canvasLocator.evaluate((canvas: HTMLCanvasElement) => {
    const rawBounds = canvas.dataset.modelBounds;
    const rawMask = canvas.dataset.modelMask;
    const bounds = rawBounds ? JSON.parse(rawBounds) as Record<string, number> : null;
    const mask = rawMask ? JSON.parse(rawMask) as Record<string, number> : null;
    const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    if (!gl || !bounds || !mask) {
      return { width: canvas.width, height: canvas.height, nonzero: 0, shades: 0, bounds, mask, modelPixels: 0, modelShades: 0 };
    }
    const values = new Uint8Array(canvas.width * canvas.height * 4);
    gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, values);
    let nonzero = 0;
    const shades = new Set<number>();
    const stride = Math.max(4, Math.floor(values.length / 20_000 / 4) * 4);
    for (let index = 0; index < values.length; index += stride) {
      const red = values[index];
      const green = values[index + 1];
      const blue = values[index + 2];
      if (red || green || blue) nonzero += 1;
      shades.add((red >> 4) * 256 + (green >> 4) * 16 + (blue >> 4));
    }
    const pixel = (x: number, topDownY: number) => {
      const y = canvas.height - 1 - topDownY;
      const index = (y * canvas.width + x) * 4;
      return [values[index], values[index + 1], values[index + 2]];
    };
    const background = pixel(2, 2);
    const modelShades = new Set<number>();
    let modelPixels = 0;
    const left = Math.max(0, Math.floor(bounds.left));
    const top = Math.max(0, Math.floor(bounds.top));
    const right = Math.min(canvas.width - 1, Math.ceil(bounds.right));
    const bottom = Math.min(canvas.height - 1, Math.ceil(bounds.bottom));
    const sampleStep = Math.max(1, Math.floor(Math.min(right - left, bottom - top) / 100));
    for (let y = top; y <= bottom; y += sampleStep) {
      for (let x = left; x <= right; x += sampleStep) {
        const [red, green, blue] = pixel(x, y);
        const distance = Math.abs(red - background[0]) + Math.abs(green - background[1]) + Math.abs(blue - background[2]);
        if (distance > 24) modelPixels += 1;
        modelShades.add((red >> 4) * 256 + (green >> 4) * 16 + (blue >> 4));
      }
    }
    return {
      width: canvas.width,
      height: canvas.height,
      nonzero,
      shades: shades.size,
      bounds,
      mask,
      modelPixels,
      modelShades: modelShades.size,
    };
  });

  expect(pixels.width).toBeGreaterThan(0);
  expect(pixels.height).toBeGreaterThan(0);
  expect(pixels.nonzero).toBeGreaterThan(100);
  expect(pixels.shades).toBeGreaterThan(4);
  expect(pixels.bounds).not.toBeNull();
  expect(pixels.mask).not.toBeNull();
  const bounds = pixels.bounds as Record<string, number>;
  const mask = pixels.mask as Record<string, number>;
  const margin = Math.max(8, Math.min(pixels.width, pixels.height) * 0.02);
  expect(bounds.left).toBeGreaterThanOrEqual(margin);
  expect(bounds.top).toBeGreaterThanOrEqual(margin);
  expect(bounds.right).toBeLessThanOrEqual(pixels.width - margin);
  expect(bounds.bottom).toBeLessThanOrEqual(pixels.height - margin);
  expect(bounds.width).toBeGreaterThanOrEqual(pixels.width * 0.12);
  expect(bounds.height).toBeGreaterThanOrEqual(pixels.height * 0.35);
  expect(bounds.width).toBeLessThanOrEqual(pixels.width);
  expect(bounds.height).toBeLessThanOrEqual(pixels.height);
  expect(mask.width).toBe(pixels.width);
  expect(mask.height).toBe(pixels.height);
  expect(mask.pixelCount).toBeGreaterThan(pixels.width * pixels.height * 0.01);
  expect(mask.pixelRatio).toBeGreaterThan(0.01);
  expect(mask.pixelRatio).toBeLessThan(0.7);
  expect(pixels.modelPixels).toBeGreaterThan(100);
  expect(pixels.modelShades).toBeGreaterThan(8);
}

test("runs the real console, lease, command, and sensor chain", async ({ browser, page }, testInfo) => {
  const motionEvents: string[] = [];
  page.on("websocket", (socket) => {
    if (!socket.url().endsWith("/ws/console")) return;
    socket.on("framereceived", ({ payload }) => {
      if (typeof payload !== "string") return;
      try {
        const message = JSON.parse(payload) as { type?: string; event?: { type?: string } };
        if (message.type === "console.event" && message.event?.type?.startsWith("motion.")) {
          motionEvents.push(message.event.type);
        }
      } catch {
        // Ignore non-JSON frames; protocol validation is covered separately.
      }
    });
  });
  const ownerContext = await browser.newContext({ viewport: testInfo.project.use.viewport });
  const owner = await ownerContext.newPage();
  await owner.goto("/?renderDiagnostics=1");
  await expectOnline(owner);
  await expect(owner.locator(".lease-fact.controller")).toBeVisible();

  await page.goto("/?renderDiagnostics=1");
  await expectOnline(page);
  await expect(page.getByLabel("目标设备")).toContainText("模拟器");
  await expect(page.getByLabel("接管控制权")).toBeVisible();
  await page.getByRole("button", { name: "动作", exact: true }).click();
  await expect(page.getByRole("button", { name: "向左", exact: true })).toBeDisabled();

  await page.getByLabel("接管控制权").click();
  await expect(page.locator(".lease-fact.controller")).toBeVisible();
  await expect(owner.getByLabel("接管控制权")).toBeVisible();
  await owner.getByRole("button", { name: "动作", exact: true }).click();
  await expect(owner.getByRole("button", { name: "向左", exact: true })).toBeDisabled();
  await ownerContext.close();

  await page.getByRole("button", { name: "概览", exact: true }).click();
  await page.getByRole("button", { name: "休眠", exact: true }).click();
  await expect.poll(() => motionEvents.filter((type) => type === "motion.finished").length).toBeGreaterThan(0);
  const wristAxis = page.locator(".telemetry-axis").filter({ hasText: "wrist_pitch" });
  await expect(wristAxis.locator(".axis-value strong")).toContainText("45.0");
  motionEvents.length = 0;

  const wristBeforeNod = await wristAxis.locator(".axis-value strong").textContent();
  await page.getByRole("button", { name: "点头", exact: true }).click();
  await expect.poll(() => motionEvents).toContain("motion.started");
  await expect.poll(() => motionEvents).toContain("motion.progress");
  await expect.poll(() => wristAxis.locator(".axis-value strong").textContent()).not.toBe(wristBeforeNod);
  await expect(page.locator("canvas[aria-label='LeFly 三维实时姿态']")).toBeVisible();
  await expect.poll(() => motionEvents).toContain("motion.finished");

  const motionRevision = await revision(page);
  await page.getByRole("button", { name: "动作", exact: true }).click();
  await page.getByRole("button", { name: "向左", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByLabel("base_yaw")).toHaveValue("60");
  await expectRevisionAbove(page, motionRevision);

  await page.getByRole("button", { name: "灯光", exact: true }).click();
  const lightRevision = await revision(page);
  await page.getByLabel("选择头部灯 #20A8B5").click();
  await page.getByLabel("头部灯亮度").fill("74");
  await page.getByLabel("应用头部灯").click();
  await expectRevisionAbove(page, lightRevision);
  const headLightPanel = page.locator(".light-channel-panel").first();
  await expect(headLightPanel.locator(".light-readout strong")).toHaveText("#20A8B5");
  await expect(headLightPanel.locator(".light-readout output")).toHaveText("74%");

  await expect(page.locator(".status-state strong")).toHaveText("自动跟随设备状态");
  await expect(page.getByLabel("状态灯 breath")).toHaveCount(0);
  await expect(page.getByLabel("恢复自动状态")).toHaveCount(0);

  await page.getByRole("button", { name: "传感器", exact: true }).click();
  await page.getByLabel("注入左侧触摸").click();
  await expect(page.locator(".event-list")).toContainText("sensor.touch");

  await page.getByRole("button", { name: "概览", exact: true }).click();
  await page.evaluate(() => scrollTo(0, 0));
  await expect(page.locator("canvas[aria-label='LeFly 三维实时姿态']")).toBeVisible();
  await assertCanvasPixels(page);
  await assertResponsiveLayout(page);

  const screenshotPath = path.join(process.cwd(), "test-results", "visual", `${testInfo.project.name}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true });
});

test("disables actions and sensor injection after a revision gap", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-1440x900", "The protocol gate is viewport-independent.");

  let interceptedGap = false;
  await page.routeWebSocket("**/ws/console", (socket) => {
    const server = socket.connectToServer();
    server.onMessage((message) => {
      if (!interceptedGap && typeof message === "string") {
        try {
          const parsed = JSON.parse(message) as {
            type?: string;
            event?: { type?: string; payload?: { revision?: number } };
          };
          const revision = parsed.event?.payload?.revision;
          if (parsed.type === "console.event" && parsed.event?.type === "device.state_changed" && typeof revision === "number") {
            interceptedGap = true;
            socket.send(JSON.stringify({
              ...parsed,
              event: { ...parsed.event, payload: { ...parsed.event.payload, revision: revision + 1 } },
            }));
            return;
          }
        } catch {
          // Preserve any non-JSON server frames unchanged.
        }
      }
      socket.send(message);
    });
  });

  await page.goto("/?renderDiagnostics=1");
  await expectOnline(page);
  await page.getByRole("button", { name: "灯光", exact: true }).click();
  await page.getByLabel("选择头部灯 #20A8B5").click();
  await page.getByLabel("应用头部灯").click();
  await expect(page.locator(".connection-fact")).toHaveClass(/stale/);
  await expect(page.getByText("状态陈旧", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "动作", exact: true }).click();
  const left = page.getByRole("button", { name: "向左", exact: true });
  await expect(left).toBeDisabled();
  await expect(left).toHaveAttribute("title", /状态陈旧/);

  await page.getByRole("button", { name: "传感器", exact: true }).click();
  const touch = page.getByLabel("注入左侧触摸");
  await expect(touch).toBeDisabled();
  await expect(touch).toHaveAttribute("title", /状态陈旧/);
  expect(interceptedGap).toBe(true);
});
