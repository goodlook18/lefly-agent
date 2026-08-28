"""Hardware-free target adapters for simulator and remote endpoints."""

import asyncio
import copy
import inspect
import logging
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Literal, Optional, Protocol
from uuid import uuid4

from lefly_protocol import DeviceCommand, DeviceEvent, ProtocolError, validate_state
from lefly_sdk import DeviceDisconnectedError

from .engine import SimulatorEngine


EventHandler = Callable[[DeviceEvent], Any]
Unsubscribe = Callable[[], None]
logger = logging.getLogger(__name__)


class TargetClosedError(RuntimeError):
    """Raised when a command is sent to a permanently closed target."""


class Target(Protocol):
    target_id: str
    kind: Literal["simulator", "remote"]

    @property
    def is_connected(self) -> bool: ...

    @property
    def status(self) -> str: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def command(self, command: DeviceCommand) -> DeviceEvent: ...

    def subscribe(self, handler: EventHandler) -> Unsubscribe: ...

    def snapshot(self) -> Dict[str, Any]: ...


class SimulatorTarget:
    """Expose a :class:`SimulatorEngine` through the target interface."""

    kind: Literal["simulator"] = "simulator"

    def __init__(self, target_id: str, engine: SimulatorEngine) -> None:
        if not target_id:
            raise ValueError("target_id must be a non-empty string")
        self.target_id = target_id
        self.device_id = engine.state.device_id
        self._engine = engine
        self._started = False
        self._closed = False
        self._close_task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        return self._started and not self._closed

    @property
    def status(self) -> str:
        if self._closed:
            return "closed"
        return "ready" if self._started else "stopped"

    async def start(self) -> None:
        if self._closed:
            raise TargetClosedError("simulator target is closed")
        self._started = True

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._started = False
            self._close_task = asyncio.create_task(self._engine.close())
        await asyncio.shield(self._close_task)

    async def command(self, command: DeviceCommand) -> DeviceEvent:
        if self._closed:
            raise TargetClosedError("simulator target is closed")
        if not self._started:
            raise RuntimeError("simulator target is not started")
        events = await self._engine.execute(command)
        return events[0]

    async def inject_sensor(
        self, sensor_type: str, payload: Mapping[str, Any]
    ) -> DeviceEvent:
        if self._closed:
            raise TargetClosedError("simulator target is closed")
        if not self._started:
            raise RuntimeError("simulator target is not started")
        return await self._engine.inject_sensor(sensor_type, payload)

    def subscribe(self, handler: EventHandler) -> Unsubscribe:
        return self._engine.subscribe(handler)

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self._engine.state.snapshot())


class RemoteTarget:
    """Adapt an SDK ``DeviceClient`` without importing physical drivers."""

    kind: Literal["remote"] = "remote"

    def __init__(
        self,
        target_id: str,
        *,
        url: Optional[str] = None,
        device_id: Optional[str] = None,
        client: Optional[Any] = None,
        client_factory: Optional[Callable[[str], Any]] = None,
        connect_timeout: float = 5.0,
        snapshot_timeout: float = 5.0,
        resync_base_delay: float = 0.25,
        resync_max_delay: float = 5.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not target_id:
            raise ValueError("target_id must be a non-empty string")
        if connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        if (
            isinstance(snapshot_timeout, bool)
            or not isinstance(snapshot_timeout, (int, float))
            or not math.isfinite(snapshot_timeout)
            or snapshot_timeout <= 0
        ):
            raise ValueError("snapshot_timeout must be a finite positive number")
        for name, delay in (
            ("resync_base_delay", resync_base_delay),
            ("resync_max_delay", resync_max_delay),
        ):
            if (
                isinstance(delay, bool)
                or not isinstance(delay, (int, float))
                or not math.isfinite(delay)
                or delay <= 0
            ):
                raise ValueError(f"{name} must be a finite positive number")
        if resync_max_delay < resync_base_delay:
            raise ValueError("resync_max_delay must be at least resync_base_delay")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if client is not None and client_factory is not None:
            raise ValueError("provide client or client_factory, not both")
        if client is None:
            if url is None:
                raise ValueError("url is required when client is not provided")
            if client_factory is None:
                from lefly_sdk import DeviceClient

                client_factory = DeviceClient
            client = client_factory(url)

        self.target_id = target_id
        self.device_id = device_id or target_id
        self._client = client
        self._connect_timeout = connect_timeout
        self._snapshot_timeout = float(snapshot_timeout)
        self._resync_base_delay = float(resync_base_delay)
        self._resync_max_delay = float(resync_max_delay)
        self._sleep = sleep
        self._id_factory = id_factory
        self._utc_now = utc_now
        self._handlers = []
        self._latest_snapshot: Dict[str, Any] = {"device_id": self.device_id}
        self._snapshot_ready = asyncio.Event()
        self._resync_ready = asyncio.Event()
        self._snapshot_stale = True
        self._recovery_floor: Optional[int] = None
        self._unsubscribe_client: Optional[Unsubscribe] = None
        self._start_task: Optional[asyncio.Task] = None
        self._close_task: Optional[asyncio.Task] = None
        self._resync_task: Optional[asyncio.Task] = None
        self._lifecycle_lock = asyncio.Lock()
        self._client_needs_close = False
        self._callback_tasks = set()
        self._start_failure: Optional[BaseException] = None
        self._closed = False
        self._starting = False

    @property
    def is_connected(self) -> bool:
        return not self._closed and bool(self._client.is_connected)

    @property
    def status(self) -> str:
        if self._closed:
            return "closed"
        if self.is_connected:
            return "connected"
        return "connecting" if self._starting else "disconnected"

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                message = (
                    "remote target start failed"
                    if self._start_failure is not None
                    else "remote target is closed"
                )
                raise TargetClosedError(message) from self._start_failure
            if self._start_task is None:
                self._start_task = asyncio.create_task(self._start())
            task = self._start_task
        try:
            await asyncio.shield(task)
        except BaseException:
            raise

    async def _start(self) -> None:
        self._starting = True
        try:
            if self._unsubscribe_client is None:
                self._unsubscribe_client = self._client.subscribe("*", self._on_event)
            self._client_needs_close = True
            await self._client.start()
            await self._client.wait_until_connected(timeout=self._connect_timeout)
            await self._client.request(self._state_request())
            await asyncio.wait_for(
                self._snapshot_ready.wait(), timeout=self._snapshot_timeout
            )
        except BaseException as error:
            async with self._lifecycle_lock:
                self._closed = True
                self._start_failure = error
            self._detach_client_subscription()
            await self._close_client_once()
            raise
        finally:
            self._starting = False

    async def close(self) -> None:
        current_task = asyncio.current_task()
        async with self._lifecycle_lock:
            from_callback = current_task in self._callback_tasks
            if self._close_task is None:
                self._closed = True
                self._close_task = asyncio.create_task(self._close_core())
            task = self._close_task
        await asyncio.shield(task)
        if not from_callback:
            await self._drain_callbacks()

    async def _close_core(self) -> None:
        async with self._lifecycle_lock:
            start_task = self._start_task
        if start_task is not None and not start_task.done():
            start_task.cancel()
            try:
                await start_task
            except BaseException:
                pass
        resync_task = self._resync_task
        if resync_task is not None and not resync_task.done():
            resync_task.cancel()
            await asyncio.gather(resync_task, return_exceptions=True)
        self._detach_client_subscription()
        await self._close_client_once()

    def _detach_client_subscription(self) -> None:
        if self._unsubscribe_client is not None:
            self._unsubscribe_client()
            self._unsubscribe_client = None

    async def _close_client_once(self) -> None:
        async with self._lifecycle_lock:
            if not self._client_needs_close:
                return
            self._client_needs_close = False
        await self._client.close()

    async def command(self, command: DeviceCommand) -> DeviceEvent:
        if self._closed:
            raise TargetClosedError("remote target is closed")
        try:
            return await self._client.request(command)
        except DeviceDisconnectedError:
            self._snapshot_stale = True
            self._recovery_floor = None
            self._resync_ready.clear()
            self._ensure_resync()
            raise

    def subscribe(self, handler: EventHandler) -> Unsubscribe:
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def snapshot(self) -> Dict[str, Any]:
        if not self._snapshot_ready.is_set():
            raise RuntimeError("remote target has no complete device state")
        result = copy.deepcopy(self._latest_snapshot)
        if not self.is_connected:
            result["connection"] = "offline"
        elif self._snapshot_stale:
            result["connection"] = "degraded"
        else:
            result["connection"] = "ready"
        return result

    @property
    def snapshot_stale(self) -> bool:
        return self._snapshot_stale or not self.is_connected

    @property
    def pending_task_count(self) -> int:
        tasks = tuple(self._callback_tasks)
        if self._start_task is not None:
            tasks += (self._start_task,)
        if self._close_task is not None:
            tasks += (self._close_task,)
        if self._resync_task is not None:
            tasks += (self._resync_task,)
        return sum(not task.done() for task in tasks)

    def _state_request(self) -> DeviceCommand:
        now = self._utc_now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("utc_now must return a timezone-aware datetime")
        timestamp = now.astimezone(timezone.utc).isoformat(timespec="milliseconds")
        return DeviceCommand(
            message_id=self._id_factory(),
            message_type="device.get_state",
            timestamp=timestamp.replace("+00:00", "Z"),
            device_id=self.device_id,
            payload={},
        )

    def _on_event(self, event: DeviceEvent) -> None:
        if self._closed:
            return
        if event.device_id is not None and event.device_id != self.device_id:
            return
        if event.message_type == "device.state_changed":
            payload = event.to_dict()["payload"]
            if not self._valid_full_snapshot(payload, self.device_id):
                return
            if not self._snapshot_ready.is_set():
                self._latest_snapshot = copy.deepcopy(payload)
                self._snapshot_stale = False
                self._recovery_floor = None
                self._snapshot_ready.set()
            else:
                revision = payload.get("revision")
                current_revision = self._latest_snapshot.get("revision")
                if not self._valid_revision(revision):
                    return
                if self._snapshot_stale:
                    if (
                        self._recovery_floor is not None
                        and revision < self._recovery_floor
                    ):
                        self._ensure_resync()
                    else:
                        self._latest_snapshot = copy.deepcopy(payload)
                        self._snapshot_stale = False
                        self._recovery_floor = None
                        self._resync_ready.set()
                elif revision <= current_revision:
                    return
                elif revision > current_revision + 1:
                    self._snapshot_stale = True
                    self._raise_recovery_floor(revision)
                    self._resync_ready.clear()
                    self._ensure_resync()
                else:
                    self._latest_snapshot = copy.deepcopy(payload)
                    self._snapshot_stale = False
                    self._recovery_floor = None

        for handler in tuple(self._handlers):
            self._schedule_callback(handler, event)

    def _ensure_resync(self) -> None:
        if self._closed:
            return
        if self._resync_task is not None and not self._resync_task.done():
            return
        self._resync_task = asyncio.create_task(self._resync_state())
        self._resync_task.add_done_callback(self._resync_finished)

    def _raise_recovery_floor(self, revision: int) -> None:
        if self._recovery_floor is None or revision > self._recovery_floor:
            self._recovery_floor = revision

    async def _resync_state(self) -> None:
        delay = self._resync_base_delay
        while self._snapshot_stale and not self._closed:
            self._resync_ready.clear()
            try:
                await self._client.request(self._state_request())
                if not self._snapshot_stale:
                    return
                await asyncio.wait_for(
                    self._resync_ready.wait(), timeout=self._snapshot_timeout
                )
                if not self._snapshot_stale:
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("remote target state resync failed", exc_info=True)
            if self._closed or not self._snapshot_stale:
                return
            sleep_result = self._sleep(delay)
            if not inspect.isawaitable(sleep_result):
                raise TypeError("sleep must return an awaitable")
            woke_for_snapshot = await self._wait_for_retry(
                sleep_result, self._resync_ready
            )
            if woke_for_snapshot and not self._snapshot_stale:
                return
            delay = min(self._resync_max_delay, delay * 2)

    @staticmethod
    async def _wait_for_retry(sleep_result: Any, ready: asyncio.Event) -> bool:
        sleep_task = asyncio.ensure_future(sleep_result)
        ready_task = asyncio.create_task(ready.wait())
        try:
            done, _ = await asyncio.wait(
                (sleep_task, ready_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if sleep_task in done:
                await sleep_task
                return False
            await ready_task
            return True
        finally:
            for task in (sleep_task, ready_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleep_task, ready_task, return_exceptions=True)

    def _resync_finished(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "remote target resync task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _schedule_callback(self, handler: EventHandler, event: DeviceEvent) -> None:
        task = asyncio.create_task(self._run_callback(handler, event))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_finished)

    def _callback_finished(self, task: asyncio.Task) -> None:
        self._callback_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "remote target callback task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _run_callback(self, handler: EventHandler, event: DeviceEvent) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("remote target event subscriber failed")

    async def _drain_callbacks(self) -> None:
        pending = list(self._callback_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def _valid_revision(revision: object) -> bool:
        return isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0

    @classmethod
    def _valid_full_snapshot(cls, snapshot: object, device_id: str) -> bool:
        try:
            validate_state(snapshot, device_id)
        except (ProtocolError, TypeError):
            return False
        return True
