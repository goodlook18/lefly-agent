import { act, cleanup, render, screen } from "@testing-library/react";
import { Color, Group, Mesh, MeshStandardMaterial, PerspectiveCamera, Scene } from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = {
  controls: [] as Array<Record<string, unknown>>,
  models: [] as Array<Record<string, unknown>>,
  observers: [] as Array<Record<string, unknown>>,
  renderers: [] as Array<Record<string, unknown>>,
  renderTargets: [] as Array<Record<string, unknown>>,
  rafCallbacks: [] as Array<FrameRequestCallback | undefined>,
  controlsShouldThrow: false,
  modelShouldThrow: false,
  observerShouldThrow: false,
  rafShouldThrow: false,
  rendererShouldThrow: false,
};

vi.doMock("three", async (importOriginal) => {
  const actual = await importOriginal<typeof import("three")>();

  class FakeWebGLRenderer {
    domElement = document.createElement("canvas");
    removeCanvas = vi.spyOn(this.domElement, "remove");
    dispose = vi.fn();
    render = vi.fn();
    setAnimationLoop = vi.fn();
    setPixelRatio = vi.fn();
    setSize = vi.fn();
    shadowMap = { enabled: false, type: 0 };
    outputColorSpace = "";
    toneMapping = 0;
    toneMappingExposure = 0;
    currentTarget: unknown = null;
    options: Record<string, unknown>;
    clear = vi.fn();
    getClearAlpha = vi.fn(() => 1);
    getClearColor = vi.fn((color: Color) => color.set(0xe7eaeb));
    getRenderTarget = vi.fn(() => this.currentTarget);
    readRenderTargetPixels = vi.fn((
      _target: unknown,
      _x: number,
      _y: number,
      _width: number,
      _height: number,
      pixels: Uint8Array,
    ) => {
      for (let index = 3; index < pixels.length; index += 4) pixels[index] = 255;
    });
    setClearColor = vi.fn();
    setRenderTarget = vi.fn((target: unknown) => { this.currentTarget = target; });

    constructor(options: Record<string, unknown>) {
      if (mocks.rendererShouldThrow) throw new Error("WebGL unavailable");
      this.options = options;
      mocks.renderers.push(this as unknown as Record<string, unknown>);
    }
  }

  class FakeWebGLRenderTarget {
    dispose = vi.fn();
    setSize = vi.fn();

    constructor() {
      mocks.renderTargets.push(this as unknown as Record<string, unknown>);
    }
  }

  return { ...actual, WebGLRenderer: FakeWebGLRenderer, WebGLRenderTarget: FakeWebGLRenderTarget };
});

vi.doMock("./createLeFlyModel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./createLeFlyModel")>();
  return {
    ...actual,
    createLeFlyModel: () => {
      if (mocks.modelShouldThrow) throw new Error("model construction failed");
      const model = actual.createLeFlyModel();
      model.dispose = vi.fn(model.dispose.bind(model));
      mocks.models.push(model as unknown as Record<string, unknown>);
      return model;
    },
  };
});

vi.doMock("three/addons/controls/OrbitControls.js", async () => {
  const { Vector3 } = await vi.importActual<typeof import("three")>("three");
  class FakeOrbitControls {
    dispose = vi.fn();
    update = vi.fn();
    addEventListener = vi.fn();
    removeEventListener = vi.fn();
    target = new Vector3();
    enableDamping = false;
    enablePan = true;
    minDistance = 0;
    maxDistance = 0;
    minPolarAngle = 0;
    maxPolarAngle = 0;

    constructor() {
      if (mocks.controlsShouldThrow) throw new Error("controls construction failed");
      mocks.controls.push(this as unknown as Record<string, unknown>);
    }
  }

  return { OrbitControls: FakeOrbitControls };
});

const { LeFlyScene } = await import("./LeFlyScene");

class FakeResizeObserver {
  disconnect = vi.fn();
  observe = vi.fn(() => {
    this.callback(
      [{ contentRect: { width: 640, height: 480 } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  });

  constructor(private readonly callback: ResizeObserverCallback) {
    if (mocks.observerShouldThrow) throw new Error("observer construction failed");
    mocks.observers.push(this as unknown as Record<string, unknown>);
  }
}

describe("LeFlyScene", () => {
  beforeEach(() => {
    mocks.controls.length = 0;
    mocks.models.length = 0;
    mocks.observers.length = 0;
    mocks.renderers.length = 0;
    mocks.renderTargets.length = 0;
    mocks.rafCallbacks.length = 0;
    mocks.controlsShouldThrow = false;
    mocks.modelShouldThrow = false;
    mocks.observerShouldThrow = false;
    mocks.rafShouldThrow = false;
    mocks.rendererShouldThrow = false;
    window.history.replaceState(null, "", "/");
    let nextFrame = 0;
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn((callback: FrameRequestCallback) => {
        if (mocks.rafShouldThrow) throw new Error("animation scheduling failed");
        nextFrame += 1;
        mocks.rafCallbacks[nextFrame] = callback;
        return nextFrame;
      }),
    );
    vi.stubGlobal(
      "cancelAnimationFrame",
      vi.fn((frame: number) => {
        delete mocks.rafCallbacks[frame];
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders responsively, applies props, and cleans up every browser resource", () => {
    const { container, rerender, unmount } = render(
      <LeFlyScene
        joints={{ base_yaw: 15, wrist_roll: -20 }}
        headLight={{ color: "#ff9257", brightness: 0.5 }}
        statusStrip={{ color: "#31a8ff", effect: "breath" }}
      />,
    );

    const renderer = mocks.renderers[0] as {
      domElement: HTMLCanvasElement;
      dispose: ReturnType<typeof vi.fn>;
      options: Record<string, unknown>;
      removeCanvas: ReturnType<typeof vi.fn>;
      render: ReturnType<typeof vi.fn>;
      setSize: ReturnType<typeof vi.fn>;
    };
    const controls = mocks.controls[0] as { dispose: ReturnType<typeof vi.fn> };
    const observer = mocks.observers[0] as { disconnect: ReturnType<typeof vi.fn> };

    expect(container.querySelector("canvas")).toBe(renderer.domElement);
    expect(renderer.options.preserveDrawingBuffer).toBe(false);
    expect(mocks.renderTargets).toHaveLength(0);
    expect(renderer.setSize).toHaveBeenCalledWith(640, 480, false);
    expect(mocks.rafCallbacks.filter(Boolean)).toHaveLength(1);

    act(() => {
      mocks.rafCallbacks.find(Boolean)?.(500);
    });
    const scene = renderer.render.mock.calls[0][0] as Scene;
    const camera = renderer.render.mock.calls[0][1] as PerspectiveCamera;
    expect(renderer.domElement.dataset.modelBounds).toBeUndefined();
    expect(renderer.domElement.dataset.modelMask).toBeUndefined();
    expect(camera.aspect).toBeCloseTo(4 / 3);
    expect((scene.getObjectByName("base_yaw") as Group).rotation.y).toBeCloseTo(Math.PI / 12);
    expect((scene.getObjectByName("wrist_roll") as Group).rotation.y).toBeCloseTo(-Math.PI / 9);
    expect(
      ((scene.getObjectByName("head-light-surface") as Mesh).material as MeshStandardMaterial).color,
    ).toEqual(new Color("#ff9257"));

    rerender(<LeFlyScene joints={{ elbow_pitch: 30 }} />);
    expect((scene.getObjectByName("elbow_pitch") as Group).rotation.x).toBeCloseTo(Math.PI / 6);

    unmount();
    expect(observer.disconnect).toHaveBeenCalledOnce();
    expect(controls.dispose).toHaveBeenCalledOnce();
    expect(renderer.dispose).toHaveBeenCalledOnce();
    expect(cancelAnimationFrame).toHaveBeenCalledOnce();
    expect(renderer.removeCanvas).toHaveBeenCalledOnce();
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("enables model diagnostics only by URL and disposes the mask target", () => {
    window.history.replaceState(null, "", "/?renderDiagnostics=1");
    const { container, unmount } = render(
      <LeFlyScene statusStrip={{ color: "#2f9d68", effect: "level_sweep" }} />,
    );
    const renderer = mocks.renderers[0] as {
      domElement: HTMLCanvasElement;
      options: Record<string, unknown>;
    };
    const controls = mocks.controls[0] as {
      addEventListener: ReturnType<typeof vi.fn>;
      removeEventListener: ReturnType<typeof vi.fn>;
    };

    act(() => {
      mocks.rafCallbacks.find(Boolean)?.(250);
    });

    expect(renderer.options.preserveDrawingBuffer).toBe(true);
    expect(mocks.renderTargets).toHaveLength(1);
    expect(controls.addEventListener).toHaveBeenCalledWith("change", expect.any(Function));
    expect(JSON.parse(renderer.domElement.dataset.modelBounds ?? "null")).toEqual(expect.objectContaining({
      left: expect.any(Number),
      top: expect.any(Number),
      right: expect.any(Number),
      bottom: expect.any(Number),
    }));
    expect(JSON.parse(renderer.domElement.dataset.modelMask ?? "null")).toEqual(expect.objectContaining({
      pixelCount: expect.any(Number),
      pixelRatio: expect.any(Number),
    }));
    expect(renderer.domElement.dataset.statusEffect).toBe("level_sweep");

    unmount();
    const target = mocks.renderTargets[0] as { dispose: ReturnType<typeof vi.fn> };
    expect(controls.removeEventListener).toHaveBeenCalledWith("change", expect.any(Function));
    expect(target.dispose).toHaveBeenCalledOnce();
    expect(renderer.domElement.dataset.modelBounds).toBeUndefined();
    expect(renderer.domElement.dataset.modelMask).toBeUndefined();
    expect(renderer.domElement.dataset.statusEffect).toBeUndefined();
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("updates its aria label without recreating the scene or losing model state", () => {
    const joints = { base_yaw: 24, wrist_roll: -12 };
    const headLight = { color: "#ff9257", brightness: 0.45 };
    const statusStrip = { color: "#31a8ff", effect: "breath" } as const;
    const { container, rerender, unmount } = render(
      <LeFlyScene
        aria-label="LeFly initial view"
        joints={joints}
        headLight={headLight}
        statusStrip={statusStrip}
      />,
    );

    const renderer = mocks.renderers[0] as {
      domElement: HTMLCanvasElement;
      dispose: ReturnType<typeof vi.fn>;
      render: ReturnType<typeof vi.fn>;
    };
    const controls = mocks.controls[0] as { dispose: ReturnType<typeof vi.fn> };
    const observer = mocks.observers[0] as { disconnect: ReturnType<typeof vi.fn> };
    const initialFrame = mocks.rafCallbacks.findIndex(Boolean);
    const callback = mocks.rafCallbacks[initialFrame];
    delete mocks.rafCallbacks[initialFrame];
    act(() => callback?.(250));

    const scene = renderer.render.mock.calls[0][0] as Scene;
    const baseYaw = scene.getObjectByName("base_yaw") as Group;
    const headLightPanel = scene.getObjectByName("head-light-surface") as Mesh;
    const headLightMaterial = headLightPanel.material as MeshStandardMaterial;
    const rafCallsBefore = vi.mocked(requestAnimationFrame).mock.calls.length;

    rerender(
      <LeFlyScene
        aria-label="LeFly updated view"
        joints={joints}
        headLight={headLight}
        statusStrip={statusStrip}
      />,
    );

    expect(mocks.renderers).toHaveLength(1);
    expect(mocks.controls).toHaveLength(1);
    expect(mocks.observers).toHaveLength(1);
    expect(renderer.dispose).not.toHaveBeenCalled();
    expect(controls.dispose).not.toHaveBeenCalled();
    expect(observer.disconnect).not.toHaveBeenCalled();
    expect(requestAnimationFrame).toHaveBeenCalledTimes(rafCallsBefore);
    expect(container.querySelector("canvas")).toBe(renderer.domElement);
    expect(renderer.domElement).toHaveAttribute("aria-label", "LeFly updated view");
    expect(scene.getObjectByName("base_yaw")).toBe(baseYaw);
    expect(baseYaw.rotation.y).toBeCloseTo((24 * Math.PI) / 180);
    expect(scene.getObjectByName("head-light-surface")).toBe(headLightPanel);
    expect(headLightMaterial.color).toEqual(new Color("#ff9257"));
    expect(headLightPanel.userData.brightness).toBe(0.45);

    unmount();
    expect(observer.disconnect).toHaveBeenCalledOnce();
    expect(controls.dispose).toHaveBeenCalledOnce();
    expect(renderer.dispose).toHaveBeenCalledOnce();
  });

  it.each([
    ["model", "modelShouldThrow", 0, 0, 0],
    ["controls", "controlsShouldThrow", 1, 0, 0],
    ["observer", "observerShouldThrow", 1, 1, 0],
    ["RAF", "rafShouldThrow", 1, 1, 1],
  ] as const)(
    "cleans up partial initialization and shows fallback when %s construction fails",
    (_stage, failureFlag, expectedModels, expectedControls, expectedObservers) => {
      mocks[failureFlag] = true;

      const { container } = render(<LeFlyScene />);

      const renderer = mocks.renderers[0] as {
        dispose: ReturnType<typeof vi.fn>;
        removeCanvas: ReturnType<typeof vi.fn>;
      };
      expect(screen.getByRole("status")).toHaveTextContent("3D preview is unavailable");
      expect(mocks.models).toHaveLength(expectedModels);
      expect(mocks.controls).toHaveLength(expectedControls);
      expect(mocks.observers).toHaveLength(expectedObservers);
      for (const model of mocks.models) {
        expect(model.dispose).toHaveBeenCalledOnce();
      }
      for (const controls of mocks.controls) {
        expect(controls.dispose).toHaveBeenCalledOnce();
      }
      for (const observer of mocks.observers) {
        expect(observer.disconnect).toHaveBeenCalledOnce();
      }
      expect(renderer.dispose).toHaveBeenCalledOnce();
      expect(renderer.removeCanvas).toHaveBeenCalledOnce();
      expect(cancelAnimationFrame).not.toHaveBeenCalled();
      expect(container.querySelector("canvas")).toBeNull();
    },
  );

  it("falls back to window resize events when ResizeObserver is unavailable", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    const addEventListener = vi.spyOn(window, "addEventListener");
    const removeEventListener = vi.spyOn(window, "removeEventListener");

    const { unmount } = render(<LeFlyScene />);
    const renderer = mocks.renderers[0] as { setSize: ReturnType<typeof vi.fn> };

    expect(screen.queryByRole("status")).toBeNull();
    expect(mocks.observers).toHaveLength(0);
    expect(renderer.setSize).toHaveBeenCalledWith(1, 1, false);
    expect(addEventListener).toHaveBeenCalledWith("resize", expect.any(Function));

    unmount();
    expect(removeEventListener).toHaveBeenCalledWith("resize", expect.any(Function));
  });

  it("shows a non-crashing fallback when WebGL creation fails", () => {
    mocks.rendererShouldThrow = true;

    render(<LeFlyScene />);

    expect(screen.getByRole("status")).toHaveTextContent("3D preview is unavailable");
    expect(mocks.observers).toHaveLength(0);
    expect(mocks.rafCallbacks.filter(Boolean)).toHaveLength(0);
  });
});
