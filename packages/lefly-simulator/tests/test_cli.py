import asyncio
from contextlib import redirect_stderr
from io import StringIO
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from lefly_simulator.__main__ import _create_application, build_parser, serve
from lefly_simulator.server import APP_ROUTER


class CliValidationTest(unittest.TestCase):
    def test_host_and_port_validation(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["--port", "0"]).port, 0)
        self.assertEqual(parser.parse_args(["--port", "65535"]).port, 65535)
        for arguments in (["--host", ""], ["--port", "-1"], ["--port", "65536"]):
            with self.subTest(arguments=arguments):
                with redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args(arguments)

    def test_quickstart_target_uses_the_agent_default_device_id(self):
        app = _create_application(None)
        target = app[APP_ROUTER].targets["simulator"]

        self.assertEqual(target.target_id, "simulator")
        self.assertEqual(target.device_id, "lefly-sim-01")

    def test_remote_quickstart_uses_the_agent_default_device_id(self):
        app = _create_application("ws://127.0.0.1:8767/ws/device/simulator")
        target = app[APP_ROUTER].targets["remote"]

        self.assertEqual(target.target_id, "remote")
        self.assertEqual(target.device_id, "lefly-sim-01")


class ServeTest(unittest.IsolatedAsyncioTestCase):
    async def test_port_zero_prints_actual_bound_url_after_start(self):
        runner = MagicMock()
        runner.setup = AsyncMock()
        runner.cleanup = AsyncMock()
        socket = MagicMock()
        socket.getsockname.return_value = ("127.0.0.1", 49152)
        site = MagicMock()
        site.start = AsyncMock()
        site._server.sockets = [socket]
        shutdown = asyncio.Event()
        shutdown.set()

        with patch("lefly_simulator.__main__.web.AppRunner", return_value=runner), patch(
            "lefly_simulator.__main__.web.TCPSite", return_value=site
        ), patch("builtins.print") as output:
            await serve("127.0.0.1", 0, None, shutdown_event=shutdown)

        site.start.assert_awaited_once()
        output.assert_called_once_with(
            "LeFly simulator: http://127.0.0.1:49152", flush=True
        )
        runner.cleanup.assert_awaited_once()

    async def test_bind_failure_does_not_print_success(self):
        runner = MagicMock()
        runner.setup = AsyncMock()
        runner.cleanup = AsyncMock()
        site = MagicMock()
        site.start = AsyncMock(side_effect=OSError("bind failed"))

        with patch("lefly_simulator.__main__.web.AppRunner", return_value=runner), patch(
            "lefly_simulator.__main__.web.TCPSite", return_value=site
        ), patch("builtins.print") as output:
            with self.assertRaisesRegex(OSError, "bind failed"):
                await serve("127.0.0.1", 8766, None)

        output.assert_not_called()
        runner.cleanup.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
