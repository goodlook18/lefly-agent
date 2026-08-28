import { describe, expect, it } from "vitest";

import { MOTION_PRESETS, SLEEP_POSE } from "./motionPresets";

describe("MOTION_PRESETS", () => {
  it("exposes the initial action library in a stable operator order", () => {
    expect(MOTION_PRESETS).toEqual([
      { name: "wake_up", label: "唤醒" },
      { name: "nod", label: "点头" },
      { name: "headshake", label: "摇头" },
      { name: "happy_wiggle", label: "开心摇摆" },
      { name: "look_up", label: "向上看" },
      { name: "look_down", label: "向下看" },
      { name: "look_left", label: "向左看" },
      { name: "look_right", label: "向右看" },
      { name: "dance_demo", label: "舞蹈演示" },
      { name: "sleep", label: "休眠" },
    ]);
  });

  it("defines Sleep as the complete requested joint pose", () => {
    expect(SLEEP_POSE).toEqual({
      base_yaw: 0,
      base_pitch: -45,
      elbow_pitch: 105,
      wrist_roll: 0,
      wrist_pitch: 45,
    });
  });
});
