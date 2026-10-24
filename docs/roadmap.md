# Roadmap

English | [简体中文](zh-CN/roadmap.md)

This roadmap communicates the direction of LeFly Agent after the `v0.1.2` Software Alpha. Priorities may change as the architecture, safety requirements, and community feedback develop.

## Available now

The current source Alpha provides:

- LeFly Device Protocol v1 and an asynchronous Python SDK;
- Simulator Lite and the packaged three-dimensional Web Console;
- built-in five-joint motion presets, head-light control, lifecycle status,
  virtual sensors, and diagnostics;
- deterministic text commands and optional LiveKit-backed LLM tools; and
- reproducible source export, clean-install, integration, browser, visual, and
  license-boundary checks.

See [Compatibility](compatibility.md) for the exact supported surface and known limits of `v0.1.2`.

## Next

### Portable action pipeline

Replace isolated built-in presets with a portable, hardware-independent action
pipeline. The intended outcome includes:

- a versioned action format with normalized five-joint motion timelines;
- CSV import and validation for user-authored actions;
- deterministic playback in Simulator Lite;
- the same named-action entry point for Console, SDK, and Agent clients; and
- a clear execution boundary for future physical-device adapters.

The action pipeline will be presented as a supported public feature once its format, playback behavior, and safety limits have been accepted together.

### Software Alpha stabilization

- improve installation, diagnostics, and failure guidance from community use;
- keep Protocol v1 changes additive and capability-gated;
- expand compatibility evidence without overstating unsupported platforms; and
- provide English-first documentation with maintained Simplified Chinese
  translations for the primary user workflows.

## Later

### Physical LeFly adapter and developer kit

- provide a separately distributed Device Protocol adapter for supported LeFly
  hardware; hardware drivers will remain outside the clean software core;
- run the same protocol conformance behavior against Simulator and hardware;
- publish versioned compatibility, calibration, safety, assembly, and recovery
  information before claiming a supported kit; and
- keep hardware sources, third-party dependencies, and licenses independently
  auditable.

The current [Hardware preview](hardware-preview.md) is exploratory.

### Voice and near-field interaction

- add a LiveKit voice path using the existing Agent and SDK boundary;
- integrate microphone input, ASR, TTS, interruption, and reconnect behavior;
- support configurable touch, gesture, and face-event responses; and
- validate latency, motor-noise handling, false recognition, and lifecycle
  status on supported hardware.

### Education labs and course materials

- provide simulator-first labs for Device Protocol, SDK, Text Agent, motion,
  lighting, and virtual sensors;
- add project-based extension exercises for custom behaviors and Agent tools;
- prepare teacher guidance, student worksheets, troubleshooting, and example
  projects; and
- extend the learning path to supported physical hardware once the adapter,
  kit, and safety boundaries are stable.

## Exploring

- **Visual interaction:** camera-based perception of people, gestures, objects,
  and nearby regions, coordinated with gaze, motion, and expressive eye displays;
- **Motion intelligence:** assisted choreography, motion adaptation, and later
  learned motion generation beyond fixed presets;
- **Music-driven behavior:** offline audio analysis and synchronized dance
  timelines before considering real-time generation; and
- **Interaction data:** consent-aware collection and evaluation loops for
  improving near-field interaction without making private data a requirement.
