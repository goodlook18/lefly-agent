# Python SDK

English | [简体中文](zh-CN/sdk-python.md)

The async Python SDK controls any LeFly Device Protocol v1 endpoint. Changing from Simulator Lite to a conforming physical adapter changes the WebSocket URL, not the controller API.

## Install

```bash
python -m pip install packages/lefly-protocol packages/lefly-sdk-python
```

## Connect and control

```python
import asyncio

from lefly_sdk import DeviceClient, RemoteHardwareController


async def main():
    client = DeviceClient("ws://127.0.0.1:8766/ws/device/simulator")
    await client.start()
    try:
        await client.wait_until_connected(timeout=5)
        robot = RemoteHardwareController(client, device_id="lefly-sim-01")
        await robot.play_movement("nod")
        await robot.play_absolute_move({"base_yaw": -60}, duration_ms=700)
        await robot.set_light_color("green")
        await robot.set_light_brightness(0.6)
        await robot.get_state()
        await robot.enter_rest_state()
    finally:
        await client.close()


asyncio.run(main())
```

Controller methods return the correlated `command.accepted`; motion completion arrives later through `motion.finished`.

## Subscribe to events

```python
def on_motion(event):
    print(event.message_type, event.correlation_id, dict(event.payload))


unsubscribe = client.subscribe("motion.finished", on_motion)
# Later:
unsubscribe()
```

Subscribe to `device.state_changed` for complete authoritative state and capabilities. Do not assume every endpoint implements every command.

## Errors

- `DeviceDisconnectedError`: nothing was sent because no connection existed.
- `RequestTimeoutError`: no acknowledgement arrived before the deadline.
- `CommandOutcomeUnknownError`: transport was lost after send; reconcile state
  before retrying.
- `RemoteDeviceError`: the endpoint returned a structured `device.error`.

The client reconnects transport automatically. Consumers still wait for fresh complete state before re-enabling controls.
