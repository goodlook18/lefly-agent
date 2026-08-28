import { Box3, OrthographicCamera, Vector3 } from "three";
import { describe, expect, it } from "vitest";

import { projectModelBounds } from "./projectModelBounds";

function camera() {
  const value = new OrthographicCamera(-2, 2, 2, -2, 0.1, 10);
  value.position.set(0, 0, 5);
  value.lookAt(0, 0, 0);
  value.updateProjectionMatrix();
  value.updateMatrixWorld(true);
  return value;
}

describe("projectModelBounds", () => {
  it("projects all eight box corners into canvas pixels", () => {
    const bounds = projectModelBounds(
      new Box3(new Vector3(-1, -1, -1), new Vector3(1, 1, 1)),
      camera(),
      400,
      200,
    );

    expect(bounds).toEqual({ left: 100, top: 50, right: 300, bottom: 150, width: 200, height: 100 });
  });

  it("does not clamp an out-of-frame model", () => {
    const bounds = projectModelBounds(
      new Box3(new Vector3(-3, -1, -1), new Vector3(3, 1, 1)),
      camera(),
      400,
      200,
    );

    expect(bounds?.left).toBe(-100);
    expect(bounds?.right).toBe(500);
  });

  it("rejects empty and non-finite boxes", () => {
    expect(projectModelBounds(new Box3(), camera(), 400, 200)).toBeNull();
    expect(projectModelBounds(
      new Box3(new Vector3(Number.NaN, 0, 0), new Vector3(1, 1, 1)),
      camera(),
      400,
      200,
    )).toBeNull();
  });
});
