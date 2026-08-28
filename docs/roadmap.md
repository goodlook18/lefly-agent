# Roadmap

English | [简体中文](zh-CN/roadmap.md)

This roadmap communicates the direction of LeFly Agent after the `v0.1.1` Software Alpha. It describes intended outcomes rather than fixed release dates. Priorities may change as the architecture, safety requirements, and community feedback develop. An item listed here is not a delivery commitment.

## Available now

The current source Alpha provides:

- LeFly Device Protocol v1 and an asynchronous Python SDK;
- Simulator Lite and the packaged three-dimensional Web Console;
- built-in five-joint motion presets, head-light control, lifecycle status,
  virtual sensors, and diagnostics;
- deterministic text commands and optional LiveKit-backed LLM tools; and
- reproducible source export, clean-install, integration, browser, visual, and
  license-boundary checks.

See [Compatibility](compatibility.md) for the exact supported surface and known limits of `v0.1.1`.

## Next

### Portable action pipeline

Replace isolated built-in presets with a portable, hardware-independent action
pipeline. The intended outcome includes:

- a versioned action format with normalized five-joint motion timelines;
- CSV import and validation for user-authored actions;
- deterministic playback in Simulator Lite;
- the same named-action entry point for Console, SDK, and Agent clients; and
- a clear execution boundary for future physical-device adapters.

The format, playback behavior, and safety limits must be accepted together before the action pipeline is presented as a supported public feature.

### Software Alpha stabilization

- improve installation, diagnostics, and failure guidance from community use;
- keep Protocol v1 changes additive and capability-gated;
- expand compatibility evidence without overstating unsupported platforms; and
- provide English-first documentation with maintained Simplified Chinese
  translations for the primary user workflows.

## Later

### Physical LeFly adapter and developer kit

- provide a separately distributed Device Protocol adapter for supported LeFly
  hardware without importing hardware drivers into the clean software core;
- run the same protocol conformance behavior against Simulator and hardware;
- publish versioned compatibility, calibration, safety, assembly, and recovery
  information before claiming a supported kit; and
- keep hardware sources, third-party dependencies, and licenses independently
  auditable.

The current [Hardware preview](hardware-preview.md) is exploratory and does not announce availability or a delivery date.

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
- extend the learning path to supported physical hardware only after the
  adapter, kit, and safety boundaries are stable.

## Exploring

Exploration items require separate design approval and may not become committed
features:

- **Visual interaction:** camera-based perception of people, gestures, objects,
  and nearby regions, coordinated with gaze, motion, and expressive eye displays;
- **Motion intelligence:** assisted choreography, motion adaptation, and later
  learned motion generation beyond fixed presets;
- **Music-driven behavior:** offline audio analysis and synchronized dance
  timelines before considering real-time generation; and
- **Interaction data:** consent-aware collection and evaluation loops for
  improving near-field interaction without making private data a requirement.

## How priorities become work

Roadmap topics become scheduled only after their scope, architecture boundary, acceptance criteria, and release impact are reviewed. Once the public repository is available, concrete work should be linked to GitHub Issues or Milestones so contributors can distinguish active tasks from longer-term direction.
