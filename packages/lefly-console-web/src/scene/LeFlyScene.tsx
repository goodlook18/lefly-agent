import { useEffect, useRef, useState } from "react";
import {
  ACESFilmicToneMapping,
  Color,
  DirectionalLight,
  HemisphereLight,
  Mesh,
  MeshStandardMaterial,
  PCFSoftShadowMap,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  SRGBColorSpace,
  WebGLRenderer,
  type ColorRepresentation,
} from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

import {
  createLeFlyModel,
  type JointPositions,
  type LeFlyModel,
  type StatusStripSettings,
} from "./createLeFlyModel";
import {
  createModelRenderDiagnostics,
  type ModelRenderDiagnostics,
} from "./modelRenderDiagnostics";
import "./LeFlyScene.css";

export interface HeadLightState {
  color?: ColorRepresentation | null;
  brightness?: number | null;
}

export interface LeFlySceneProps {
  joints?: JointPositions;
  headLight?: HeadLightState | null;
  statusStrip?: StatusStripSettings | null;
  className?: string;
  "aria-label"?: string;
}

export function LeFlyScene({
  joints,
  headLight,
  statusStrip,
  className,
  "aria-label": ariaLabel = "Interactive 3D view of LeFly",
}: LeFlySceneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const modelRef = useRef<LeFlyModel | null>(null);
  const diagnosticsUpdateRef = useRef<(() => void) | null>(null);
  const [webGlUnavailable, setWebGlUnavailable] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let renderer: WebGLRenderer | null = null;
    let scene: Scene | null = null;
    let model: LeFlyModel | null = null;
    let groundGeometry: PlaneGeometry | null = null;
    let groundMaterial: MeshStandardMaterial | null = null;
    let controls: OrbitControls | null = null;
    let observer: ResizeObserver | null = null;
    let resizeListener: (() => void) | null = null;
    let frameId: number | null = null;
    let diagnostics: ModelRenderDiagnostics | null = null;
    let diagnosticsListener: (() => void) | null = null;
    let active = true;
    let cleaned = false;

    const attempt = (operation: () => void) => {
      try {
        operation();
      } catch {
        // Continue releasing the remaining independently-owned resources.
      }
    };
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      active = false;
      if (frameId !== null) attempt(() => cancelAnimationFrame(frameId as number));
      if (observer) attempt(() => observer?.disconnect());
      if (resizeListener) attempt(() => window.removeEventListener("resize", resizeListener as () => void));
      if (controls && diagnosticsListener) {
        attempt(() => controls?.removeEventListener("change", diagnosticsListener as () => void));
      }
      if (controls) attempt(() => controls?.dispose());
      if (diagnosticsUpdateRef.current === diagnostics?.update) diagnosticsUpdateRef.current = null;
      if (diagnostics) attempt(() => diagnostics?.dispose());
      if (renderer) delete renderer.domElement.dataset.statusEffect;
      if (modelRef.current === model) modelRef.current = null;
      if (canvasRef.current === renderer?.domElement) canvasRef.current = null;
      if (model) attempt(() => model?.dispose());
      if (groundGeometry) attempt(() => groundGeometry?.dispose());
      if (groundMaterial) attempt(() => groundMaterial?.dispose());
      if (renderer) {
        attempt(() => renderer?.dispose());
        if (renderer.domElement.parentNode === container) {
          attempt(() => renderer?.domElement.remove());
        }
      }
    };

    try {
      const diagnosticsEnabled = new URLSearchParams(window.location.search).get("renderDiagnostics") === "1";
      renderer = new WebGLRenderer({
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: diagnosticsEnabled,
        powerPreference: "high-performance",
      });
      scene = new Scene();
      scene.background = new Color(0xe7eaeb);

      const camera = new PerspectiveCamera(42, 1, 0.1, 60);
      camera.position.set(8.85, 5.56, 11.18);
      camera.lookAt(0, 3, 0);

      renderer.outputColorSpace = SRGBColorSpace;
      renderer.toneMapping = ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.05;
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = PCFSoftShadowMap;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.domElement.setAttribute("role", "img");
      container.appendChild(renderer.domElement);
      canvasRef.current = renderer.domElement;

      model = createLeFlyModel();
      modelRef.current = model;
      scene.add(model.root);

      groundGeometry = new PlaneGeometry(22, 22);
      groundMaterial = new MeshStandardMaterial({
        color: 0xd8dcdd,
        roughness: 0.88,
        metalness: 0.03,
      });
      const ground = new Mesh(groundGeometry, groundMaterial);
      ground.name = "studio-ground";
      ground.rotation.x = -Math.PI / 2;
      ground.receiveShadow = true;
      scene.add(ground);

      const hemisphere = new HemisphereLight(0xf8fbff, 0x737a7d, 1.9);
      const keyLight = new DirectionalLight(0xffffff, 4.2);
      keyLight.position.set(5.5, 8.5, 7);
      keyLight.castShadow = true;
      keyLight.shadow.mapSize.set(1024, 1024);
      keyLight.shadow.camera.near = 1;
      keyLight.shadow.camera.far = 24;
      const fillLight = new DirectionalLight(0xd8eaff, 2.1);
      fillLight.position.set(-6, 4, 4);
      scene.add(hemisphere, keyLight, fillLight);

      controls = new OrbitControls(camera, renderer.domElement);
      controls.target.set(0, 3, 0);
      controls.enableDamping = true;
      controls.enablePan = false;
      controls.minDistance = 5;
      controls.maxDistance = 16;
      controls.minPolarAngle = 0.35;
      controls.maxPolarAngle = Math.PI / 2.05;
      controls.update();
      if (diagnosticsEnabled) {
        diagnostics = createModelRenderDiagnostics({
          renderer,
          scene,
          camera,
          modelRoot: model.root,
          canvas: renderer.domElement,
        });
        diagnosticsListener = diagnostics.update;
        diagnosticsUpdateRef.current = diagnostics.update;
        controls.addEventListener("change", diagnosticsListener);
      }

      const resize = (width: number, height: number) => {
        const safeWidth = Math.max(1, Math.round(width));
        const safeHeight = Math.max(1, Math.round(height));
        camera.aspect = safeWidth / safeHeight;
        camera.updateProjectionMatrix();
        renderer?.setSize(safeWidth, safeHeight, false);
        diagnostics?.update();
      };
      if (typeof ResizeObserver === "function") {
        observer = new ResizeObserver((entries) => {
          const bounds = entries[0]?.contentRect;
          if (bounds) resize(bounds.width, bounds.height);
        });
        observer.observe(container);
      } else {
        resizeListener = () => {
          const bounds = container.getBoundingClientRect();
          resize(bounds.width || container.clientWidth, bounds.height || container.clientHeight);
        };
        resizeListener();
        window.addEventListener("resize", resizeListener);
      }

      const renderFrame = (time: number) => {
        if (!active || !controls || !model || !renderer || !scene) return;
        controls.update();
        model.update(time / 1000);
        renderer.render(scene, camera);
        frameId = requestAnimationFrame(renderFrame);
      };
      frameId = requestAnimationFrame(renderFrame);
      setWebGlUnavailable(false);
    } catch {
      cleanup();
      setWebGlUnavailable(true);
      return;
    }

    return cleanup;
  }, []);

  useEffect(() => {
    canvasRef.current?.setAttribute("aria-label", ariaLabel);
  }, [ariaLabel]);

  useEffect(() => {
    if (joints) {
      modelRef.current?.setJointPositions(joints);
      diagnosticsUpdateRef.current?.();
    }
  }, [joints]);

  useEffect(() => {
    if (headLight?.brightness == null) return;
    modelRef.current?.setHeadLight(headLight.color, headLight.brightness);
  }, [headLight]);

  useEffect(() => {
    if (statusStrip) modelRef.current?.setStatusStrip(statusStrip);
    const canvas = canvasRef.current;
    if (canvas && new URLSearchParams(window.location.search).get("renderDiagnostics") === "1") {
      if (statusStrip?.effect) canvas.dataset.statusEffect = statusStrip.effect;
      else delete canvas.dataset.statusEffect;
    }
    diagnosticsUpdateRef.current?.();
  }, [statusStrip]);

  const classes = ["lefly-scene", className].filter(Boolean).join(" ");
  return (
    <div ref={containerRef} className={classes} data-webgl={webGlUnavailable ? "unavailable" : "ready"}>
      {webGlUnavailable ? (
        <div className="lefly-scene__fallback" role="status">
          3D preview is unavailable on this device.
        </div>
      ) : null}
    </div>
  );
}
