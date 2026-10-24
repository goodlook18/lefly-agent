"""Focused checks for the one-command public local demo."""

import argparse
import os
import socket
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from tools import run_local_demo


class LocalDemoTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.default_config = Path(directory.name) / "agent.toml"
        default_patch = patch.object(
            run_local_demo, "DEFAULT_AGENT_CONFIG", self.default_config
        )
        default_patch.start()
        self.addCleanup(default_patch.stop)

    def agent_command(self, args):
        simulator, agent = Mock(), Mock()
        simulator.poll.return_value = None
        agent.poll.return_value = None
        with patch.object(run_local_demo, "ensure_port_available"), patch.object(
            run_local_demo.subprocess, "Popen", side_effect=[simulator, agent]
        ) as start, patch.object(run_local_demo, "wait_healthy"), patch.object(
            run_local_demo.time, "sleep", side_effect=KeyboardInterrupt
        ):
            self.assertEqual(run_local_demo.run_demo(args), 0)
        return start.call_args_list[1].args[0]

    def test_existing_default_config_is_loaded_from_any_cwd(self):
        self.default_config.write_text("[search]\nmax_results = 5\n")
        args = argparse.Namespace(model_profile=None, config=None)
        with TemporaryDirectory() as directory, chdir(directory):
            command = self.agent_command(args)
        self.assertEqual(
            command[-2:], ["--config", str(self.default_config)]
        )

    def test_model_profile_and_existing_default_config_are_combined(self):
        self.default_config.write_text("[search]\nmax_results = 5\n")
        args = argparse.Namespace(model_profile="qwen", config=None)
        with patch.dict(os.environ, {"LEFLY_LLM_API_KEY": "test-only"}):
            command = self.agent_command(args)
        self.assertEqual(
            command[-4:],
            ["--config", str(self.default_config), "--model-profile", "qwen"],
        )

    def test_missing_default_config_keeps_builtin_defaults(self):
        args = argparse.Namespace(model_profile=None, config=None)
        self.assertNotIn("--config", self.agent_command(args))

    def test_explicit_config_overrides_default(self):
        self.default_config.write_text("[search]\nmax_results = 5\n")
        override = self.default_config.with_name("other.toml")
        override.write_text("[search]\nmax_results = 3\n")
        args = argparse.Namespace(model_profile=None, config=override)
        command = self.agent_command(args)
        self.assertEqual(command[-2:], ["--config", str(override.resolve())])
        self.assertNotIn(str(self.default_config), command)

    def test_missing_explicit_config_fails_even_when_default_exists(self):
        self.default_config.write_text("[search]\nmax_results = 5\n")
        missing = self.default_config.with_name("missing.toml")
        args = argparse.Namespace(model_profile=None, config=missing)
        with patch.object(run_local_demo.subprocess, "Popen") as start:
            with self.assertRaisesRegex(run_local_demo.DemoError, "config file not found"):
                run_local_demo.run_demo(args)
        start.assert_not_called()

    def test_profile_start_passes_launcher_source_root_from_any_cwd(self):
        args = argparse.Namespace(model_profile="qwen", config=None)
        simulator, agent = Mock(), Mock()
        simulator.poll.return_value = None
        agent.poll.return_value = None
        started = []

        def start(command, **kwargs):
            started.append((command, kwargs["env"]))
            return simulator if command[2] == "lefly_simulator" else agent

        with TemporaryDirectory() as directory, chdir(directory), patch.dict(
            os.environ, {"LEFLY_LLM_API_KEY": "test-only"}
        ), patch.object(run_local_demo, "ensure_port_available"), patch.object(
            run_local_demo.subprocess, "Popen", side_effect=start
        ), patch.object(run_local_demo, "wait_healthy"), patch.object(
            run_local_demo.time, "sleep", side_effect=KeyboardInterrupt
        ):
            self.assertEqual(run_local_demo.run_demo(args), 0)

        self.assertEqual(
            started[1][1].get("LEFLY_PROFILE_SOURCE_ROOT"),
            str(Path(run_local_demo.__file__).resolve().parents[1]),
        )

    def test_profile_requires_key_before_starting_children(self):
        args = argparse.Namespace(model_profile="deepseek", config=None)
        with patch.dict(os.environ, {"LEFLY_LLM_API_KEY": ""}), patch.object(
            run_local_demo.subprocess, "Popen"
        ) as start:
            with self.assertRaisesRegex(run_local_demo.DemoError, "LEFLY_LLM_API_KEY"):
                run_local_demo.run_demo(args)
        start.assert_not_called()

    def test_default_is_offline_and_starts_simulator_before_agent(self):
        args = argparse.Namespace(model_profile=None, config=None)
        simulator = Mock()
        simulator.poll.return_value = None
        agent = Mock()
        agent.poll.return_value = None
        events = []

        def start(command, **kwargs):
            events.append(("start", command[2], kwargs["env"].get("LEFLY_LLM_API_KEY")))
            return simulator if command[2] == "lefly_simulator" else agent

        def ready(service, process):
            events.append(("ready", service, None))

        with patch.dict(os.environ, {"LEFLY_LLM_API_KEY": "secret"}), patch.object(
            run_local_demo, "ensure_port_available"
        ), patch.object(run_local_demo.subprocess, "Popen", side_effect=start), patch.object(
            run_local_demo, "wait_healthy", side_effect=ready
        ), patch.object(run_local_demo.time, "sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(run_local_demo.run_demo(args), 0)

        self.assertEqual(
            events[:4],
            [
                ("start", "lefly_simulator", None),
                ("ready", "lefly-simulator", None),
                ("start", "lefly_agent", None),
                ("ready", "lefly-agent", None),
            ],
        )
        agent.terminate.assert_called_once()
        simulator.terminate.assert_called_once()

    def test_busy_port_is_named(self):
        with patch.object(run_local_demo.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError("in use")
            with self.assertRaisesRegex(run_local_demo.DemoError, "Simulator.*port"):
                run_local_demo.ensure_port_available("Simulator", 8766)

    def test_recently_closed_connection_does_not_block_restart(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen()
            with socket.create_connection(("127.0.0.1", port)) as client:
                with listener.accept()[0]:
                    pass
                self.assertEqual(client.recv(1), b"")
        run_local_demo.ensure_port_available("Simulator", port)

    def test_active_listener_still_blocks_restart(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(run_local_demo.DemoError, "Simulator.*occupied"):
                run_local_demo.ensure_port_available("Simulator", port)

    def test_agent_readiness_failure_stops_both_children(self):
        args = argparse.Namespace(model_profile=None, config=None)
        children = [Mock(), Mock()]
        for child in children:
            child.poll.return_value = None
        with patch.object(run_local_demo, "ensure_port_available"), patch.object(
            run_local_demo.subprocess, "Popen", side_effect=children
        ), patch.object(
            run_local_demo, "wait_healthy", side_effect=[None, run_local_demo.DemoError("Agent timeout")]
        ):
            with self.assertRaisesRegex(run_local_demo.DemoError, "Agent timeout"):
                run_local_demo.run_demo(args)
        for child in children:
            child.terminate.assert_called_once()

    def test_health_wait_reports_early_exit_and_timeout(self):
        child = Mock()
        child.poll.return_value = 1
        with self.assertRaisesRegex(run_local_demo.DemoError, "Simulator exited"):
            run_local_demo.wait_healthy("lefly-simulator", child)

        child.poll.return_value = None
        with patch.object(run_local_demo, "probe_health", return_value=False), patch.object(
            run_local_demo.time, "monotonic", side_effect=[0.0, 0.0, 1.0]
        ), patch.object(run_local_demo.time, "sleep"):
            with self.assertRaisesRegex(run_local_demo.DemoError, "Simulator.*healthy"):
                run_local_demo.wait_healthy("lefly-simulator", child, timeout=1.0)

    def test_started_service_exit_stops_peer(self):
        args = argparse.Namespace(model_profile=None, config=None)
        simulator, agent = Mock(), Mock()
        simulator.poll.side_effect = [None, None, 1, 1]
        agent.poll.return_value = None
        with patch.object(run_local_demo, "ensure_port_available"), patch.object(
            run_local_demo.subprocess, "Popen", side_effect=[simulator, agent]
        ), patch.object(run_local_demo, "wait_healthy"):
            with self.assertRaisesRegex(run_local_demo.DemoError, "Simulator exited"):
                run_local_demo.run_demo(args)
        agent.terminate.assert_called_once()
