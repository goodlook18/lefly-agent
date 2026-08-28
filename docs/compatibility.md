# Compatibility

English | [简体中文](zh-CN/compatibility.md)

This matrix describes the `v0.1.0` source Alpha. `Tested` means the release gate runs that configuration. `Supported` means it is an intended public contract. Anything else is not claimed by this release.

## Release components

| Component            | Version | Status                                |
| -------------------- | ------- | ------------------------------------- |
| Device Protocol      | `1`     | Frozen v1 contract surface            |
| `lefly-protocol`     | `0.1.0` | Included as source                    |
| `lefly-sdk-python`   | `0.1.0` | Included as source                    |
| `lefly-simulator`    | `0.1.0` | Included as source                    |
| `@lefly/console-web` | `0.1.0` | Included as source and packaged build |
| `lefly-agent`        | `0.1.0` | Included as source                    |

No package is published to PyPI or npm for this release.

## Planned capabilities

| Capability | Status | Current boundary |
| --- | --- | --- |
| Portable action assets and CSV playback | Planned | Built-in Simulator presets only; not included in `v0.1.0` |
| Physical-device adapter | Planned | No supported physical endpoint in this release |
| Voice and near-field interaction | Planned | Text interaction and virtual sensor injection only |

Exploration areas, including visual interaction, motion intelligence, and music-driven behavior, are described in the [Roadmap](roadmap.md) and are not compatibility or delivery commitments.

## Runtime matrix

| Area | Tested | Supported or known limit |
| --- | --- | --- |
| Python clean core | CPython 3.9 and 3.12 in CI | Package metadata allows `>=3.9`; release quickstart uses 3.12 |
| Text Agent with LiveKit LLM extra | CPython 3.12 | Desktop text only; no Room, ASR, TTS, or Pi4 voice claim |
| Node.js contributor build | 22.12 | Needed for Console development, tests, and rebuilds only |
| Browser | Playwright Chromium desktop, tablet, and mobile viewports | WebGL required; Safari and Firefox are not claimed |
| Operating system | Linux CI and owner-tested macOS development workflow | Windows and Raspberry Pi runtime support are not claimed |
| Network | Loopback development services | No production authentication, authorization, TLS, or public-network claim |
| Physical hardware | None | A separately distributed adapter is future work |

Provider-specific LLM, weather, and search integrations are optional. Their availability does not affect the credential-free Offline Demo.
