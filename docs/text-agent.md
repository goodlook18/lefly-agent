# Text Agent

English | [简体中文](zh-CN/text-agent.md)

The Text Agent connects text interaction to the same public SDK used by other clients. One standard startup command supports both the credential-free deterministic path and the optional LiveKit LLM path. Neither uses a LiveKit Room, microphone, speaker, ASR, TTS, VAD, Camera, or physical driver in `v0.1.1`.

## Standard startup

Create a local, untracked base configuration once, start Simulator, then run Agent:

```bash
cp packages/lefly-agent/agent.example.toml packages/lefly-agent/agent.toml
python -m lefly_agent \
  --config packages/lefly-agent/agent.toml \
  --model-profile deepseek
```

Strict unambiguous phrases such as `点头`, `摇头`, `向左看`, `向右看`, `变黄灯`, `关灯`, `休息`, and `醒来` use the deterministic fast path. Questions, negation, hypotheticals, multiple intents, and conversation receive a safe offline response when no model is configured.

## Optional LLM Demo

Install the separately audited extra:

```bash
python -m pip install 'packages/lefly-agent[llm]'
export LEFLY_LLM_API_KEY='your-provider-key'
```

Restart with the same standard command above. Available profile names are
`openai`, `qwen`, `deepseek`, `huawei-maas`, and `openai-compatible`.

The base TOML retains device, server, city, search, queue, touch, and tool-loop settings. A profile changes only model provider, model ID, and optional endpoint. Secrets stay in environment variables.

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

Add `--debug` to the standard startup command to create a timestamped log under
`logs/`. Logs include request IDs, stages, durations, provider identity, status codes, and error types, but must not contain credentials or complete conversation/provider bodies.

## Security

Agent Control is intended for loopback development. Do not expose port 8767 to
an untrusted network. Model-backed text and tool data are processed under the
configured provider's policies.
