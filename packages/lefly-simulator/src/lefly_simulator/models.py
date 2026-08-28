"""Canonical Protocol v1 state for the deterministic LeFly simulator."""

import copy
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from lefly_protocol import EVENT_TYPES, ProtocolError, validate_state


class StateValidationError(ValueError):
    """Raised when a requested simulator state is invalid."""


@dataclass(frozen=True)
class JointProfile:
    name: str
    default: float
    minimum: float
    maximum: float


@dataclass(frozen=True)
class JointState:
    profile: JointProfile
    position: float


DEFAULT_JOINTS: Tuple[JointProfile, ...] = (
    JointProfile("base_yaw", 0, -90, 90),
    JointProfile("base_pitch", -45, -45, 45),
    JointProfile("elbow_pitch", 105, -15, 105),
    JointProfile("wrist_roll", 0, -180, 180),
    JointProfile("wrist_pitch", 45, -45, 45),
)

DEFAULT_PRESETS = (
    ("wake_up", "唤醒"),
    ("nod", "点头"),
    ("headshake", "摇头"),
    ("happy_wiggle", "开心摆动"),
    ("look_up", "向上看"),
    ("look_down", "向下看"),
    ("look_left", "向左看"),
    ("look_right", "向右看"),
    ("dance_demo", "舞蹈演示"),
    ("sleep", "休眠"),
)

MATRIX_WIDTH = 8
MATRIX_HEIGHT = 8
MATRIX_SIZE = MATRIX_WIDTH * MATRIX_HEIGHT

STATUS_VISUALS = {
    "starting": ("#FFF0D0", "fade"),
    "resting": ("#FFD33D", "breath"),
    "active": ("#FFF0D0", "solid"),
    "listening": ("#438CFF", "breath"),
    "thinking": ("#FFD33D", "marquee"),
    "speaking": ("#2F9D68", "level_sweep"),
    "error": ("#FF3B30", "blink"),
}

_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _validate_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateValidationError(f"{label} must be numeric")
    if not math.isfinite(value):
        raise StateValidationError(f"{label} must be finite")
    return value


def _normalize_color(value: object, label: str) -> str:
    if not isinstance(value, str) or _COLOR_PATTERN.fullmatch(value) is None:
        raise StateValidationError(f"{label} must use #RRGGBB format")
    return value.upper()


def _default_capabilities() -> Dict[str, Any]:
    commands = {
        name: {"scope": "system" if name == "status.set" else "control"}
        for name in (
            "motion.play",
            "motion.relative_move",
            "motion.absolute_move",
            "light.solid",
            "light.paint",
            "light.brightness",
            "status.set",
            "device.rest",
            "device.get_state",
        )
    }
    return {
        "commands": commands,
        "events": sorted(EVENT_TYPES),
        "motion": {
            "joints": [profile.name for profile in DEFAULT_JOINTS],
            "presets": [
                {"name": name, "label": label} for name, label in DEFAULT_PRESETS
            ],
        },
        "lights": [
            {
                "target": "head_matrix",
                "kind": "rgb_matrix",
                "width": MATRIX_WIDTH,
                "height": MATRIX_HEIGHT,
            }
        ],
    }


class SimulatorState:
    """Mutable device state with publication-owned revision numbers."""

    def __init__(
        self,
        device_id: str,
        joints: Mapping[str, JointState],
        *,
        command_queue_capacity: int = 8,
    ) -> None:
        self._device_id = device_id
        self._joints = dict(joints)
        self._revision = 0
        self._capabilities = _default_capabilities()
        self._connection = "ready"
        self._motion_state = "idle"
        self._motion_action: Optional[str] = None
        self._head_light_brightness = 0.5
        self._head_light_pixels = tuple("#FFF0D0" for _ in range(MATRIX_SIZE))
        self._status_mode = "active"
        self._command_queue_size = 0
        self._command_queue_capacity = command_queue_capacity
        self._validate_complete_state()

    @classmethod
    def default(cls, device_id: str) -> "SimulatorState":
        if not isinstance(device_id, str) or not device_id.strip():
            raise StateValidationError("device_id must be a non-empty string")
        return cls(
            device_id,
            {
                profile.name: JointState(profile=profile, position=profile.default)
                for profile in DEFAULT_JOINTS
            },
        )

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def joints(self) -> Mapping[str, JointState]:
        return MappingProxyType(self._joints)

    @property
    def capabilities(self) -> Mapping[str, Any]:
        return MappingProxyType(copy.deepcopy(self._capabilities))

    @property
    def connection(self) -> str:
        return self._connection

    @property
    def motion_state(self) -> str:
        return self._motion_state

    @property
    def motion_action(self) -> Optional[str]:
        return self._motion_action

    @property
    def head_light_color(self) -> str:
        return self._head_light_pixels[0]

    @property
    def head_light_brightness(self) -> float:
        return self._head_light_brightness

    @property
    def status_mode(self) -> str:
        return self._status_mode

    @property
    def command_queue_size(self) -> int:
        return self._command_queue_size

    @property
    def command_queue_capacity(self) -> int:
        return self._command_queue_capacity

    def set_joint(self, name: str, position: float) -> None:
        self.set_joints({name: position})

    def set_joints(self, positions: Mapping[str, float]) -> None:
        if not isinstance(positions, Mapping):
            raise StateValidationError("joint positions must be a mapping")
        validated: Dict[str, JointState] = {}
        for name, position in positions.items():
            try:
                joint = self._joints[name]
            except (KeyError, TypeError) as error:
                raise StateValidationError(f"unknown joint: {name!r}") from error
            numeric = _validate_number(position, "joint position")
            if numeric < joint.profile.minimum or numeric > joint.profile.maximum:
                raise StateValidationError(
                    f"{name} position must be between "
                    f"{joint.profile.minimum} and {joint.profile.maximum}"
                )
            validated[name] = JointState(joint.profile, numeric)
        self._joints.update(validated)

    def set_head_light(self, color: str, brightness: float) -> None:
        normalized = _normalize_color(color, "head light color")
        validated = _validate_number(brightness, "head light brightness")
        if validated < 0 or validated > 1:
            raise StateValidationError("head light brightness must be between 0 and 1")
        self._head_light_pixels = tuple(normalized for _ in range(MATRIX_SIZE))
        self._head_light_brightness = validated

    def set_head_brightness(self, brightness: float) -> None:
        validated = _validate_number(brightness, "head light brightness")
        if validated < 0 or validated > 1:
            raise StateValidationError("head light brightness must be between 0 and 1")
        self._head_light_brightness = validated

    def set_head_pixels(self, pixels: Sequence[str]) -> None:
        if isinstance(pixels, (str, bytes)) or len(pixels) != MATRIX_SIZE:
            raise StateValidationError(
                f"head light frame must contain exactly {MATRIX_SIZE} pixels"
            )
        normalized = tuple(
            _normalize_color(pixel, "head light pixel") for pixel in pixels
        )
        self._head_light_pixels = normalized

    def set_motion(self, state: str, action: Optional[str]) -> None:
        self.set_motion_and_queue(
            state,
            action,
            self._command_queue_size,
            self._command_queue_capacity,
        )

    def set_motion_and_queue(
        self,
        state: str,
        action: Optional[str],
        size: int,
        capacity: Optional[int] = None,
    ) -> None:
        if state not in {"idle", "moving", "error"}:
            raise StateValidationError("invalid motion state")
        if state == "moving":
            if not isinstance(action, str) or _ACTION_PATTERN.fullmatch(action) is None:
                raise StateValidationError("moving motion requires a valid action")
        elif action is not None:
            raise StateValidationError("idle or error motion requires a null action")
        new_capacity = self._command_queue_capacity if capacity is None else capacity
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise StateValidationError("command queue size must be a non-negative integer")
        if (
            isinstance(new_capacity, bool)
            or not isinstance(new_capacity, int)
            or new_capacity < 0
        ):
            raise StateValidationError(
                "command queue capacity must be a non-negative integer"
            )
        if size > new_capacity:
            raise StateValidationError("command queue size cannot exceed capacity")
        self._motion_state = state
        self._motion_action = action
        self._command_queue_size = size
        self._command_queue_capacity = new_capacity

    def set_command_queue(self, size: int, capacity: Optional[int] = None) -> None:
        self.set_motion_and_queue(
            self._motion_state,
            self._motion_action,
            size,
            capacity,
        )

    def set_status(self, mode: str) -> None:
        if mode not in STATUS_VISUALS:
            raise StateValidationError("invalid status mode")
        if self._status_mode == "error" and mode != "error":
            raise StateValidationError("error status must be explicitly cleared")
        self._status_mode = mode

    def clear_error_status(self, mode: str = "active") -> None:
        if mode == "error" or mode not in STATUS_VISUALS:
            raise StateValidationError("error status must clear to a non-error mode")
        self._status_mode = mode

    def set_connection(self, connection: str) -> None:
        if connection not in {"connecting", "ready", "degraded", "offline"}:
            raise StateValidationError("invalid connection state")
        self._connection = connection

    def observe(self) -> Dict[str, Any]:
        """Return a detached complete state without consuming a revision."""
        snapshot = self._build_snapshot(self._revision)
        self._validate_snapshot(snapshot)
        return snapshot

    def snapshot(self) -> Dict[str, Any]:
        """Backward-compatible alias for non-publishing observation."""
        return self.observe()

    def publish_snapshot(self) -> Dict[str, Any]:
        """Return a detached complete state and advance publication revision once."""
        next_revision = self._revision + 1
        snapshot = self._build_snapshot(next_revision)
        self._validate_snapshot(snapshot)
        self._revision = next_revision
        return snapshot

    def _build_snapshot(self, revision: int) -> Dict[str, Any]:
        strip_color, strip_effect = STATUS_VISUALS[self._status_mode]
        return {
            "device_id": self._device_id,
            "revision": revision,
            "connection": self._connection,
            "capabilities": copy.deepcopy(self._capabilities),
            "motion": {
                "state": self._motion_state,
                "action": self._motion_action,
                "joints": {
                    name: {
                        "pos": joint.position,
                        "min": joint.profile.minimum,
                        "max": joint.profile.maximum,
                    }
                    for name, joint in self._joints.items()
                },
            },
            "light": {
                "brightness": self._head_light_brightness,
                "matrix": {"width": MATRIX_WIDTH, "height": MATRIX_HEIGHT},
                "pixels": list(self._head_light_pixels),
            },
            "status": {"mode": self._status_mode},
            "status_strip": {"color": strip_color, "effect": strip_effect},
            "command_queue": {
                "size": self._command_queue_size,
                "capacity": self._command_queue_capacity,
            },
        }

    def _validate_complete_state(self) -> None:
        self._validate_snapshot(self._build_snapshot(self._revision))

    def _validate_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        try:
            validate_state(snapshot, self._device_id)
        except ProtocolError as error:
            raise StateValidationError(str(error)) from error
