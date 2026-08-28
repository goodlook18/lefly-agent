import { Box3, Color, Material, Mesh, PointLight, ShaderMaterial, Vector3, type BufferGeometry } from "three";
import { describe, expect, it, vi } from "vitest";

import { createLeFlyModel } from "./createLeFlyModel";
import {
  STATUS_SEGMENT_COUNT,
  statusStripEmissiveIntensity,
  statusStripLevels,
  statusStripSurfaceLevel,
} from "./statusStripAnimation";

const degrees = (value: number) => (value * Math.PI) / 180;

describe("createLeFlyModel", () => {
  it("builds the exact five-joint parent hierarchy", () => {
    const model = createLeFlyModel();

    expect(model.pivots.baseYaw.parent).toBe(model.root);
    expect(model.pivots.basePitch.parent).toBe(model.pivots.baseYaw);
    expect(model.pivots.elbowPitch.parent).toBe(model.pivots.basePitch);
    expect(model.pivots.wristRoll.parent).toBe(model.pivots.elbowPitch);
    expect(model.pivots.wristPitch.parent).toBe(model.pivots.wristRoll);
    expect(model.head.parent).toBe(model.pivots.wristPitch);

    model.dispose();
  });

  it("maps degrees to the specified local joint axes", () => {
    const model = createLeFlyModel();

    model.setJointPositions({
      base_yaw: 30,
      base_pitch: -15,
      elbow_pitch: 25,
      wrist_pitch: -35,
      wrist_roll: 45,
    });

    expect(model.pivots.baseYaw.rotation.toArray().slice(0, 3)).toEqual([0, degrees(30), 0]);
    expect(model.pivots.basePitch.rotation.toArray().slice(0, 3)).toEqual([
      degrees(-15),
      0,
      0,
    ]);
    expect(model.pivots.elbowPitch.rotation.toArray().slice(0, 3)).toEqual([
      degrees(25),
      0,
      0,
    ]);
    expect(model.pivots.wristPitch.rotation.toArray().slice(0, 3)).toEqual([
      degrees(-35),
      0,
      0,
    ]);
    expect(model.pivots.wristRoll.rotation.toArray().slice(0, 3)).toEqual([
      0,
      degrees(45),
      0,
    ]);
    expect(model.head.parent).toBe(model.pivots.wristPitch);

    model.dispose();
  });

  it("keeps the elbow and servo housing fixed while wrist roll rotates only the head assembly", () => {
    const model = createLeFlyModel();
    const upperArm = model.root.getObjectByName("upper-arm");
    const rollHousing = model.root.getObjectByName("wrist-roll-housing");

    expect(upperArm?.parent).toBe(model.pivots.elbowPitch);
    expect(rollHousing?.parent).toBe(model.pivots.elbowPitch);
    model.root.updateMatrixWorld(true);
    const upperArmBefore = upperArm?.matrixWorld.clone();
    const housingBefore = rollHousing?.matrixWorld.clone();
    const headBefore = model.head.matrixWorld.clone();

    model.setJointPositions({ wrist_roll: 60 });
    model.root.updateMatrixWorld(true);

    expect(upperArm?.matrixWorld.elements).toEqual(upperArmBefore?.elements);
    expect(rollHousing?.matrixWorld.elements).toEqual(housingBefore?.elements);
    expect(model.head.matrixWorld.elements).not.toEqual(headBefore.elements);

    model.dispose();
  });

  it("preserves omitted joints and ignores non-finite input", () => {
    const model = createLeFlyModel();
    model.setJointPositions({ base_yaw: 12, base_pitch: 18, wrist_roll: -22 });

    model.setJointPositions({
      base_yaw: Number.NaN,
      base_pitch: Number.POSITIVE_INFINITY,
      elbow_pitch: 9,
    });

    expect(model.pivots.baseYaw.rotation.y).toBe(degrees(12));
    expect(model.pivots.basePitch.rotation.x).toBe(degrees(18));
    expect(model.pivots.elbowPitch.rotation.x).toBe(degrees(9));
    expect(model.pivots.wristRoll.rotation.y).toBe(degrees(-22));
    for (const pivot of Object.values(model.pivots)) {
      expect(pivot.rotation.toArray().slice(0, 3).every(Number.isFinite)).toBe(true);
    }

    model.dispose();
  });

  it("updates head light color and bounded brightness", () => {
    const model = createLeFlyModel();
    const nearGlow = model.head.getObjectByName("head-light-glow-near") as Mesh<BufferGeometry, ShaderMaterial>;
    const farGlow = model.head.getObjectByName("head-light-glow-far") as Mesh<BufferGeometry, ShaderMaterial>;
    const castLight = model.head.getObjectByName("head-light-cast") as PointLight;

    model.setHeadLight("#ff6b35", 0.4);

    expect(model.headLight.material.color.equals(new Color("#ff6b35"))).toBe(true);
    expect(model.headLight.material.emissive.equals(new Color("#ff6b35"))).toBe(true);
    expect(model.headLight.material.emissiveIntensity).toBeCloseTo(2.8);
    expect(nearGlow.material.uniforms.uColor.value.equals(new Color("#ff6b35"))).toBe(true);
    expect(farGlow.material.uniforms.uColor.value.equals(new Color("#ff6b35"))).toBe(true);
    expect(nearGlow.material.uniforms.uOpacity.value).toBeGreaterThan(0);
    expect(farGlow.material.uniforms.uOpacity.value).toBeGreaterThan(0);
    expect(castLight.intensity).toBeGreaterThan(5);
    expect(model.headLight.userData.brightness).toBe(0.4);

    model.setHeadLight(null, 0);
    expect(model.headLight.material.emissiveIntensity).toBe(0);
    expect(nearGlow.material.uniforms.uOpacity.value).toBe(0);
    expect(farGlow.material.uniforms.uOpacity.value).toBe(0);
    expect(castLight.intensity).toBe(0);

    model.setHeadLight("not-a-color", Number.NaN);
    expect(model.headLight.material.color.equals(new Color("#ff6b35"))).toBe(true);
    expect(model.headLight.userData.brightness).toBe(0);

    model.dispose();
  });

  it("uses the front face with two glow layers and provides milk-white circular displays", () => {
    const model = createLeFlyModel();
    model.root.updateMatrixWorld(true);

    expect(model.headLight.name).toBe("head-light-surface");
    expect(model.headLight.parent).toBe(model.head);
    expect(model.headLight.geometry.type).toBe("ShapeGeometry");
    const lightFaceSize = new Box3().setFromObject(model.headLight).getSize(new Vector3());
    expect(lightFaceSize.x).toBeGreaterThan(1.6);
    expect(lightFaceSize.y).toBeGreaterThan(0.95);
    expect(model.head.getObjectByName("head-light-panel")).toBeUndefined();
    expect(model.head.getObjectByName("left-eye")).toBeUndefined();
    expect(model.head.getObjectByName("right-eye")).toBeUndefined();

    for (const name of ["head-light-glow-near", "head-light-glow-far"]) {
      const glow = model.head.getObjectByName(name);
      expect(glow).toBeInstanceOf(Mesh);
      expect((glow as Mesh).material).toBeInstanceOf(ShaderMaterial);
    }

    for (const name of ["left-display-screen", "right-display-screen"]) {
      const screen = model.head.getObjectByName(name);
      expect(screen).toBeInstanceOf(Mesh);
      const size = new Box3().setFromObject(screen as Mesh).getSize(new Vector3());
      expect(size.x).toBeGreaterThan(0.35);
      expect(size.x).toBeCloseTo(size.y, 2);
      expect((screen as Mesh).geometry.type).toBe("CylinderGeometry");
      expect(((screen as Mesh).material as Material & { color: Color }).color.equals(new Color("#f4efe3"))).toBe(true);
      expect((screen as Mesh).position.z).toBeGreaterThan(model.headLight.position.z);
    }

    model.dispose();
  });

  it("rejects malformed functional colors without changing light state", () => {
    const model = createLeFlyModel();
    model.setHeadLight("#ff6b35", 0.4);
    model.setStatusStrip({ color: "#31a8ff", effect: "breath" });
    const headColor = model.headLight.material.color.clone();
    const headEmissive = model.headLight.material.emissive.clone();
    const statusColors = model.statusSegments.map((segment) => segment.material.color.clone());
    const statusState = { ...model.statusStrip.userData.status };

    model.setHeadLight("rgb(foo)", 0.9);
    model.setHeadLight("hsl(foo)", 0.9);
    model.setStatusStrip({ color: "rgb(foo)" });
    model.setStatusStrip({ color: "hsl(foo)" });

    expect(model.headLight.material.color).toEqual(headColor);
    expect(model.headLight.material.emissive).toEqual(headEmissive);
    expect(model.headLight.userData.brightness).toBe(0.4);
    expect(model.statusSegments.map((segment) => segment.material.color)).toEqual(statusColors);
    expect(model.statusStrip.userData.status).toEqual(statusState);

    model.dispose();
  });

  it("builds twelve named status emitters and animates every protocol effect", () => {
    const model = createLeFlyModel();

    expect(model.statusSegments).toHaveLength(STATUS_SEGMENT_COUNT);
    expect(model.statusSegments.map((segment) => segment.name)).toEqual(
      Array.from({ length: STATUS_SEGMENT_COUNT }, (_, index) =>
        `status-strip-segment-${String(index).padStart(2, "0")}`),
    );

    let now = 10;
    model.update(now);
    for (const effect of ["fade", "breath", "solid", "marquee", "level_sweep", "blink"] as const) {
      model.setStatusStrip({ color: "#31a8ff", effect });
      model.update(now + 0.7);
      const expected = statusStripLevels(effect, 0.7);
      model.statusSegments.forEach((segment, index) => {
        expect(segment.material.emissive.equals(new Color("#31a8ff"))).toBe(true);
        const expectedSurface = new Color("#31a8ff").multiplyScalar(
          statusStripSurfaceLevel(expected[index]),
        );
        expect(segment.material.color.r).toBeCloseTo(expectedSurface.r);
        expect(segment.material.color.g).toBeCloseTo(expectedSurface.g);
        expect(segment.material.color.b).toBeCloseTo(expectedSurface.b);
        expect(segment.material.emissiveIntensity).toBeCloseTo(
          statusStripEmissiveIntensity(expected[index]),
        );
      });
      expect(model.statusStrip.userData.status).toEqual({ color: "#31a8ff", effect });
      now += 2;
      model.update(now);
    }

    model.dispose();
  });

  it("disposes every unique resource once and remains idempotent", () => {
    const model = createLeFlyModel();
    const geometries = new Set<BufferGeometry>();
    const materials = new Set<Material>();

    model.root.traverse((object) => {
      if (!(object instanceof Mesh)) return;
      geometries.add(object.geometry);
      const meshMaterials = Array.isArray(object.material) ? object.material : [object.material];
      meshMaterials.forEach((material) => materials.add(material));
    });

    const geometrySpies = [...geometries].map((geometry) => vi.spyOn(geometry, "dispose"));
    const materialSpies = [...materials].map((material) => vi.spyOn(material, "dispose"));

    model.dispose();
    model.dispose();

    geometrySpies.forEach((spy) => expect(spy).toHaveBeenCalledTimes(1));
    materialSpies.forEach((spy) => expect(spy).toHaveBeenCalledTimes(1));
  });
});
