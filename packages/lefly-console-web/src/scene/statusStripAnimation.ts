import type { StatusEffect } from "../deviceProtocol";

export const STATUS_SEGMENT_COUNT = 12;

const DIM_LEVEL = 0.12;
const DIM_EMISSIVE_INTENSITY = 0.02;
const BRIGHT_EMISSIVE_INTENSITY = 3.2;
const DIM_SURFACE_LEVEL = 0.03;
const BRIGHT_SURFACE_LEVEL = 0.25;
const BREATH_PERIOD_SECONDS = 4;

function normalizedLevel(level: number): number {
  if (!Number.isFinite(level)) return 0;
  return Math.min(1, Math.max(0, (level - DIM_LEVEL) / (1 - DIM_LEVEL)));
}

export function statusStripEmissiveIntensity(level: number): number {
  const normalized = normalizedLevel(level);
  return DIM_EMISSIVE_INTENSITY
    + normalized * (BRIGHT_EMISSIVE_INTENSITY - DIM_EMISSIVE_INTENSITY);
}

export function statusStripSurfaceLevel(level: number): number {
  const normalized = normalizedLevel(level);
  return DIM_SURFACE_LEVEL + normalized * (BRIGHT_SURFACE_LEVEL - DIM_SURFACE_LEVEL);
}

export function statusStripLevels(
  effect: StatusEffect,
  elapsedSeconds: number,
  segmentCount = STATUS_SEGMENT_COUNT,
): number[] {
  if (!Number.isInteger(segmentCount) || segmentCount <= 0) {
    throw new RangeError("status strip segment count must be a positive integer");
  }
  const elapsed = Number.isFinite(elapsedSeconds) && elapsedSeconds > 0 ? elapsedSeconds : 0;

  if (effect === "solid") return Array(segmentCount).fill(1);
  if (effect === "fade") {
    const level = DIM_LEVEL + (1 - DIM_LEVEL) * Math.min(1, elapsed / 1.2);
    return Array(segmentCount).fill(level);
  }
  if (effect === "breath") {
    const phase = (elapsed % BREATH_PERIOD_SECONDS) / BREATH_PERIOD_SECONDS;
    const level = DIM_LEVEL + (1 - DIM_LEVEL) * (0.5 - 0.5 * Math.cos(phase * Math.PI * 2));
    return Array(segmentCount).fill(level);
  }
  if (effect === "marquee") {
    const head = Math.floor(elapsed * 4) % segmentCount;
    return Array.from({ length: segmentCount }, (_, index) => {
      const distance = (head - index + segmentCount) % segmentCount;
      return distance === 0 ? 1 : distance === 1 ? 0.65 : distance === 2 ? 0.3 : DIM_LEVEL;
    });
  }
  if (effect === "level_sweep") {
    const filled = Math.floor(((elapsed % 1.5) / 1.5) * segmentCount) + 1;
    return Array.from({ length: segmentCount }, (_, index) => index < filled ? 1 : DIM_LEVEL);
  }

  const level = Math.floor(elapsed / 0.5) % 2 === 0 ? 1 : DIM_LEVEL;
  return Array(segmentCount).fill(level);
}
