import { Box3, Vector3, type Camera } from "three";

export interface ModelCanvasBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
}

export function projectModelBounds(
  box: Box3,
  camera: Camera,
  canvasWidth: number,
  canvasHeight: number,
): ModelCanvasBounds | null {
  const boxValues = [box.min.x, box.min.y, box.min.z, box.max.x, box.max.y, box.max.z];
  if (
    box.isEmpty()
    || !boxValues.every(Number.isFinite)
    || !Number.isFinite(canvasWidth)
    || !Number.isFinite(canvasHeight)
    || canvasWidth <= 0
    || canvasHeight <= 0
  ) return null;

  let left = Number.POSITIVE_INFINITY;
  let top = Number.POSITIVE_INFINITY;
  let right = Number.NEGATIVE_INFINITY;
  let bottom = Number.NEGATIVE_INFINITY;
  for (const x of [box.min.x, box.max.x]) {
    for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) {
        const projected = new Vector3(x, y, z).project(camera);
        if (![projected.x, projected.y, projected.z].every(Number.isFinite)) return null;
        const pixelX = (projected.x + 1) * canvasWidth / 2;
        const pixelY = (1 - projected.y) * canvasHeight / 2;
        left = Math.min(left, pixelX);
        top = Math.min(top, pixelY);
        right = Math.max(right, pixelX);
        bottom = Math.max(bottom, pixelY);
      }
    }
  }

  const values = [left, top, right, bottom, right - left, bottom - top];
  if (!values.every(Number.isFinite) || right <= left || bottom <= top) return null;
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}
