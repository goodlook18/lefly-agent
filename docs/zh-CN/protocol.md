# LeFly Device Protocol v1

[English](../protocol.md) | 简体中文

LeFly Device Protocol 是 Python SDK、Simulator、Agent 工具、Console 网关和未来实体适配器共享的版本化边界。规范 JSON Schema 和示例位于 `contracts/`。

## 消息封装

每条命令和事件都包含：

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

命令使用 UUID 标识。事件使用自己的 UUID；当事件属于某条命令的生命周期时，通过可为空的 `correlation_id` 与该命令关联。

## 命令

| 类型 | 用途 |
| --- | --- |
| `device.get_state` | 请求完整状态快照 |
| `device.rest` | 安全抢占动作并进入休息姿态 |
| `motion.play` | 播放具名预设动作 |
| `motion.relative_move` | 相对当前位置移动关节 |
| `motion.absolute_move` | 将关节移动到绝对位置 |
| `light.solid` | 设置用户可控灯头矩阵颜色 |
| `light.brightness` | 设置灯头矩阵亮度 |
| `light.paint` | 绘制明确的灯头矩阵帧 |
| `status.set` | 仅供系统使用的机器人状态切换 |

用户界面只能启用能力声明中带有 `scope: "control"` 的命令。`status.set` 由系统持有，不能作为用户灯光控制项出现。

## 事件

| 类型 | 用途 |
| --- | --- |
| `command.accepted` | 命令已应用或进入队列 |
| `motion.started` | 已接受的动作开始执行 |
| `motion.progress` | 可选的进度和关节遥测 |
| `motion.finished` | 唯一终态：完成、取消或失败 |
| `device.state_changed` | 完整的权威状态快照 |
| `device.error` | 结构化拒绝或设备端点自发错误 |
| `sensor.touch` | 原始触摸位置和按下状态 |
| `sensor.vision.gesture` | 原始手势 ID、可选标签和置信度 |
| `sensor.vision.face` | 原始人脸 ID、可选标签和置信度 |

视觉 ID 在协议中没有永久语义。产品配置负责把原始 ID 映射为具体行为。

## 动作生命周期

```text
command.accepted -> motion.started -> motion.progress x 0..N -> motion.finished
```

`command.accepted` 只确认命令通过校验并被接受，不代表执行完成。每个已接受动作必须收到且只收到一个相关联的 `motion.finished`。被 `device.rest` 或有序关机取消的排队动作，可能从未开始执行就直接结束。

## 状态

每个 `device.state_changed` 的 payload 都是完整替换快照，并带有单调递增的 `revision`。出现 revision 缺口后，缓存状态视为陈旧，直到收到新的完整快照。

七种机器人语义状态分别是 `starting`、`resting`、`active`、`listening`、`thinking`、`speaking` 和 `error`。底座状态灯是只读遥测，其效果包括 `fade`、`breath`、`solid`、`marquee`、`level_sweep` 和 `blink`。

`device.rest`、内部休息动作、`status.mode=resting`、`motion.state=idle` 和 `motion.play {name: "idle"}` 分属不同层级，不能互相替代。

## 兼容性

语法有效但未知的 v1 事件会保留用于诊断，但不会修改状态。未知命令类型和格式错误的已知消息会通过结构化 `device.error` 拒绝。未来新增命令必须通过能力声明进行门控。
