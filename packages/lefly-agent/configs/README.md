# LLM Provider Profiles

These files are the public, non-secret examples behind `--model-profile`.
They contain only provider, model ID, and optional model endpoint settings.
Device, weather, search, touch, queue, and tool-loop settings remain in the
complete private `agent.toml`.

```bash
python -m lefly_agent \
  --config packages/lefly-agent/agent.toml \
  --model-profile deepseek
```

Supported profile names:

- `openai`
- `qwen`
- `deepseek`
- `huawei-maas`
- `openai-compatible`

API keys always come from environment variables. Never store credentials in
these files or in `agent.toml`.

The same profiles are packaged inside `lefly_agent.model_profiles` so an
installed wheel works outside the source repository. Automated tests require
the public examples and packaged copies to remain identical.
