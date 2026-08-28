# LeFly Agent

[English](README.md) | 简体中文

**让 AI Agent 拥有一个桌面身体。**

LeFly Agent 是一个面向 AI 教育、创客和实体 Agent 原型开发的开源桌面具身智能体平台。`v0.1.0` 源码 Alpha 无需实体硬件即可运行：Simulator Lite 提供动作、灯光、传感器和设备状态，浏览器 Console 与 Text Agent 提供两条控制链路。

![LeFly Agent Console](docs/assets/console-overview-v0.1.0.png)

## 可以用它做什么

- 在浏览器中控制一个五关节虚拟桌面机器人。
- 使用确定性文本命令或可选 LLM 触发动作与灯头灯光。
- 注入触摸、手势和人脸事件，验证近场交互逻辑。
- 使用 Python SDK 开发兼容 LeFly Device Protocol 的客户端和设备端点。

## 适合谁

- 希望讲授或学习 AI Agent、机器人和具身交互的教师与学生；
- 正在验证实体行为的 Agent 与 AI 硬件开发者；
- 开发可扩展示例的创客、极客和桌面机器人研究者。

## v0.1.0 包含内容

- 带版本的 LeFly Device Protocol v1 合约与示例。
- 面向 Simulator 和未来兼容适配器的异步 Python SDK。
- Simulator Lite 与内置 Web Console，包括五关节、预设动作、灯光、状态显示、虚拟传感器和诊断能力。
- 确定性文本控制和可选的 LiveKit LLM 工具调用。
- 协议、集成、浏览器、视觉与发布自动化检查。

本 Alpha 不包含语音、实体适配器、硬件设计文件和教育课程。它是面向开发与教育的软件，不是完成态的消费级陪伴机器人，也不是已经提供支持的实体硬件套件。[LeFly 实验性硬件套件预览与非约束性意向调查](docs/zh-CN/hardware-preview.md)展示了正在探索的方向。

## 快速开始

在仓库根目录使用 Python 3.12：

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  packages/lefly-protocol \
  packages/lefly-sdk-python \
  packages/lefly-simulator \
  packages/lefly-agent
python -m lefly_simulator --host 127.0.0.1 --port 8766
```

打开 `http://127.0.0.1:8766/`。编译后的 Console 已经包含在 Simulator 中；只有重新构建或测试前端时才需要 Node.js。

Text Agent 的配置和故障排查见[完整快速开始](docs/zh-CN/quickstart.md)。

## 架构

```text
浏览器 Console -- Console Control --> Simulator / 设备端点
浏览器 Console -- Agent Control ----> Text Agent -- Python SDK --> 设备端点
```

浏览器是客户端，不是被模拟的设备。直接控制命令不经过 LLM；文本输入由 Agent 处理，Agent 的机器人工具调用与其他客户端相同的 SDK。实体硬件应位于独立发布的 Device Protocol 适配器之后。

边界说明见[系统架构](docs/zh-CN/architecture.md)和 [Device Protocol](docs/zh-CN/protocol.md)。

## 路线图

当前 Simulator 使用内置预设动作。后续计划包括可移植动作格式、CSV 时间线导入，以及 Simulator、Agent 与未来实体设备适配器之间的统一播放链路。语音、实体设备和视觉交互会作为独立能力分别评审。

当前优先级和探索方向见公开[路线图](docs/zh-CN/roadmap.md)。

## 文档

- [文档索引](docs/zh-CN/README.md)
- [兼容性](docs/zh-CN/compatibility.md)
- [Python SDK](docs/zh-CN/sdk-python.md)
- [Simulator 与 Web Console](docs/zh-CN/simulator.md)
- [Text Agent](docs/zh-CN/text-agent.md)
- [路线图](docs/zh-CN/roadmap.md)
- [硬件预览](docs/zh-CN/hardware-preview.md)

## 安全

当前服务是用于本机开发的回环地址服务，不提供生产环境所需的身份认证、授权或 TLS。不要将它们暴露到不可信网络。详见英文权威版本 [SECURITY.md](SECURITY.md)。

## 许可证

LeFly Agent 使用 [Apache License 2.0](LICENSE) 发布。法律文本以英文原文为准，另见 [NOTICE](NOTICE) 和[第三方声明](docs/third-party-notices.md)。
