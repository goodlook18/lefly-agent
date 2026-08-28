# Changelog

All notable changes to LeFly Agent are recorded in this file.

## Unreleased

No changes have been recorded after the corrective source Alpha.

## 0.1.1 - 2026-08-29

Corrective source-only software Alpha.

### Fixed

- Run the frozen source-inventory gate before package installation can create
  local `build/` and `*.egg-info/` artifacts in GitHub Actions.

### Changed

- Align all public Python packages, the Console, compatibility documentation,
  and release inventory on version `0.1.1`.
- Preserve the immutable `v0.1.0` tag as release history without promoting it
  to a GitHub Release.

## 0.1.0 - 2026-08-28

First source-only software Alpha.

### Included

- LeFly Device Protocol v1 contracts, fixtures, and validation.
- Async Python SDK and reconnecting WebSocket transport.
- Hardware-free Simulator Lite and packaged unified Web Console.
- Five-joint motion, preset actions, head-light control, robot-owned status
  rendering, virtual sensors, diagnostics, and device lifecycle behavior.
- Credential-free Offline Text Agent and optional LiveKit LLM tools.
- Clean-core, dependency, integration, browser, visual, and clean-install checks.

### Known limits

- Voice, ASR, TTS, Camera processing, and physical-device adapters are not included.
- Portable action assets, CSV import, and runtime timeline playback are not included.
- BOM, CAD, STL/3MF, schematics, Gerbers, and assembly guides are not included.
- No Python or npm package is published to a package registry in this release.
- Local Simulator, Console, and Agent services do not provide production
  authentication, authorization, or TLS.
