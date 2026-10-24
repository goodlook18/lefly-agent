# Text Agent

[English](../text-agent.md) | 简体中文

Text Agent 将文本交互连接到其他客户端同样使用的公开 SDK。本地演示命令会同时启动它和 Simulator。默认运行无需凭据的确定性链路；显式选择模型 profile 后才使用可选 LiveKit LLM 链路。`v0.1.2` 中两条链路都不使用 LiveKit Room、麦克风、扬声器、ASR、TTS、VAD、Camera 或实体驱动。

## 标准启动方式

在仓库根目录按[快速开始](quickstart.md)安装四个包，然后启动两个服务：

```bash
python tools/run_local_demo.py
```

打开 `http://127.0.0.1:8766/`。页面直接控制由 Simulator 处理；Console
文本输入需要 Text Agent。即使终端环境里已有 `LEFLY_LLM_API_KEY`，默认
命令仍保持离线。

`点头`、`摇头`、`向左看`、`向右看`、`变黄灯`、`关灯`、`休息` 和 `醒来` 等严格且无歧义的短语走确定性快速链路。没有配置模型时，问题、否定、假设、多意图和一般对话会收到安全的离线响应。

## 可选 LLM 演示

快速开始中的单条安装命令已经包含经过审计的 `llm` 可选依赖。设置服务商
的 Key 并显式选择对应 profile：

```bash
export LEFLY_LLM_API_KEY='your-provider-key'
python tools/run_local_demo.py --model-profile deepseek
```

可用 profile 包括 `openai`、`qwen`、`deepseek`、`huawei-maas` 和 `openai-compatible`。只有 profile 而没有 Key 时，启动脚本会在启动服务前报错。请勿将密钥写入 TOML 或 Git。

在源码仓库中，每个 profile 自动读取对应的
`packages/lefly-agent/configs/agent.<profile>.toml`。修改其中的模型 ID 后重启
Agent 即可，不必为了模型配置添加 `--config`。仓库外的已安装 wheel 使用构建时
生成的配置快照。

如需自定义 Agent 行为，可将 `packages/lefly-agent/agent.example.toml` 复制为不纳入 Git 的 `packages/lefly-agent/agent.toml`。只要文件存在，启动脚本就会自动加载；从其他工作目录执行也一样。文件不存在时使用 Agent 内置默认值。只有改用其他基础 TOML 时才需显式传 `--config`；单独运行 `python -m lefly_agent` 时仍需显式指定该文件。基础 TOML 保留设备、服务、城市、搜索、队列、触摸和工具循环设置。profile 只覆盖模型厂商、模型 ID 和可选端点。密钥始终由环境变量提供。脚本固定使用本机 8766、8767 端口；其他端点请分别运行两个服务。

QWeather 和 Tavily 均为可选配置：

访问[和风天气开发者服务](https://dev.qweather.com/)注册账号并获取 API
凭据。

```bash
export QWEATHER_API_KEY='your-qweather-key'
export TAVILY_API_KEY='your-tavily-key'
```

QWeather 还需要开发者控制台中显示的账号专属 API Host：

```toml
[search]
qweather_api_host = "https://your-account-host.qweatherapi.com"
```

缺少密钥或 QWeather API Host 时，只禁用对应的信息工具。

## 路由与状态

Console 的直接控制永远不进入 Agent 或 LLM。文本首先经过严格的快速意图路由器，只有歧义请求和一般对话才交给模型。快速链路与模型选择的机器人工具共享同一个有界串行命令服务，并使用相同的 SDK 校验。

文本推理可以设置由机器人持有的 `thinking` 状态。文本输出不是音频 `speaking`；`listening` 和 `speaking` 属于后续语音版本。配置的触摸行为是确定性的，也不会进入模型对话历史。

## 调试日志

单独运行 Agent 并增加 `--debug`，会在 `logs/` 下创建带时间戳的日志。日志包括请求 ID、处理阶段、耗时、模型厂商、状态码和错误类型。

## 安全

Agent Control 仅用于本机回环地址开发。不要把 8767 端口暴露到不可信网络。模型文本和工具数据按照所配置厂商的政策处理。
