# Text Agent

English | [简体中文](zh-CN/text-agent.md)

The Text Agent connects text interaction to the same public SDK used by other clients. The local-demo command starts it alongside Simulator. The default is credential-free and deterministic; an explicitly selected profile enables the optional LiveKit LLM path. Neither path uses a LiveKit Room, microphone, speaker, ASR, TTS, VAD, Camera, or physical driver in `v0.1.2`.

## Standard startup

From the repository root, install all four packages as in the
[quickstart](quickstart.md), then start both services:

```bash
python tools/run_local_demo.py
```

Open `http://127.0.0.1:8766/`. Browser direct controls go to Simulator;
Console text input requires this Agent. The default command stays offline
even when `LEFLY_LLM_API_KEY` happens to be present in the shell.

Strict unambiguous phrases such as `点头`, `摇头`, `向左看`, `向右看`, `变黄灯`, `关灯`, `休息`, and `醒来` use the deterministic fast path. Questions, negation, hypotheticals, multiple intents, and conversation receive a safe offline response when no model is configured.

## Optional LLM Demo

The single quickstart install command already includes the audited `llm`
extra. Set your provider's key and explicitly select its profile:

```bash
export LEFLY_LLM_API_KEY='your-provider-key'
python tools/run_local_demo.py --model-profile deepseek
```

Available profile names are
`openai`, `qwen`, `deepseek`, `huawei-maas`, and `openai-compatible`.
In this source checkout, each name reads its editable
`packages/lefly-agent/configs/agent.<profile>.toml` automatically. Edit the
model ID there and restart the Agent; no `--config` flag is needed for this.
An installed wheel outside the checkout uses its build-time profile snapshot.
The launcher rejects a profile without a key before starting either service.
Keep credentials out of TOML and Git.

For custom Agent settings, copy `packages/lefly-agent/agent.example.toml` to
an untracked `packages/lefly-agent/agent.toml`. The launcher loads it
automatically when present, including from another working directory, and
otherwise uses built-in Agent defaults. Pass `--config` to select another base
TOML. Standalone `python -m lefly_agent` still requires an explicit `--config`
for this file. The base TOML retains device, server, city, search, queue,
touch, and tool-loop settings. A profile changes only model provider, model ID,
and optional endpoint. Secrets stay in environment variables. The launcher
keeps local ports 8766 and 8767; run the services individually for other
endpoints.

QWeather and Tavily are optional:

Create a QWeather developer account and API credential at
[QWeather Developers](https://dev.qweather.com/).

```bash
export QWEATHER_API_KEY='your-qweather-key'
export TAVILY_API_KEY='your-tavily-key'
```

QWeather also needs the account-specific API Host shown in its developer
console:

```toml
[search]
qweather_api_host = "https://your-account-host.qweatherapi.com"
```

Missing keys or a missing QWeather API Host disable only the corresponding information tool.

## Routing and status

Direct Console controls never enter the Agent or an LLM. Text first uses the strict fast-intent router; only ambiguous or conversational requests reach the model. Fast and model-selected robot tools share one bounded serial command service and the same SDK validation.

Text inference may set robot-owned `thinking`. Text output is not audio `speaking`; listening and speaking belong to a later voice release. Configured touch behavior is deterministic and does not enter model conversation history.

## Debug logs

Run Agent individually with `--debug` to create a timestamped log under
`logs/`. Logs include request IDs, stages, durations, provider identity, status codes, and error types.

## Security

Agent Control is intended for loopback development. Do not expose port 8767 to
an untrusted network. Model-backed text and tool data are processed under the
configured provider's policies.
