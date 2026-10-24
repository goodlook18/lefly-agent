# LLM Provider Profiles

Wheel builds generate these non-secret fallback profiles from
`packages/lefly-agent/configs/agent.<profile>.toml`. Edit the source files in a
checkout; do not maintain a second set in this package directory. A wheel
started outside a source checkout uses its build-time snapshot.

Select one profile from the repository root:

```bash
export LEFLY_LLM_API_KEY='...'
python -m lefly_agent \
  --model-profile qwen
```

Supported CLI profile names are `openai`, `qwen`, `deepseek`, `huawei-maas`,
and `openai-compatible`. The profile supplies provider, model ID, and optional
endpoint. The local-demo launcher automatically loads
`packages/lefly-agent/agent.toml` when present and accepts `--config` to
override it. Standalone `python -m lefly_agent` requires an explicit
`--config` for device, server, touch, city, search, queue, and tool-loop
settings. An explicit
`LEFLY_MODEL` environment value overrides the profile model ID. API keys
always come from the environment; never store credentials in TOML.

Model IDs are provider-owned and may change. A custom Qwen- or
DeepSeek-compatible gateway should retain that provider family so its reviewed
tool policy remains active.
