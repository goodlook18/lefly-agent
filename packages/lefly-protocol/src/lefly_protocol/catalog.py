"""Canonical Protocol v1 catalogs and semantic constants."""

COMMAND_TYPES = frozenset(
    {
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
)

EVENT_TYPES = frozenset(
    {
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
)

MOTION_COMMAND_TYPES = frozenset(
    {"motion.play", "motion.relative_move", "motion.absolute_move", "device.rest"}
)
IMMEDIATE_COMMAND_TYPES = COMMAND_TYPES - MOTION_COMMAND_TYPES
STATUS_MODES = frozenset(
    {"starting", "resting", "active", "listening", "thinking", "speaking", "error"}
)
STATUS_EFFECTS = frozenset({"fade", "breath", "solid", "marquee", "level_sweep", "blink"})


def is_known_command_type(message_type: str) -> bool:
    return message_type in COMMAND_TYPES


def is_known_event_type(message_type: str) -> bool:
    return message_type in EVENT_TYPES
