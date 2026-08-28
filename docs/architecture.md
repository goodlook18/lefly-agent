# Architecture

English | [简体中文](zh-CN/architecture.md)

LeFly Agent separates user interaction, direct operation, device semantics, and
physical hardware through explicit process and protocol boundaries.

```text
Browser Console -- Console Control --> Simulator gateway --> Device endpoint
Browser Console -- Agent Control ----> Text Agent -- Python SDK --> Device endpoint

Device endpoint:
  - Simulator Lite in v0.1.0
  - separately distributed physical adapter in a later release
```

The browser is a client and visualizer. The Python Simulator owns simulated
state and command execution and serves the compiled browser assets.

## Processes

### Simulator and Console

`lefly-simulator` normally listens on `127.0.0.1:8766`.

| Endpoint | Responsibility |
| --- | --- |
| `/` | Packaged Web Console |
| `/health` | Service readiness |
| `/api/targets` | Available built-in and remote targets |
| `/ws/console` | Console Control, target selection, lease, and virtual sensors |
| `/ws/device/simulator` | Device Protocol endpoint for the virtual device |

One browser session owns the renewable control lease. Other sessions remain
read-only, but an explicit Console takeover transfers the lease and immediately
makes the previous page read-only. The gateway can expose the built-in Simulator
and one configured remote Device Protocol endpoint.

### Text Agent

`lefly-agent` normally listens on `127.0.0.1:8767`.

| Endpoint | Responsibility |
| --- | --- |
| `/health` | Agent and device-connection readiness |
| `/ws/agent` | Text requests, streaming response, tool progress, and errors |

Strict unambiguous commands use a deterministic fast path. Other text uses an
optional LiveKit model session when configured. Robot tools always call the
public Python SDK; they never import a motor or hardware driver.

## Three distinct protocols

- **LeFly Device Protocol** defines commands, events, complete state,
  capabilities, correlation, and structured errors at the device boundary.
- **Console Control** connects the browser to the local target gateway for
  direct controls, telemetry, target selection, leases, and Simulator-only
  sensor injection.
- **Agent Control** connects the browser to the Text Agent for text submissions,
  streaming responses, tool progress, history, and Agent errors.

All three currently use JSON over WebSocket, but their messages and ownership
are intentionally different.

## Safety and state authority

The device endpoint validates limits, orders work, owns the motion queue, and
publishes authoritative state. Clients do not invent successful state before
an acknowledgement or event confirms it. The head RGB matrix is user
controllable; the base status strip is robot-owned telemetry and cannot be
overridden by user or model commands.

Physical motors, GPIO, RGB, serial, I2C, Camera, touch, and audio integrations
belong in a separately distributed adapter process whose dependencies and
licenses are reviewed independently from the clean software core.
