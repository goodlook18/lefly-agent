"""Bounded FIFO motion execution with canonical Protocol v1 lifecycle events."""

import asyncio
import inspect
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, Mapping, Optional, Union
from uuid import uuid4

from lefly_protocol import DeviceCommand, DeviceEvent

from .clock import Clock
from .models import DEFAULT_JOINTS, SimulatorState, StateValidationError


EventSubscriber = Callable[[DeviceEvent], Union[None, Awaitable[None]]]

PRESET_POSES = {
    "wake_up": {"base_pitch": -10, "elbow_pitch": 38, "wrist_pitch": 8},
    "nod": {"wrist_pitch": 18},
    "headshake": {"wrist_roll": 18},
    "happy_wiggle": {"base_yaw": 18, "elbow_pitch": 52, "wrist_roll": -14},
    "look_up": {"wrist_pitch": -28},
    "look_down": {"wrist_pitch": 24},
    "look_left": {"base_yaw": 42},
    "look_right": {"base_yaw": -42},
    "dance_demo": {
        "base_yaw": 14,
        "base_pitch": -8,
        "elbow_pitch": 60,
        "wrist_roll": 24,
    },
    "sleep": {
        "base_yaw": 0,
        "base_pitch": -45,
        "elbow_pitch": 105,
        "wrist_roll": 0,
        "wrist_pitch": 45,
    },
}

DEFAULT_DURATION_MS = 600
MAX_DURATION_MS = 60_000
SEEN_COMMAND_LIMIT = 1024

logger = logging.getLogger(__name__)


class EventFactory:
    """Create canonical events with endpoint identity and UTC timestamps."""

    def __init__(
        self,
        device_id: str,
        *,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id must be a non-empty string")
        self._device_id = device_id
        self._id_factory = id_factory
        self._utc_now = utc_now

    def create(
        self,
        message_type: str,
        payload: Mapping[str, Any],
        *,
        correlation_id: Optional[str] = None,
    ) -> DeviceEvent:
        now = self._utc_now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("utc_now must return a timezone-aware datetime")
        timestamp = now.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        return DeviceEvent(
            message_id=self._id_factory(),
            message_type=message_type,
            timestamp=timestamp.replace("+00:00", "Z"),
            payload=payload,
            device_id=self._device_id,
            correlation_id=correlation_id,
        )


@dataclass
class _MotionJob:
    command: DeviceCommand
    action: str
    targets: Mapping[str, float]
    duration_ms: int
    resting: bool = False
    ready: asyncio.Event = field(default_factory=asyncio.Event, compare=False)
    elapsed_ms: int = 0


class _MotionCancelled(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MotionQueue:
    """Execute one active motion while bounding waiting jobs separately."""

    def __init__(
        self,
        state: SimulatorState,
        clock: Clock,
        event_factory: EventFactory,
        emit: EventSubscriber,
        *,
        capacity: int = 8,
        steps: int = 4,
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if isinstance(steps, bool) or not isinstance(steps, int) or steps <= 0:
            raise ValueError("steps must be a positive integer")
        self._state = state
        self._clock = clock
        self._event_factory = event_factory
        self._emit_callback = emit
        self._capacity = capacity
        self._steps = steps
        self._waiting: Deque[_MotionJob] = deque()
        self._accepted: Dict[str, _MotionJob] = {}
        self._seen_order: Deque[str] = deque()
        self._seen_ids = set()
        self._current: Optional[_MotionJob] = None
        self._wake = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._close_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._event_lock = asyncio.Lock()
        self._enqueue_tasks = set()
        self._closed = False
        self._rest_transition_id: Optional[str] = None
        self._interrupt = asyncio.Event()
        self._projected = {
            name: joint.position for name, joint in self._state.joints.items()
        }
        self._state.set_command_queue(0, capacity)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def size(self) -> int:
        return len(self._waiting)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def worker_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def pending_task_count(self) -> int:
        tasks = list(self._enqueue_tasks)
        if self._worker is not None:
            tasks.append(self._worker)
        if self._close_task is not None:
            tasks.append(self._close_task)
        return sum(not task.done() for task in tasks)

    async def enqueue(self, command: DeviceCommand) -> DeviceEvent:
        operation = asyncio.create_task(self._enqueue(command))
        self._enqueue_tasks.add(operation)
        operation.add_done_callback(self._enqueue_tasks.discard)
        return await asyncio.shield(operation)

    async def _enqueue(self, command: DeviceCommand) -> DeviceEvent:
        try:
            job = self._prepare(command)
        except (StateValidationError, ValueError, TypeError) as error:
            return await self._error(command, "invalid_command", str(error), True)

        cancelled_waiting = []
        interrupt_active = False
        async with self._lock:
            if self._closed:
                rejection = ("queue_closed", "motion queue is closed", False)
            elif command.message_id in self._seen_ids:
                rejection = (
                    "duplicate_command",
                    "motion command id has already been accepted",
                    False,
                )
            elif self._rest_transition_id is not None:
                rejection = (
                    "device_resting",
                    "device rest transition is already in progress",
                    True,
                )
            elif job.resting:
                rejection = None
                cancelled_waiting = list(self._waiting)
                for waiting_job in cancelled_waiting:
                    self._accepted.pop(waiting_job.command.message_id, None)
                self._waiting.clear()
                self._waiting.append(job)
                self._accepted[command.message_id] = job
                self._remember_id_locked(command.message_id)
                self._rest_transition_id = command.message_id
                self._projected = {
                    name: joint.position for name, joint in self._state.joints.items()
                }
                self._projected.update(job.targets)
                self._state.set_command_queue(len(self._waiting), self._capacity)
                accepted = self._event_factory.create(
                    "command.accepted",
                    {"command_type": command.message_type, "disposition": "queued"},
                    correlation_id=command.message_id,
                )
                queued_state = self._state_event(command.message_id)
                interrupt_active = (
                    self._current is not None and not self._current.resting
                )
                self._ensure_worker_locked()
                self._wake.set()
            elif len(self._waiting) >= self._capacity:
                rejection = ("queue_full", "motion waiting queue is full", True)
            else:
                rejection = None
                self._waiting.append(job)
                self._accepted[command.message_id] = job
                self._remember_id_locked(command.message_id)
                self._projected.update(job.targets)
                self._state.set_command_queue(len(self._waiting), self._capacity)
                accepted = self._event_factory.create(
                    "command.accepted",
                    {"command_type": command.message_type, "disposition": "queued"},
                    correlation_id=command.message_id,
                )
                queued_state = self._state_event(command.message_id)
                self._ensure_worker_locked()
                self._wake.set()
        if rejection is not None:
            return await self._error(command, *rejection)

        try:
            cancelled_events = [
                self._finished_event(
                    waiting_job, "cancelled", reason="device_rest"
                )
                for waiting_job in cancelled_waiting
            ]
            await self._emit_group(accepted, queued_state, *cancelled_events)
            if interrupt_active:
                self._interrupt.set()
            return accepted
        finally:
            job.ready.set()

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close(), name="lefly-motion-queue-close"
            )
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        async with self._lock:
            self._closed = True
            worker = self._worker
            if worker is not None and not worker.done():
                worker.cancel()
        if worker is not None:
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("motion worker failed while closing")

        async with self._lock:
            unfinished = list(self._accepted.values())
            self._accepted.clear()
            self._waiting.clear()
            self._current = None
            self._rest_transition_id = None
            self._interrupt.clear()
            self._projected = {
                name: joint.position for name, joint in self._state.joints.items()
            }
            self._state.set_motion_and_queue("idle", None, 0, self._capacity)
            self._worker = None

        events = []
        for job in unfinished:
            events.append(self._state_event(job.command.message_id))
            events.append(self._finished_event(job, "cancelled", reason="device_shutdown"))
        await self._emit_group(*events)

    def _ensure_worker_locked(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="lefly-motion-queue")

    def _remember_id_locked(self, message_id: str) -> None:
        if len(self._seen_order) >= SEEN_COMMAND_LIMIT:
            expired = self._seen_order.popleft()
            if expired not in self._accepted:
                self._seen_ids.discard(expired)
        self._seen_order.append(message_id)
        self._seen_ids.add(message_id)

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            async with self._lock:
                if self._closed:
                    return
                if not self._waiting:
                    self._wake.clear()
                    continue
                job = self._waiting.popleft()
                self._current = job
                if not self._waiting:
                    self._wake.clear()
            await job.ready.wait()
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except _MotionCancelled as cancelled:
                await self._finish_job(
                    job, "cancelled", reason=cancelled.reason
                )
            except Exception as error:
                logger.exception("motion worker failed for %s", job.command.message_id)
                await self._finish_job(job, "failed", error_message=str(error))
            else:
                await self._finish_job(job, "completed")

    async def _run_job(self, job: _MotionJob) -> None:
        correlation_id = job.command.message_id
        async with self._lock:
            if not job.resting and self._state.status_mode == "resting":
                self._state.set_status("starting")
            self._state.set_motion_and_queue(
                "moving", job.action, len(self._waiting), self._capacity
            )
            started = self._event_factory.create(
                "motion.started",
                {
                    "command_type": job.command.message_type,
                    "action": job.action,
                    "duration_ms": job.duration_ms,
                },
                correlation_id=correlation_id,
            )
            started_state = self._state_event(correlation_id)
        await self._emit_group(started, started_state)

        starts = {
            name: self._state.joints[name].position for name in job.targets
        }
        step_seconds = job.duration_ms / self._steps / 1000.0
        for step in range(1, self._steps + 1):
            await self._sleep_or_interrupt(step_seconds, job)
            fraction = step / self._steps
            positions = {
                name: (
                    target
                    if step == self._steps
                    else starts[name] + (target - starts[name]) * fraction
                )
                for name, target in job.targets.items()
            }
            self._state.set_joints(positions)
            job.elapsed_ms = round(job.duration_ms * fraction)
            progress = self._event_factory.create(
                "motion.progress",
                {
                    "action": job.action,
                    "progress": fraction,
                    "elapsed_ms": job.elapsed_ms,
                    "joints": self._joint_positions(),
                },
                correlation_id=correlation_id,
            )
            await self._emit_group(progress, self._state_event(correlation_id))

    async def _finish_job(
        self,
        job: _MotionJob,
        status: str,
        *,
        reason: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        clear_rest_transition = False
        clear_interrupt = False
        async with self._lock:
            if job.command.message_id not in self._accepted:
                return
            del self._accepted[job.command.message_id]
            self._current = None
            if status == "completed" and job.resting:
                if self._state.status_mode != "error":
                    self._state.set_status("resting")
            elif status == "completed" and self._state.status_mode == "starting":
                self._state.set_status("active")
            elif status == "failed":
                self._state.set_status("error")
            motion_state = "error" if status == "failed" else "idle"
            self._state.set_motion_and_queue(
                motion_state, None, len(self._waiting), self._capacity
            )
            state_event = self._state_event(job.command.message_id)
            finished = self._finished_event(
                job, status, reason=reason, error_message=error_message
            )
            clear_rest_transition = job.resting
            clear_interrupt = status == "cancelled" and reason == "device_rest"
        await self._emit_group(state_event, finished)
        async with self._lock:
            if clear_interrupt:
                self._interrupt.clear()
            if (
                clear_rest_transition
                and self._rest_transition_id == job.command.message_id
            ):
                self._rest_transition_id = None

    async def _sleep_or_interrupt(self, seconds: float, job: _MotionJob) -> None:
        if job.resting:
            await self._clock.sleep(seconds)
            return

        sleep_task = asyncio.create_task(self._clock.sleep(seconds))
        interrupt_task = asyncio.create_task(self._interrupt.wait())
        tasks = (sleep_task, interrupt_task)
        try:
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            if interrupt_task in done and self._interrupt.is_set():
                raise _MotionCancelled("device_rest")
            await sleep_task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _finished_event(
        self,
        job: _MotionJob,
        status: str,
        *,
        reason: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> DeviceEvent:
        error = None
        if status == "failed":
            error = {
                "code": "motion_failed",
                "message": error_message or "motion execution failed",
                "recoverable": True,
                "details": None,
            }
        return self._event_factory.create(
            "motion.finished",
            {
                "action": job.action,
                "status": status,
                "elapsed_ms": job.elapsed_ms,
                "joints": self._joint_positions(),
                "reason": reason if status == "cancelled" else None,
                "error": error,
            },
            correlation_id=job.command.message_id,
        )

    def _prepare(self, command: DeviceCommand) -> _MotionJob:
        if command.message_type == "motion.absolute_move":
            duration_ms = self._duration(command.payload["duration_ms"])
            targets = self._absolute_targets(command.payload["joints"])
            return _MotionJob(command, "absolute_move", targets, duration_ms)
        if command.message_type == "motion.relative_move":
            duration_ms = self._duration(command.payload["duration_ms"])
            targets = self._relative_targets(command.payload["joints"])
            return _MotionJob(command, "relative_move", targets, duration_ms)
        if command.message_type == "motion.play":
            name = command.payload["name"]
            if name not in PRESET_POSES:
                raise ValueError("unknown motion preset: %r" % name)
            return _MotionJob(
                command,
                name,
                dict(PRESET_POSES[name]),
                DEFAULT_DURATION_MS,
            )
        if command.message_type == "device.rest":
            targets = {profile.name: profile.default for profile in DEFAULT_JOINTS}
            return _MotionJob(
                command,
                "rest",
                targets,
                DEFAULT_DURATION_MS,
                resting=True,
            )
        raise ValueError("unsupported motion command: %s" % command.message_type)

    def _absolute_targets(self, joints: object) -> Dict[str, float]:
        values = self._joint_mapping(joints)
        return {
            name: self._validated_target(name, value)
            for name, value in values.items()
        }

    def _relative_targets(self, joints: object) -> Dict[str, float]:
        values = self._joint_mapping(joints)
        return {
            name: self._validated_target(
                name,
                self._projected[name] + self._number(delta, "%s delta" % name),
            )
            for name, delta in values.items()
        }

    def _joint_mapping(self, joints: object) -> Mapping[str, Any]:
        if not isinstance(joints, Mapping) or not joints:
            raise ValueError("joints must be a non-empty object")
        for name in joints:
            if name not in self._state.joints:
                raise ValueError("unknown joint: %r" % name)
        return joints

    def _validated_target(self, name: str, value: object) -> float:
        numeric = self._number(value, "%s position" % name)
        profile = self._state.joints[name].profile
        if numeric < profile.minimum or numeric > profile.maximum:
            raise ValueError(
                "%s position must be between %s and %s"
                % (name, profile.minimum, profile.maximum)
            )
        return numeric

    @staticmethod
    def _duration(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("duration_ms must be an integer")
        if value <= 0 or value > MAX_DURATION_MS:
            raise ValueError(
                "duration_ms must be greater than 0 and at most %d" % MAX_DURATION_MS
            )
        return value

    @staticmethod
    def _number(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("%s must be numeric" % label)
        if not math.isfinite(value):
            raise ValueError("%s must be finite" % label)
        return value

    def _joint_positions(self) -> Dict[str, float]:
        return {name: joint.position for name, joint in self._state.joints.items()}

    def _state_event(self, correlation_id: Optional[str]) -> DeviceEvent:
        return self._event_factory.create(
            "device.state_changed",
            self._state.publish_snapshot(),
            correlation_id=correlation_id,
        )

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
        await self._emit_group(event)
        return event

    async def _emit_group(self, *events: DeviceEvent) -> None:
        async with self._event_lock:
            for event in events:
                await self._emit(event)

    async def _emit(self, event: DeviceEvent) -> None:
        result = self._emit_callback(event)
        if inspect.isawaitable(result):
            await result
