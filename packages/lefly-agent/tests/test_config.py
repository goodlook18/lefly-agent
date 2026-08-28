from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from importlib.resources import files
from pathlib import Path

from lefly_agent.config import ConfigError, MODEL_PROFILE_FILES, load_agent_config


class AgentConfigTests(unittest.TestCase):
    def write_config(self, value: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "agent.toml"
        path.write_text(value, encoding="utf-8")
        return path

    def test_precedence_is_cli_then_environment_then_toml_then_defaults(self):
        path = self.write_config(
            """
[agent]
device_url = "ws://toml.example/ws"
device_id = "toml-device"
default_city = "Shanghai"
queue_capacity = 3

[server]
port = 7001

[model]
model = "toml-model"
"""
        )

        config = load_agent_config(
            path,
            environ={
                "LEFLY_DEVICE_URL": "ws://env.example/ws",
                "LEFLY_DEVICE_ID": "env-device",
                "LEFLY_AGENT_PORT": "7002",
                "LEFLY_MODEL": "env-model",
            },
            cli={"device_id": "cli-device", "port": 7003},
        )

        self.assertEqual(config.agent.device_url, "ws://env.example/ws")
        self.assertEqual(config.agent.device_id, "cli-device")
        self.assertEqual(config.agent.default_city, "Shanghai")
        self.assertEqual(config.agent.queue_capacity, 3)
        self.assertEqual(config.server.port, 7003)
        self.assertEqual(config.model.model, "env-model")
        self.assertEqual(config.agent.history_capacity, 100)

    def test_model_provider_precedence_and_default(self):
        default = load_agent_config(environ={})
        self.assertEqual(default.model.provider, "openai")

        path = self.write_config(
            "[model]\nprovider='qwen'\nmodel='qwen3.6-flash'"
        )
        configured = load_agent_config(
            path,
            environ={"LEFLY_LLM_PROVIDER": "deepseek"},
        )

        self.assertEqual(configured.model.provider, "deepseek")

    def test_published_provider_profiles_load_without_secrets(self):
        expected = {
            "openai": "openai",
            "qwen": "qwen",
            "deepseek": "deepseek",
            "huawei-maas": "huawei_maas",
            "openai-compatible": "openai_compatible",
        }

        self.assertEqual(set(MODEL_PROFILE_FILES), set(expected))
        for profile, provider in expected.items():
            with self.subTest(profile=profile):
                config = load_agent_config(model_profile=profile, environ={})
                self.assertEqual(config.model.provider, provider)
                self.assertIsNone(config.secrets.llm_api_key)

    def test_public_profile_examples_match_packaged_runtime_profiles(self):
        public_dir = Path(__file__).resolve().parents[1] / "configs"

        for filename in MODEL_PROFILE_FILES.values():
            with self.subTest(filename=filename):
                with (public_dir / filename).open("rb") as public_file:
                    public_profile = tomllib.load(public_file)
                packaged_resource = files("lefly_agent.model_profiles").joinpath(
                    filename
                )
                with packaged_resource.open("rb") as packaged_file:
                    packaged_profile = tomllib.load(packaged_file)
                self.assertEqual(public_profile, packaged_profile)

    def test_model_profile_overrides_only_model_and_preserves_base_config(self):
        path = self.write_config(
            """
[agent]
default_city = "Hangzhou"

[model]
provider = "openai"
model = "base-model"
max_tool_steps = 5

[search]
qweather_api_host = "https://weather.example.com"

[touch.left]
motion = "look_left"
light_color = "#123456"
"""
        )

        config = load_agent_config(
            path,
            model_profile="deepseek",
            environ={},
        )

        self.assertEqual(config.model.provider, "deepseek")
        self.assertEqual(config.model.model, "deepseek-v4-flash")
        self.assertIsNone(config.model.base_url)
        self.assertEqual(config.model.max_tool_steps, 5)
        self.assertEqual(config.agent.default_city, "Hangzhou")
        self.assertEqual(
            config.search.qweather_api_host,
            "https://weather.example.com",
        )
        self.assertEqual(config.touch["left"].motion, "look_left")
        self.assertEqual(config.touch["left"].light_color, "#123456")

    def test_rejects_unknown_model_profile(self):
        with self.assertRaisesRegex(ConfigError, "unknown model profile"):
            load_agent_config(model_profile="not-a-provider", environ={})

    def test_health_summary_includes_non_secret_model_identity(self):
        config = load_agent_config(
            self.write_config("[model]\nprovider='qwen'\nmodel='qwen3.6-flash'"),
            environ={"LEFLY_LLM_API_KEY": "must-not-render"},
        )

        health = config.health_summary()

        self.assertEqual(health["provider"], "qwen")
        self.assertEqual(health["model"], "qwen3.6-flash")
        self.assertNotIn("must-not-render", repr(health))

    def test_missing_optional_sections_use_defaults(self):
        config = load_agent_config(self.write_config("[agent]\ndefault_city='Ningbo'"), environ={})

        self.assertEqual(config.agent.default_city, "Ningbo")
        self.assertEqual(config.model.max_tool_steps, 3)
        self.assertEqual(config.search.max_results, 5)
        self.assertIsNone(config.search.qweather_api_host)
        self.assertEqual(config.search.tavily_base_url, "https://api.tavily.com")
        self.assertIsNone(config.model.base_url)
        self.assertIsNone(config.touch["left"].motion)

    def test_provider_endpoints_are_non_secret_config_with_environment_override(self):
        path = self.write_config(
            """
[search]
qweather_api_host = "https://toml.qweatherapi.com"
tavily_base_url = "https://toml.tavily.example"
"""
        )

        config = load_agent_config(
            path,
            environ={
                "QWEATHER_API_HOST": "https://env.qweatherapi.com/",
                "TAVILY_BASE_URL": "https://env.tavily.example/",
            },
        )

        self.assertEqual(
            config.search.qweather_api_host, "https://env.qweatherapi.com"
        )
        self.assertEqual(
            config.search.tavily_base_url, "https://env.tavily.example"
        )

    def test_rejects_non_http_provider_endpoint(self):
        path = self.write_config("[search]\nqweather_api_host='file:///tmp/secret'")
        with self.assertRaisesRegex(ConfigError, "HTTP endpoint"):
            load_agent_config(path, environ={})

    def test_accepts_model_endpoint_api_paths_and_normalizes_trailing_slash(self):
        cases = (
            "https://provider.example/v1/",
            "https://provider.example/openai/v1/",
            "http://127.0.0.1:8000/v1/",
        )
        for endpoint in cases:
            with self.subTest(endpoint=endpoint):
                path = self.write_config(
                    "[model]\n"
                    "provider='openai_compatible'\n"
                    "model='local-model'\n"
                    "base_url='%s'\n" % endpoint
                )
                config = load_agent_config(path, environ={})
                self.assertEqual(config.model.base_url, endpoint.rstrip("/"))

    def test_rejects_invalid_model_provider_and_endpoint_combinations(self):
        cases = (
            ("[model]\nprovider='unknown'", "unsupported model provider"),
            ("[model]\nprovider='huawei_maas'", "requires model.base_url"),
            (
                "[model]\nprovider='openai_compatible'",
                "requires model.base_url",
            ),
            (
                "[model]\nprovider='openai_compatible'\n"
                "base_url='file:///tmp/x'",
                "HTTP or HTTPS",
            ),
            (
                "[model]\nprovider='openai_compatible'\n"
                "base_url='https://user:password@provider.example/v1'",
                "must not contain credentials",
            ),
            (
                "[model]\nprovider='openai_compatible'\n"
                "base_url='https://provider.example/v1?q=1'",
                "query or fragment",
            ),
        )
        for value, message in cases:
            with self.subTest(value=value), self.assertRaisesRegex(
                ConfigError, message
            ):
                load_agent_config(self.write_config(value), environ={})

    def test_warns_when_llm_key_uses_legacy_implicit_provider(self):
        with self.assertLogs("lefly_agent.config", level="WARNING") as captured:
            config = load_agent_config(
                self.write_config(
                    "[model]\nmodel='legacy-model'\n"
                    "base_url='https://tenant.example/private/v1'"
                ),
                environ={"LEFLY_LLM_API_KEY": "legacy-secret"},
            )

        rendered = "\n".join(captured.output)
        self.assertEqual(config.model.provider, "openai")
        self.assertIn("provider was omitted", rendered)
        self.assertNotIn("legacy-secret", rendered)
        self.assertNotIn("/private/v1", rendered)

    def test_explicit_provider_avoids_legacy_warning(self):
        path = self.write_config("[model]\nprovider='qwen'\nmodel='qwen-model'")
        with self.assertNoLogs("lefly_agent.config", level="WARNING"):
            load_agent_config(
                path,
                environ={"LEFLY_LLM_API_KEY": "test-secret"},
            )

        with self.assertNoLogs("lefly_agent.config", level="WARNING"):
            load_agent_config(
                self.write_config("[model]\nmodel='env-provider-model'"),
                environ={
                    "LEFLY_LLM_API_KEY": "test-secret",
                    "LEFLY_LLM_PROVIDER": "deepseek",
                },
            )

    def test_rejects_runtime_other_than_python_3_12(self):
        with self.assertRaisesRegex(ConfigError, "Python 3.12"):
            load_agent_config(environ={}, python_version=(3, 11, 9))

    def test_rejects_invalid_queue_and_timeout_values(self):
        cases = (
            "[agent]\nqueue_capacity=0",
            "[agent]\nhistory_capacity=-1",
            "[agent]\nrequest_timeout=0",
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load_agent_config(self.write_config(value), environ={})

    def test_rejects_unknown_intent_names(self):
        path = self.write_config(
            "[fast_intent.aliases]\nrun_shell = ['执行命令']"
        )

        with self.assertRaisesRegex(ConfigError, "unknown fast intent"):
            load_agent_config(path, environ={})

    def test_rejects_aliases_shared_by_two_intents(self):
        path = self.write_config(
            """
[fast_intent.aliases]
nod = ["动作"]
shake_head = ["动作。"]
"""
        )

        with self.assertRaisesRegex(ConfigError, "conflicting fast intent alias"):
            load_agent_config(path, environ={})

    def test_secrets_are_environment_only_and_never_rendered(self):
        path = self.write_config(
            "[model]\nprovider='openai'\nmodel='test-model'"
        )
        secrets = {
            "LEFLY_LLM_API_KEY": "llm-secret-value",
            "QWEATHER_API_KEY": "weather-secret-value",
            "TAVILY_API_KEY": "search-secret-value",
        }

        config = load_agent_config(path, environ=secrets)
        rendered = repr(config)
        health = repr(config.health_summary())

        self.assertEqual(config.secrets.llm_api_key, "llm-secret-value")
        for value in secrets.values():
            self.assertNotIn(value, rendered)
            self.assertNotIn(value, health)

    def test_rejects_non_ascii_or_whitespace_in_environment_secrets(self):
        invalid_values = {
            "LEFLY_LLM_API_KEY": "sk-test”",
            "QWEATHER_API_KEY": "weather key",
            "TAVILY_API_KEY": "tvly-test\n",
        }

        for variable, value in invalid_values.items():
            with self.subTest(variable=variable):
                with self.assertRaisesRegex(
                    ConfigError,
                    "%s must contain printable ASCII characters without whitespace"
                    % variable,
                ) as raised:
                    load_agent_config(environ={variable: value})
                self.assertNotIn(value, str(raised.exception))

    def test_rejects_api_keys_stored_in_toml(self):
        path = self.write_config("[model]\napi_key='must-not-be-stored'")

        with self.assertRaisesRegex(ConfigError, "environment variables only"):
            load_agent_config(path, environ={})

    def test_explicit_missing_config_file_is_fatal(self):
        missing = Path(tempfile.gettempdir()) / "lefly-missing-agent-config.toml"
        if missing.exists():
            missing.unlink()

        with self.assertRaisesRegex(ConfigError, "does not exist"):
            load_agent_config(missing, environ={})


if __name__ == "__main__":
    unittest.main()
