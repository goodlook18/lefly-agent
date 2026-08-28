"""Capability-gated, serialized robot mutations over the public LeFly SDK."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from lefly_protocol import ProtocolError, validate_state


_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RobotPolicyError(RuntimeError):
    """Raised when current authoritative state does not permit a command."""


class RobotController(Protocol):
    async def play_movement(self, name: str): ...
    async def set_light_color(self, color: str): ...
    async def set_light_brightness(self, brightness: float): ...
    async def enter_rest_state(self): ...
    async def get_state(self): ...


class RobotDeviceClient(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def subscribe(self, message_type: str, handler): ...


@dataclass(frozen=True)
class RobotCommandResult:
    tool: str
    correlation_id: str | None
    disposition: str


def _copy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_value(item) for item in value]
    return value


class RobotCommandService:
    """Own robot policy and serialize all Agent-originated mutations."""

    def __init__(
        self,
        controller: RobotController,
        device_client: RobotDeviceClient,
        *,
        device_id: str,
    ) -> None:
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be non-empty")
        self._controller = controller
        self._client = device_client
        self._device_id = device_id.strip()
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] | None = None
        self._last_revision: int | None = None
        self._rest_transition_pending = False
        self._state_ready = asyncio.Event()
        self._unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self._client.subscribe(
                "device.state_changed", self._on_state
            )

    async def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._state = None
        self._state_ready.clear()

    @property
    def is_ready(self) -> bool:
        return self._client.is_connected and self._state is not None

    def invalidate_state(self) -> None:
        self._state = None
        self._last_revision = None
        self._rest_transition_pending = False
        self._state_ready.clear()

    async def synchronize_state(self, *, timeout: float = 2.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with self._lock:
            if not self._client.is_connected:
                self.invalidate_state()
                raise RobotPolicyError("device is disconnected")
            self.invalidate_state()
            await self._controller.get_state()
            try:
                await asyncio.wait_for(self._state_ready.wait(), timeout=timeout)
            except asyncio.TimeoutError as error:
                self.invalidate_state()
                raise RobotPolicyError(
                    "authoritative device state synchronization timed out"
                ) from error

    def advertised_motion_presets(self) -> tuple[str, ...]:
        """Return a prompt-only summary; command methods still enforce policy."""
        if self._state is None or not self._client.is_connected:
            return ()
        presets = self._state["capabilities"]["motion"]["presets"]
        return tuple(item["name"] for item in presets)

    async def play_motion(self, name: str) -> RobotCommandResult:
        if not isinstance(name, str) or _ACTION_PATTERN.fullmatch(name) is None:
            raise RobotPolicyError("motion preset name is invalid")
        if self._rest_transition_pending:
            raise RobotPolicyError("device rest transition is in progress")
        async with self._lock:
            state = self._require_state()
            if self._rest_transition_pending:
                raise RobotPolicyError("device rest transition is in progress")
            if state["status"]["mode"] == "resting" and name != "wake_up":
                raise RobotPolicyError("device is resting")
            self._require_command(state, "motion.play")
            presets = {
                item["name"]
                for item in state["capabilities"]["motion"]["presets"]
            }
            if name not in presets:
                raise RobotPolicyError("motion preset is not advertised: %s" % name)
            acknowledgement = await self._controller.play_movement(name)
            return self._result("motion.play", acknowledgement)

    async def set_head_light(self, color: str) -> RobotCommandResult:
        if not isinstance(color, str) or _COLOR_PATTERN.fullmatch(color) is None:
            raise RobotPolicyError("head light color must use #RRGGBB format")
        async with self._lock:
            state = self._require_state()
            self._require_command(state, "light.solid")
            self._require_head_light(state)
            acknowledgement = await self._controller.set_light_color(color.upper())
            return self._result("light.solid", acknowledgement)

    async def set_head_light_brightness(self, value: float) -> RobotCommandResult:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise RobotPolicyError("head light brightness must be between 0 and 1")
        async with self._lock:
            state = self._require_state()
            self._require_command(state, "light.brightness")
            self._require_head_light(state)
            acknowledgement = await self._controller.set_light_brightness(float(value))
            return self._result("light.brightness", acknowledgement)

    async def enter_rest_state(self) -> RobotCommandResult:
        if self._rest_transition_pending:
            raise RobotPolicyError("device rest transition is already in progress")
        async with self._lock:
            state = self._require_state()
            self._require_command(state, "device.rest")
            if state["status"]["mode"] == "resting":
                raise RobotPolicyError("device is already resting")
            self._rest_transition_pending = True
            try:
                acknowledgement = await self._controller.enter_rest_state()
            except BaseException:
                self._rest_transition_pending = False
                raise
            return self._result("device.rest", acknowledgement)

    async def execute_action(self, action: Any) -> RobotCommandResult:
        """Adapt the Offline Demo's protocol-shaped action at one boundary."""
        arguments = action.arguments
        if action.tool == "motion.play":
            return await self.play_motion(arguments["name"])
        if action.tool == "light.solid":
            return await self.set_head_light(arguments["color"])
        if action.tool == "light.brightness":
            return await self.set_head_light_brightness(arguments["brightness"])
        if action.tool == "device.rest":
            return await self.enter_rest_state()
        raise RobotPolicyError("unsupported agent tool: %s" % action.tool)

    def _on_state(self, event: Any) -> None:
        payload = _copy_value(getattr(event, "payload", None))
        try:
            validate_state(payload, self._device_id)
        except (ProtocolError, TypeError, ValueError):
            self._state = None
            self._state_ready.clear()
            return

        revision = payload["revision"]
        if self._last_revision is not None and revision != self._last_revision + 1:
            self._last_revision = revision
            self._state = None
            self._state_ready.clear()
            return
        self._last_revision = revision
        if payload["connection"] not in {"ready", "degraded"}:
            self._state = None
            self._state_ready.clear()
            return
        self._state = payload
        self._state_ready.set()
        if payload["status"]["mode"] == "resting":
            self._rest_transition_pending = False

    def _require_state(self) -> dict[str, Any]:
        if not self._client.is_connected:
            self._state = None
            raise RobotPolicyError("device is disconnected")
        if self._state is None:
            raise RobotPolicyError("authoritative device state is unavailable")
        return self._state

    @staticmethod
    def _require_command(state: Mapping[str, Any], tool: str) -> None:
        metadata = state["capabilities"]["commands"].get(tool)
        if not isinstance(metadata, Mapping) or metadata.get("scope") != "control":
            raise RobotPolicyError("command is not advertised for control: %s" % tool)

    @staticmethod
    def _require_head_light(state: Mapping[str, Any]) -> None:
        if not any(
            item.get("target") == "head_matrix" and item.get("kind") == "rgb_matrix"
            for item in state["capabilities"]["lights"]
        ):
            raise RobotPolicyError("head_matrix light is not advertised")

    @staticmethod
    def _result(tool: str, acknowledgement: Any) -> RobotCommandResult:
        payload = getattr(acknowledgement, "payload", {})
        disposition = str(payload.get("disposition", "accepted"))
        correlation_id = getattr(acknowledgement, "correlation_id", None)
        return RobotCommandResult(tool, correlation_id, disposition)
