import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from lefly_protocol import (
    COMMAND_TYPES,
    EVENT_TYPES,
    DeviceCommand,
    DeviceEvent,
    ProtocolError,
    is_known_command_type,
    is_known_event_type,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/examples/v1"
FIXTURES = ROOT / "contracts/fixtures/v1"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class ProtocolMessagesTest(unittest.TestCase):
    def test_fixture_manifest_agrees_with_python_validation(self):
        manifest = load_json(FIXTURES / "manifest.json")
        for case in manifest["cases"]:
            data = load_json(FIXTURES / case["path"])
            parser = DeviceCommand.from_dict if case["kind"] == "command" else DeviceEvent.from_dict
            with self.subTest(path=case["path"]):
                if case["valid"]:
                    parser(data)
                else:
                    with self.assertRaises(ProtocolError):
                        parser(data)

    def test_all_canonical_examples_round_trip(self):
        seen_commands = set()
        for path in sorted((EXAMPLES / "commands").glob("*.json")):
            data = load_json(path)
            self.assertEqual(DeviceCommand.from_dict(data).to_dict(), data)
            seen_commands.add(data["type"])

        seen_events = set()
        for path in sorted((EXAMPLES / "events").glob("*.json")):
            data = load_json(path)
            self.assertEqual(DeviceEvent.from_dict(data).to_dict(), data)
            seen_events.add(data["type"])

        self.assertEqual(seen_commands, set(COMMAND_TYPES))
        self.assertEqual(seen_events, set(EVENT_TYPES))

    def test_namespaced_payload_extensions_are_preserved(self):
        command = DeviceCommand(
            message_id="10000000-0000-4000-8000-000000000010",
            message_type="motion.play",
            timestamp="2026-08-17T08:00:00.000Z",
            device_id="lefly-001",
            payload={
                "name": "nod",
                "extensions": {"dev.lefly.motion": {"emotion": "happy"}},
            },
        )

        restored = DeviceCommand.from_json(command.to_json())

        self.assertEqual(restored, command)
        self.assertEqual(
            restored.payload["extensions"]["dev.lefly.motion"]["emotion"],
            "happy",
        )

    def test_known_command_payload_is_strict(self):
        with self.assertRaisesRegex(ProtocolError, "unknown payload field"):
            DeviceCommand(
                message_id="10000000-0000-4000-8000-000000000011",
                message_type="motion.play",
                timestamp="2026-08-17T08:00:00.000Z",
                device_id="lefly-001",
                payload={"name": "nod", "speed": 2},
            )

    def test_unknown_additive_v1_types_preserve_the_envelope(self):
        command = DeviceCommand.from_dict(
            {
                "version": "1",
                "id": "10000000-0000-4000-8000-000000000012",
                "type": "motion.sequence",
                "timestamp": "2026-08-17T08:00:00.000Z",
                "device_id": "lefly-001",
                "payload": {"future": True},
            }
        )
        event = DeviceEvent.from_dict(
            {
                "version": "1",
                "id": "20000000-0000-4000-8000-000000000012",
                "type": "sensor.distance",
                "timestamp": "2026-08-17T08:00:00.000Z",
                "device_id": "lefly-001",
                "payload": {"millimeters": 240},
            }
        )

        self.assertFalse(is_known_command_type(command.message_type))
        self.assertFalse(is_known_event_type(event.message_type))

    def test_envelope_requires_canonical_uuid_timestamp_and_device_id(self):
        base = {
            "version": "1",
            "id": "10000000-0000-4000-8000-000000000013",
            "type": "device.get_state",
            "timestamp": "2026-08-17T08:00:00.000Z",
            "device_id": "lefly-001",
            "payload": {},
        }
        for field, value in (
            ("id", "cmd-13"),
            ("timestamp", "2026-08-17T08:00:00Z"),
            ("device_id", "LeFly 001"),
        ):
            data = dict(base)
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                DeviceCommand.from_dict(data)

        missing = dict(base)
        missing.pop("device_id")
        with self.assertRaisesRegex(ProtocolError, "Missing envelope field: device_id"):
            DeviceCommand.from_dict(missing)

    def test_command_correlation_is_forbidden_and_lifecycle_correlation_is_required(self):
        command = load_json(EXAMPLES / "commands/device-get-state.json")
        command["correlation_id"] = "10000000-0000-4000-8000-000000000001"
        with self.assertRaisesRegex(ProtocolError, "correlation_id"):
            DeviceCommand.from_dict(command)

        event = load_json(EXAMPLES / "events/motion-started.json")
        event.pop("correlation_id")
        with self.assertRaisesRegex(ProtocolError, "correlation_id"):
            DeviceEvent.from_dict(event)

    def test_sensor_correlation_is_forbidden(self):
        event = load_json(EXAMPLES / "events/sensor-touch.json")
        event["correlation_id"] = "10000000-0000-4000-8000-000000000001"
        with self.assertRaisesRegex(ProtocolError, "correlation_id"):
            DeviceEvent.from_dict(event)

    def test_complete_state_semantics_are_validated(self):
        event = load_json(EXAMPLES / "events/device-state-changed.json")
        event["payload"]["command_queue"] = {"size": 9, "capacity": 8}
        with self.assertRaisesRegex(ProtocolError, "queue size"):
            DeviceEvent.from_dict(event)

        event = load_json(EXAMPLES / "events/device-state-changed.json")
        event["payload"]["motion"]["joints"]["base_yaw"]["pos"] = 120
        with self.assertRaisesRegex(ProtocolError, "joint position"):
            DeviceEvent.from_dict(event)

    def test_envelope_and_payload_are_immutable(self):
        command = DeviceCommand.from_dict(load_json(EXAMPLES / "commands/device-rest.json"))
        with self.assertRaises(FrozenInstanceError):
            command.message_id = "changed"
        with self.assertRaises(TypeError):
            command.payload["new"] = "value"


if __name__ == "__main__":
    unittest.main()
