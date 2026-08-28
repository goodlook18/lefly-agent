# Text Agent

[English](../text-agent.md) | 简体中文

Text Agent 将文本交互连接到其他客户端同样使用的公开 SDK。一条标准启动命令同时支持无需凭据的确定性链路和可选 LiveKit LLM 链路。`v0.1.0` 中两条链路都不使用 LiveKit Room、麦克风、扬声器、ASR、TTS、VAD、Camera 或实体驱动。

## 标准启动方式

首次运行时创建一份不纳入 Git 的本地基础配置，启动 Simulator，然后运行 Agent：

```bash
cp packages/lefly-agent/agent.example.toml packages/lefly-agent/agent.toml
python -m lefly_agent \
  --config packages/lefly-agent/agent.toml \
  --model-profile deepseek
```

`点头`、`摇头`、`向左看`、`向右看`、`变黄灯`、`关灯`、`休息` 和 `醒来` 等严格且无歧义的短语走确定性快速链路。没有配置模型时，问题、否定、假设、多意图和一般对话会收到安全的离线响应。

## 可选 LLM 演示

安装单独审计的可选依赖：

```bash
python -m pip install 'packages/lefly-agent[llm]'
export LEFLY_LLM_API_KEY='your-provider-key'
```

使用上面的标准命令重新启动。可用 profile 包括 `openai`、`qwen`、`deepseek`、`huawei-maas` 和 `openai-compatible`。

基础 TOML 保留设备、服务、城市、搜索、队列、触摸和工具循环设置。profile 只覆盖模型厂商、模型 ID 和可选端点。密钥始终由环境变量提供。

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

在标准启动命令中增加 `--debug`，会在 `logs/` 下创建带时间戳的日志。日志包括请求 ID、处理阶段、耗时、模型厂商、状态码和错误类型，但不得包含凭据、完整对话或模型厂商响应正文。

## 安全

Agent Control 仅用于本机回环地址开发。不要把 8767 端口暴露到不可信网络。模型文本和工具数据按照所配置厂商的政策处理。
