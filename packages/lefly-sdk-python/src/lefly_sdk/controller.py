"""Agent-facing hardware operations implemented with LeFly protocol commands."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Protocol
from uuid import uuid4

from lefly_protocol import DeviceCommand, DeviceEvent, STATUS_MODES


_NAMED_COLORS = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "green": "#00FF00",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
    "warm_white": "#FFF0D0",
}


class _DeviceRequester(Protocol):
    async def request(self, command: DeviceCommand) -> DeviceEvent:
        ...


class RemoteHardwareController:
    def __init__(
        self,
        client: _DeviceRequester,
        *,
        device_id: str,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._client = client
        self._device_id = device_id
        self._id_factory = id_factory
        self._clock = clock

    async def set_light_color(self, color: str) -> DeviceEvent:
        color = self._normalize_color(color)
        return await self._request(
            "light.solid", {"target": "head_matrix", "color": color}
        )

    async def set_light_rgb(self, red: int, green: int, blue: int) -> DeviceEvent:
        channels = (red, green, blue)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in channels):
            raise ValueError("RGB channels must be integers")
        if any(value < 0 or value > 255 for value in channels):
            raise ValueError("RGB channels must be between 0 and 255")
        return await self.set_light_color("#%02X%02X%02X" % channels)

    async def set_light_brightness(self, brightness: float) -> DeviceEvent:
        if isinstance(brightness, bool) or not isinstance(brightness, (int, float)):
            raise ValueError("brightness must be a number")
        if brightness < 0 or brightness > 1:
            raise ValueError("brightness must be between 0 and 1")
        return await self._request(
            "light.brightness",
            {"target": "head_matrix", "brightness": float(brightness)},
        )

    async def paint_rgb_pattern(self, pixels: Sequence[Sequence[int]]) -> DeviceEvent:
        if isinstance(pixels, (str, bytes)) or not pixels:
            raise ValueError("pixels must contain at least one RGB value")
        normalized = []
        for pixel in pixels:
            if len(pixel) != 3:
                raise ValueError("each pixel must contain three RGB channels")
            channels = list(pixel)
            if any(isinstance(value, bool) or not isinstance(value, int) for value in channels):
                raise ValueError("RGB channels must be integers")
            if any(value < 0 or value > 255 for value in channels):
                raise ValueError("RGB channels must be between 0 and 255")
            normalized.append("#%02X%02X%02X" % tuple(channels))
        return await self._request(
            "light.paint", {"target": "head_matrix", "pixels": normalized}
        )

    async def play_movement(self, name: str) -> DeviceEvent:
        self._require_text("movement name", name)
        return await self._request("motion.play", {"name": name})

    async def play_relative_movement(
        self, joints: Mapping[str, Any], *, duration_ms: int = 700
    ) -> DeviceEvent:
        return await self._move("motion.relative_move", joints, duration_ms)

    async def play_absolute_move(
        self, joints: Mapping[str, Any], *, duration_ms: int = 700
    ) -> DeviceEvent:
        return await self._move("motion.absolute_move", joints, duration_ms)

    async def enter_rest_state(self) -> DeviceEvent:
        return await self._request("device.rest", {})

    async def get_state(self) -> DeviceEvent:
        return await self._request("device.get_state", {})

    async def set_status(self, mode: str) -> DeviceEvent:
        if mode not in STATUS_MODES:
            raise ValueError("status mode is not supported")
        return await self._request("status.set", {"mode": mode})

    async def _move(
        self, message_type: str, joints: Mapping[str, Any], duration_ms: int
    ) -> DeviceEvent:
        if not isinstance(joints, Mapping) or not joints:
            raise ValueError("joints must be a non-empty mapping")
        normalized = {}
        for name, value in joints.items():
            self._require_text("joint name", name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("joint values must be numeric degrees")
            normalized[name] = value
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not 1 <= duration_ms <= 60000:
            raise ValueError("duration_ms must be between 1 and 60000")
        return await self._request(
            message_type, {"joints": normalized, "duration_ms": duration_ms}
        )

    async def _request(self, message_type: str, payload: Dict[str, Any]) -> DeviceEvent:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        timestamp = now.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        command = DeviceCommand(
            message_id=self._id_factory(),
            message_type=message_type,
            timestamp=timestamp.replace("+00:00", "Z"),
            payload=payload,
            device_id=self._device_id,
        )
        return await self._client.request(command)

    @staticmethod
    def _require_text(name: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _normalize_color(color: str) -> str:
        if not isinstance(color, str):
            raise ValueError("color must be text")
        normalized = _NAMED_COLORS.get(color.strip().lower(), color.strip().upper())
        if len(normalized) != 7 or normalized[0] != "#":
            raise ValueError("color must be a known name or #RRGGBB")
        try:
            int(normalized[1:], 16)
        except ValueError as exc:
            raise ValueError("color must be a known name or #RRGGBB") from exc
        return normalized
