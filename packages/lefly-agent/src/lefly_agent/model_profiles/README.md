# LLM Provider Profiles

These packaged, non-secret profiles override only the model provider, model ID,
and optional model endpoint. The base Agent TOML continues to own device,
server, touch, city, search, queue, and tool-loop settings. API keys always
come from environment variables.

Create a private base configuration once:

```bash
cp packages/lefly-agent/agent.example.toml \
  packages/lefly-agent/agent.toml
```

Then select one profile from the repository root:

```bash
export LEFLY_LLM_API_KEY='...'
python -m lefly_agent \
  --config packages/lefly-agent/agent.toml \
  --model-profile qwen
```

Supported CLI profile names are `openai`, `qwen`, `deepseek`, `huawei-maas`,
and `openai-compatible`. The profile cannot override any non-model section.
The private `agent.toml` is ignored by Git. Never store credentials in TOML.

Model IDs are provider-owned and may change. A custom Qwen- or
DeepSeek-compatible gateway should retain that provider family so its reviewed
tool policy remains active.
