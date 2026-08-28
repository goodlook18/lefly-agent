import copy
import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver, ValidationError

from lefly_protocol import (
    COMMAND_TYPES as PYTHON_COMMAND_TYPES,
    EVENT_TYPES as PYTHON_EVENT_TYPES,
    DeviceCommand,
    DeviceEvent,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"

COMMAND_TYPES = {
    "motion.play",
    "motion.relative_move",
    "motion.absolute_move",
    "light.solid",
    "light.paint",
    "light.brightness",
    "status.set",
    "device.rest",
    "device.get_state",
}

EVENT_TYPES = {
    "command.accepted",
    "sensor.touch",
    "sensor.vision.gesture",
    "sensor.vision.face",
    "motion.started",
    "motion.progress",
    "motion.finished",
    "device.state_changed",
    "device.error",
}

REQUIRED_STATE_FIELDS = {
    "device_id",
    "revision",
    "connection",
    "capabilities",
    "motion",
    "light",
    "status",
    "status_strip",
    "command_queue",
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def schema_store():
    store = {}
    for path in CONTRACTS.rglob("*.schema.json"):
        schema = load_json(path)
        if "$id" in schema:
            store[schema["$id"]] = schema
    return store


def validator(name):
    schema = load_json(CONTRACTS / name)
    resolver = RefResolver.from_schema(schema, store=schema_store())
    return Draft202012Validator(schema, resolver=resolver)


class ContractStructureTest(unittest.TestCase):
    def test_schema_python_and_typescript_catalogs_are_identical(self):
        command_schema = load_json(CONTRACTS / "device-command-v1.schema.json")
        event_schema = load_json(CONTRACTS / "device-event-v1.schema.json")
        typescript = (
            ROOT / "packages/lefly-console-web/src/deviceProtocol.ts"
        ).read_text(encoding="utf-8")

        def typescript_catalog(name):
            match = re.search(
                rf"export const {name} = (\[.*?\]) as const;",
                typescript,
                re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing TypeScript catalog {name}")
            return set(re.findall(r'"([^"]+)"', match.group(1)))

        self.assertEqual(set(command_schema["properties"]["type"]["enum"]), COMMAND_TYPES)
        self.assertEqual(set(event_schema["properties"]["type"]["enum"]), EVENT_TYPES)
        self.assertEqual(set(PYTHON_COMMAND_TYPES), COMMAND_TYPES)
        self.assertEqual(set(PYTHON_EVENT_TYPES), EVENT_TYPES)
        self.assertEqual(typescript_catalog("COMMAND_TYPES"), COMMAND_TYPES)
        self.assertEqual(typescript_catalog("EVENT_TYPES"), EVENT_TYPES)

    def test_all_schemas_are_valid_draft_2020_12(self):
        schemas = sorted(CONTRACTS.rglob("*.schema.json"))
        self.assertGreaterEqual(len(schemas), 20)
        for path in schemas:
            with self.subTest(path=path.relative_to(CONTRACTS)):
                schema = load_json(path)
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertIn("$id", schema)
                Draft202012Validator.check_schema(schema)

    def test_known_envelopes_are_strict_and_require_device_identity(self):
        command = load_json(CONTRACTS / "device-command-v1.schema.json")
        event = load_json(CONTRACTS / "device-event-v1.schema.json")

        required = {"version", "id", "type", "timestamp", "device_id", "payload"}
        self.assertEqual(set(command["required"]), required)
        self.assertEqual(set(event["required"]), required)
        self.assertNotIn("correlation_id", command["properties"])
        self.assertEqual(set(command["properties"]["type"]["enum"]), COMMAND_TYPES)
        self.assertEqual(set(event["properties"]["type"]["enum"]), EVENT_TYPES)
        self.assertEqual(len(command["oneOf"]), len(COMMAND_TYPES))
        self.assertEqual(len(event["oneOf"]), len(EVENT_TYPES))
        self.assertFalse(command["additionalProperties"])
        self.assertFalse(event["additionalProperties"])

    def test_state_capabilities_and_error_are_closed_complete_contracts(self):
        state = load_json(CONTRACTS / "device-state-v1.schema.json")
        capabilities = load_json(CONTRACTS / "v1/capabilities.schema.json")
        common = load_json(CONTRACTS / "v1/common.schema.json")
        error = common["$defs"]["error"]

        self.assertEqual(set(state["required"]), REQUIRED_STATE_FIELDS)
        self.assertFalse(state["additionalProperties"])
        self.assertEqual(
            set(capabilities["required"]),
            {"commands", "events", "motion", "lights"},
        )
        self.assertFalse(capabilities["additionalProperties"])
        self.assertEqual(
            set(error["required"]),
            {"code", "message", "recoverable", "details"},
        )
        self.assertFalse(error["additionalProperties"])

        status_strip = state["properties"]["status_strip"]
        self.assertEqual(set(status_strip["required"]), {"color", "effect"})
        self.assertNotIn("automatic", status_strip["properties"])
        self.assertNotIn("resting", state["properties"]["motion"]["properties"]["state"]["enum"])

    def test_resting_is_the_only_canonical_rest_status(self):
        event = load_json(CONTRACTS / "examples/v1/events/device-state-changed.json")
        resting = copy.deepcopy(event["payload"])
        resting["status"]["mode"] = "resting"
        validator("device-state-v1.schema.json").validate(resting)

        sleeping = copy.deepcopy(resting)
        sleeping["status"]["mode"] = "sleeping"
        with self.assertRaises(ValidationError):
            validator("device-state-v1.schema.json").validate(sleeping)

        status_command = load_json(CONTRACTS / "examples/v1/commands/status-set.json")
        status_command["payload"]["mode"] = "resting"
        validator("device-command-v1.schema.json").validate(status_command)
        status_command["payload"]["mode"] = "sleeping"
        with self.assertRaises(ValidationError):
            validator("device-command-v1.schema.json").validate(status_command)

    def test_every_catalog_type_has_a_valid_versioned_example(self):
        command_examples = sorted((CONTRACTS / "examples/v1/commands").glob("*.json"))
        event_examples = sorted((CONTRACTS / "examples/v1/events").glob("*.json"))
        self.assertEqual(len(command_examples), len(COMMAND_TYPES))
        self.assertEqual(len(event_examples), len(EVENT_TYPES))

        seen_commands = set()
        for path in command_examples:
            with self.subTest(path=path.name):
                data = load_json(path)
                validator("device-command-v1.schema.json").validate(data)
                self.assertEqual(DeviceCommand.from_dict(data).to_dict(), data)
                seen_commands.add(data["type"])

        seen_events = set()
        for path in event_examples:
            with self.subTest(path=path.name):
                data = load_json(path)
                validator("device-event-v1.schema.json").validate(data)
                self.assertEqual(DeviceEvent.from_dict(data).to_dict(), data)
                seen_events.add(data["type"])

        self.assertEqual(seen_commands, COMMAND_TYPES)
        self.assertEqual(seen_events, EVENT_TYPES)

    def test_complete_state_example_obeys_cross_field_invariants(self):
        event = load_json(CONTRACTS / "examples/v1/events/device-state-changed.json")
        state = event["payload"]
        validator("device-state-v1.schema.json").validate(state)

        self.assertEqual(event["device_id"], state["device_id"])
        self.assertEqual(
            set(state["capabilities"]["motion"]["joints"]),
            set(state["motion"]["joints"]),
        )
        for joint in state["motion"]["joints"].values():
            self.assertLessEqual(joint["min"], joint["pos"])
            self.assertLessEqual(joint["pos"], joint["max"])
        self.assertLessEqual(
            state["command_queue"]["size"],
            state["command_queue"]["capacity"],
        )
        matrix = state["light"]["matrix"]
        self.assertEqual(len(state["light"]["pixels"]), matrix["width"] * matrix["height"])

    def test_manifest_invalid_schema_cases_are_rejected(self):
        manifest = load_json(CONTRACTS / "fixtures/v1/manifest.json")
        self.assertEqual(manifest["version"], "1")
        self.assertGreaterEqual(len(manifest["cases"]), 1)
        for case in manifest["cases"]:
            with self.subTest(path=case["path"]):
                data = load_json(CONTRACTS / "fixtures/v1" / case["path"])
                schema_name = (
                    "device-command-v1.schema.json"
                    if case["kind"] == "command"
                    else "device-event-v1.schema.json"
                )
                if case["valid"]:
                    validator(schema_name).validate(data)
                else:
                    with self.assertRaises(ValidationError):
                        validator(schema_name).validate(data)


if __name__ == "__main__":
    unittest.main()
