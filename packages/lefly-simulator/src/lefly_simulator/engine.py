"""Command validation and execution for the deterministic simulator."""

import asyncio
import inspect
import logging
from collections.abc import Mapping
from typing import Any, Callable, Dict, List, Optional

from lefly_protocol import DeviceCommand, DeviceEvent

from .clock import Clock, RealClock
from .models import SimulatorState, StateValidationError
from .queue import EventFactory, EventSubscriber, MotionQueue


MOTION_TYPES = {
    "motion.absolute_move",
    "motion.relative_move",
    "motion.play",
    "device.rest",
}
IMMEDIATE_TYPES = {
    "device.get_state",
    "light.solid",
    "light.paint",
    "light.brightness",
    "status.set",
}

logger = logging.getLogger(__name__)


class SimulatorEngine:
    """Execute protocol commands and publish each event as it occurs."""

    def __init__(
        self,
        device_id: str,
        *,
        state: Optional[SimulatorState] = None,
        clock: Optional[Clock] = None,
        queue_capacity: int = 8,
        motion_steps: int = 4,
        event_factory: Optional[EventFactory] = None,
    ) -> None:
        self.state = SimulatorState.default(device_id) if state is None else state
        if self.state.device_id != device_id:
            raise ValueError("state device_id must match engine device_id")
        self._event_factory = event_factory or EventFactory(device_id)
        self._subscribers: List[EventSubscriber] = []
        self._publish_lock = asyncio.Lock()
        self.motion_queue = MotionQueue(
            self.state,
            clock or RealClock(),
            self._event_factory,
            self._publish,
            capacity=queue_capacity,
            steps=motion_steps,
        )

    def subscribe(self, subscriber: EventSubscriber) -> Callable[[], None]:
        if not callable(subscriber):
            raise TypeError("subscriber must be callable")
        self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

        return unsubscribe

    async def execute(self, command: DeviceCommand) -> List[DeviceEvent]:
        if not isinstance(command, DeviceCommand):
            raise TypeError("command must be a DeviceCommand")
        if command.device_id != self.state.device_id:
            return [
                await self._error(
                    command,
                    "wrong_device",
                    "command device_id does not match this endpoint",
                    False,
                )
            ]
        if command.message_type in MOTION_TYPES:
            return [await self.motion_queue.enqueue(command)]
        if command.message_type not in IMMEDIATE_TYPES:
            return [
                await self._error(
                    command,
                    "unsupported_command",
                    "unsupported command: %s" % command.message_type,
                    False,
                )
            ]

        try:
            mutation = self._validate_immediate(command)
            mutation()
        except (StateValidationError, ValueError, TypeError) as error:
            return [await self._error(command, "invalid_command", str(error), True)]

        accepted = self._event_factory.create(
            "command.accepted",
            {"command_type": command.message_type, "disposition": "applied"},
            correlation_id=command.message_id,
        )
        changed = self._event_factory.create(
            "device.state_changed",
            self.state.publish_snapshot(),
            correlation_id=command.message_id,
        )
        await self._publish(accepted)
        await self._publish(changed)
        return [accepted, changed]

    async def handle(self, command: DeviceCommand) -> List[DeviceEvent]:
        return await self.execute(command)

    async def inject_sensor(
        self, sensor_type: str, payload: Mapping[str, Any]
    ) -> DeviceEvent:
        """Validate and publish one synthetic sensor event."""
        event_type, normalized = self._validate_sensor(sensor_type, payload)
        event = self._event_factory.create(event_type, normalized)
        await self._publish(event)
        return event

    async def close(self) -> None:
        await self.motion_queue.close()

    @staticmethod
    def _validate_sensor(sensor_type: str, payload: Mapping[str, Any]):
        if sensor_type not in {"touch", "gesture", "face"}:
            raise ValueError("sensor_type must be touch, gesture, or face")
        if not isinstance(payload, Mapping):
            raise ValueError("sensor payload must be an object")

        if sensor_type == "touch":
            unknown = set(payload) - {"position", "pressed"}
            if unknown:
                raise ValueError("touch payload contains unknown fields")
            position = payload.get("position")
            if position not in {"left", "middle", "right"}:
                raise ValueError("touch position must be left, middle, or right")
            pressed = payload.get("pressed", True)
            if not isinstance(pressed, bool):
                raise ValueError("touch pressed must be boolean")
            return "sensor.touch", {"position": position, "pressed": pressed}

        unknown = set(payload) - {"id", "label", "confidence"}
        if unknown:
            raise ValueError("%s payload contains unknown fields" % sensor_type)
        if "id" not in payload:
            raise ValueError("%s id is required" % sensor_type)
        identifier = payload["id"]
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier < 0
        ):
            raise ValueError("%s id must be a non-negative integer" % sensor_type)
        normalized = {"id": identifier, "label": None, "confidence": None}
        if payload.get("label") is not None:
            label = payload.get("label")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("%s label must be non-empty" % sensor_type)
            normalized["label"] = label
        if payload.get("confidence") is not None:
            confidence = payload.get("confidence")
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or confidence < 0
                or confidence > 1
            ):
                raise ValueError("%s confidence must be between 0 and 1" % sensor_type)
            normalized["confidence"] = confidence
        event_type = (
            "sensor.vision.gesture" if sensor_type == "gesture" else "sensor.vision.face"
        )
        return event_type, normalized

    def _validate_immediate(self, command: DeviceCommand) -> Callable[[], None]:
        payload = command.payload
        if command.message_type == "device.get_state":
            return lambda: None
        if command.message_type == "light.solid":
            self._require_head_matrix(payload)
            color = payload["color"]
            return lambda: self.state.set_head_light(
                color, self.state.head_light_brightness
            )
        if command.message_type == "light.paint":
            self._require_head_matrix(payload)
            return lambda: self.state.set_head_pixels(payload["pixels"])
        if command.message_type == "light.brightness":
            self._require_head_matrix(payload)
            return lambda: self.state.set_head_brightness(payload["brightness"])
        if command.message_type == "status.set":
            return lambda: self._set_system_status(payload["mode"])
        raise ValueError("unsupported immediate command")

    def _set_system_status(self, mode: str) -> None:
        if self.state.status_mode == "error" and mode != "error":
            self.state.clear_error_status(mode)
            return
        self.state.set_status(mode)

    @staticmethod
    def _require_head_matrix(payload: Mapping[str, Any]) -> None:
        if payload.get("target") != "head_matrix":
            raise ValueError("target must be head_matrix")

    async def _error(
        self,
        command: DeviceCommand,
        code: str,
        message: str,
        recoverable: bool,
    ) -> DeviceEvent:
        event = self._event_factory.create(
            "device.error",
            {
                "code": code,
                "message": message,
                "recoverable": recoverable,
                "details": None,
            },
            correlation_id=command.message_id,
        )
        await self._publish(event)
        return event

    async def _publish(self, event: DeviceEvent) -> None:
        async with self._publish_lock:
            for subscriber in tuple(self._subscribers):
                try:
                    result = subscriber(event)
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "simulator event subscriber failed for %s", event.message_type
                    )
