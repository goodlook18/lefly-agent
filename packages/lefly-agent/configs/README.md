# LLM Provider Profiles

These files are the editable, non-secret model configuration selected by
`--model-profile` when running from this source checkout. Each provider uses
the same rule: edit `agent.<profile>.toml`, then restart the Agent. They contain
only provider, model ID, and optional model endpoint settings.
Device, weather, search, touch, queue, and tool-loop settings remain in the
complete private `agent.toml`.

```bash
export LEFLY_LLM_API_KEY='...'
python tools/run_local_demo.py --model-profile deepseek
```

Supported profile names:

- `openai`
- `qwen`
- `deepseek`
- `huawei-maas`
- `openai-compatible`

The local-demo launcher automatically loads
`packages/lefly-agent/agent.toml` when present; use `--config` to select a
different base TOML. Without the local file it uses built-in defaults. The
selected profile supplies model identity; an explicit
`LEFLY_MODEL` environment value overrides its model ID. API keys always come
from environment variables. Never store credentials in TOML.

Wheel builds generate a bundled snapshot from these five files. An installed
wheel started outside a source checkout uses that snapshot; edit the source
and reinstall the wheel to change its bundled defaults.
