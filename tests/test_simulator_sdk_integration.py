import asyncio
import time
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from aiohttp.test_utils import TestServer

from lefly_protocol import COMMAND_TYPES, EVENT_TYPES, DeviceCommand
from lefly_sdk import DeviceClient, RemoteDeviceError, RemoteHardwareController
from lefly_simulator.engine import SimulatorEngine
from lefly_simulator.router import TargetRouter
from lefly_simulator.server import create_app
from lefly_simulator.target import SimulatorTarget


DEVICE_ID = "lefly-sim-01"


class SimulatorSdkIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        target = SimulatorTarget("simulator", SimulatorEngine(DEVICE_ID))
        self.server = TestServer(create_app(router=TargetRouter([target])))
        await self.server.start_server()

        http_url = str(self.server.make_url("/ws/device/simulator"))
        device_url = http_url.replace("http://", "ws://", 1)
        self.client = DeviceClient(
            device_url,
            request_timeout=2.0,
            reconnect_delay=0.05,
        )
        self.events = []
        self.event_ready = asyncio.Event()
        self.client.subscribe("*", self._record_event)
        self.controller = RemoteHardwareController(self.client, device_id=DEVICE_ID)
        await self.client.start()
        await self.client.wait_until_connected(timeout=2.0)

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    def _record_event(self, event):
        self.events.append(event)
        self.event_ready.set()

    async def wait_event(
        self, message_type, correlation_id=None, predicate=None, *, latest=False
    ):
        deadline = time.monotonic() + 2.0
        while True:
            indexes = (
                range(len(self.events) - 1, -1, -1)
                if latest
                else range(len(self.events))
            )
            for index in indexes:
                event = self.events[index]
                if event.message_type != message_type:
                    continue
                if correlation_id is not None and event.correlation_id != correlation_id:
                    continue
                if predicate is not None and not predicate(event):
                    continue
                return self.events.pop(index)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail(
                    "timed out waiting for %s correlated to %s"
                    % (message_type, correlation_id)
                )
            self.event_ready.clear()
            await asyncio.wait_for(self.event_ready.wait(), timeout=remaining)

    async def wait_motion(self, acknowledgement):
        correlation_id = acknowledgement.correlation_id
        started = await self.wait_event("motion.started", correlation_id)
        progress = await self.wait_event("motion.progress", correlation_id)
        finished = await self.wait_event("motion.finished", correlation_id)
        finished_joints = finished.payload["joints"]
        state = await self.wait_event(
            "device.state_changed",
            correlation_id,
            lambda event: (
                event.payload["motion"]["state"] == "idle"
                and {
                    name: joint["pos"]
                    for name, joint in event.payload["motion"]["joints"].items()
                }
                == finished_joints
            ),
            latest=True,
        )
        self.assertEqual(finished.payload["status"], "completed")
        return started, progress, finished, state

    def assert_acknowledgement(self, acknowledgement, command_type):
        self.assertEqual(acknowledgement.message_type, "command.accepted")
        self.assertIsNotNone(acknowledgement.correlation_id)
        self.assertEqual(acknowledgement.payload["command_type"], command_type)

    async def test_public_command_matrix(self):
        revisions = []

        acknowledgement = await self.controller.play_movement("nod")
        self.assert_acknowledgement(acknowledgement, "motion.play")
        _, progress, _, state = await self.wait_motion(acknowledgement)
        self.assertIn("wrist_pitch", progress.payload["joints"])
        self.assertEqual(state.payload["motion"]["joints"]["wrist_pitch"]["pos"], 18)
        revisions.append(state.payload["revision"])

        acknowledgement = await self.controller.play_relative_movement(
            {"base_yaw": 10}, duration_ms=80
        )
        self.assert_acknowledgement(acknowledgement, "motion.relative_move")
        _, _, _, state = await self.wait_motion(acknowledgement)
        self.assertEqual(state.payload["motion"]["joints"]["base_yaw"]["pos"], 10)
        self.assertEqual(len(state.payload["motion"]["joints"]), 5)
        revisions.append(state.payload["revision"])

        acknowledgement = await self.controller.play_absolute_move(
            {"base_yaw": -20}, duration_ms=80
        )
        self.assert_acknowledgement(acknowledgement, "motion.absolute_move")
        _, _, _, state = await self.wait_motion(acknowledgement)
        self.assertEqual(state.payload["motion"]["joints"]["base_yaw"]["pos"], -20)
        revisions.append(state.payload["revision"])

        acknowledgement = await self.controller.set_light_color("cyan")
        self.assert_acknowledgement(acknowledgement, "light.solid")
        state = await self.wait_event(
            "device.state_changed", acknowledgement.correlation_id
        )
        self.assertEqual(set(state.payload["light"]["pixels"]), {"#00FFFF"})
        self.assertEqual(len(state.payload["light"]["pixels"]), 64)
        revisions.append(state.payload["revision"])

        rgb_frame = [
            [255, 0, 0] if index % 2 == 0 else [0, 0, 255]
            for index in range(64)
        ]
        expected_frame = [
            "#FF0000" if index % 2 == 0 else "#0000FF"
            for index in range(64)
        ]
        acknowledgement = await self.controller.paint_rgb_pattern(rgb_frame)
        self.assert_acknowledgement(acknowledgement, "light.paint")
        state = await self.wait_event(
            "device.state_changed", acknowledgement.correlation_id
        )
        self.assertEqual(list(state.payload["light"]["pixels"]), expected_frame)
        revisions.append(state.payload["revision"])

        acknowledgement = await self.controller.set_light_brightness(0.37)
        self.assert_acknowledgement(acknowledgement, "light.brightness")
        state = await self.wait_event(
            "device.state_changed", acknowledgement.correlation_id
        )
        self.assertEqual(state.payload["light"]["brightness"], 0.37)
        revisions.append(state.payload["revision"])

        acknowledgement = await self.controller.set_status("speaking")
        self.assert_acknowledgement(acknowledgement, "status.set")
        state = await self.wait_event(
            "device.state_changed", acknowledgement.correlation_id
        )
        self.assertEqual(state.payload["status"], {"mode": "speaking"})
        self.assertEqual(
            state.payload["status_strip"],
            {"color": "#2F9D68", "effect": "level_sweep"},
        )
        revisions.append(state.payload["revision"])

        acknowledgement = await self.controller.enter_rest_state()
        self.assert_acknowledgement(acknowledgement, "device.rest")
        _, _, _, state = await self.wait_motion(acknowledgement)
        self.assertEqual(state.payload["status"], {"mode": "resting"})
        self.assertEqual(
            state.payload["status_strip"],
            {"color": "#FFD33D", "effect": "breath"},
        )
        self.assertEqual(
            {name: joint["pos"] for name, joint in state.payload["motion"]["joints"].items()},
            {
                "base_yaw": 0,
                "base_pitch": -45,
                "elbow_pitch": 105,
                "wrist_roll": 0,
                "wrist_pitch": 45,
            },
        )
        revisions.append(state.payload["revision"])

        acknowledgement = await self.controller.get_state()
        self.assert_acknowledgement(acknowledgement, "device.get_state")
        state = await self.wait_event(
            "device.state_changed", acknowledgement.correlation_id
        )
        self.assertEqual(state.payload["device_id"], DEVICE_ID)
        self.assertEqual(
            set(state.payload["capabilities"]["commands"]), set(COMMAND_TYPES)
        )
        self.assertEqual(
            set(state.payload["capabilities"]["events"]), set(EVENT_TYPES)
        )
        revisions.append(state.payload["revision"])

        self.assertEqual(revisions, sorted(revisions))
        self.assertEqual(len(revisions), len(set(revisions)))

    async def test_public_client_rejects_motion_during_rest_transition(self):
        rest = await self.controller.enter_rest_state()
        self.assert_acknowledgement(rest, "device.rest")

        with self.assertRaises(RemoteDeviceError) as raised:
            await self.controller.play_movement("nod")

        self.assertEqual(raised.exception.code, "device_resting")
        self.assertTrue(raised.exception.recoverable)
        _, _, finished, state = await self.wait_motion(rest)
        self.assertEqual(finished.payload["status"], "completed")
        self.assertEqual(state.payload["status"], {"mode": "resting"})

    async def test_unsupported_additive_command_is_structured_and_non_terminal(self):
        command = DeviceCommand(
            message_id=str(uuid4()),
            message_type="future.command",
            timestamp=datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            device_id=DEVICE_ID,
            payload={"future": True},
        )

        with self.assertRaises(RemoteDeviceError) as raised:
            await self.client.request(command)

        self.assertEqual(raised.exception.code, "unsupported_command")
        self.assertFalse(raised.exception.recoverable)
        error = await self.wait_event("device.error", command.message_id)
        self.assertEqual(error.correlation_id, command.message_id)
        self.assertEqual(
            set(error.payload), {"code", "message", "recoverable", "details"}
        )

        acknowledgement = await self.controller.get_state()
        self.assert_acknowledgement(acknowledgement, "device.get_state")
        await self.wait_event("device.state_changed", acknowledgement.correlation_id)
        self.assertTrue(self.client.is_connected)


if __name__ == "__main__":
    unittest.main()
