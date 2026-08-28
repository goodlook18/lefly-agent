from __future__ import annotations

import asyncio
import copy
import unittest
from types import MappingProxyType

from lefly_agent.robot import RobotCommandService, RobotPolicyError


class FakeEvent:
    def __init__(self, payload):
        self.payload = MappingProxyType(payload)


class FakeAcknowledgement:
    def __init__(self, correlation_id="command-1", disposition="queued"):
        self.correlation_id = correlation_id
        self.payload = MappingProxyType({"disposition": disposition})


class FakeDeviceClient:
    def __init__(self):
        self.is_connected = True
        self.handlers = {}

    def subscribe(self, message_type, handler):
        self.handlers[message_type] = handler

        def unsubscribe():
            self.handlers.pop(message_type, None)

        return unsubscribe

    def emit_state(self, state):
        self.handlers["device.state_changed"](FakeEvent(state))


class RecordingController:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.on_get_state = None

    async def _record(self, tool, value):
        self.calls.append((tool, value))
        self.entered.set()
        if self.block:
            await self.release.wait()
        return FakeAcknowledgement("ack-%s" % len(self.calls))

    async def play_movement(self, name):
        return await self._record("motion.play", {"name": name})

    async def set_light_color(self, color):
        return await self._record("light.solid", {"color": color})

    async def set_light_brightness(self, brightness):
        return await self._record("light.brightness", {"brightness": brightness})

    async def enter_rest_state(self):
        return await self._record("device.rest", {})

    async def get_state(self):
        self.calls.append(("device.get_state", {}))
        if self.on_get_state is not None:
            self.on_get_state()
        return FakeAcknowledgement("ack-state", "accepted")


def complete_state(revision=1, *, status="active"):
    commands = {
        name: {"scope": "control"}
        for name in ("motion.play", "light.solid", "light.brightness", "device.rest")
    }
    joints = {
        name: {"pos": 0, "min": -180, "max": 180}
        for name in (
            "base_yaw",
            "base_pitch",
            "elbow_pitch",
            "wrist_roll",
            "wrist_pitch",
        )
    }
    return {
        "device_id": "lefly-sim-01",
        "revision": revision,
        "connection": "ready",
        "capabilities": {
            "commands": commands,
            "events": ["device.state_changed"],
            "motion": {
                "joints": list(joints),
                "presets": [
                    {"name": "nod", "label": "点头"},
                    {"name": "wake_up", "label": "唤醒"},
                ],
            },
            "lights": [
                {
                    "target": "head_matrix",
                    "kind": "rgb_matrix",
                    "width": 8,
                    "height": 8,
                }
            ],
        },
        "motion": {"state": "idle", "action": None, "joints": joints},
        "light": {
            "brightness": 0.5,
            "matrix": {"width": 8, "height": 8},
            "pixels": ["#FFFFFF"] * 64,
        },
        "status": {"mode": status},
        "status_strip": {"color": "#FFF0D0", "effect": "solid"},
        "command_queue": {"size": 0, "capacity": 8},
    }


class RobotCommandServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeDeviceClient()
        self.controller = RecordingController()
        self.service = RobotCommandService(
            self.controller,
            self.client,
            device_id="lefly-sim-01",
        )
        await self.service.start()
        self.addAsyncCleanup(self.service.close)

    def publish(self, state=None):
        self.client.emit_state(state or complete_state())

    async def test_executes_only_advertised_command_and_preset(self):
        self.publish()

        result = await self.service.play_motion("nod")

        self.assertEqual(self.controller.calls, [("motion.play", {"name": "nod"})])
        self.assertEqual(result.tool, "motion.play")
        self.assertEqual(result.correlation_id, "ack-1")
        self.assertEqual(result.disposition, "queued")
        with self.assertRaisesRegex(RobotPolicyError, "preset"):
            await self.service.play_motion("dance_demo")

        unsupported = complete_state(revision=2)
        del unsupported["capabilities"]["commands"]["motion.play"]
        self.publish(unsupported)
        with self.assertRaisesRegex(RobotPolicyError, "not advertised"):
            await self.service.play_motion("nod")

    async def test_advertised_motion_summary_uses_only_authoritative_state(self):
        self.assertEqual(self.service.advertised_motion_presets(), ())
        self.publish()
        self.assertEqual(
            self.service.advertised_motion_presets(), ("nod", "wake_up")
        )
        self.client.is_connected = False
        self.assertEqual(self.service.advertised_motion_presets(), ())

    async def test_validates_light_values_before_controller_calls(self):
        self.publish()

        with self.assertRaises(RobotPolicyError):
            await self.service.set_head_light("yellow")
        with self.assertRaises(RobotPolicyError):
            await self.service.set_head_light_brightness(1.5)

        await self.service.set_head_light("#F1A22E")
        await self.service.set_head_light_brightness(0.25)
        self.assertEqual(len(self.controller.calls), 2)

    async def test_disconnected_or_revision_gap_state_fails_closed(self):
        self.publish()
        self.client.is_connected = False
        with self.assertRaisesRegex(RobotPolicyError, "disconnected"):
            await self.service.play_motion("nod")

        self.client.is_connected = True
        self.publish(complete_state(revision=3))
        with self.assertRaisesRegex(RobotPolicyError, "authoritative"):
            await self.service.play_motion("nod")

    async def test_reconnect_resynchronizes_a_fresh_revision_epoch(self):
        self.publish(complete_state(revision=5))
        self.client.is_connected = False
        self.service.invalidate_state()
        self.client.is_connected = True
        self.controller.on_get_state = lambda: self.publish(
            complete_state(revision=1)
        )

        await self.service.synchronize_state(timeout=0.1)

        self.assertTrue(self.service.is_ready)
        self.assertEqual(
            self.service.advertised_motion_presets(), ("nod", "wake_up")
        )
        self.assertIn(("device.get_state", {}), self.controller.calls)

    async def test_rest_transition_and_resting_reject_ordinary_motion(self):
        self.publish()
        self.controller.block = True
        rest_task = asyncio.create_task(self.service.enter_rest_state())
        await self.controller.entered.wait()

        with self.assertRaisesRegex(RobotPolicyError, "rest transition"):
            await self.service.play_motion("nod")

        self.controller.release.set()
        await rest_task
        self.publish(complete_state(revision=2, status="resting"))
        with self.assertRaisesRegex(RobotPolicyError, "resting"):
            await self.service.play_motion("nod")

        self.controller.block = False
        await self.service.play_motion("wake_up")
        self.assertEqual(self.controller.calls[-1], ("motion.play", {"name": "wake_up"}))

    async def test_concurrent_mutations_are_serialized(self):
        self.publish()
        self.controller.block = True

        first = asyncio.create_task(self.service.play_motion("nod"))
        await self.controller.entered.wait()
        second = asyncio.create_task(self.service.set_head_light("#FFFFFF"))
        await asyncio.sleep(0)

        self.assertEqual(len(self.controller.calls), 1)
        self.controller.release.set()
        await asyncio.gather(first, second)
        self.assertEqual(
            [tool for tool, _ in self.controller.calls],
            ["motion.play", "light.solid"],
        )

    async def test_acknowledgement_finishes_without_motion_completion_event(self):
        self.publish()

        result = await asyncio.wait_for(self.service.play_motion("nod"), timeout=0.1)

        self.assertEqual(result.disposition, "queued")

    async def test_invalid_or_incomplete_state_is_not_authoritative(self):
        broken = copy.deepcopy(complete_state())
        del broken["capabilities"]
        self.publish(broken)

        with self.assertRaisesRegex(RobotPolicyError, "authoritative"):
            await self.service.play_motion("nod")


if __name__ == "__main__":
    unittest.main()
