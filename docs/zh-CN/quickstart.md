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
python -m pip install packages/lefly-protocol packages/lefly-sdk-python packages/lefly-simulator 'packages/lefly-agent[llm]'
```

这一条命令会安装 Simulator、Text Agent 及其 LLM 可选依赖。安装依赖
不会自动启用模型，也不需要 Key；默认启动保持离线。

## 一条命令启动两个服务

```bash
python tools/run_local_demo.py
```

脚本先启动 Simulator，确认健康后再启动 Text Agent。打开
`http://127.0.0.1:8766/`。页面直接控制使用 Simulator；文本输入需要
Text Agent。可以试试 `点头`、`向左看`、`变绿灯`、`关灯`、`休息` 或 `醒来`。
这些确定性指令无需联网模型。一般对话在没有显式选择模型时会收到离线回复。
底座状态灯只随机器人生命周期状态变化。

在这个终端按 `Ctrl-C` 即可停止两个服务。若启动失败，脚本会指出失败的
服务，并停止已启动的子进程。

## 可选模型对话

先在环境变量中设置所选服务商的 API Key，再指定对应的 profile：

```bash
export LEFLY_LLM_API_KEY='your-provider-key'
python tools/run_local_demo.py --model-profile deepseek
```

也可选择 `openai`、`qwen`、`huawei-maas` 或 `openai-compatible`。
在源码仓库中修改模型名称或端点时，编辑对应的
`packages/lefly-agent/configs/agent.<profile>.toml`。例如，选择
`--model-profile qwen` 会读取 `agent.qwen.toml`。修改后重启 Agent 才会生效。
在源码仓库外运行的已安装 wheel 使用构建时生成的配置快照。
即使环境中已有 Key，默认启动命令仍保持离线；只指定 `--model-profile`
而没有 Key 时，两个服务都不会启动。请只在环境变量或本地密钥管理工具中
保存 Key，不要写入 TOML 或提交到 Git。模型行为见 [Text Agent 指南](text-agent.md)。

配置模型不需要 `--config`。若还要调整其他 Agent 行为，可将基础示例复制到
启动脚本默认读取的本地路径：

```bash
cp packages/lefly-agent/agent.example.toml packages/lefly-agent/agent.toml
python tools/run_local_demo.py
```

只要该文件存在，启动脚本就会自动加载，即使从其他工作目录执行也是如此；文件
不存在时使用 Agent 内置默认值。只有改用其他 TOML 文件才需要显式传
`--config`。单独运行 `python -m lefly_agent` 时仍须显式传入 `--config`。

启动脚本固定使用本机 8766、8767 端口。若需其他地址或端口，请使用下面的
独立启动命令。

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

重新启动时只需指定模型 profile；脚本会自动加载本地天气 Host。请确保同一终端
已导出 `QWEATHER_API_KEY`：

```bash
python tools/run_local_demo.py --model-profile deepseek
```

如需修改天气问题未指定城市时使用的默认城市，请在同一文件中设置
`[agent].default_city`。两个服务均可单独省略，只会禁用对应工具。两个 Key
都不要写入 TOML 或提交到 Git。

Python 环境可以由 Conda、venv 或其他工具管理。LeFly 不要求使用特定的环境名称。

## 分别启动服务以排查问题

正常首次体验使用上面的一条启动命令。排查服务问题时，可以在一个终端启动
Simulator Lite：

```bash
python -m lefly_simulator --host 127.0.0.1 --port 8766
```

保持 Simulator 运行，在第二个终端启动 Text Agent：

```bash
python -m lefly_agent --host 127.0.0.1 --port 8767
```

独立运行 Agent 的模型模式时，设置 Key 后添加 `--model-profile deepseek`。
如果 Console 此前显示 Text Agent 未连接，请刷新页面。分别运行时，先在
Agent 终端按 `Ctrl-C`，再停止 Simulator。

## 故障排查

- **端口已占用：** 停止之前的进程。启动脚本固定使用 8766 和 8767；
  其他端口请使用独立命令。
- **Console 不可用：** 确认 `http://127.0.0.1:8766/health` 返回 JSON。
- **Text Agent 未连接：** 确认 `http://127.0.0.1:8767/health` 可访问，并确保先启动 Simulator。
- **LLM 不可用：** 确认一条安装命令已完成，设置 `LEFLY_LLM_API_KEY`，
  并确认 `--model-profile` 与服务商匹配。
- **本地打包工具过旧导致安装失败：** 在仓库根目录升级工具后，重试完整
  安装命令：

  ```bash
  python -m pip install --upgrade pip setuptools wheel
  python -m pip install \
    packages/lefly-protocol \
    packages/lefly-sdk-python \
    packages/lefly-simulator \
    'packages/lefly-agent[llm]'
  ```
- **天气不可用：** 设置 `QWEATHER_API_KEY`，并配置账号专属的
  `[search].qweather_api_host`。
- **搜索不可用：** 启动 Agent 前设置 `TAVILY_API_KEY`。
- **3D 视图空白：** 启用 WebGL 和浏览器硬件加速。
- **控件不可用：** 使用内置目标，等待完整状态同步，并在需要时点击“接管控制权”。

继续阅读 [Simulator 指南](simulator.md)、[Text Agent 指南](text-agent.md)或 [Python SDK 指南](sdk-python.md)。
