# 兼容性

[English](../compatibility.md) | 简体中文

本表描述 `v0.1.1` 源码 Alpha。`Tested` 表示发布门禁实际运行了该配置；`Supported` 表示它属于预期的公开契约。本版本不对其他配置作出兼容性声明。

## 发布组件

| 组件 | 版本 | 状态 |
| --- | --- | --- |
| Device Protocol | `1` | 已冻结的 v1 契约范围 |
| `lefly-protocol` | `0.1.1` | 以源码形式包含 |
| `lefly-sdk-python` | `0.1.1` | 以源码形式包含 |
| `lefly-simulator` | `0.1.1` | 以源码形式包含 |
| `@lefly/console-web` | `0.1.1` | 包含源码和内置构建产物 |
| `lefly-agent` | `0.1.1` | 以源码形式包含 |

本版本没有向 PyPI 或 npm 发布任何包。

## 计划能力

| 能力 | 状态 | 当前边界 |
| --- | --- | --- |
| 可移植动作资产和 CSV 播放 | 已计划 | 目前只有 Simulator 内置预设动作；`v0.1.1` 不包含该能力 |
| 实体设备适配器 | 已计划 | 本版本没有受支持的实体设备端点 |
| 语音与近场交互 | 已计划 | 目前只有文本交互和虚拟传感器注入 |

视觉交互、运动智能和音乐律动等探索方向记录在[路线图](roadmap.md)中，不构成兼容性或交付承诺。

## 运行环境矩阵

| 范围 | 已测试 | 支持范围或已知限制 |
| --- | --- | --- |
| Python clean core | CI 中的 CPython 3.12 | Protocol、SDK 和 Simulator 的包元数据支持 Python 3.9+；v0.1.1 发布支持基线为 Python 3.12 |
| 带 LiveKit LLM 可选依赖的 Text Agent | CPython 3.12 | 仅桌面文本；不声明支持 Room、ASR、TTS 或 Pi4 语音 |
| Node.js 贡献者构建 | 22.12 | 仅 Console 开发、测试和重新构建时需要 |
| 浏览器 | Playwright Chromium 的桌面、平板和移动端视口 | 需要 WebGL；不声明支持 Safari 和 Firefox |
| 操作系统 | Linux CI 和负责人验证的 macOS 开发流程 | 不声明支持 Windows 和 Raspberry Pi 运行环境 |
| 网络 | 本机回环地址开发服务 | 不提供生产级认证、授权、TLS 或公网暴露能力 |
| 实体硬件 | 无 | 后续由独立发布的适配器提供 |

不同厂商的 LLM、天气和搜索集成都是可选能力。它们是否可用，不影响无需凭据的离线演示。
