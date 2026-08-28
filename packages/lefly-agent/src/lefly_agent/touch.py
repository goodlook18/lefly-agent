"""Bounded deterministic touch-to-behavior dispatch."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .config import TOUCH_POSITIONS, TouchBehavior


logger = logging.getLogger(__name__)
_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MOTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class TouchRobotService(Protocol):
    async def play_motion(self, name: str): ...
    async def set_head_light(self, color: str): ...


@dataclass(frozen=True)
class TouchDispatchEvent:
    position: str
    outcome: str
    error_type: str | None = None


class TouchBehaviorDispatcher:
    def __init__(
        self,
        robot: TouchRobotService,
        mappings: Mapping[str, TouchBehavior],
        *,
        queue_capacity: int = 4,
        observer: Callable[[TouchDispatchEvent], Any] | None = None,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if set(mappings) != set(TOUCH_POSITIONS):
            raise ValueError("touch mappings must contain left, middle, and right positions")
        self._robot = robot
        self._mappings = {
            position: self._validate_behavior(position, mappings[position])
            for position in TOUCH_POSITIONS
        }
        self._observers = [] if observer is None else [observer]
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_capacity)
        self._worker: asyncio.Task | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    def subscribe(self, observer: Callable[[TouchDispatchEvent], Any]) -> Callable[[], None]:
        if not callable(observer):
            raise TypeError("observer must be callable")
        self._observers.append(observer)

        def unsubscribe() -> None:
            if observer in self._observers:
                self._observers.remove(observer)

        return unsubscribe

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(
                self._run(), name="lefly-touch-behavior-worker"
            )

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        self._idle.set()

    def submit(self, event: Any) -> str:
        payload = getattr(event, "payload", {})
        if payload.get("pressed") is not True:
            return "ignored"
        position = payload.get("position")
        if position not in self._mappings:
            self._emit(TouchDispatchEvent(str(position), "invalid_position"))
            return "ignored"
        behavior = self._mappings[position]
        if behavior.motion is None and behavior.light_color is None:
            return "ignored"
        try:
            self._queue.put_nowait(position)
        except asyncio.QueueFull:
            self._emit(TouchDispatchEvent(position, "dropped_queue_full"))
            return "dropped"
        self._idle.clear()
        self._emit(TouchDispatchEvent(position, "enqueued"))
        return "enqueued"

    async def wait_until_idle(self, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self._idle.wait(), timeout)

    async def _run(self) -> None:
        while True:
            position = await self._queue.get()
            try:
                behavior = self._mappings[position]
                if behavior.motion is not None:
                    await self._robot.play_motion(behavior.motion)
                if behavior.light_color is not None:
                    await self._robot.set_head_light(behavior.light_color)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._emit(
                    TouchDispatchEvent(position, "failed", type(error).__name__)
                )
            else:
                self._emit(TouchDispatchEvent(position, "completed"))
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._idle.set()

    def _emit(self, event: TouchDispatchEvent) -> None:
        for observer in tuple(self._observers):
            try:
                result = observer(event)
                if inspect.isawaitable(result):
                    task = asyncio.create_task(result)
                    task.add_done_callback(self._observer_finished)
            except Exception:
                logger.error("touch observer failed", exc_info=True)

    @staticmethod
    def _observer_finished(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("async touch observer failed", exc_info=True)

    @staticmethod
    def _validate_behavior(position: str, behavior: TouchBehavior) -> TouchBehavior:
        if not isinstance(behavior, TouchBehavior):
            raise TypeError("touch.%s must be TouchBehavior" % position)
        if behavior.motion is not None and _MOTION_PATTERN.fullmatch(behavior.motion) is None:
            raise ValueError("touch.%s motion is invalid" % position)
        if behavior.light_color is not None and _COLOR_PATTERN.fullmatch(behavior.light_color) is None:
            raise ValueError("touch.%s light_color must use #RRGGBB" % position)
        return behavior
