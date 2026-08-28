# Python SDK

[English](../sdk-python.md) | 简体中文

异步 Python SDK 可以控制任意 LeFly Device Protocol v1 端点。从 Simulator Lite 切换到兼容的实体适配器时，只需更换 WebSocket URL，控制器 API 保持不变。

## 安装

```bash
python -m pip install packages/lefly-protocol packages/lefly-sdk-python
```

## 连接与控制

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

控制器方法返回相关联的 `command.accepted`；动作完成事件随后通过 `motion.finished` 到达。

## 订阅事件

```python
def on_motion(event):
    print(event.message_type, event.correlation_id, dict(event.payload))


unsubscribe = client.subscribe("motion.finished", on_motion)
# Later:
unsubscribe()
```

订阅 `device.state_changed` 可以获得完整的权威状态和能力。不要假设每个设备端点都实现了所有命令。

## 错误

- `DeviceDisconnectedError`：连接不存在，因此没有发送任何内容。
- `RequestTimeoutError`：在截止时间前没有收到确认。
- `CommandOutcomeUnknownError`：发送后传输中断；重试前需要先核对状态。
- `RemoteDeviceError`：设备端点返回了结构化 `device.error`。

客户端会自动重连传输层。使用方仍需等待新的完整状态，再重新启用控制功能。
