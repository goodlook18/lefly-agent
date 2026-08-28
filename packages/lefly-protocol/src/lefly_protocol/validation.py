"""Strict standard-library validation for Protocol v1 wire messages."""

import math
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Optional

from .catalog import COMMAND_TYPES, EVENT_TYPES, STATUS_EFFECTS, STATUS_MODES


class ProtocolError(ValueError):
    """Raised when a wire message violates Protocol v1."""


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
DEVICE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MESSAGE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
COLOR_RE = re.compile(r"^#[0-9A-F]{6}$")
EXTENSION_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9_-]*)+$")


def validate_envelope(
    *,
    version: Any,
    message_id: Any,
    message_type: Any,
    timestamp: Any,
    device_id: Any,
    correlation_id: Any,
    payload: Any,
) -> None:
    if version != "1":
        raise ProtocolError(f"Unsupported protocol version: {version!r}")
    _uuid("id", message_id)
    _match("type", message_type, MESSAGE_TYPE_RE)
    _timestamp(timestamp)
    _match("device_id", device_id, DEVICE_ID_RE)
    if correlation_id is not None:
        _uuid("correlation_id", correlation_id)
    _mapping("payload", payload)


def validate_command(message_type: str, payload: Mapping[str, Any], correlation_id: Any) -> None:
    if correlation_id is not None:
        raise ProtocolError("correlation_id is prohibited on commands")
    if message_type not in COMMAND_TYPES:
        return
    if message_type == "motion.play":
        _keys(payload, {"name"}, {"extensions"})
        _match("payload.name", payload["name"], ACTION_ID_RE)
    elif message_type in {"motion.relative_move", "motion.absolute_move"}:
        _keys(payload, {"joints", "duration_ms"}, {"extensions"})
        _joint_numbers(payload["joints"], require_nonempty=True)
        _integer_range("payload.duration_ms", payload["duration_ms"], 1, 60000)
    elif message_type == "light.solid":
        _keys(payload, {"target", "color"}, {"extensions"})
        _target(payload["target"])
        _match("payload.color", payload["color"], COLOR_RE)
    elif message_type == "light.paint":
        _keys(payload, {"target", "pixels"}, {"extensions"})
        _target(payload["target"])
        _colors("payload.pixels", payload["pixels"])
    elif message_type == "light.brightness":
        _keys(payload, {"target", "brightness"}, {"extensions"})
        _target(payload["target"])
        _number_range("payload.brightness", payload["brightness"], 0.0, 1.0)
    elif message_type == "status.set":
        _keys(payload, {"mode"}, {"extensions"})
        _enum("payload.mode", payload["mode"], STATUS_MODES)
    else:
        _keys(payload, set(), {"extensions"})
    _extensions(payload.get("extensions"))


def validate_event(
    message_type: str,
    payload: Mapping[str, Any],
    correlation_id: Optional[str],
    device_id: str,
) -> None:
    if message_type not in EVENT_TYPES:
        return
    if message_type in {"command.accepted", "motion.started", "motion.progress", "motion.finished"}:
        if correlation_id is None:
            raise ProtocolError(f"correlation_id is required for {message_type}")
    if message_type.startswith("sensor.") and correlation_id is not None:
        raise ProtocolError(f"correlation_id is prohibited for {message_type}")

    if message_type == "command.accepted":
        _keys(payload, {"command_type", "disposition"}, {"extensions"})
        _enum("payload.command_type", payload["command_type"], COMMAND_TYPES)
        _enum("payload.disposition", payload["disposition"], {"applied", "queued"})
    elif message_type == "sensor.touch":
        _keys(payload, {"position", "pressed"}, {"extensions"})
        _enum("payload.position", payload["position"], {"left", "middle", "right"})
        _boolean("payload.pressed", payload["pressed"])
    elif message_type in {"sensor.vision.gesture", "sensor.vision.face"}:
        _keys(payload, {"id", "label", "confidence"}, {"extensions"})
        _integer_range("payload.id", payload["id"], 0, None)
        if payload["label"] is not None:
            _text("payload.label", payload["label"])
        if payload["confidence"] is not None:
            _number_range("payload.confidence", payload["confidence"], 0.0, 1.0)
    elif message_type == "motion.started":
        _keys(payload, {"command_type", "action", "duration_ms"}, {"extensions"})
        _enum(
            "payload.command_type",
            payload["command_type"],
            {"motion.play", "motion.relative_move", "motion.absolute_move", "device.rest"},
        )
        _match("payload.action", payload["action"], ACTION_ID_RE)
        _integer_range("payload.duration_ms", payload["duration_ms"], 1, 60000)
        expected = {
            "motion.relative_move": "relative_move",
            "motion.absolute_move": "absolute_move",
            "device.rest": "rest",
        }.get(payload["command_type"])
        if expected is not None and payload["action"] != expected:
            raise ProtocolError("payload.action does not match command_type")
    elif message_type == "motion.progress":
        _keys(payload, {"action", "progress", "elapsed_ms", "joints"}, {"extensions"})
        _match("payload.action", payload["action"], ACTION_ID_RE)
        _number_range("payload.progress", payload["progress"], 0.0, 1.0)
        _integer_range("payload.elapsed_ms", payload["elapsed_ms"], 0, None)
        _joint_numbers(payload["joints"])
    elif message_type == "motion.finished":
        _motion_finished(payload)
    elif message_type == "device.state_changed":
        validate_state(payload, device_id)
    elif message_type == "device.error":
        validate_error(payload)
    _extensions(payload.get("extensions"))


def validate_error(value: Any) -> None:
    error = _mapping("error", value)
    _keys(error, {"code", "message", "recoverable", "details"})
    _match("error.code", error["code"], ACTION_ID_RE)
    _text("error.message", error["message"])
    _boolean("error.recoverable", error["recoverable"])
    if error["details"] is not None:
        _mapping("error.details", error["details"])


def validate_state(value: Any, envelope_device_id: Optional[str] = None) -> None:
    state = _mapping("state", value)
    required = {
        "device_id", "revision", "connection", "capabilities", "motion",
        "light", "status", "status_strip", "command_queue",
    }
    _keys(state, required, {"extensions"})
    _match("state.device_id", state["device_id"], DEVICE_ID_RE)
    if envelope_device_id is not None and state["device_id"] != envelope_device_id:
        raise ProtocolError("state device_id does not match envelope device_id")
    _integer_range("state.revision", state["revision"], 0, None)
    _enum("state.connection", state["connection"], {"connecting", "ready", "degraded", "offline"})
    joint_names = _capabilities(state["capabilities"])
    _motion_state(state["motion"], joint_names)
    _light_state(state["light"], state["capabilities"])
    status = _mapping("state.status", state["status"])
    _keys(status, {"mode"})
    _enum("state.status.mode", status["mode"], STATUS_MODES)
    strip = _mapping("state.status_strip", state["status_strip"])
    _keys(strip, {"color", "effect"})
    _match("state.status_strip.color", strip["color"], COLOR_RE)
    _enum("state.status_strip.effect", strip["effect"], STATUS_EFFECTS)
    queue = _mapping("state.command_queue", state["command_queue"])
    _keys(queue, {"size", "capacity"})
    _integer_range("state.command_queue.size", queue["size"], 0, None)
    _integer_range("state.command_queue.capacity", queue["capacity"], 0, None)
    if queue["size"] > queue["capacity"]:
        raise ProtocolError("queue size exceeds capacity")
    _extensions(state.get("extensions"))


def _motion_finished(payload: Mapping[str, Any]) -> None:
    _keys(payload, {"action", "status", "elapsed_ms", "joints", "reason", "error"}, {"extensions"})
    _match("payload.action", payload["action"], ACTION_ID_RE)
    _enum("payload.status", payload["status"], {"completed", "cancelled", "failed"})
    _integer_range("payload.elapsed_ms", payload["elapsed_ms"], 0, None)
    _joint_numbers(payload["joints"])
    status = payload["status"]
    if status == "completed" and (payload["reason"] is not None or payload["error"] is not None):
        raise ProtocolError("completed motion requires null reason and error")
    if status == "cancelled":
        _match("payload.reason", payload["reason"], ACTION_ID_RE)
        if payload["error"] is not None:
            raise ProtocolError("cancelled motion requires null error")
    if status == "failed":
        if payload["reason"] is not None:
            raise ProtocolError("failed motion requires null reason")
        validate_error(payload["error"])


def _capabilities(value: Any) -> set:
    caps = _mapping("state.capabilities", value)
    _keys(caps, {"commands", "events", "motion", "lights"})
    commands = _mapping("state.capabilities.commands", caps["commands"])
    for name, metadata in commands.items():
        _match("capability command", name, MESSAGE_TYPE_RE)
        item = _mapping("capability command metadata", metadata)
        _keys(item, {"scope"})
        _enum("capability scope", item["scope"], {"control", "system"})
    events = _sequence("state.capabilities.events", caps["events"])
    _unique_texts("state.capabilities.events", events, MESSAGE_TYPE_RE)
    motion = _mapping("state.capabilities.motion", caps["motion"])
    _keys(motion, {"joints", "presets"})
    joints = _sequence("state.capabilities.motion.joints", motion["joints"])
    _unique_texts("state.capabilities.motion.joints", joints, ACTION_ID_RE)
    presets = _sequence("state.capabilities.motion.presets", motion["presets"])
    preset_names = []
    for preset in presets:
        item = _mapping("motion preset", preset)
        _keys(item, {"name", "label"})
        _match("motion preset name", item["name"], ACTION_ID_RE)
        if item["label"] is not None:
            _text("motion preset label", item["label"])
        preset_names.append(item["name"])
    if len(preset_names) != len(set(preset_names)):
        raise ProtocolError("motion preset names must be unique")
    lights = _sequence("state.capabilities.lights", caps["lights"])
    for light in lights:
        item = _mapping("light capability", light)
        _keys(item, {"target", "kind", "width", "height"})
        _match("light target", item["target"], ACTION_ID_RE)
        _enum("light kind", item["kind"], {"rgb_matrix"})
        _integer_range("light width", item["width"], 1, None)
        _integer_range("light height", item["height"], 1, None)
    return set(joints)


def _motion_state(value: Any, joint_names: set) -> None:
    motion = _mapping("state.motion", value)
    _keys(motion, {"state", "action", "joints"})
    _enum("state.motion.state", motion["state"], {"idle", "moving", "error"})
    if motion["state"] == "moving":
        _match("state.motion.action", motion["action"], ACTION_ID_RE)
    elif motion["action"] is not None:
        raise ProtocolError("idle or error motion requires null action")
    joints = _mapping("state.motion.joints", motion["joints"])
    if set(joints) != joint_names:
        raise ProtocolError("capability joints do not match state joints")
    for name, raw in joints.items():
        _match("joint name", name, ACTION_ID_RE)
        joint = _mapping("joint state", raw)
        _keys(joint, {"pos", "min", "max"})
        for field in ("pos", "min", "max"):
            _number(f"joint {field}", joint[field])
        if not joint["min"] <= joint["pos"] <= joint["max"]:
            raise ProtocolError("joint position is outside min and max")


def _light_state(value: Any, capabilities: Mapping[str, Any]) -> None:
    light = _mapping("state.light", value)
    _keys(light, {"brightness", "matrix", "pixels"})
    _number_range("state.light.brightness", light["brightness"], 0.0, 1.0)
    matrix = _mapping("state.light.matrix", light["matrix"])
    _keys(matrix, {"width", "height"})
    _integer_range("state.light.matrix.width", matrix["width"], 1, None)
    _integer_range("state.light.matrix.height", matrix["height"], 1, None)
    pixels = _colors("state.light.pixels", light["pixels"])
    if len(pixels) != matrix["width"] * matrix["height"]:
        raise ProtocolError("pixel count does not match matrix dimensions")
    head = [item for item in capabilities["lights"] if item["target"] == "head_matrix"]
    if len(head) != 1 or (head[0]["width"], head[0]["height"]) != (matrix["width"], matrix["height"]):
        raise ProtocolError("head_matrix capability does not match light state")


def _keys(value: Mapping[str, Any], required: set, optional: set = set()) -> None:
    missing = required - set(value)
    if missing:
        raise ProtocolError(f"missing payload field: {sorted(missing)[0]}")
    unknown = set(value) - required - optional
    if unknown:
        raise ProtocolError(f"unknown payload field: {sorted(unknown)[0]}")


def _extensions(value: Any) -> None:
    if value is None:
        return
    extensions = _mapping("extensions", value)
    for key in extensions:
        _match("extension key", key, EXTENSION_RE)


def _joint_numbers(value: Any, require_nonempty: bool = False) -> Mapping[str, Any]:
    joints = _mapping("joints", value)
    if require_nonempty and not joints:
        raise ProtocolError("joints must not be empty")
    for name, number in joints.items():
        _match("joint name", name, ACTION_ID_RE)
        _number("joint value", number)
    return joints


def _colors(name: str, value: Any) -> Sequence[Any]:
    values = _sequence(name, value)
    if not values:
        raise ProtocolError(f"{name} must not be empty")
    for color in values:
        _match(name, color, COLOR_RE)
    return values


def _target(value: Any) -> None:
    if value != "head_matrix":
        raise ProtocolError("payload.target must be head_matrix")


def _mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError(f"{name} must be an object")
    return value


def _sequence(name: str, value: Any) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ProtocolError(f"{name} must be an array")
    return value


def _text(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{name} must be a non-empty string")


def _match(name: str, value: Any, pattern: re.Pattern) -> None:
    _text(name, value)
    if pattern.fullmatch(value) is None:
        raise ProtocolError(f"{name} has invalid format")


def _uuid(name: str, value: Any) -> None:
    _match(name, value, UUID_RE)
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise ProtocolError(f"{name} has invalid UUID format") from exc


def _timestamp(value: Any) -> None:
    _match("timestamp", value, TIMESTAMP_RE)
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ProtocolError("timestamp has invalid UTC date-time") from exc


def _number(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolError(f"{name} must be a finite number")


def _number_range(name: str, value: Any, minimum: float, maximum: Optional[float]) -> None:
    _number(name, value)
    if value < minimum or (maximum is not None and value > maximum):
        raise ProtocolError(f"{name} is outside the allowed range")


def _integer_range(name: str, value: Any, minimum: int, maximum: Optional[int]) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise ProtocolError(f"{name} is outside the allowed range")


def _boolean(name: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ProtocolError(f"{name} must be a boolean")


def _enum(name: str, value: Any, choices: set) -> None:
    if value not in choices:
        raise ProtocolError(f"{name} has unsupported value")


def _unique_texts(name: str, values: Sequence[Any], pattern: re.Pattern) -> None:
    for value in values:
        _match(name, value, pattern)
    if len(values) != len(set(values)):
        raise ProtocolError(f"{name} values must be unique")
