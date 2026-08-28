# 系统架构

[English](../architecture.md) | 简体中文

LeFly Agent 通过明确的进程与协议边界，分离用户交互、直接操作、设备语义和实体硬件。

```text
浏览器 Console -- Console Control --> Simulator 网关 --> 设备端点
浏览器 Console -- Agent Control ----> Text Agent -- Python SDK --> 设备端点

设备端点：
  - v0.1.0 使用 Simulator Lite
  - 后续版本使用独立发布的实体适配器
```

浏览器是客户端和可视化界面。Python Simulator 持有模拟状态、执行命令，并提供已经编译的浏览器资源。

## 进程

### Simulator 与 Console

`lefly-simulator` 默认监听 `127.0.0.1:8766`。

| 端点 | 职责 |
| --- | --- |
| `/` | 内置 Web Console |
| `/health` | 服务就绪状态 |
| `/api/targets` | 可用的内置目标和远程目标 |
| `/ws/console` | Console Control、目标选择、控制租约和虚拟传感器 |
| `/ws/device/simulator` | 虚拟设备的 Device Protocol 端点 |

同一时间只有一个浏览器会话持有可续期的控制租约。其他会话保持只读；用户可以在 Console 中明确接管控制权，租约转移后，原页面会立即变成只读。网关可以同时暴露内置 Simulator 和一个已配置的远程 Device Protocol 端点。

### Text Agent

`lefly-agent` 默认监听 `127.0.0.1:8767`。

| 端点 | 职责 |
| --- | --- |
| `/health` | Agent 和设备连接就绪状态 |
| `/ws/agent` | 文本请求、流式响应、工具进度和错误 |

严格且无歧义的命令走确定性快速链路；配置模型后，其他文本由可选的 LiveKit 模型会话处理。机器人工具始终调用公开 Python SDK，不导入电机或硬件驱动。

## 三种独立协议

- **LeFly Device Protocol**：定义设备边界上的命令、事件、完整状态、能力、关联关系和结构化错误。
- **Console Control**：连接浏览器与本地目标网关，用于直接控制、遥测、目标选择、租约和仅限 Simulator 的传感器注入。
- **Agent Control**：连接浏览器与 Text Agent，用于提交文本、流式响应、工具进度、历史记录和 Agent 错误。

三者目前都使用 WebSocket 传输 JSON，但消息结构和所有权有意保持独立。

## 安全与状态权威

设备端点负责校验限制、安排任务顺序、维护动作队列并发布权威状态。在收到确认或事件之前，客户端不能自行假定命令已成功。灯头 RGB 矩阵可由用户控制；底座状态灯是机器人持有的遥测信息，用户命令和模型命令都不能覆盖它。

实体电机、GPIO、RGB、串口、I2C、Camera、触摸和音频集成都应放在独立发布的适配器进程中，其依赖和许可证与 clean core 分别审查。
