from __future__ import annotations

import logging
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lefly_agent.logging_setup import configure_debug_logging


class DebugLoggingTests(unittest.TestCase):
    def test_disabled_mode_does_not_create_a_log_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)

            session = configure_debug_logging(False, working_directory=root)

            self.assertIsNone(session)
            self.assertFalse((root / "logs").exists())

    def test_enabled_mode_creates_unique_session_file_and_writes_debug(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = configure_debug_logging(
                True,
                working_directory=root,
                now=datetime(2026, 8, 22, 19, 30, 0),
                pid=12345,
            )
            self.addCleanup(session.close)

            logging.getLogger("lefly_agent.test").debug(
                "agent.debug.test stage=ready"
            )
            for handler in session.handlers:
                handler.flush()

            self.assertTrue(session.path.is_absolute())
            self.assertEqual(
                session.path.name,
                "lefly-agent.20260822-193000.12345.log",
            )
            rendered = session.path.read_text(encoding="utf-8")
            self.assertIn("DEBUG", rendered)
            self.assertIn("lefly_agent.test", rendered)
            self.assertIn("agent.debug.test stage=ready", rendered)

    def test_formatter_renders_only_allowlisted_structured_metadata(self):
        with TemporaryDirectory() as directory:
            session = configure_debug_logging(
                True,
                working_directory=Path(directory),
                now=datetime(2026, 8, 22, 19, 30, 30),
                pid=12347,
            )
            self.addCleanup(session.close)

            logging.getLogger("lefly_agent.telemetry").info(
                "agent.latency.stage",
                extra={
                    "lefly_latency": {
                        "stage": "route_decided",
                        "total_elapsed_ms": 25.0,
                    },
                    "api_key": "must-not-be-rendered",
                },
            )
            for handler in session.handlers:
                handler.flush()
            rendered = session.path.read_text(encoding="utf-8")

        self.assertIn('"stage":"route_decided"', rendered)
        self.assertIn('"total_elapsed_ms":25.0', rendered)
        self.assertNotIn("api_key", rendered)
        self.assertNotIn("must-not-be-rendered", rendered)

    def test_close_restores_logger_state_and_removes_owned_handlers(self):
        root_logger = logging.getLogger()
        package_logger = logging.getLogger("lefly_agent")
        original_root_level = root_logger.level
        original_package_level = package_logger.level
        original_propagate = package_logger.propagate

        with TemporaryDirectory() as directory:
            session = configure_debug_logging(
                True,
                working_directory=Path(directory),
                now=datetime(2026, 8, 22, 19, 31, 0),
                pid=12346,
            )
            owned_handlers = session.handlers

            session.close()
            session.close()

        self.assertEqual(root_logger.level, original_root_level)
        self.assertEqual(package_logger.level, original_package_level)
        self.assertEqual(package_logger.propagate, original_propagate)
        for handler in owned_handlers:
            self.assertNotIn(handler, root_logger.handlers)

    def test_file_creation_failure_falls_back_without_raising(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").write_text("not a directory", encoding="utf-8")

            with patch("builtins.print") as output:
                session = configure_debug_logging(True, working_directory=root)

            self.assertIsNone(session)
            self.assertTrue(output.called)
            self.assertIn("debug log unavailable", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
