# lefly-sdk-python

Asynchronous Python client and high-level controller for LeFly Device Protocol
endpoints.

The repository-level [LeFly Python SDK guide](../../docs/sdk-python.md) is the
authoritative lifecycle, command, subscription, and error-handling reference.

## Install from the workspace

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e packages/lefly-protocol -e packages/lefly-sdk-python
```

## Connect and control

```python
import asyncio

from lefly_sdk import DeviceClient, RemoteHardwareController


async def main():
    client = DeviceClient("ws://127.0.0.1:8765/device")
    await client.start()
    try:
        await client.wait_until_connected(timeout=5)
        hardware = RemoteHardwareController(client, device_id="lefly-sim-01")
        await hardware.set_light_color("yellow")
        await hardware.play_absolute_move({"base_yaw": 60}, duration_ms=700)
    finally:
        await client.close()


asyncio.run(main())
```

The controller always emits canonical Protocol v1 commands: UUID message IDs,
millisecond UTC timestamps, an explicit target `device_id`, uppercase
`#RRGGBB` colors, and numeric joint degrees.

`request()` completes when the endpoint emits a correlated
`command.accepted`. This confirms that the device applied or queued the
command; it does not mean a queued motion has completed. Subscribe to
`motion.started`, `motion.progress`, and `motion.finished` for the rest of the
motion lifecycle.

The client supports typed and wildcard event subscriptions, response timeouts,
structured remote errors, callback isolation, and automatic reconnection. If
the connection is lost after a command is sent but before acknowledgement, the
client raises `CommandOutcomeUnknownError`; callers must not assume either
success or failure.
