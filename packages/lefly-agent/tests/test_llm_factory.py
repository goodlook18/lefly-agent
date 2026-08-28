from __future__ import annotations

import unittest

from lefly_agent.config import ModelSettings
from lefly_agent.llm_factory import build_llm, resolve_llm_options


class RecordingLLM:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class LLMFactoryTests(unittest.TestCase):
    def settings(
        self,
        provider: str,
        *,
        base_url: str | None = None,
        model: str = "test-model",
    ) -> ModelSettings:
        return ModelSettings(
            provider=provider,
            model=model,
            base_url=base_url,
            max_tool_steps=3,
        )

    def test_resolves_exact_builtin_provider_options(self):
        cases = {
            "openai": {},
            "qwen": {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "extra_body": {"enable_thinking": False},
                "_strict_tool_schema": False,
            },
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "extra_body": {"thinking": {"type": "disabled"}},
                "tool_choice": "auto",
                "_strict_tool_schema": False,
            },
            "huawei_maas": {
                "base_url": "https://maas.example/openai/v1",
                "extra_body": {"thinking": {"type": "disabled"}},
                "tool_choice": "auto",
                "_strict_tool_schema": False,
            },
            "openai_compatible": {
                "base_url": "http://127.0.0.1:18000/v1",
                "_strict_tool_schema": False,
            },
        }

        for provider, expected_policy in cases.items():
            with self.subTest(provider=provider):
                base_url = expected_policy.get("base_url")
                configured_url = (
                    base_url
                    if provider in {"huawei_maas", "openai_compatible"}
                    else None
                )
                options = resolve_llm_options(
                    self.settings(provider, base_url=configured_url),
                    api_key="test-secret",
                )

                self.assertEqual(options["model"], "test-model")
                self.assertEqual(options["api_key"], "test-secret")
                self.assertEqual(
                    {key: value for key, value in options.items() if key not in {"model", "api_key"}},
                    expected_policy,
                )

    def test_custom_endpoint_retains_selected_provider_policy(self):
        endpoint = "https://gateway.example/custom/v1"
        for provider, thinking in (
            ("qwen", {"enable_thinking": False}),
            ("deepseek", {"thinking": {"type": "disabled"}}),
        ):
            with self.subTest(provider=provider):
                options = resolve_llm_options(
                    self.settings(provider, base_url=endpoint),
                    api_key="test-secret",
                )
                self.assertEqual(options["base_url"], endpoint)
                self.assertEqual(options["extra_body"], thinking)
                self.assertFalse(options["_strict_tool_schema"])

    def test_build_uses_injected_constructor_and_logs_safe_identity(self):
        settings = self.settings(
            "huawei_maas",
            model="deepseek-v4-flash",
            base_url="https://tenant.example/private/openai/v1",
        )

        with self.assertLogs("lefly_agent.llm_factory", level="INFO") as captured:
            model = build_llm(
                settings,
                api_key="must-not-render",
                llm_type=RecordingLLM,
            )

        rendered = "\n".join(captured.output)
        self.assertEqual(model.kwargs["model"], "deepseek-v4-flash")
        self.assertIn("provider=huawei_maas", rendered)
        self.assertIn("model=deepseek-v4-flash", rendered)
        self.assertIn("endpoint_host=tenant.example", rendered)
        self.assertNotIn("must-not-render", rendered)
        self.assertNotIn("/private/openai/v1", rendered)

    def test_rejects_blank_key_before_constructor_invocation(self):
        with self.assertRaisesRegex(ValueError, "API key must be non-empty"):
            build_llm(
                self.settings("openai"),
                api_key="  ",
                llm_type=RecordingLLM,
            )


if __name__ == "__main__":
    unittest.main()
