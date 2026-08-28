# 快速开始

[English](../quickstart.md) | 简体中文

下面的流程全部运行在一台开发电脑上，不需要机器人或 LiveKit Room。云端模型、天气和搜索凭据均为可选配置。

## 环境要求

- Python 3.12
- 支持 WebGL 的浏览器

只有重新构建或测试 Web Console 时才需要 Node.js。Simulator 包已经包含本快速开始使用的编译版 Console。

## 安装

在仓库根目录使用当前已激活的 Python 环境：

```bash
python --version
python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  packages/lefly-protocol \
  packages/lefly-sdk-python \
  packages/lefly-simulator \
  packages/lefly-agent
```

以上命令足以运行无需凭据的确定性 Agent。如果要配合 API Key 使用
DeepSeek、Qwen、OpenAI、Huawei MaaS 或其他模型配置，请额外安装 LLM
可选依赖：

```bash
python -m pip install 'packages/lefly-agent[llm]'
```

启动 Agent 前，通过环境变量设置模型 API Key：

```bash
export LEFLY_LLM_API_KEY='your-provider-key'
```

请只在环境变量或本地密钥管理工具中保存 Key，不要写入 `agent.toml`，也不要
提交到 Git。

首次运行时，复制一份不纳入 Git 的本地 Agent 配置：

```bash
cp packages/lefly-agent/agent.example.toml \
  packages/lefly-agent/agent.toml
```

### 可选天气与搜索

天气和网络搜索是由模型调用的工具，因此需要先按上面的步骤启用 LLM 链路，
再为需要使用的服务设置凭据：

访问[和风天气开发者服务](https://dev.qweather.com/)注册账号并获取 API
凭据。

```bash
export QWEATHER_API_KEY='your-qweather-key'
export TAVILY_API_KEY='your-tavily-key'
```

QWeather 还需要开发者控制台中显示的账号专属 API Host。将它写入本地
`packages/lefly-agent/agent.toml`：

```toml
[search]
qweather_api_host = "https://your-account-host.qweatherapi.com"
```

如需修改天气问题未指定城市时使用的默认城市，请在同一文件中设置
`[agent].default_city`。两个服务均可单独省略，只会禁用对应工具。两个 Key
都不要写入 TOML 或提交到 Git。

Python 环境可以由 Conda、venv 或其他工具管理。LeFly 不要求使用特定的环境名称。

## 启动 Simulator Lite

```bash
python -m lefly_simulator --host 127.0.0.1 --port 8766
```

打开 `http://127.0.0.1:8766/`。可以尝试预设动作、关节滑块、灯头颜色和虚拟触摸事件。底座状态灯只随机器人生命周期状态变化。

## 启动 Text Agent

保持 Simulator 运行，在第二个终端执行：

```bash
python -m lefly_agent \
  --config packages/lefly-agent/agent.toml \
  --model-profile deepseek
```

这是标准 Agent 启动命令。未设置 `LEFLY_LLM_API_KEY` 时，Agent 运行不需要凭据的确定性链路，也不需要安装 LiveKit；安装 LLM 可选依赖并设置密钥后，同一条命令会启用模型对话和工具调用。可以按需把 `deepseek` 替换为 `openai`、`qwen`、`huawei-maas` 或 `openai-compatible`。详见[可选 LLM 演示](text-agent.md)。

如果 Console 此前显示 Text Agent 不可用，请刷新页面。可以输入 `点头`、`向左看`、`变绿灯`、`关灯`、`休息` 或 `醒来` 等确定性命令。

模型对话、天气和搜索配置见 [Text Agent 指南](text-agent.md)。

## 停止服务

先在 Agent 终端按 `Ctrl-C`，再停止 Simulator。

## 故障排查

- **端口已占用：** 停止之前的进程，或者为服务及其对应 URL 选择其他端口。
- **Console 不可用：** 确认 `http://127.0.0.1:8766/health` 返回 JSON。
- **Text Agent 未连接：** 确认 `http://127.0.0.1:8767/health` 可访问，并确保先启动 Simulator。
- **LLM 不可用：** 安装 `packages/lefly-agent[llm]`，设置 `LEFLY_LLM_API_KEY`，并确认 `--model-profile` 选择正确。
- **天气不可用：** 设置 `QWEATHER_API_KEY`，并配置账号专属的
  `[search].qweather_api_host`。
- **搜索不可用：** 启动 Agent 前设置 `TAVILY_API_KEY`。
- **3D 视图空白：** 启用 WebGL 和浏览器硬件加速。
- **控件不可用：** 使用内置目标，等待完整状态同步，并在需要时点击“接管控制权”。

继续阅读 [Simulator 指南](simulator.md)、[Text Agent 指南](text-agent.md)或 [Python SDK 指南](sdk-python.md)。
