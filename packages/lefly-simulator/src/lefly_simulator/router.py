"""Lease-protected routing between simulator and remote targets."""

import asyncio
import copy
import inspect
import logging
import math
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional, Union

from lefly_protocol import DeviceCommand, DeviceEvent, ProtocolError, validate_state

from .target import EventHandler, Target


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouterError:
    code: str
    message: str
    recoverable: bool = False

    @property
    def payload(self) -> Dict[str, Any]:
        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }


@dataclass(frozen=True)
class LeaseGrant:
    owner: str
    token: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class RouterEvent:
    target_id: str
    target_epoch: int
    event: DeviceEvent
    error: Optional[RouterError] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "target_id": self.target_id,
            "target_epoch": self.target_epoch,
            "event": self.event.to_dict(),
        }
        if self.error is not None:
            result["error"] = self.error.to_dict()
        return result


LeaseResult = Union[LeaseGrant, RouterError]
RouteResult = Union[DeviceEvent, RouterError]
SelectionResult = Union[int, RouterError]


class ControlLease:
    """A renewable single-owner lease protected by an opaque token."""

    def __init__(
        self,
        ttl: float = 15.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
            raise ValueError("ttl must be a finite positive number")
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be a finite positive number")
        self._ttl = float(ttl)
        self._monotonic = monotonic
        self._token_factory = token_factory
        self._grant: Optional[LeaseGrant] = None

    @property
    def owner(self) -> Optional[str]:
        self._expire()
        return None if self._grant is None else self._grant.owner

    def acquire(self, owner: str) -> LeaseResult:
        if not isinstance(owner, str) or not owner.strip():
            return RouterError("invalid_session", "session owner must be non-empty")
        self._expire()
        if self._grant is not None:
            return RouterError(
                "control_lease_unavailable",
                "control is currently owned by another session",
                True,
            )
        token = self._token_factory()
        if not self._valid_token(token):
            return RouterError(
                "invalid_control_lease", "generated control token is invalid"
            )
        self._grant = LeaseGrant(owner, token, self._now() + self._ttl)
        return self._grant

    def renew(self, credential: Union[LeaseGrant, str]) -> LeaseResult:
        error = self.validate(credential)
        if error is not None:
            return error
        assert self._grant is not None
        self._grant = LeaseGrant(
            self._grant.owner,
            self._grant.token,
            self._now() + self._ttl,
        )
        return self._grant

    def release(self, credential: Union[LeaseGrant, str]) -> Union[bool, RouterError]:
        error = self.validate(credential)
        if error is not None:
            return error
        self._grant = None
        return True

    def expiry_epoch(
        self,
        grant: LeaseGrant,
        *,
        wall_time: Callable[[], float] = time.time,
    ) -> float:
        """Project an active grant using this lease's monotonic clock."""
        error = self.validate(grant)
        if error is not None:
            raise ValueError("control lease is not active")
        wall_now = wall_time()
        if isinstance(wall_now, bool) or not isinstance(wall_now, (int, float)):
            raise ValueError("wall clock must return a finite number")
        if not math.isfinite(wall_now):
            raise ValueError("wall clock must return a finite number")
        return float(wall_now) + max(0.0, grant.expires_at - self._now())

    def validate(
        self, credential: Union[LeaseGrant, str]
    ) -> Optional[RouterError]:
        self._expire()
        if self._grant is None:
            return RouterError("invalid_control_lease", "control lease is not active")
        if isinstance(credential, LeaseGrant):
            matches = (
                self._valid_token(credential.token)
                and secrets.compare_digest(credential.token, self._grant.token)
                and credential.owner == self._grant.owner
            )
        elif self._valid_token(credential):
            matches = secrets.compare_digest(credential, self._grant.token)
        else:
            matches = False
        if not matches:
            return RouterError(
                "invalid_control_lease", "control lease credential is invalid"
            )
        return None

    def _expire(self) -> None:
        if self._grant is not None and self._now() >= self._grant.expires_at:
            self._grant = None

    def _now(self) -> float:
        now = self._monotonic()
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("monotonic clock must return a finite number")
        if not math.isfinite(now):
            raise ValueError("monotonic clock must return a finite number")
        return float(now)

    @staticmethod
    def _valid_token(token: object) -> bool:
        return isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]+", token) is not None


class TargetRouter:
    """Route commands to one selected target and isolate stale target events."""

    def __init__(
        self,
        targets: Iterable[Target],
        *,
        active_target_id: Optional[str] = None,
        diagnostics: Optional[EventHandler] = None,
        lease: Optional[ControlLease] = None,
        lease_ttl: float = 15.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        target_list = list(targets)
        self.targets = {target.target_id: target for target in target_list}
        if not self.targets:
            raise ValueError("at least one target is required")
        if len(self.targets) != len(target_list):
            raise ValueError("target ids must be unique")
        if active_target_id is None and "simulator" in self.targets:
            active_target_id = "simulator"
        if active_target_id is not None and active_target_id not in self.targets:
            raise ValueError("active target is not registered")

        self.active_target_id = active_target_id
        self.target_epoch = 1 if active_target_id is not None else 0
        self._current_state: Dict[str, Any] = {}
        self._state_stale = False
        self._transport_stale = False
        self._stale_revision_floor: Optional[int] = None
        self._diagnostics = diagnostics
        self._subscribers = []
        self._lease = lease or ControlLease(lease_ttl, monotonic=monotonic)
        self._lock = asyncio.Lock()
        self._inflight: Dict[asyncio.Task, tuple] = {}
        self._active_unsubscribe: Optional[Callable[[], None]] = None
        self._callback_tasks = set()
        self._started_targets = set()
        self._closed_targets = set()
        self._started = False
        self._closed = False
        self._start_task: Optional[asyncio.Task] = None
        self._close_task: Optional[asyncio.Task] = None
        self._start_failure: Optional[BaseException] = None

    @property
    def lease_owner(self) -> Optional[str]:
        return self._lease.owner

    @property
    def current_state(self) -> Dict[str, Any]:
        return copy.deepcopy(self._current_state)

    @property
    def state_stale(self) -> bool:
        return self._state_stale

    @property
    def pending_task_count(self) -> int:
        tasks = tuple(self._inflight)
        if self._start_task is not None:
            tasks += (self._start_task,)
        if self._close_task is not None:
            tasks += (self._close_task,)
        tasks += tuple(self._callback_tasks)
        return sum(not task.done() for task in tasks)

    async def start(self) -> None:
        async with self._lock:
            if self._closed:
                if self._start_failure is not None:
                    raise self._start_failure
                raise RuntimeError("target router is closed")
            if self._start_task is None:
                self._start_task = asyncio.create_task(self._start())
            task = self._start_task
        await asyncio.shield(task)

    async def _start(self) -> None:
        results = await asyncio.gather(
            *(self._start_target(target_id) for target_id in self.targets),
            return_exceptions=True,
        )
        failure = next(
            (result for result in results if isinstance(result, BaseException)), None
        )
        if failure is not None:
            await self._close_targets_once(tuple(self._started_targets))
            async with self._lock:
                self._started = False
                self._closed = True
                self._start_failure = failure
            raise failure

        prepared = None
        try:
            if self.active_target_id is not None:
                prepared = self._prepare_activation(
                    self.active_target_id, self.target_epoch
                )
        except BaseException as failure:
            await self._close_targets_once(tuple(self._started_targets))
            async with self._lock:
                self._started = False
                self._closed = True
                self._start_failure = failure
            raise failure
        async with self._lock:
            if self._closed:
                should_commit = False
            else:
                should_commit = True
                self._started = True
                if prepared is not None:
                    state, unsubscribe, activation = prepared
                    self._current_state = state
                    self._state_stale = bool(
                        getattr(
                            self.targets[self.active_target_id],
                            "snapshot_stale",
                            False,
                        )
                    )
                    self._stale_revision_floor = None
                    self._active_unsubscribe = unsubscribe
                    activation["committed"] = True
                    activation["ready"].set()
        if not should_commit and prepared is not None:
            prepared[2]["ready"].set()
            prepared[1]()

    async def _start_target(self, target_id: str) -> None:
        await self.targets[target_id].start()
        async with self._lock:
            self._started_targets.add(target_id)
            self._closed_targets.discard(target_id)

    async def close(self) -> None:
        current_task = asyncio.current_task()
        async with self._lock:
            from_callback = current_task in self._callback_tasks
            if self._close_task is None:
                self._closed = True
                self._close_task = asyncio.create_task(self._close_core())
            task = self._close_task
        await asyncio.shield(task)
        if not from_callback:
            await self._drain_callbacks()

    async def _close_core(self) -> None:
        async with self._lock:
            start_task = self._start_task
        if start_task is not None and not start_task.done():
            try:
                await asyncio.shield(start_task)
            except BaseException:
                pass

        async with self._lock:
            inflight = tuple(self._inflight)
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)

        async with self._lock:
            unsubscribe = self._active_unsubscribe
            self._active_unsubscribe = None
        if unsubscribe is not None:
            unsubscribe()
        await self._close_targets_once(tuple(self.targets))
        async with self._lock:
            self._started = False

    async def _close_targets_once(self, target_ids: Iterable[str]) -> None:
        async with self._lock:
            pending = [
                target_id
                for target_id in target_ids
                if target_id not in self._closed_targets
            ]
            self._closed_targets.update(pending)
        await asyncio.gather(
            *(self.targets[target_id].close() for target_id in pending),
            return_exceptions=True,
        )

    def acquire_control(self, owner: str) -> LeaseResult:
        return self._lease.acquire(owner)

    def renew_control(self, credential: Union[LeaseGrant, str]) -> LeaseResult:
        return self._lease.renew(credential)

    def lease_expiry_epoch(
        self,
        grant: LeaseGrant,
        *,
        wall_time: Callable[[], float] = time.time,
    ) -> float:
        return self._lease.expiry_epoch(grant, wall_time=wall_time)

    def release_control(
        self, credential: Union[LeaseGrant, str]
    ) -> Union[bool, RouterError]:
        return self._lease.release(credential)

    async def select_target(
        self, credential: Union[LeaseGrant, str], target_id: str
    ) -> SelectionResult:
        async with self._lock:
            error = self._selection_error(credential, target_id)
            if error is not None:
                return error
            if target_id == self.active_target_id:
                return self.target_epoch
            previous_target = self.active_target_id
            previous_epoch = self.target_epoch
            next_epoch = previous_epoch + 1

        try:
            prepared = self._prepare_activation(target_id, next_epoch)
        except ValueError as error:
            return RouterError("invalid_target_snapshot", str(error))
        except _SubscriptionError as error:
            return RouterError("target_subscription_failed", str(error), True)
        except Exception as error:
            return RouterError("target_snapshot_failed", str(error), True)

        state, unsubscribe, activation = prepared
        old_unsubscribe = None
        async with self._lock:
            error = self._selection_error(credential, target_id)
            if error is None and (
                self.active_target_id != previous_target
                or self.target_epoch != previous_epoch
            ):
                error = RouterError(
                    "stale_target_epoch", "target changed while switch was prepared", True
                )
            if error is None:
                old_unsubscribe = self._active_unsubscribe
                self.active_target_id = target_id
                self.target_epoch = next_epoch
                self._current_state = state
                self._state_stale = bool(
                    getattr(self.targets[target_id], "snapshot_stale", False)
                )
                self._transport_stale = self._state_stale
                self._stale_revision_floor = None
                self._active_unsubscribe = unsubscribe
                activation["committed"] = True
            activation["ready"].set()
        if error is not None:
            unsubscribe()
            return error
        if old_unsubscribe is not None:
            old_unsubscribe()
        return next_epoch

    def _selection_error(
        self, credential: Union[LeaseGrant, str], target_id: str
    ) -> Optional[RouterError]:
        if self._closed:
            return self._terminal_error()
        if not self._started:
            return RouterError("router_not_started", "target router is not started")
        error = self._lease.validate(credential)
        if error is not None:
            return error
        if target_id not in self.targets:
            return RouterError("unknown_target", "target is not registered")
        if target_id == self.active_target_id:
            return None
        if any(epoch == self.target_epoch for _, epoch in self._inflight.values()):
            return RouterError(
                "target_busy", "active target has an in-flight command", True
            )
        if self._motion_busy(self._current_state):
            return RouterError(
                "target_busy", "active target has accepted or running motion", True
            )
        return None

    def _prepare_activation(self, target_id: str, epoch: int):
        target = self.targets[target_id]
        snapshot = target.snapshot()
        self._validate_snapshot(snapshot)
        state = copy.deepcopy(snapshot)
        activation = {"committed": False, "ready": asyncio.Event()}
        try:
            unsubscribe = target.subscribe(
                self._target_handler(target_id, epoch, activation)
            )
        except Exception as error:
            raise _SubscriptionError(str(error)) from error
        return state, unsubscribe, activation

    async def route(
        self,
        credential: Union[LeaseGrant, str],
        target_epoch: int,
        command: DeviceCommand,
    ) -> RouteResult:
        task = asyncio.current_task()
        assert task is not None
        async with self._lock:
            error = self._route_error(credential, target_epoch, command)
            if error is not None:
                return error
            assert self.active_target_id is not None
            target_id = self.active_target_id
            target = self.targets[target_id]
            self._inflight[task] = (target_id, target_epoch)
        try:
            return await target.command(command)
        finally:
            async with self._lock:
                self._inflight.pop(task, None)

    async def inject_sensor(
        self,
        credential: Union[LeaseGrant, str],
        sensor_type: str,
        payload: Mapping[str, Any],
    ) -> RouteResult:
        async with self._lock:
            if self._closed:
                return self._terminal_error()
            if not self._started:
                return RouterError("router_not_started", "target router is not started")
            error = self._lease.validate(credential)
            if error is not None:
                return error
            if self.active_target_id is None:
                return RouterError("no_active_target", "no target is selected")
            if self._state_stale:
                return RouterError(
                    "state_stale",
                    "active target state is stale",
                    True,
                )
            target = self.targets[self.active_target_id]
            if target.kind != "simulator":
                return RouterError(
                    "sensor_injection_unavailable",
                    "active target does not support sensor injection",
                )
            inject = getattr(target, "inject_sensor", None)
            if inject is None:
                return RouterError(
                    "sensor_injection_unavailable",
                    "active target does not support sensor injection",
                )
        try:
            return await inject(sensor_type, payload)
        except (TypeError, ValueError) as error:
            return RouterError("invalid_sensor", str(error))

    def _route_error(
        self,
        credential: Union[LeaseGrant, str],
        target_epoch: int,
        command: DeviceCommand,
    ) -> Optional[RouterError]:
        if self._closed:
            return self._terminal_error()
        if not self._started:
            return RouterError("router_not_started", "target router is not started")
        error = self._lease.validate(credential)
        if error is not None:
            return error
        if target_epoch != self.target_epoch:
            return RouterError(
                "stale_target_epoch",
                "command belongs to an earlier target selection",
                True,
            )
        if self.active_target_id is None:
            return RouterError("no_active_target", "no target is selected")
        target = self.targets[self.active_target_id]
        if getattr(target, "snapshot_stale", False):
            self._state_stale = True
            self._transport_stale = True
            self._stale_revision_floor = None
        if self._state_stale and command.message_type != "device.get_state":
            return RouterError(
                "state_stale",
                "active target state is stale",
                True,
            )
        device_id = getattr(target, "device_id", target.target_id)
        if command.device_id is not None and command.device_id != device_id:
            return RouterError(
                "target_mismatch", "command device_id does not match active target"
            )
        capabilities = self._current_state.get("capabilities")
        commands = (
            capabilities.get("commands")
            if isinstance(capabilities, Mapping)
            else None
        )
        metadata = (
            commands.get(command.message_type)
            if isinstance(commands, Mapping)
            else None
        )
        capability_allowed = isinstance(metadata, Mapping)
        if command.message_type != "device.get_state" and not capability_allowed:
            return RouterError(
                "unsupported_capability",
                "active target does not support this command",
            )
        if metadata is not None and metadata.get("scope") == "system":
            return RouterError(
                "system_command_forbidden",
                "system-scoped command is unavailable through console control",
            )
        return None

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    def snapshot(self) -> Dict[str, Any]:
        state = self.current_state
        target = self.targets.get(self.active_target_id)
        if target is not None:
            try:
                live_connection = target.snapshot().get("connection")
            except Exception:
                live_connection = None
            if live_connection in {"ready", "degraded", "offline"}:
                state["connection"] = live_connection
                if live_connection != "ready":
                    self._state_stale = True
                    self._transport_stale = True
                    self._stale_revision_floor = None
        if self._state_stale and state.get("connection") == "ready":
            state["connection"] = "degraded"
        return {
            "target_id": self.active_target_id,
            "target_epoch": self.target_epoch,
            "state": state,
        }

    def _target_handler(self, target_id: str, epoch: int, activation) -> EventHandler:
        def handle(event: DeviceEvent) -> None:
            if self._closed:
                return
            task = asyncio.create_task(
                self._process_target_event(target_id, epoch, activation, event)
            )
            self._callback_tasks.add(task)
            task.add_done_callback(self._callback_finished)

        return handle

    async def _process_target_event(
        self, target_id: str, epoch: int, activation, event: DeviceEvent
    ) -> None:
        await activation["ready"].wait()
        if not activation["committed"]:
            return
        diagnostic_error = None
        forward = False
        async with self._lock:
            is_active = (
                not self._closed
                and target_id == self.active_target_id
                and epoch == self.target_epoch
            )
            if is_active and event.message_type == "device.state_changed":
                payload = event.to_dict()["payload"]
                revision = payload.get("revision")
                current_revision = self._current_state.get("revision")
                if not self._valid_revision(revision):
                    forward = False
                else:
                    authoritative = self._authoritative_snapshot(
                        target_id,
                        revision,
                        current_revision,
                        allow_revision_reset=self._transport_stale,
                    )
                    if self._transport_stale and authoritative is not None:
                        self._current_state = authoritative
                        self._state_stale = False
                        self._transport_stale = False
                        self._stale_revision_floor = None
                        forward = True
                    elif revision <= current_revision:
                        forward = False
                        authoritative = None
                    else:
                        authoritative = None
                if self._valid_revision(revision) and not forward and revision > current_revision:
                    if revision > current_revision + 1:
                        self._raise_stale_revision_floor(revision)
                    authoritative = self._authoritative_snapshot(
                        target_id, revision, current_revision
                    )
                    if authoritative is not None and (
                        self._state_stale or revision > current_revision + 1
                    ):
                        self._current_state = authoritative
                        self._state_stale = False
                        self._transport_stale = False
                        self._stale_revision_floor = None
                        forward = True
                    elif self._state_stale or revision > current_revision + 1:
                        self._state_stale = True
                        diagnostic_error = RouterError(
                            "revision_gap",
                            "state revision skipped one or more updates",
                            True,
                        )
                        forward = True
                    else:
                        if not self._valid_full_snapshot(payload):
                            self._state_stale = True
                            diagnostic_error = RouterError(
                                "invalid_state",
                                "device state update is not a complete Protocol v1 snapshot",
                                True,
                            )
                        else:
                            self._current_state = copy.deepcopy(payload)
                            self._state_stale = False
                            self._transport_stale = False
                            self._stale_revision_floor = None
                            forward = True
            elif is_active:
                forward = True
            subscribers = tuple(self._subscribers) if forward else ()
        routed = RouterEvent(target_id, epoch, event, diagnostic_error)
        self._schedule_callback(self._diagnostics, routed, "diagnostics")
        for subscriber in subscribers:
            self._schedule_callback(
                subscriber, routed, "router event subscriber"
            )

    def _authoritative_snapshot(
        self,
        target_id: str,
        revision: int,
        current_revision: int,
        *,
        allow_revision_reset: bool = False,
    ) -> Optional[Dict[str, Any]]:
        try:
            snapshot = self.targets[target_id].snapshot()
        except Exception:
            return None
        if getattr(self.targets[target_id], "snapshot_stale", False):
            return None
        if snapshot.get("revision") != revision:
            return None
        if not allow_revision_reset and revision <= current_revision:
            return None
        if not allow_revision_reset and (
            self._stale_revision_floor is not None
            and revision < self._stale_revision_floor
        ):
            return None
        if not self._valid_full_snapshot(snapshot):
            return None
        return copy.deepcopy(snapshot)

    def _raise_stale_revision_floor(self, revision: int) -> None:
        if (
            self._stale_revision_floor is None
            or revision > self._stale_revision_floor
        ):
            self._stale_revision_floor = revision

    def _schedule_callback(
        self, handler: Optional[Callable], value: Any, label: str
    ) -> None:
        if handler is None:
            return
        task = asyncio.create_task(self._run_callback(handler, value, label))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_finished)

    def _callback_finished(self, task: asyncio.Task) -> None:
        self._callback_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "router callback task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _run_callback(
        self, handler: Callable, value: Any, label: str
    ) -> None:
        try:
            result = handler(value)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s failed", label)

    async def _drain_callbacks(self) -> None:
        pending = list(self._callback_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _terminal_error(self) -> RouterError:
        if self._start_failure is not None:
            return RouterError(
                "start_failed", "target router failed during startup"
            )
        return RouterError("router_closed", "target router is closed")

    @classmethod
    def _validate_snapshot(cls, snapshot: object) -> None:
        if not cls._valid_full_snapshot(snapshot):
            raise ValueError("target snapshot must be a complete Protocol v1 state")

    @classmethod
    def _valid_full_snapshot(cls, snapshot: object) -> bool:
        if not isinstance(snapshot, Mapping):
            return False
        device_id = snapshot.get("device_id")
        try:
            validate_state(snapshot, device_id)
        except (ProtocolError, TypeError):
            return False
        return True

    @staticmethod
    def _valid_revision(revision: object) -> bool:
        return isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0

    @staticmethod
    def _motion_busy(snapshot: Dict[str, Any]) -> bool:
        motion = snapshot.get("motion", {})
        queue = snapshot.get("command_queue", {})
        return motion.get("state") == "moving" or queue.get("size", 0) > 0


class _SubscriptionError(RuntimeError):
    pass
