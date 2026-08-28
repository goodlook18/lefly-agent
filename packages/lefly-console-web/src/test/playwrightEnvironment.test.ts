import { describe, expect, it } from "vitest";

import { chromiumLaunchOptions, withLocalNoProxy } from "../../playwright.environment";

describe("Playwright environment compatibility", () => {
  it("appends localhost bypasses without replacing existing proxy exclusions", () => {
    expect(withLocalNoProxy({ NO_PROXY: "internal.example", no_proxy: "10.0.0.1" })).toMatchObject({
      NO_PROXY: "internal.example,127.0.0.1,localhost",
      no_proxy: "10.0.0.1,127.0.0.1,localhost",
    });
  });

  it("uses Playwright Chromium by default and an executable only when configured", () => {
    expect(chromiumLaunchOptions(undefined)).toBeUndefined();
    expect(chromiumLaunchOptions("/Applications/Google Chrome")).toEqual({
      executablePath: "/Applications/Google Chrome",
    });
  });
});
