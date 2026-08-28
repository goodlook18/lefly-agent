import asyncio
import json
import socket
import time
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from aiohttp import ClientSession, WSMsgType

from lefly_protocol import DeviceCommand

from tests.support.simulator_process import SimulatorProcess


DEVICE_ID = "lefly-sim-01"


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def command(message_type, payload):
    return DeviceCommand(
        message_id=str(uuid4()),
        message_type=message_type,
        timestamp=datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        device_id=DEVICE_ID,
        payload=payload,
    )


class RemoteTargetProcessIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        upstream_port = allocate_port()
        gateway_port = allocate_port()
        while gateway_port == upstream_port:
            gateway_port = allocate_port()
        self.upstream = SimulatorProcess(port=upstream_port)
        self.gateway = SimulatorProcess(
            port=gateway_port,
            remote_url=self.upstream.device_url,
        )
        self.upstream.start()
        self.gateway.start()
        self.session = ClientSession()
        self.websockets = []

    async def asyncTearDown(self):
        await asyncio.gather(
            *(websocket.close() for websocket in self.websockets),
            return_exceptions=True,
        )
        await self.session.close()
        await asyncio.to_thread(self.gateway.stop)
        await asyncio.to_thread(self.upstream.stop)

    async def receive(self, websocket, predicate, timeout=8.0):
        deadline = time.monotonic() + timeout
        seen = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail(
                    "timed out waiting for console message; seen=%r\n"
                    "gateway output:\n%s\nupstream output:\n%s"
                    % (seen[-8:], self.gateway.output, self.upstream.output)
                )
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                self.fail(
                    "timed out waiting for console frame; seen=%r\n"
                    "gateway output:\n%s\nupstream output:\n%s"
                    % (seen[-8:], self.gateway.output, self.upstream.output)
                )
            if message.type != WSMsgType.TEXT:
                self.fail("console socket closed before expected message: %r" % message)
            value = json.loads(message.data)
            seen.append(value)
            if predicate(value):
                return value

    async def target_summary(self, target_id):
        async with self.session.get(
            self.gateway.health_url.replace("/health", "/api/targets")
        ) as response:
            payload = await response.json()
        return next(item for item in payload["targets"] if item["id"] == target_id)

    async def wait_target_status(self, target_id, expected, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            summary = await self.target_summary(target_id)
            if summary["status"] in expected:
                return summary
            await asyncio.sleep(0.05)
        self.fail(
            "target %s did not reach %r\ngateway output:\n%s\nupstream output:\n%s"
            % (target_id, expected, self.gateway.output, self.upstream.output)
        )

    async def send_command(self, websocket, target_epoch, item):
        await websocket.send_json(
            {
                "type": "console.command",
                "target_epoch": target_epoch,
                "command": item.to_dict(),
            }
        )

    async def test_remote_loss_never_falls_back_and_recovery_resynchronizes(self):
        websocket = await self.session.ws_connect(self.gateway.console_url)
        self.websockets.append(websocket)
        hello = await self.receive(websocket, lambda value: value["type"] == "console.hello")
        self.assertEqual(hello["target_id"], "simulator")
        self.assertEqual(hello["target_epoch"], 1)

        await websocket.send_json(
            {"type": "console.select_target", "target_id": "remote"}
        )
        selected = await self.receive(
            websocket,
            lambda value: value["type"] == "console.state"
            and value["target_id"] == "remote",
        )
        self.assertEqual(selected["target_epoch"], 2)
        self.assertEqual(selected["state"]["connection"], "ready")

        move = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": -30}, "duration_ms": 80},
        )
        await self.send_command(websocket, 2, move)
        await self.receive(
            websocket,
            lambda value: value.get("type") == "console.event"
            and value.get("event", {}).get("type") == "motion.finished"
            and value["event"].get("correlation_id") == move.message_id,
        )

        await websocket.send_json(
            {"type": "console.select_target", "target_id": "simulator"}
        )
        local = await self.receive(
            websocket,
            lambda value: value["type"] == "console.state"
            and value["target_id"] == "simulator",
        )
        self.assertEqual(local["target_epoch"], 3)
        self.assertEqual(local["state"]["motion"]["joints"]["base_yaw"]["pos"], 0)

        await websocket.send_json(
            {"type": "console.select_target", "target_id": "remote"}
        )
        remote = await self.receive(
            websocket,
            lambda value: value["type"] == "console.state"
            and value["target_id"] == "remote",
        )
        self.assertEqual(remote["target_epoch"], 4)
        self.assertEqual(remote["state"]["motion"]["joints"]["base_yaw"]["pos"], -30)

        await asyncio.to_thread(self.upstream.stop)
        await self.wait_target_status("remote", {"disconnected", "connecting"})

        lost = command("device.get_state", {})
        await self.send_command(websocket, 4, lost)
        rejected = await self.receive(
            websocket,
            lambda value: value.get("type") == "console.error"
            and value.get("request_id") == lost.message_id,
        )
        self.assertIn(rejected["code"], {"device_disconnected", "outcome_unknown"})

        observer = await self.session.ws_connect(self.gateway.console_url)
        self.websockets.append(observer)
        offline = await self.receive(
            observer, lambda value: value["type"] == "console.hello"
        )
        self.assertEqual(offline["target_id"], "remote")
        self.assertIn(offline["state"]["connection"], {"offline", "degraded"})
        await observer.close()

        await asyncio.to_thread(self.upstream.restart)
        await self.wait_target_status("remote", {"connected"}, timeout=12.0)
        recovered = await self.receive(
            websocket,
            lambda value: value.get("type") == "console.event"
            and value.get("event", {}).get("type") == "device.state_changed"
            and value["event"]["payload"].get("connection") == "ready",
            timeout=12.0,
        )
        self.assertEqual(recovered["target_id"], "remote")
        self.assertEqual(
            recovered["event"]["payload"]["motion"]["joints"]["base_yaw"]["pos"],
            0,
        )

        after_recovery = command("device.get_state", {})
        await self.send_command(websocket, 4, after_recovery)
        await self.receive(
            websocket,
            lambda value: value.get("type") == "console.event"
            and value.get("event", {}).get("type") == "command.accepted"
            and value["event"].get("correlation_id") == after_recovery.message_id,
        )
        await websocket.close()


if __name__ == "__main__":
    unittest.main()
