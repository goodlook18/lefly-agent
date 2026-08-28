import { expect, test, type Page } from "@playwright/test";

type SentCommand = { channel: "console" | "agent"; type: string; commandType?: string; text?: string };

function observeCommands(page: Page, sent: SentCommand[]) {
  page.on("websocket", (socket) => {
    const channel = socket.url().endsWith("/ws/agent")
      ? "agent"
      : socket.url().endsWith("/ws/console")
        ? "console"
        : null;
    if (channel === null) return;
    socket.on("framesent", ({ payload }) => {
      if (typeof payload !== "string") return;
      try {
        const message = JSON.parse(payload) as {
          type?: string;
          text?: string;
          command?: { type?: string };
        };
        sent.push({
          channel,
          type: message.type ?? "unknown",
          ...(message.command?.type ? { commandType: message.command.type } : {}),
          ...(message.text ? { text: message.text } : {}),
        });
      } catch {
        // Non-JSON frames are rejected by protocol tests elsewhere.
      }
    });
  });
}

async function submit(page: Page, text: string) {
  const input = page.getByRole("textbox", { name: "文本指令" });
  await input.fill(text);
  await input.press("Enter");
  return input;
}

test("M3 Agent keeps direct controls deterministic and streams recoverable text turns", async ({ page }) => {
  const sent: SentCommand[] = [];
  observeCommands(page, sent);
  await page.goto("/");

  const input = page.getByRole("textbox", { name: "文本指令" });
  const chat = page.getByRole("log", { name: "最近聊天记录" });
  await expect(page.locator(".connection-fact")).toHaveClass(/online/);
  const acquireControl = page.getByLabel("接管控制权");
  if (await acquireControl.isVisible()) await acquireControl.click();
  await expect(page.locator(".lease-fact.controller")).toBeVisible();
  await expect(input).toBeEnabled();

  await page.getByRole("button", { name: "点头", exact: true }).click();
  await expect.poll(() => sent.filter((item) => item.commandType === "motion.play").length).toBe(1);
  expect(sent.filter((item) => item.type === "agent.submit_text")).toHaveLength(0);

  await submit(page, "蓝灯");
  await expect(chat).toContainText("好的，正在执行：头灯设为蓝色。");
  await expect(input).toBeFocused();
  expect(sent.filter((item) => item.type === "agent.submit_text" && item.text === "蓝灯")).toHaveLength(1);
  await expect.poll(() => sent.filter((item) => item.commandType === "light.solid").length).toBe(0);

  await submit(page, "请执行M3流式动作");
  const streaming = chat.locator(".agent-message.stream-streaming");
  await expect(streaming).toContainText("流式");
  await expect(chat).toContainText("流式动作完成");
  await expect(chat.getByRole("status", { name: "工具执行进度" }).last()).toContainText("执行动作 · 已完成");
  await expect(input).toBeFocused();
  await expect.poll(() => sent.filter((item) => item.commandType === "motion.play").length).toBe(1);
  expect(sent.filter((item) => item.type === "agent.submit_text" && item.text === "请执行M3流式动作")).toHaveLength(1);
  await expect(chat.locator(".agent-message", { hasText: "流式动作完成" })).toHaveCount(1);

  await submit(page, "请做M3中断测试");
  const interrupted = chat.locator(".agent-message.stream-interrupted").last();
  await expect(interrupted).toContainText("回复到一半");
  await expect(interrupted).toContainText("处理请求失败，请稍后重试。");

  await submit(page, "测试模型恢复");
  await expect(chat).toContainText("模型恢复成功");
  await expect(input).toBeFocused();
  await expect(chat.locator(".agent-message", { hasText: "模型恢复成功" })).toHaveCount(1);
});
