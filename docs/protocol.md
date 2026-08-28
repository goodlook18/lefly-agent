# LeFly Device Protocol v1

English | [简体中文](zh-CN/protocol.md)

LeFly Device Protocol is the versioned boundary shared by the Python SDK, Simulator, Agent tools, Console gateway, and future physical adapters. Canonical JSON Schemas and fixtures live under `contracts/`.

## Envelope

Every command and event contains:

```json
{
  "version": "1",
  "id": "20000000-0000-4000-8000-000000000001",
  "type": "device.get_state",
  "timestamp": "2026-08-23T08:00:00.000Z",
  "device_id": "lefly-sim-01",
  "payload": {}
}
```

Commands use UUID identifiers. Events use their own UUID and carry a nullable
`correlation_id` when they belong to a command lifecycle.

## Commands

| Type | Purpose |
| --- | --- |
| `device.get_state` | Request a complete state snapshot |
| `device.rest` | Safely preempt motion and enter the rest pose |
| `motion.play` | Play a named preset action |
| `motion.relative_move` | Move joints relative to current positions |
| `motion.absolute_move` | Move joints to absolute positions |
| `light.solid` | Set the user-controlled head matrix color |
| `light.brightness` | Set head-matrix brightness |
| `light.paint` | Paint an explicit head-matrix frame |
| `status.set` | System-only robot status transition |

User interfaces enable only commands advertised by capabilities with
`scope: "control"`. `status.set` is system-owned and must not appear as a user
light control.

## Events

| Type | Purpose |
| --- | --- |
| `command.accepted` | Command was applied or queued |
| `motion.started` | Accepted motion began |
| `motion.progress` | Optional progress and joint telemetry |
| `motion.finished` | Single terminal result: completed, cancelled, or failed |
| `device.state_changed` | Complete authoritative state snapshot |
| `device.error` | Structured rejection or spontaneous endpoint error |
| `sensor.touch` | Raw touch position and pressed state |
| `sensor.vision.gesture` | Raw gesture ID, optional label, and confidence |
| `sensor.vision.face` | Raw face ID, optional label, and confidence |

Vision IDs have no permanent protocol meaning. Product configuration owns the
mapping from raw IDs to behavior.

## Motion lifecycle

```text
command.accepted -> motion.started -> motion.progress x 0..N -> motion.finished
```

`command.accepted` confirms validation and acceptance, not completion. An
accepted motion receives exactly one correlated `motion.finished`. A queued
motion cancelled by `device.rest` or orderly shutdown may finish without ever
starting.

## State

Every `device.state_changed` payload is a complete replacement snapshot with a
monotonic `revision`. A revision gap makes cached state stale until a fresh
complete snapshot is received.

The seven semantic robot modes are `starting`, `resting`, `active`, `listening`,
`thinking`, `speaking`, and `error`. The base status strip is read-only telemetry.
Its effects are `fade`, `breath`, `solid`, `marquee`, `level_sweep`, and `blink`.

`device.rest`, internal rest motion, `status.mode=resting`,
`motion.state=idle`, and `motion.play {name: "idle"}` describe different layers
and are not interchangeable.

## Compatibility

Unknown syntactically valid v1 events are retained for diagnostics but do not mutate state. Unknown command types and malformed known messages are rejected with a structured `device.error`. Additive future commands are capability-gated.
