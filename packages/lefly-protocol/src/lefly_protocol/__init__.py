"""Public API for the LeFly Device Protocol."""

from .catalog import (
    COMMAND_TYPES,
    EVENT_TYPES,
    IMMEDIATE_COMMAND_TYPES,
    MOTION_COMMAND_TYPES,
    STATUS_EFFECTS,
    STATUS_MODES,
    is_known_command_type,
    is_known_event_type,
)

from .messages import (
    PROTOCOL_VERSION,
    DeviceCommand,
    DeviceEvent,
    ProtocolError,
)
from .validation import validate_command, validate_envelope, validate_error, validate_event, validate_state

__all__ = [
    "COMMAND_TYPES",
    "EVENT_TYPES",
    "IMMEDIATE_COMMAND_TYPES",
    "MOTION_COMMAND_TYPES",
    "PROTOCOL_VERSION",
    "STATUS_EFFECTS",
    "STATUS_MODES",
    "DeviceCommand",
    "DeviceEvent",
    "ProtocolError",
    "is_known_command_type",
    "is_known_event_type",
    "validate_command",
    "validate_envelope",
    "validate_error",
    "validate_event",
    "validate_state",
]
