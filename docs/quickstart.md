# Quickstart

English | [简体中文](zh-CN/quickstart.md)

This path runs entirely on one development computer and requires no robot or LiveKit Room. Cloud model, weather, and search credentials remain optional.

## Requirements

- Python 3.12
- A browser with WebGL enabled

Node.js is required only when rebuilding or testing the Web Console. The Simulator package already contains the compiled Console used by this quickstart.

## Install

From the repository root, use the currently active Python environment:

```bash
python --version
python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  packages/lefly-protocol \
  packages/lefly-sdk-python \
  packages/lefly-simulator \
  packages/lefly-agent
```

The command above is sufficient for the credential-free deterministic Agent.
To use DeepSeek, Qwen, OpenAI, Huawei MaaS, or another model profile with an
API key, install the LLM extra instead of the final base package:

```bash
python -m pip install 'packages/lefly-agent[llm]'
```

Set the model API key in the environment before starting the Agent:

```bash
export LEFLY_LLM_API_KEY='your-provider-key'
```

Keep the key in the environment or a local secret manager. Do not write it to
`agent.toml` or commit it to Git.

Create a local, untracked Agent configuration once:

```bash
cp packages/lefly-agent/agent.example.toml \
  packages/lefly-agent/agent.toml
```

### Optional weather and search

Weather and web search are model-invoked tools, so first enable the LLM path
above. Then export the credentials for the services you want to use:

Create a QWeather developer account and API credential at
[QWeather Developers](https://dev.qweather.com/).

```bash
export QWEATHER_API_KEY='your-qweather-key'
export TAVILY_API_KEY='your-tavily-key'
```

QWeather also requires the account-specific API Host shown in its developer
console. Add it to the local `packages/lefly-agent/agent.toml`:

```toml
[search]
qweather_api_host = "https://your-account-host.qweatherapi.com"
```

Set `[agent].default_city` in the same file to change the city used when a
weather question does not name one. Either service may be omitted; only its
corresponding tool will be unavailable. Keep both keys out of TOML and Git.

The environment may be managed by Conda, venv, or another Python tool. LeFly does not require a specific environment name.

## Start Simulator Lite

```bash
python -m lefly_simulator --host 127.0.0.1 --port 8766
```

Open `http://127.0.0.1:8766/`. Try a preset action, a joint slider, a head-light color, and a virtual touch event. The base status strip changes only with robot lifecycle state.

## Start the Text Agent

Keep the Simulator running. In a second terminal:

```bash
python -m lefly_agent \
  --config packages/lefly-agent/agent.toml \
  --model-profile deepseek
```

This is the standard Agent startup command. Without `LEFLY_LLM_API_KEY`, it runs the credential-free deterministic path and does not require LiveKit. With the LLM extra installed and the key set, the same command enables model-backed conversation and tools.
Replace `deepseek` with other supported model profile when needed, like `openai`, `qwen`, `huawei-maas`, and `openai-compatible`. See [Optional LLM Demo](text-agent.md).

Refresh the Console if its Text Agent connection was previously unavailable. Submit a deterministic command such as `点头`, `向左看`, `变绿灯`, `关灯`, `休息`, or `醒来`.

For model-backed conversation, weather, and search setup, continue with the [Text Agent guide](text-agent.md).

## Stop

Press `Ctrl-C` in the Agent terminal and then in the Simulator terminal.

## Troubleshooting

- **Port already in use:** stop the earlier process or choose another port for
  both the service and its corresponding URL.
- **Console unavailable:** confirm `http://127.0.0.1:8766/health` returns JSON.
- **Text Agent disconnected:** confirm `http://127.0.0.1:8767/health` and start
  the Simulator before the Agent.
- **LLM unavailable:** install `packages/lefly-agent[llm]`, export
  `LEFLY_LLM_API_KEY`, and confirm the selected `--model-profile`.
- **Weather unavailable:** export `QWEATHER_API_KEY` and configure the
  account-specific `[search].qweather_api_host`.
- **Search unavailable:** export `TAVILY_API_KEY` before starting the Agent.
- **Blank 3D view:** enable WebGL and browser hardware acceleration.
- **Controls are disabled:** use the built-in target, wait for current state,
  and acquire the browser control lease.

Continue with the [Simulator guide](simulator.md), [Text Agent guide](text-agent.md), or [Python SDK guide](sdk-python.md).
