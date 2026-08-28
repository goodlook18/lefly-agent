import {
  Box3,
  Color,
  WebGLRenderTarget,
  type Object3D,
  type PerspectiveCamera,
  type Scene,
  type WebGLRenderer,
} from "three";

import { projectModelBounds } from "./projectModelBounds";

interface ModelRenderDiagnosticsOptions {
  renderer: WebGLRenderer;
  scene: Scene;
  camera: PerspectiveCamera;
  modelRoot: Object3D;
  canvas: HTMLCanvasElement;
}

export interface ModelRenderDiagnostics {
  update(): void;
  dispose(): void;
}

export function createModelRenderDiagnostics({
  renderer,
  scene,
  camera,
  modelRoot,
  canvas,
}: ModelRenderDiagnosticsOptions): ModelRenderDiagnostics {
  const box = new Box3();
  const renderTarget = new WebGLRenderTarget(1, 1, {
    depthBuffer: true,
    stencilBuffer: false,
  });
  let pixels = new Uint8Array(4);
  let targetWidth = 1;
  let targetHeight = 1;
  let disposed = false;

  const clearDatasets = () => {
    delete canvas.dataset.modelBounds;
    delete canvas.dataset.modelMask;
  };

  const update = () => {
    if (disposed) return;
    const width = canvas.width;
    const height = canvas.height;
    if (width <= 0 || height <= 0) {
      clearDatasets();
      return;
    }

    modelRoot.updateWorldMatrix(true, true);
    camera.updateMatrixWorld(true);
    box.makeEmpty().setFromObject(modelRoot, true);
    const bounds = projectModelBounds(box, camera, width, height);
    if (!bounds) {
      clearDatasets();
      return;
    }

    if (width !== targetWidth || height !== targetHeight) {
      targetWidth = width;
      targetHeight = height;
      renderTarget.setSize(width, height);
      pixels = new Uint8Array(width * height * 4);
    }

    const previousTarget = renderer.getRenderTarget();
    const previousBackground = scene.background;
    const previousClearColor = renderer.getClearColor(new Color()).clone();
    const previousClearAlpha = renderer.getClearAlpha();
    const siblings = scene.children
      .filter((child) => child !== modelRoot)
      .map((child) => ({ child, visible: child.visible }));
    try {
      for (const { child } of siblings) child.visible = false;
      scene.background = null;
      renderer.setRenderTarget(renderTarget);
      renderer.setClearColor(0x000000, 0);
      renderer.clear(true, true, true);
      renderer.render(scene, camera);
      renderer.readRenderTargetPixels(renderTarget, 0, 0, width, height, pixels);

      let pixelCount = 0;
      for (let index = 3; index < pixels.length; index += 4) {
        if (pixels[index] > 0) pixelCount += 1;
      }
      canvas.dataset.modelBounds = JSON.stringify(bounds);
      canvas.dataset.modelMask = JSON.stringify({
        pixelCount,
        pixelRatio: pixelCount / (width * height),
        width,
        height,
      });
    } finally {
      for (const { child, visible } of siblings) child.visible = visible;
      scene.background = previousBackground;
      renderer.setRenderTarget(previousTarget);
      renderer.setClearColor(previousClearColor, previousClearAlpha);
    }
  };

  return {
    update,
    dispose() {
      if (disposed) return;
      disposed = true;
      clearDatasets();
      renderTarget.dispose();
      pixels = new Uint8Array(0);
    },
  };
}
