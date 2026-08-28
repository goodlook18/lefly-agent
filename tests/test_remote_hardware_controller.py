import unittest
from datetime import datetime, timezone

from lefly_protocol import DeviceEvent
from lefly_sdk import RemoteHardwareController


COMMAND_ID = "10000000-0000-4000-8000-000000000020"
EVENT_ID = "20000000-0000-4000-8000-000000000020"


class RecordingClient:
    def __init__(self):
        self.commands = []

    async def request(self, command):
        self.commands.append(command)
        return DeviceEvent(
            message_id=EVENT_ID,
            message_type="command.accepted",
            timestamp="2026-08-17T08:00:00.050Z",
            device_id=command.device_id,
            correlation_id=command.message_id,
            payload={"command_type": command.message_type, "disposition": "queued"},
        )


class RemoteHardwareControllerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = RecordingClient()
        self.controller = RemoteHardwareController(
            self.client,
            device_id="lefly-001",
            id_factory=lambda: COMMAND_ID,
            clock=lambda: datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        )

    def last_command(self):
        return self.client.commands[-1]

    async def test_head_light_convenience_inputs_normalize_to_hex(self):
        await self.controller.set_light_color("yellow")
        self.assertEqual(
            dict(self.last_command().payload),
            {"target": "head_matrix", "color": "#FFFF00"},
        )

        await self.controller.set_light_rgb(10, 20, 30)
        self.assertEqual(self.last_command().payload["color"], "#0A141E")

        await self.controller.paint_rgb_pattern([[255, 0, 0], [0, 0, 0]])
        self.assertEqual(self.last_command().payload["pixels"], ("#FF0000", "#000000"))

    async def test_motion_commands_use_numeric_joints_and_duration(self):
        await self.controller.play_movement("happy_wiggle")
        self.assertEqual(dict(self.last_command().payload), {"name": "happy_wiggle"})

        await self.controller.play_relative_movement({"base_yaw": 15}, duration_ms=500)
        self.assertEqual(
            dict(self.last_command().payload),
            {"joints": {"base_yaw": 15}, "duration_ms": 500},
        )

        await self.controller.play_absolute_move({"base_yaw": 60})
        self.assertEqual(
            dict(self.last_command().payload),
            {"joints": {"base_yaw": 60}, "duration_ms": 700},
        )

        await self.controller.enter_rest_state()
        self.assertEqual(dict(self.last_command().payload), {})

    async def test_status_command_contains_semantic_mode_only(self):
        await self.controller.set_status("speaking")
        self.assertEqual(dict(self.last_command().payload), {"mode": "speaking"})

    async def test_get_state_uses_the_canonical_empty_payload(self):
        await self.controller.get_state()
        self.assertEqual(self.last_command().message_type, "device.get_state")
        self.assertEqual(dict(self.last_command().payload), {})

    async def test_commands_have_uuid_millisecond_timestamp_and_device_id(self):
        response = await self.controller.enter_rest_state()
        command = self.last_command()
        self.assertEqual(command.message_id, COMMAND_ID)
        self.assertEqual(command.timestamp, "2026-08-17T08:00:00.000Z")
        self.assertEqual(command.device_id, "lefly-001")
        self.assertEqual(response.correlation_id, COMMAND_ID)

    async def test_invalid_light_motion_and_status_arguments_are_rejected(self):
        invalid_calls = (
            self.controller.set_light_color("unknown-color"),
            self.controller.set_light_rgb(-1, 0, 0),
            self.controller.set_light_brightness(1.1),
            self.controller.paint_rgb_pattern([]),
            self.controller.play_movement(" "),
            self.controller.play_absolute_move({}),
            self.controller.play_absolute_move({"base_yaw": {"pos": 60}}),
            self.controller.play_absolute_move({"base_yaw": 60}, duration_ms=0),
            self.controller.set_status("rest"),
        )
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                await call


if __name__ == "__main__":
    unittest.main()
