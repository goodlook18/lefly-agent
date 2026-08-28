import { describe, expect, it } from "vitest";

import {
  STATUS_SEGMENT_COUNT,
  statusStripEmissiveIntensity,
  statusStripLevels,
  statusStripSurfaceLevel,
} from "./statusStripAnimation";

describe("statusStripLevels", () => {
  it("renders stable solid and bounded fade levels", () => {
    expect(statusStripLevels("solid", 0)).toEqual(Array(STATUS_SEGMENT_COUNT).fill(1));
    expect(statusStripLevels("fade", 0)).toEqual(Array(STATUS_SEGMENT_COUNT).fill(0.12));
    expect(statusStripLevels("fade", 2)).toEqual(Array(STATUS_SEGMENT_COUNT).fill(1));
  });

  it("breathes every segment together on a four-second cycle", () => {
    const dim = statusStripLevels("breath", 0);
    const rising = statusStripLevels("breath", 1);
    const peak = statusStripLevels("breath", 2);
    const falling = statusStripLevels("breath", 3);
    const nextCycle = statusStripLevels("breath", 4);

    expect(new Set(rising).size).toBe(1);
    expect(peak[0]).toBeCloseTo(1);
    expect(falling[0]).toBeCloseTo(rising[0]);
    expect(nextCycle[0]).toBeCloseTo(dim[0]);
  });

  it("moves marquee and level sweep from left to right", () => {
    const first = statusStripLevels("marquee", 0);
    const second = statusStripLevels("marquee", 0.25);
    expect(first.indexOf(1)).toBe(0);
    expect(second.indexOf(1)).toBe(1);
    expect(statusStripLevels("level_sweep", 0.7).filter((level) => level === 1).length).toBeGreaterThan(3);
    expect(statusStripLevels("level_sweep", 1.49).filter((level) => level === 1)).toHaveLength(12);
    expect(statusStripLevels("level_sweep", 1.5).filter((level) => level === 1)).toHaveLength(1);
  });

  it("maps semantic levels to high-contrast virtual materials", () => {
    expect(statusStripEmissiveIntensity(0.12)).toBeCloseTo(0.02);
    expect(statusStripEmissiveIntensity(1)).toBeCloseTo(3.2);
    expect(statusStripSurfaceLevel(0.12)).toBeCloseTo(0.03);
    expect(statusStripSurfaceLevel(1)).toBeCloseTo(0.25);
    expect(statusStripEmissiveIntensity(0.56)).toBeLessThan(
      statusStripEmissiveIntensity(1),
    );
  });

  it("blinks the complete strip and normalizes invalid elapsed time", () => {
    expect(statusStripLevels("blink", 0)).toEqual(Array(STATUS_SEGMENT_COUNT).fill(1));
    expect(statusStripLevels("blink", 0.6)).toEqual(Array(STATUS_SEGMENT_COUNT).fill(0.12));
    expect(statusStripLevels("solid", Number.NaN)).toEqual(Array(STATUS_SEGMENT_COUNT).fill(1));
  });

  it("returns finite bounded levels with the requested count", () => {
    for (const effect of ["fade", "breath", "solid", "marquee", "level_sweep", "blink"] as const) {
      const levels = statusStripLevels(effect, 0.73, 7);
      expect(levels).toHaveLength(7);
      expect(levels.every((level) => Number.isFinite(level) && level >= 0.12 && level <= 1)).toBe(true);
    }
    expect(() => statusStripLevels("solid", 0, 0)).toThrow(/segment count/i);
    expect(() => statusStripLevels("solid", 0, -1)).toThrow(/segment count/i);
  });
});
