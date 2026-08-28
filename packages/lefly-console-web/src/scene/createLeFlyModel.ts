import {
  AdditiveBlending,
  BufferGeometry,
  Color,
  CylinderGeometry,
  ExtrudeGeometry,
  Group,
  Material,
  Mesh,
  MeshPhysicalMaterial,
  MeshStandardMaterial,
  Object3D,
  PlaneGeometry,
  PointLight,
  Shape,
  ShapeGeometry,
  ShaderMaterial,
  Texture,
  type ColorRepresentation,
} from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

import type { StatusEffect } from "../deviceProtocol";
import {
  STATUS_SEGMENT_COUNT,
  statusStripEmissiveIntensity,
  statusStripLevels,
  statusStripSurfaceLevel,
} from "./statusStripAnimation";

export const LEFLY_JOINT_NAMES = [
  "base_yaw",
  "base_pitch",
  "elbow_pitch",
  "wrist_roll",
  "wrist_pitch",
] as const;

export type LeFlyJointName = (typeof LEFLY_JOINT_NAMES)[number];
export type JointPositionValue = number | { pos?: number };
export type JointPositions = Partial<Record<LeFlyJointName, JointPositionValue>>;

export interface StatusStripSettings {
  color?: ColorRepresentation | null;
  effect?: StatusEffect | null;
}

export interface LeFlyPivots {
  baseYaw: Group;
  basePitch: Group;
  elbowPitch: Group;
  wristPitch: Group;
  wristRoll: Group;
}

export interface LeFlyModel {
  root: Group;
  group: Group;
  pivots: LeFlyPivots;
  head: Group;
  headLight: Mesh<BufferGeometry, MeshStandardMaterial>;
  statusStrip: Mesh<BufferGeometry, MeshStandardMaterial>;
  statusSegments: Array<Mesh<BufferGeometry, MeshStandardMaterial>>;
  setJointPositions(positions: JointPositions): void;
  setHeadLight(color: ColorRepresentation | null | undefined, brightness: number): void;
  setStatusStrip(settings: StatusStripSettings): void;
  update(elapsedSeconds: number): void;
  dispose(): void;
}

const METAL = 0xd5d8d7;
const DARK_METAL = 0x242a2e;
const DISPLAY_IVORY = "#f4efe3";
const STATUS_BLUE = "#31a8ff";
const WARM_LIGHT = "#ffd6a0";

const roundedBox = (width: number, height: number, depth: number, radius = 0.08) =>
  new RoundedBoxGeometry(width, height, depth, 4, Math.min(radius, width / 2, height / 2));

function setName<T extends Object3D>(object: T, name: string): T {
  object.name = name;
  return object;
}

function addMesh(
  parent: Object3D,
  name: string,
  geometry: BufferGeometry,
  material: Material,
  position: [number, number, number] = [0, 0, 0],
): Mesh {
  const mesh = setName(new Mesh(geometry, material), name);
  mesh.position.set(...position);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function createWedgeShape(scale = 1): Shape {
  const shape = new Shape();
  shape.moveTo(-0.7 * scale, -0.48 * scale);
  shape.lineTo(0.7 * scale, -0.48 * scale);
  shape.lineTo(0.92 * scale, 0.18 * scale);
  shape.lineTo(0.55 * scale, 0.62 * scale);
  shape.lineTo(-0.55 * scale, 0.62 * scale);
  shape.lineTo(-0.92 * scale, 0.18 * scale);
  shape.closePath();
  return shape;
}

function createWedgeGeometry(): ExtrudeGeometry {
  const geometry = new ExtrudeGeometry(createWedgeShape(), {
    depth: 0.58,
    bevelEnabled: true,
    bevelSegments: 3,
    bevelSize: 0.07,
    bevelThickness: 0.07,
    curveSegments: 8,
  });
  geometry.translate(0, 0, -0.29);
  geometry.computeVertexNormals();
  return geometry;
}

function finitePosition(value: JointPositionValue | undefined): number | undefined {
  const position = typeof value === "number" ? value : value?.pos;
  return typeof position === "number" && Number.isFinite(position) ? position : undefined;
}

function safeColor(value: ColorRepresentation | null | undefined): Color | null {
  if (typeof value === "number" && Number.isFinite(value)) return new Color(value);
  if (value instanceof Color) {
    return [value.r, value.g, value.b].every(Number.isFinite) ? value.clone() : null;
  }
  if (typeof value !== "string") return null;

  const normalized = value.trim().toLowerCase();
  const hex = normalized.match(/^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i);
  const isNamed = Object.prototype.hasOwnProperty.call(Color.NAMES, normalized);
  if (hex) return new Color(normalized.length === 9 ? normalized.slice(0, 7) : normalized);
  return isNamed ? new Color(normalized) : null;
}

function createGlowMaterial(opacity: number): ShaderMaterial {
  return new ShaderMaterial({
    transparent: true,
    blending: AdditiveBlending,
    depthWrite: false,
    toneMapped: false,
    uniforms: {
      uColor: { value: new Color(WARM_LIGHT) },
      uOpacity: { value: opacity },
    },
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uColor;
      uniform float uOpacity;
      varying vec2 vUv;
      void main() {
        vec2 point = (vUv - 0.5) * vec2(1.75, 2.0);
        float distanceFromCenter = length(point);
        float falloff = 1.0 - smoothstep(0.1, 1.25, distanceFromCenter);
        float edgeFade = smoothstep(0.0, 0.08, vUv.x)
          * smoothstep(0.0, 0.08, vUv.y)
          * smoothstep(0.0, 0.08, 1.0 - vUv.x)
          * smoothstep(0.0, 0.08, 1.0 - vUv.y);
        gl_FragColor = vec4(uColor, falloff * edgeFade * uOpacity);
      }
    `,
  });
}

export function createLeFlyModel(): LeFlyModel {
  const root = setName(new Group(), "lefly-model");

  const bodyMaterial = new MeshPhysicalMaterial({
    color: METAL,
    metalness: 0.72,
    roughness: 0.3,
    clearcoat: 0.25,
    clearcoatRoughness: 0.35,
  });
  const edgeMaterial = new MeshStandardMaterial({
    color: DARK_METAL,
    metalness: 0.68,
    roughness: 0.38,
  });
  const displayMaterial = new MeshPhysicalMaterial({
    color: DISPLAY_IVORY,
    emissive: 0xd9d1bf,
    emissiveIntensity: 0.65,
    metalness: 0.03,
    roughness: 0.18,
    clearcoat: 0.95,
    clearcoatRoughness: 0.08,
  });
  const headLightMaterial = new MeshStandardMaterial({
    color: WARM_LIGHT,
    emissive: WARM_LIGHT,
    emissiveIntensity: 7,
    roughness: 0.25,
  });
  const nearGlowMaterial = createGlowMaterial(1.05);
  const farGlowMaterial = createGlowMaterial(0.82);
  const statusMaterial = new MeshStandardMaterial({
    color: 0x101619,
    emissive: 0x000000,
    emissiveIntensity: 0,
    roughness: 0.38,
  });

  const base = addMesh(root, "metal-base", roundedBox(3.1, 0.55, 2.15, 0.16), bodyMaterial);
  base.position.y = 0.275;
  addMesh(root, "base-inset", roundedBox(1.05, 0.08, 0.75, 0.04), edgeMaterial, [0, 0.59, 0]);

  const statusStrip = addMesh(
    root,
    "status-strip",
    roundedBox(1.15, 0.1, 0.045, 0.025),
    statusMaterial,
    [0, 0.28, 1.09],
  ) as Mesh<BufferGeometry, MeshStandardMaterial>;
  statusStrip.castShadow = false;
  statusStrip.userData.status = { color: STATUS_BLUE, effect: "solid" };
  const segmentWidth = 0.075;
  const segmentGap = 0.015;
  const stripWidth = STATUS_SEGMENT_COUNT * segmentWidth + (STATUS_SEGMENT_COUNT - 1) * segmentGap;
  const statusSegments = Array.from({ length: STATUS_SEGMENT_COUNT }, (_, index) => {
    const material = new MeshStandardMaterial({
      color: STATUS_BLUE,
      emissive: STATUS_BLUE,
      emissiveIntensity: 1.8,
      roughness: 0.22,
    });
    const x = -stripWidth / 2 + segmentWidth / 2 + index * (segmentWidth + segmentGap);
    const segment = addMesh(
      root,
      `status-strip-segment-${String(index).padStart(2, "0")}`,
      roundedBox(segmentWidth, 0.075, 0.055, 0.012),
      material,
      [x, 0.28, 1.095],
    ) as Mesh<BufferGeometry, MeshStandardMaterial>;
    segment.castShadow = false;
    return segment;
  });

  const baseYaw = setName(new Group(), "base_yaw");
  baseYaw.position.y = 0.61;
  root.add(baseYaw);
  addMesh(baseYaw, "yaw-collar", new CylinderGeometry(0.45, 0.51, 0.28, 40), edgeMaterial, [0, 0.14, 0]);

  const basePitch = setName(new Group(), "base_pitch");
  basePitch.position.y = 0.42;
  baseYaw.add(basePitch);

  const pitchHingeGeometry = new CylinderGeometry(0.32, 0.32, 0.58, 32);
  const baseHinge = addMesh(basePitch, "base-pitch-hinge", pitchHingeGeometry, edgeMaterial);
  baseHinge.rotation.z = Math.PI / 2;
  addMesh(basePitch, "lower-arm", roundedBox(0.38, 2.35, 0.32, 0.12), bodyMaterial, [0, 1.28, 0]);
  addMesh(basePitch, "lower-arm-spine", roundedBox(0.1, 1.96, 0.35, 0.04), edgeMaterial, [0, 1.28, -0.01]);

  const elbowPitch = setName(new Group(), "elbow_pitch");
  elbowPitch.position.y = 2.55;
  basePitch.add(elbowPitch);
  const elbowHinge = addMesh(elbowPitch, "elbow-hinge", pitchHingeGeometry, edgeMaterial);
  elbowHinge.rotation.z = Math.PI / 2;
  addMesh(elbowPitch, "upper-arm", roundedBox(0.34, 2.02, 0.29, 0.11), bodyMaterial, [0, 1.09, 0]);
  addMesh(elbowPitch, "upper-arm-spine", roundedBox(0.085, 1.66, 0.32, 0.035), edgeMaterial, [0, 1.09, -0.01]);
  addMesh(
    elbowPitch,
    "wrist-roll-housing",
    new CylinderGeometry(0.24, 0.24, 0.42, 32),
    edgeMaterial,
    [0, 1.09, 0],
  );

  const wristRoll = setName(new Group(), "wrist_roll");
  wristRoll.position.y = 2.18;
  elbowPitch.add(wristRoll);

  const wristPitch = setName(new Group(), "wrist_pitch");
  wristRoll.add(wristPitch);
  const wristHinge = addMesh(wristPitch, "wrist-pitch-hinge", new CylinderGeometry(0.28, 0.28, 0.52, 32), edgeMaterial);
  wristHinge.rotation.z = Math.PI / 2;

  const head = setName(new Group(), "head");
  head.position.y = 0.48;
  wristPitch.add(head);
  addMesh(head, "wedge-head-shell", createWedgeGeometry(), bodyMaterial);
  addMesh(head, "head-rear-band", roundedBox(1.34, 0.14, 0.62, 0.05), edgeMaterial, [0, 0.45, -0.02]);
  const headLight = addMesh(
    head,
    "head-light-surface",
    new ShapeGeometry(createWedgeShape(0.94)),
    headLightMaterial,
    [0, 0, 0.38],
  ) as Mesh<BufferGeometry, MeshStandardMaterial>;
  headLight.castShadow = false;
  headLight.userData.brightness = 1;

  const farGlow = addMesh(
    head,
    "head-light-glow-far",
    new PlaneGeometry(2.65, 1.72),
    farGlowMaterial,
    [0, 0.03, 0.412],
  );
  farGlow.castShadow = false;
  farGlow.receiveShadow = false;
  farGlow.renderOrder = 2;
  const nearGlow = addMesh(
    head,
    "head-light-glow-near",
    new PlaneGeometry(2.02, 1.26),
    nearGlowMaterial,
    [0, 0.03, 0.414],
  );
  nearGlow.castShadow = false;
  nearGlow.receiveShadow = false;
  nearGlow.renderOrder = 3;

  const leftDisplay = addMesh(
    head,
    "left-display-screen",
    new CylinderGeometry(0.2, 0.2, 0.035, 48),
    displayMaterial,
    [-0.31, 0.04, 0.428],
  );
  const rightDisplay = addMesh(
    head,
    "right-display-screen",
    new CylinderGeometry(0.2, 0.2, 0.035, 48),
    displayMaterial,
    [0.31, 0.04, 0.428],
  );
  leftDisplay.rotation.x = Math.PI / 2;
  rightDisplay.rotation.x = Math.PI / 2;
  leftDisplay.castShadow = false;
  rightDisplay.castShadow = false;
  leftDisplay.renderOrder = 4;
  rightDisplay.renderOrder = 4;

  const headPointLight = setName(new PointLight(WARM_LIGHT, 16, 7.5, 1.3), "head-light-cast");
  headPointLight.position.set(0, 0.03, 0.74);
  head.add(headPointLight);

  const pivots = { baseYaw, basePitch, elbowPitch, wristPitch, wristRoll };
  let statusEffect: StatusEffect = "solid";
  let lastElapsedSeconds = 0;
  let statusEffectStartedAt = 0;
  let disposed = false;

  const setJointPositions = (positions: JointPositions) => {
    const radians = (value: number) => (value * Math.PI) / 180;
    const mappings: Array<[JointPositionValue | undefined, Group, "x" | "y" | "z"]> = [
      [positions.base_yaw, baseYaw, "y"],
      [positions.base_pitch, basePitch, "x"],
      [positions.elbow_pitch, elbowPitch, "x"],
      [positions.wrist_roll, wristRoll, "y"],
      [positions.wrist_pitch, wristPitch, "x"],
    ];

    for (const [input, pivot, axis] of mappings) {
      const value = finitePosition(input);
      if (value !== undefined) pivot.rotation[axis] = radians(value);
    }
  };

  const setHeadLight = (color: ColorRepresentation | null | undefined, brightness: number) => {
    const nextColor = safeColor(color);
    if (color != null && !nextColor) return;
    if (nextColor) {
      headLightMaterial.color.copy(nextColor);
      headLightMaterial.emissive.copy(nextColor);
      headPointLight.color.copy(nextColor);
      nearGlowMaterial.uniforms.uColor.value.copy(nextColor);
      farGlowMaterial.uniforms.uColor.value.copy(nextColor);
    }
    if (!Number.isFinite(brightness)) return;

    const level = Math.min(1, Math.max(0, brightness));
    headLightMaterial.emissiveIntensity = level * 7;
    headPointLight.intensity = level * 16;
    nearGlowMaterial.uniforms.uOpacity.value = level * 1.05;
    farGlowMaterial.uniforms.uOpacity.value = level * 0.82;
    headLight.userData.brightness = level;
  };

  const setStatusStrip = (settings: StatusStripSettings) => {
    const current = statusStrip.userData.status as { color: ColorRepresentation; effect: StatusEffect };
    const nextColor = safeColor(settings.color);
    if (settings.color != null && !nextColor) return;
    const nextEffect = settings.effect === null ? "solid" : settings.effect ?? statusEffect;
    const colorChanged = nextColor !== null && !statusSegments[0].material.color.equals(nextColor);
    const effectChanged = nextEffect !== statusEffect;
    if (nextColor) {
      statusSegments.forEach((segment) => {
        segment.material.color.copy(nextColor);
        segment.material.emissive.copy(nextColor);
      });
    }
    statusEffect = nextEffect;
    if (colorChanged || effectChanged) statusEffectStartedAt = lastElapsedSeconds;

    statusStrip.userData.status = {
      color: nextColor ? `#${nextColor.getHexString()}` : current.color,
      effect: statusEffect,
    };
  };

  const update = (elapsedSeconds: number) => {
    const time = Number.isFinite(elapsedSeconds) ? elapsedSeconds : 0;
    lastElapsedSeconds = time;
    const levels = statusStripLevels(statusEffect, Math.max(0, time - statusEffectStartedAt), statusSegments.length);
    statusSegments.forEach((segment, index) => {
      const level = levels[index];
      segment.material.color
        .copy(segment.material.emissive)
        .multiplyScalar(statusStripSurfaceLevel(level));
      segment.material.emissiveIntensity = statusStripEmissiveIntensity(level);
    });
  };

  const dispose = () => {
    if (disposed) return;
    disposed = true;

    const geometries = new Set<BufferGeometry>();
    const materials = new Set<Material>();
    const textures = new Set<Texture>();
    root.traverse((object) => {
      if (!(object instanceof Mesh)) return;
      geometries.add(object.geometry);
      const meshMaterials = Array.isArray(object.material) ? object.material : [object.material];
      meshMaterials.forEach((material) => materials.add(material));
    });
    materials.forEach((material) => {
      Object.values(material).forEach((value) => {
        if (value instanceof Texture) textures.add(value);
      });
    });

    textures.forEach((texture) => texture.dispose());
    geometries.forEach((geometry) => geometry.dispose());
    materials.forEach((material) => material.dispose());
  };

  return {
    root,
    group: root,
    pivots,
    head,
    headLight,
    statusStrip,
    statusSegments,
    setJointPositions,
    setHeadLight,
    setStatusStrip,
    update,
    dispose,
  };
}
