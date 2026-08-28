import { expect, test, type Page } from "@playwright/test";

async function tabTo(page: Page, label: string, maximum = 120) {
  for (let index = 0; index < maximum; index += 1) {
    await page.keyboard.press("Tab");
    const focusedLabel = await page.evaluate(() => document.activeElement?.getAttribute("aria-label") ?? null);
    if (focusedLabel === label) return page.locator(":focus");
  }
  throw new Error(`keyboard focus did not reach ${label}`);
}

async function expectVisibleFocus(page: Page) {
  const focus = await page.locator(":focus").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      boxShadow: style.boxShadow,
    };
  });
  const hasOutline = focus.outlineStyle !== "none" && focus.outlineWidth > 0;
  const hasShadow = focus.boxShadow !== "none";
  expect(hasOutline || hasShadow).toBe(true);
}

test("reaches primary controls without a mode switch or keyboard trap", async ({ page }) => {
  await page.goto("/?renderDiagnostics=1");
  await expect(page.locator(".connection-fact")).toHaveClass(/online/);

  await tabTo(page, "目标设备");
  await expectVisibleFocus(page);
  await tabTo(page, "动作");
  await expectVisibleFocus(page);
  await page.keyboard.press("Enter");
  await tabTo(page, "base_yaw");
  await expect(page.locator(":focus")).toHaveAttribute("aria-label", "base_yaw");
  await expectVisibleFocus(page);

  await tabTo(page, "传感器");
  await page.keyboard.press("Enter");
  await tabTo(page, "注入左侧触摸");
  await expect(page.locator(":focus")).toHaveAttribute("aria-label", "注入左侧触摸");
  await expectVisibleFocus(page);
  await expect(page.getByLabel("日常模式")).toHaveCount(0);
  await expect(page.getByLabel("开发模式")).toHaveCount(0);
});
