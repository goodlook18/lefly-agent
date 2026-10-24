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
python -m pip install packages/lefly-protocol packages/lefly-sdk-python packages/lefly-simulator 'packages/lefly-agent[llm]'
```

This installs Simulator and Text Agent, including its LLM dependencies, in one
command. Installing those dependencies does not enable a model or require a
key. The default run is credential-free and offline.

## Start both services

```bash
python tools/run_local_demo.py
```

The command starts Simulator first, waits for it to be healthy, then starts
Text Agent. Open `http://127.0.0.1:8766/`. Browser direct controls use
Simulator. Console text input needs Text Agent: try `点头`, `向左看`, `变绿灯`,
`关灯`, `休息`, or `醒来`. These deterministic commands work offline. General
conversation receives an offline response until you explicitly select a model.
The base status strip changes only with robot lifecycle state.

Press `Ctrl-C` in this terminal to stop both services. If a service cannot
start, the launcher names it and stops any child it started.

## Optional model conversation

Set your provider's API key in the environment, then select its profile:

```bash
export LEFLY_LLM_API_KEY='your-provider-key'
python tools/run_local_demo.py --model-profile deepseek
```

Other profiles are `openai`, `qwen`, `huawei-maas`, and `openai-compatible`.
To change the model ID or endpoint in this checkout, edit the corresponding
`packages/lefly-agent/configs/agent.<profile>.toml` before starting. For
example, `--model-profile qwen` reads `agent.qwen.toml`. Restart the Agent
after each edit. A wheel started outside the checkout uses the profile snapshot
created when that wheel was built.
The default launcher command stays offline even if `LEFLY_LLM_API_KEY` is set;
`--model-profile` without a key fails before either service starts. Keep the
key in the environment or a local secret manager. Do not write it to TOML or
commit it to Git. See the [Text Agent guide](text-agent.md) for model behavior.

No `--config` flag is needed to select or edit a model profile. For other
Agent settings, copy the base example to the launcher's default local path:

```bash
cp packages/lefly-agent/agent.example.toml packages/lefly-agent/agent.toml
python tools/run_local_demo.py
```

The launcher automatically loads that file when present, even if started from
another working directory. Without it, built-in Agent defaults apply. Use
`--config` only to override the default with another TOML file. Standalone
`python -m lefly_agent` still needs an explicit `--config` to load this file.

The launcher keeps the local service addresses at ports 8766 and 8767. Use the
individual service commands below if you need other endpoints or ports.

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

Restart with the model profile; the launcher loads the local weather Host
automatically. The QWeather key must be exported in the same shell:

```bash
python tools/run_local_demo.py --model-profile deepseek
```

Set `[agent].default_city` in the same file to change the city used when a
weather question does not name one. Either service may be omitted; only its
corresponding tool will be unavailable. Keep both keys out of TOML and Git.

The environment may be managed by Conda, venv, or another Python tool. LeFly does not require a specific environment name.

## Run services separately for diagnostics

The normal first run uses the single launcher command above. To isolate a
service problem, start Simulator Lite in one terminal:

```bash
python -m lefly_simulator --host 127.0.0.1 --port 8766
```

Keep Simulator running and start Text Agent in another terminal:

```bash
python -m lefly_agent --host 127.0.0.1 --port 8767
```

For an individual model-backed Agent run, add `--model-profile deepseek` after
setting the key. Refresh the Console if it previously showed a disconnected
Text Agent. Stop the Agent, then Simulator, with `Ctrl-C`.

## Troubleshooting

- **Port already in use:** stop the earlier process. The local launcher uses
  fixed ports 8766 and 8767; use individual commands for other ports.
- **Console unavailable:** confirm `http://127.0.0.1:8766/health` returns JSON.
- **Text Agent disconnected:** confirm `http://127.0.0.1:8767/health` and start
  the Simulator before the Agent.
- **LLM unavailable:** confirm the one-command install completed, export
  `LEFLY_LLM_API_KEY`, and select the matching `--model-profile`.
- **Install fails with outdated packaging tools:** from the repository root,
  upgrade the tools and repeat the complete install command:

  ```bash
  python -m pip install --upgrade pip setuptools wheel
  python -m pip install \
    packages/lefly-protocol \
    packages/lefly-sdk-python \
    packages/lefly-simulator \
    'packages/lefly-agent[llm]'
  ```
- **Weather unavailable:** export `QWEATHER_API_KEY` and configure the
  account-specific `[search].qweather_api_host`.
- **Search unavailable:** export `TAVILY_API_KEY` before starting the Agent.
- **Blank 3D view:** enable WebGL and browser hardware acceleration.
- **Controls are disabled:** use the built-in target, wait for current state,
  and acquire the browser control lease.

Continue with the [Simulator guide](simulator.md), [Text Agent guide](text-agent.md), or [Python SDK guide](sdk-python.md).
