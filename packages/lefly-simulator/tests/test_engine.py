import asyncio
import unittest
from datetime import datetime, timezone

from lefly_protocol import DeviceCommand, validate_state
from lefly_sdk import RemoteHardwareController
from lefly_simulator import EventFactory, ManualClock, SimulatorEngine


def command(message_type, payload=None, suffix=1):
    return DeviceCommand(
        message_id=f"10000000-0000-4000-8000-{suffix:012d}",
        message_type=message_type,
        timestamp="2026-08-17T08:00:00.000Z",
        device_id="lefly-sim-01",
        payload={} if payload is None else payload,
    )


class EventFactoryTest(unittest.TestCase):
    def test_events_use_canonical_identity_and_millisecond_timestamp(self):
        factory = EventFactory(
            "lefly-sim-01",
            id_factory=lambda: "20000000-0000-4000-8000-000000000001",
            utc_now=lambda: datetime(2026, 8, 17, 8, 1, 2, tzinfo=timezone.utc),
        )

        event = factory.create(
            "command.accepted",
            {"command_type": "device.get_state", "disposition": "applied"},
            correlation_id="10000000-0000-4000-8000-000000000001",
        )

        self.assertEqual(event.message_id, "20000000-0000-4000-8000-000000000001")
        self.assertEqual(event.timestamp, "2026-08-17T08:01:02.000Z")
        self.assertEqual(event.device_id, "lefly-sim-01")


class SimulatorEngineStateTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = SimulatorEngine("lefly-sim-01", clock=ManualClock())

    async def asyncTearDown(self):
        await self.engine.close()

    async def test_get_state_accepts_then_publishes_complete_snapshot(self):
        events = await self.engine.execute(command("device.get_state"))

        self.assertEqual(
            [event.message_type for event in events],
            ["command.accepted", "device.state_changed"],
        )
        self.assertEqual(
            dict(events[0].payload),
            {"command_type": "device.get_state", "disposition": "applied"},
        )
        self.assertTrue(all(event.correlation_id == command("device.get_state").message_id for event in events))
        validate_state(events[-1].payload, "lefly-sim-01")
        self.assertEqual(events[-1].payload["revision"], 1)

    async def test_no_op_publications_still_advance_revision_once(self):
        first = await self.engine.execute(command("device.get_state", suffix=1))
        second = await self.engine.execute(command("device.get_state", suffix=2))

        self.assertEqual(first[-1].payload["revision"], 1)
        self.assertEqual(second[-1].payload["revision"], 2)

    async def test_light_commands_publish_full_matrix_state(self):
        solid = await self.engine.execute(
            command(
                "light.solid",
                {"target": "head_matrix", "color": "#FFFF00"},
                1,
            )
        )
        brightness = await self.engine.execute(
            command(
                "light.brightness",
                {"target": "head_matrix", "brightness": 0.25},
                2,
            )
        )
        frame = ["#000000"] * 63 + ["#FFFFFF"]
        paint = await self.engine.execute(
            command(
                "light.paint",
                {"target": "head_matrix", "pixels": frame},
                3,
            )
        )

        self.assertEqual(set(solid[-1].payload["light"]["pixels"]), {"#FFFF00"})
        self.assertEqual(brightness[-1].payload["light"]["brightness"], 0.25)
        self.assertEqual(list(paint[-1].payload["light"]["pixels"]), frame)
        self.assertEqual(paint[-1].payload["revision"], 3)

    async def test_status_command_changes_semantic_mode_and_system_strip(self):
        events = await self.engine.execute(
            command("status.set", {"mode": "speaking"})
        )

        self.assertEqual(events[-1].payload["status"], {"mode": "speaking"})
        self.assertEqual(
            events[-1].payload["status_strip"],
            {"color": "#2F9D68", "effect": "level_sweep"},
        )

    async def test_system_status_command_explicitly_clears_error(self):
        entered = await self.engine.execute(command("status.set", {"mode": "error"}, 1))
        cleared = await self.engine.execute(command("status.set", {"mode": "active"}, 2))

        self.assertEqual(entered[-1].payload["status"], {"mode": "error"})
        self.assertEqual(
            [event.message_type for event in cleared],
            ["command.accepted", "device.state_changed"],
        )
        self.assertEqual(cleared[-1].payload["status"], {"mode": "active"})

    async def test_unknown_command_emits_one_structured_error(self):
        item = command("future.command")

        events = await self.engine.execute(item)

        self.assertEqual([event.message_type for event in events], ["device.error"])
        self.assertEqual(events[0].correlation_id, item.message_id)
        self.assertEqual(
            set(events[0].payload),
            {"code", "message", "recoverable", "details"},
        )

    async def test_subscriber_failures_are_isolated(self):
        delivered = []

        def broken(_event):
            raise RuntimeError("subscriber failed")

        self.engine.subscribe(broken)
        self.engine.subscribe(delivered.append)

        with self.assertLogs("lefly_simulator.engine", level="ERROR"):
            events = await self.engine.execute(command("device.get_state"))

        self.assertEqual(delivered, events)

    async def test_subscriber_cancelled_error_propagates(self):
        async def cancelled(_event):
            raise asyncio.CancelledError()

        self.engine.subscribe(cancelled)

        with self.assertRaises(asyncio.CancelledError):
            await self.engine.execute(command("device.get_state"))

    async def test_sensor_injection_publishes_canonical_uncorrelated_events(self):
        touch = await self.engine.inject_sensor("touch", {"position": "left"})
        gesture = await self.engine.inject_sensor("gesture", {"id": 3, "label": "yeah"})
        face = await self.engine.inject_sensor("face", {"id": 2})

        self.assertEqual(touch.message_type, "sensor.touch")
        self.assertEqual(dict(touch.payload), {"position": "left", "pressed": True})
        self.assertEqual(
            dict(gesture.payload),
            {"id": 3, "label": "yeah", "confidence": None},
        )
        self.assertEqual(
            dict(face.payload),
            {"id": 2, "label": None, "confidence": None},
        )
        self.assertTrue(all(event.correlation_id is None for event in (touch, gesture, face)))


class SdkCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = SimulatorEngine("lefly-sim-01", clock=ManualClock())

        class EngineClient:
            def __init__(inner_self, engine):
                inner_self.engine = engine
                inner_self.commands = []

            async def request(inner_self, sdk_command):
                inner_self.commands.append(sdk_command)
                return (await inner_self.engine.execute(sdk_command))[0]

        self.client = EngineClient(self.engine)
        self.controller = RemoteHardwareController(
            self.client,
            device_id="lefly-sim-01",
            id_factory=lambda: "10000000-0000-4000-8000-000000000099",
            clock=lambda: datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        )

    async def asyncTearDown(self):
        await self.engine.close()

    async def test_sdk_color_and_status_commands_drive_canonical_state(self):
        light = await self.controller.set_light_rgb(1, 128, 255)
        status = await self.controller.set_status("speaking")

        self.assertEqual(light.message_type, "command.accepted")
        self.assertEqual(status.message_type, "command.accepted")
        snapshot = self.engine.state.observe()
        self.assertEqual(set(snapshot["light"]["pixels"]), {"#0180FF"})
        self.assertEqual(snapshot["status"], {"mode": "speaking"})


if __name__ == "__main__":
    unittest.main()
