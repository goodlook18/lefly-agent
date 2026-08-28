import asyncio
import json
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from lefly_protocol import DeviceCommand, DeviceEvent
from lefly_sdk import (
    ClientClosedError,
    DeviceDisconnectedError,
    RemoteDeviceError,
    RequestTimeoutError,
)
from lefly_simulator import (
    ControlLease,
    ManualClock,
    SimulatorEngine,
    SimulatorState,
    SimulatorTarget,
    TargetClosedError,
    TargetRouter,
)
from lefly_simulator.server import (
    APP_RUNTIME,
    APP_STATIC_DIR,
    OutboundQueue,
    _command_exception_error,
    _console_error,
    _console_request_id,
    _is_reserved_path,
    _resource_response,
    _resolve_asset_path,
    create_app,
)


COMMAND_1 = "10000000-0000-4000-8000-000000000001"


def command(message_id=COMMAND_1, message_type="device.get_state", payload=None):
    return DeviceCommand(
        message_id=message_id,
        message_type=message_type,
        timestamp="2026-08-17T08:00:00.000Z",
        payload={} if payload is None else payload,
        device_id="simulator",
    )


class FakeRemoteTarget:
    kind = "remote"

    def __init__(self, target_id="remote"):
        self.target_id = target_id
        self.device_id = "remote-device"
        self.is_connected = False
        self.status = "offline"
        self._handlers = []
        self.starts = 0
        self.closes = 0
        self.command_errors = []
        self._snapshot = SimulatorState.default(self.device_id).observe()

    async def start(self):
        self.starts += 1
        self.is_connected = True
        self.status = "ready"

    async def close(self):
        self.closes += 1
        self.is_connected = False
        self.status = "closed"

    async def command(self, item):
        if self.command_errors:
            raise self.command_errors.pop(0)
        return DeviceEvent(
            message_id="20000000-0000-4000-8000-000000000090",
            message_type="command.accepted",
            timestamp=item.timestamp,
            payload={
                "command_type": item.message_type,
                "disposition": "applied",
            },
            device_id=self.device_id,
            correlation_id=item.message_id,
        )

    def subscribe(self, handler):
        self._handlers.append(handler)

        def unsubscribe():
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def snapshot(self):
        import copy

        result = copy.deepcopy(self._snapshot)
        result["connection"] = (
            self.status if self.status in {"ready", "degraded", "offline"} else "offline"
        )
        return result

    async def emit(self, item):
        for handler in tuple(self._handlers):
            result = handler(item)
            if asyncio.iscoroutine(result):
                await result


class FailingRouter:
    def __init__(self):
        self.closed = 0

    async def start(self):
        raise RuntimeError("startup failed")

    async def close(self):
        self.closed += 1


class OutboundQueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_queue_closes_only_slow_client(self):
        closed = []
        queue = OutboundQueue(1, lambda: closed.append(True))

        self.assertTrue(queue.offer({"type": "first"}))
        self.assertFalse(queue.offer({"type": "second"}))
        await asyncio.sleep(0)

        self.assertEqual(closed, [True])

    async def test_full_queue_awaits_async_close_callback(self):
        closed = asyncio.Event()

        async def close():
            closed.set()

        queue = OutboundQueue(1, close)
        queue.offer({"type": "first"})
        self.assertFalse(queue.offer({"type": "second"}))

        await asyncio.wait_for(closed.wait(), 0.5)


class StaticPathSafetyTest(unittest.TestCase):
    def test_asset_path_rejects_encoded_segments_and_unsafe_characters(self):
        static_dir = Path("/tmp/lefly-static")
        invalid = (
            "/assets/%2e%2e/index.html",
            "/assets/%252e%252e/index.html",
            "/assets/%2e/app.js",
            "/assets/%5c..%5cindex.html",
            "/assets/app%00.js",
        )

        for raw_path in invalid:
            with self.subTest(raw_path=raw_path):
                with self.assertRaises(ValueError):
                    _resolve_asset_path(static_dir, raw_path, raw_path.lstrip("/"))

    def test_asset_path_resolves_beneath_assets_root(self):
        static_dir = Path("/tmp/lefly-static")

        resolved = _resolve_asset_path(
            static_dir, "/assets/app.abc123.js", "assets/app.abc123.js"
        )

        self.assertEqual(
            resolved, (static_dir / "assets" / "app.abc123.js").resolve()
        )

    def test_reserved_paths_are_detected_at_every_decode_layer(self):
        for raw_path in (
            "/api/missing",
            "/api%2fmissing",
            "/api%252fmissing",
            "/ws%252fmissing",
        ):
            with self.subTest(raw_path=raw_path):
                self.assertTrue(_is_reserved_path(raw_path, raw_path.lstrip("/")))

        self.assertFalse(_is_reserved_path("/API%252fmissing", "API%2fmissing"))
        with self.assertRaises(ValueError):
            _is_reserved_path("/api%00missing", "api\x00missing")
        over_encoded = "/api%2fmissing"
        for _ in range(8):
            over_encoded = over_encoded.replace("%", "%25")
        with self.assertRaises(ValueError):
            _is_reserved_path(over_encoded, over_encoded.lstrip("/"))


class FakeTraversable:
    def __init__(self, files, parts=()):
        self._files = files
        self._parts = parts

    def joinpath(self, *parts):
        return FakeTraversable(self._files, self._parts + tuple(parts))

    def is_file(self):
        return self._parts in self._files

    def read_bytes(self):
        return self._files[self._parts]

    @property
    def name(self):
        return self._parts[-1] if self._parts else ""


class ResourceProviderTest(unittest.TestCase):
    def test_default_static_traversable_contains_production_build(self):
        app = create_app(router=object())
        static = app[APP_STATIC_DIR]
        index = static.joinpath("index.html")

        self.assertTrue(index.is_file())
        html = index.read_bytes().decode("utf-8")
        asset_paths = set(
            part.split('"', 1)[0]
            for part in html.split('/assets/')[1:]
        )
        self.assertGreaterEqual(len(asset_paths), 2)
        for asset_path in asset_paths:
            with self.subTest(asset_path=asset_path):
                self.assertRegex(asset_path, r".+-[A-Za-z0-9_-]+\.(?:css|js)$")
                self.assertTrue(static.joinpath("assets", asset_path).is_file())

    def test_default_static_uses_package_traversable(self):
        package = FakeTraversable({("static", "index.html"): b"console"})

        with patch("lefly_simulator.server.resources.files", return_value=package):
            app = create_app(router=object())

        self.assertIsInstance(app[APP_STATIC_DIR], FakeTraversable)
        self.assertEqual(app[APP_STATIC_DIR]._parts, ("static",))

    def test_traversable_resources_are_read_without_filesystem_paths(self):
        static = FakeTraversable(
            {
                ("index.html",): b"<h1>zip console</h1>",
                ("assets", "app.js"): b"console.log('zip')",
            }
        )

        index = _resource_response(static, ("index.html",))
        asset = _resource_response(static, ("assets", "app.js"))

        self.assertEqual(index.body, b"<h1>zip console</h1>")
        self.assertEqual(asset.body, b"console.log('zip')")


class CommandErrorMappingTest(unittest.TestCase):
    def test_console_error_request_id_is_optional_and_backward_compatible(self):
        class Hub:
            def __init__(self):
                self.values = []

            def offer(self, client, value):
                self.values.append((client, value))

        hub = Hub()
        client = object()
        _console_error(hub, client, "first", "without request")
        _console_error(
            hub,
            client,
            "second",
            "with request",
            request_id=COMMAND_1,
        )

        self.assertNotIn("request_id", hub.values[0][1])
        self.assertEqual(hub.values[1][1]["request_id"], COMMAND_1)

    def test_console_request_id_is_only_extracted_from_safe_command_shapes(self):
        self.assertEqual(
            _console_request_id(
                {"type": "console.command", "command": {"id": COMMAND_1}}
            ),
            COMMAND_1,
        )
        for value in (
            {"type": "console.select_target", "command": {"id": COMMAND_1}},
            {"type": "console.command", "command": None},
            {"type": "console.command", "command": {"id": ""}},
            {"type": "console.command", "command": {"id": 1}},
        ):
            with self.subTest(value=value):
                self.assertIsNone(_console_request_id(value))

    def test_sdk_and_target_errors_have_stable_console_codes(self):
        errors = (
            (RemoteDeviceError("motor_blocked", "joint blocked", False), "motor_blocked", False),
            (RequestTimeoutError("late"), "request_timeout", True),
            (DeviceDisconnectedError("gone"), "device_disconnected", True),
            (ClientClosedError("closed"), "client_closed", False),
            (TargetClosedError("closed"), "target_closed", False),
        )

        for error, code, recoverable in errors:
            with self.subTest(error=type(error).__name__):
                mapped = _command_exception_error(error)
                self.assertEqual(mapped.code, code)
                self.assertEqual(mapped.recoverable, recoverable)


class RuntimeStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_app_lifecycle_updates_mutable_runtime_without_warning(self):
        engine = SimulatorEngine("simulator", clock=ManualClock())
        router = TargetRouter([SimulatorTarget("simulator", engine)])
        app = create_app(router=router, static_dir=Path("/missing"))
        app.freeze()

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            await app.startup()
            self.assertTrue(app[APP_RUNTIME].started)
            await app.cleanup()
            self.assertFalse(app[APP_RUNTIME].started)


class ServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = SimulatorEngine("simulator", clock=ManualClock())
        self.simulator = SimulatorTarget("simulator", self.engine)
        self.remote = FakeRemoteTarget()
        self.router = TargetRouter([self.simulator, self.remote])
        self.temp = tempfile.TemporaryDirectory()
        self.static = Path(self.temp.name)
        (self.static / "assets").mkdir()
        (self.static / "index.html").write_text("<h1>console</h1>", encoding="utf-8")
        (self.static / "assets" / "app.abc123.js").write_text(
            "console.log('ok')", encoding="utf-8"
        )
        self.app = create_app(router=self.router, static_dir=self.static)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def receive_json(self, ws, timeout=0.5):
        message = await asyncio.wait_for(ws.receive(), timeout)
        self.assertEqual(message.type, WSMsgType.TEXT)
        return json.loads(message.data)

    async def receive_type(self, ws, message_type, timeout=0.5):
        async def find_message():
            while True:
                value = await self.receive_json(ws, timeout)
                if value.get("type") == message_type:
                    return value

        return await asyncio.wait_for(find_message(), timeout)

    async def test_health_targets_and_lifecycle_hide_connection_details(self):
        health = await (await self.client.get("/health")).json()
        targets = await (await self.client.get("/api/targets")).json()

        self.assertEqual(health["ok"], True)
        self.assertEqual(health["service"], "lefly-simulator")
        self.assertTrue(health["started"])
        self.assertEqual(health["target"]["id"], "simulator")
        self.assertEqual([item["id"] for item in targets["targets"]], ["simulator", "remote"])
        self.assertTrue(targets["targets"][0]["active"])
        self.assertNotIn("url", json.dumps(targets))
        self.assertNotIn("token", json.dumps(targets))
        self.assertEqual(self.remote.starts, 1)

    async def test_console_lease_takeover_and_validation(self):
        first = await self.client.ws_connect("/ws/console")
        second = await self.client.ws_connect("/ws/console")
        hello1 = await self.receive_json(first)
        hello2 = await self.receive_json(second)

        self.assertEqual(hello1["type"], "console.hello")
        self.assertEqual(hello1["lease"]["role"], "controller")
        self.assertNotIn("token", hello1["lease"])
        self.assertEqual(hello2["lease"], {"role": "readonly"})
        self.assertNotEqual(hello1["session_id"], hello2["session_id"])

        await second.send_json({"type": "console.command", "target_epoch": 1, "command": command().to_dict()})
        read_only = await self.receive_json(second)
        self.assertEqual(read_only["code"], "read_only")
        self.assertEqual(read_only["request_id"], COMMAND_1)
        await second.send_str("not-json")
        self.assertEqual((await self.receive_json(second))["code"], "invalid_json")
        await second.send_bytes(b"binary")
        self.assertEqual((await self.receive_json(second))["code"], "invalid_message")
        await second.send_json({"type": "console.nope"})
        self.assertEqual((await self.receive_json(second))["code"], "unknown_message")

        await first.send_json({"type": "console.command", "target_epoch": 999, "command": command().to_dict()})
        stale = await self.receive_json(first)
        self.assertEqual(stale["code"], "stale_target_epoch")
        self.assertEqual(stale["request_id"], COMMAND_1)
        await first.send_json({"type": "console.command", "target_epoch": 1, "command": {**command().to_dict(), "target_epoch": 1}})
        invalid = await self.receive_json(first)
        self.assertEqual(invalid["code"], "invalid_command")
        self.assertEqual(invalid["request_id"], COMMAND_1)

        await second.send_json({"type": "console.acquire_control"})
        displaced = await self.receive_type(first, "console.control")
        self.assertEqual(displaced["lease"], {"role": "readonly"})
        acquired = await self.receive_type(second, "console.control")
        self.assertEqual(acquired["lease"]["role"], "controller")
        self.assertNotIn("token", acquired["lease"])

        await first.send_json({"type": "console.command", "target_epoch": 1, "command": command().to_dict()})
        displaced_error = await self.receive_json(first)
        self.assertEqual(displaced_error["code"], "read_only")
        await second.send_json({"type": "console.renew_control"})
        renewed = await self.receive_type(second, "console.control")
        self.assertEqual(renewed["lease"]["role"], "controller")
        self.assertNotIn("token", renewed["lease"])
        await first.close()
        await second.close()

    async def test_console_lease_expiry_uses_the_router_injected_clock(self):
        engine = SimulatorEngine("clocked-simulator", clock=ManualClock())
        simulator = SimulatorTarget("clocked-simulator", engine)
        lease = ControlLease(
            15,
            monotonic=lambda: 100.0,
            token_factory=lambda: "server-private-token",
        )
        router = TargetRouter([simulator], lease=lease)
        app = create_app(router=router, static_dir=self.static)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            before = time.time()
            websocket = await client.ws_connect("/ws/console")
            hello = await self.receive_json(websocket)
            after = time.time()

            self.assertNotIn("token", hello["lease"])
            self.assertGreaterEqual(hello["lease"]["expires_at"], before + 15)
            self.assertLessEqual(hello["lease"]["expires_at"], after + 15)
            await websocket.close()
        finally:
            await client.close()

    async def test_valid_command_and_target_switch_fanout(self):
        controller = await self.client.ws_connect("/ws/console")
        observer = await self.client.ws_connect("/ws/console")
        await self.receive_json(controller)
        await self.receive_json(observer)

        await controller.send_json({"type": "console.command", "target_epoch": 1, "command": command().to_dict()})
        ack1 = await self.receive_type(controller, "console.event")
        ack2 = await self.receive_type(observer, "console.event")
        self.assertEqual(ack1["event"]["type"], "command.accepted")
        self.assertEqual(ack2["event"]["correlation_id"], COMMAND_1)

        await controller.send_json({"type": "console.select_target", "target_id": "remote"})
        state1 = await self.receive_type(controller, "console.state")
        state2 = await self.receive_type(observer, "console.state")
        self.assertEqual(state1["target_id"], "remote")
        self.assertEqual(state1["target_epoch"], 2)
        self.assertEqual(state2["target_epoch"], 2)
        await controller.close()
        await observer.close()

    async def test_revision_gap_is_recoverable_and_reconnect_hello_has_resynced_state(self):
        websocket = await self.client.ws_connect("/ws/console")
        await self.receive_json(websocket)
        await websocket.send_json({"type": "console.select_target", "target_id": "remote"})
        await self.receive_type(websocket, "console.state")

        gap_snapshot = self.remote.snapshot()
        gap_snapshot["revision"] = 5
        gap_snapshot["light"]["brightness"] = 0.2
        await self.remote.emit(
            DeviceEvent(
                message_id="20000000-0000-4000-8000-000000000091",
                message_type="device.state_changed",
                timestamp="2026-08-17T08:00:00.100Z",
                payload=gap_snapshot,
                device_id="remote-device",
            )
        )
        gap = await self.receive_type(websocket, "console.event")
        self.assertEqual(gap["error"]["code"], "revision_gap")
        self.assertTrue(gap["error"]["recoverable"])

        self.remote._snapshot["revision"] = 5
        self.remote._snapshot["light"]["brightness"] = 0.75
        await self.remote.emit(
            DeviceEvent(
                message_id="20000000-0000-4000-8000-000000000092",
                message_type="device.state_changed",
                timestamp="2026-08-17T08:00:01.000Z",
                payload=self.remote.snapshot(),
                device_id="remote-device",
            )
        )
        await self.receive_type(websocket, "console.event")
        await websocket.close()

        reconnected = await self.client.ws_connect("/ws/console")
        hello = await self.receive_json(reconnected)
        self.assertEqual(hello["state"]["revision"], 5)
        self.assertEqual(hello["state"]["light"]["brightness"], 0.75)
        await reconnected.close()

    async def test_remote_command_errors_do_not_close_console_socket(self):
        controller = await self.client.ws_connect("/ws/console")
        await self.receive_json(controller)
        await controller.send_json(
            {"type": "console.select_target", "target_id": "remote"}
        )
        await self.receive_type(controller, "console.state")
        remote_command = command().to_dict()
        remote_command["device_id"] = "remote-device"

        self.remote.command_errors.extend(
            [
                RemoteDeviceError("motor_blocked", "joint blocked", False),
                RequestTimeoutError("late"),
                RuntimeError("unexpected"),
            ]
        )
        expected = ("motor_blocked", "request_timeout", "internal_error")
        with self.assertLogs("lefly_simulator.server", level="ERROR") as logs:
            for code in expected:
                await controller.send_json(
                    {
                        "type": "console.command",
                        "target_epoch": 2,
                        "command": remote_command,
                    }
                )
                error = await self.receive_json(controller)
                self.assertEqual(error["code"], code)
                self.assertEqual(error["request_id"], remote_command["id"])
                await controller.send_json({"type": "console.renew_control"})
                renewed = await self.receive_type(controller, "console.control")
                self.assertEqual(renewed["lease"]["role"], "controller")

        self.assertEqual(len(logs.records), 1)
        await controller.close()

    async def test_sensor_injection_requires_virtual_controller(self):
        controller = await self.client.ws_connect("/ws/console")
        observer = await self.client.ws_connect("/ws/console")
        await self.receive_json(controller)
        await self.receive_json(observer)

        await observer.send_json({"type": "console.inject_sensor", "sensor_type": "touch", "payload": {"position": "left"}})
        self.assertEqual((await self.receive_json(observer))["code"], "read_only")
        await controller.send_json({"type": "console.inject_sensor", "sensor_type": "touch", "payload": {"position": "left"}})
        injected = await self.receive_type(controller, "console.event")
        self.assertEqual(injected["event"]["type"], "sensor.touch")
        self.assertEqual(injected["event"]["device_id"], "simulator")
        self.assertEqual(
            injected["event"]["payload"], {"position": "left", "pressed": True}
        )

        await controller.send_json({"type": "console.inject_sensor", "sensor_type": "touch", "payload": {"position": "head"}})
        self.assertEqual((await self.receive_json(controller))["code"], "invalid_sensor")
        await controller.send_json({"type": "console.inject_sensor", "sensor_type": "gesture", "payload": {"id": True}})
        self.assertEqual((await self.receive_json(controller))["code"], "invalid_sensor")
        await controller.send_json({"type": "console.inject_sensor", "sensor_type": "face", "payload": {"label": ""}})
        self.assertEqual((await self.receive_json(controller))["code"], "invalid_sensor")

        await controller.send_json({"type": "console.inject_sensor", "sensor_type": "gesture", "payload": {"id": 7, "label": "wave"}})
        gesture = await self.receive_type(controller, "console.event")
        self.assertEqual(gesture["event"]["type"], "sensor.vision.gesture")
        await controller.send_json({"type": "console.inject_sensor", "sensor_type": "face", "payload": {"id": 3, "label": "known"}})
        face = await self.receive_type(controller, "console.event")
        self.assertEqual(face["event"]["type"], "sensor.vision.face")

        await controller.send_json({"type": "console.select_target", "target_id": "remote"})
        await self.receive_type(controller, "console.state")
        await controller.send_json({"type": "console.inject_sensor", "sensor_type": "gesture", "payload": {"id": 8, "label": "wave"}})
        self.assertEqual((await self.receive_json(controller))["code"], "sensor_injection_unavailable")
        await controller.close()
        await observer.close()

    async def test_device_endpoint_is_strict_and_does_not_duplicate_ack(self):
        ws = await self.client.ws_connect("/ws/device/simulator")
        await ws.send_str(command().to_json())
        accepted = await self.receive_json(ws)
        changed = await self.receive_json(ws)
        self.assertEqual([accepted["type"], changed["type"]], ["command.accepted", "device.state_changed"])

        await ws.send_str("not-json")
        error = await self.receive_json(ws)
        self.assertEqual(error["type"], "device.error")
        self.assertIsNone(error.get("correlation_id"))
        await ws.send_bytes(b"binary")
        self.assertEqual((await self.receive_json(ws))["type"], "device.error")
        await ws.send_json({"type": "console.acquire_control"})
        self.assertEqual((await self.receive_json(ws))["type"], "device.error")
        await ws.close()

    async def test_static_fallback_api_404_and_traversal(self):
        self.assertEqual((await self.client.get("/")).status, 200)
        asset = await self.client.get("/assets/app.abc123.js")
        self.assertEqual(asset.status, 200)
        fallback = await self.client.get("/motion/developer")
        self.assertEqual(await fallback.text(), "<h1>console</h1>")
        api = await self.client.get("/api/missing")
        self.assertEqual(api.status, 404)
        self.assertEqual((await api.json())["error"]["code"], "not_found")
        self.assertEqual((await self.client.get("/ws/missing")).status, 404)
        base_url = str(self.client.make_url("/")).rstrip("/")
        unsafe_paths = (
            "/assets/%2e%2e/index.html",
            "/assets/%252e%252e/index.html",
            "/assets/%2e/app.abc123.js",
            "/assets/%5c..%5cindex.html",
            "/assets/app%00.js",
        )
        for path in unsafe_paths:
            with self.subTest(path=path):
                async with self.client.session.get(
                    URL(base_url + path, encoded=True)
                ) as response:
                    self.assertEqual(response.status, 404)

        reserved_paths = (
            "/api%252fmissing",
            "/ws%252fmissing",
        )
        for path in reserved_paths:
            with self.subTest(path=path):
                async with self.client.session.get(
                    URL(base_url + path, encoded=True)
                ) as response:
                    self.assertEqual(response.status, 404)
                    self.assertEqual(
                        (await response.json())["error"]["code"], "not_found"
                    )


class LifecycleFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_startup_failure_rolls_back_router(self):
        router = FailingRouter()
        app = create_app(router=router)
        client = TestClient(TestServer(app))

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await client.start_server()
        self.assertEqual(router.closed, 1)
        await client.close()


class MissingStaticTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_build_returns_clear_503(self):
        engine = SimulatorEngine("simulator", clock=ManualClock())
        router = TargetRouter([SimulatorTarget("simulator", engine)])
        app = create_app(router=router, static_dir=Path("/definitely/missing"))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.get("/")
            self.assertEqual(response.status, 503)
            self.assertIn("frontend build", await response.text())
        finally:
            await client.close()
