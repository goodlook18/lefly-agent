import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from lefly_agent.__main__ import (
    _create_model,
    _load_livekit_components,
    build_parser,
    resolve_settings,
    run,
)
from lefly_agent.logging_setup import configure_debug_logging


class AgentCliTests(unittest.TestCase):
    def test_debug_mode_is_explicit_and_disabled_by_default(self):
        self.assertFalse(build_parser().parse_args([]).debug)
        self.assertTrue(build_parser().parse_args(["--debug"]).debug)

    def test_defaults_match_local_simulator_quickstart(self):
        config = resolve_settings([], environ={})

        self.assertEqual(config.server.host, "127.0.0.1")
        self.assertEqual(config.server.port, 8767)
        self.assertEqual(
            config.agent.device_url,
            "ws://127.0.0.1:8766/ws/device/simulator",
        )
        self.assertEqual(config.agent.device_id, "lefly-sim-01")

    def test_device_id_can_target_an_explicit_device(self):
        config = resolve_settings(
            ["--device-id", "classroom-lefly-02"], environ={}
        )

        self.assertEqual(config.agent.device_id, "classroom-lefly-02")

    def test_config_file_is_loaded_and_explicit_cli_value_wins(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = Path(directory) / "agent.toml"
            path.write_text(
                "[agent]\ndevice_id='toml-device'\n[server]\nport=7001",
                encoding="utf-8",
            )
            config = resolve_settings(
                ["--config", str(path), "--port", "7002"], environ={}
            )

        self.assertEqual(config.agent.device_id, "toml-device")
        self.assertEqual(config.server.port, 7002)

    def test_model_profile_is_merged_with_the_base_config(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = Path(directory) / "agent.toml"
            path.write_text(
                "[agent]\ndefault_city='Chengdu'\n"
                "[model]\nprovider='openai'\nmodel='base-model'",
                encoding="utf-8",
            )
            config = resolve_settings(
                [
                    "--config",
                    str(path),
                    "--model-profile",
                    "qwen",
                ],
                environ={},
            )

        self.assertEqual(config.agent.default_city, "Chengdu")
        self.assertEqual(config.model.provider, "qwen")
        self.assertEqual(config.model.model, "qwen3.6-flash")

    def test_model_factory_is_used_only_when_llm_key_is_configured(self):
        offline = resolve_settings([], environ={})
        with patch("lefly_agent.llm_factory.build_llm") as factory:
            self.assertIsNone(_create_model(offline))
        factory.assert_not_called()

        online = resolve_settings(
            [],
            environ={
                "LEFLY_LLM_API_KEY": "test-secret",
                "LEFLY_LLM_PROVIDER": "qwen",
            },
        )
        sentinel_model = object()
        with patch(
            "lefly_agent.llm_factory.build_llm", return_value=sentinel_model
        ) as factory:
            self.assertIs(_create_model(online), sentinel_model)
        factory.assert_called_once_with(online.model, api_key="test-secret")

    def test_missing_llm_extra_has_an_actionable_error(self):
        module_names = (
            "lefly_agent.livekit_agent",
            "lefly_agent.livekit_session",
        )
        cached = {name: sys.modules.pop(name, None) for name in module_names}
        real_import = __import__

        def import_without_livekit(name, *args, **kwargs):
            if name == "livekit" or name.startswith("livekit."):
                raise ModuleNotFoundError("No module named 'livekit'", name="livekit")
            return real_import(name, *args, **kwargs)

        try:
            with (
                patch("builtins.__import__", side_effect=import_without_livekit),
                self.assertRaisesRegex(RuntimeError, r"lefly-agent\[llm\]"),
            ):
                _load_livekit_components()
        finally:
            for name, module in cached.items():
                if module is not None:
                    sys.modules[name] = module

    def test_rejects_invalid_port(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--port", "70000"])

    def test_rejects_non_loopback_bind_address(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--host", "0.0.0.0"])

    def test_rejects_noncanonical_device_id(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--device-id", "LeFly 01"])

    def test_run_configures_logging_before_settings_and_closes_on_exit(self):
        calls = []
        session = Mock()
        config = resolve_settings([], environ={})

        def configure(enabled):
            calls.append(("logging", enabled))
            return session

        def load(args, environ):
            calls.append(("settings", args.debug))
            return config

        with (
            patch(
                "lefly_agent.__main__.configure_debug_logging",
                side_effect=configure,
            ),
            patch(
                "lefly_agent.__main__._resolve_parsed_settings",
                side_effect=load,
            ),
            patch("lefly_agent.__main__.serve", new=Mock(return_value=object())),
            patch("lefly_agent.__main__.asyncio.run") as asyncio_run,
        ):
            run(["--debug"], environ={})

        self.assertEqual(calls, [("logging", True), ("settings", True)])
        asyncio_run.assert_called_once()
        session.close.assert_called_once_with()

    def test_debug_startup_summary_excludes_environment_secrets_and_chat_text(self):
        sessions = []
        secret = "private-weather-key"
        conversation = "这是不应出现在启动日志中的完整对话"

        with TemporaryDirectory() as directory:
            def configure(enabled):
                session = configure_debug_logging(
                    enabled,
                    working_directory=Path(directory),
                )
                sessions.append(session)
                return session

            with (
                patch(
                    "lefly_agent.__main__.configure_debug_logging",
                    side_effect=configure,
                ),
                patch(
                    "lefly_agent.__main__.serve",
                    new=Mock(return_value=object()),
                ),
                patch("lefly_agent.__main__.asyncio.run"),
            ):
                run(
                    ["--debug"],
                    environ={
                        "QWEATHER_API_KEY": secret,
                        "LEFLY_LLM_API_KEY": "private-model-key",
                    },
                )

            rendered = sessions[0].path.read_text(encoding="utf-8")

        self.assertIn("agent.startup.config", rendered)
        self.assertIn("'provider': 'openai'", rendered)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("private-model-key", rendered)
        self.assertNotIn(conversation, rendered)


if __name__ == "__main__":
    unittest.main()
