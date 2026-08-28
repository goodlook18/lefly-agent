"""aiohttp application for the browser console and simulator endpoint."""

import asyncio
import inspect
import json
import logging
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import unquote
from uuid import uuid4

from aiohttp import WSMsgType, WSCloseCode, web
from lefly_protocol import DeviceCommand, DeviceEvent, ProtocolError
from lefly_sdk import (
    ClientClosedError,
    DeviceDisconnectedError,
    RemoteDeviceError,
    RequestTimeoutError,
)

from .queue import EventFactory
from .router import (
    LeaseGrant,
    LeaseResult,
    RouterError,
    RouterEvent,
    TargetRouter,
)
from .models import StateValidationError
from .target import TargetClosedError


logger = logging.getLogger(__name__)


@dataclass
class _RuntimeState:
    started: bool = False


APP_ROUTER = web.AppKey("lefly.router", object)
APP_HUB = web.AppKey("lefly.console_hub", object)
APP_RUNTIME = web.AppKey("lefly.runtime", _RuntimeState)
APP_STATIC_DIR = web.AppKey("lefly.static_dir", object)
APP_STATIC_FILESYSTEM = web.AppKey("lefly.static_filesystem", bool)
APP_OUTBOUND_CAPACITY = web.AppKey("lefly.outbound_capacity", int)


class OutboundQueue:
    """A bounded queue which closes its owner once on overflow."""

    def __init__(self, capacity: int, close_callback: Callable[[], Any]) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        if not callable(close_callback):
            raise TypeError("close_callback must be callable")
        self._queue = asyncio.Queue(maxsize=capacity)
        self._close_callback = close_callback
        self._close_task: Optional[asyncio.Task] = None

    def offer(self, value: Dict[str, Any]) -> bool:
        if self._close_task is not None:
            return False
        try:
            self._queue.put_nowait(value)
            return True
        except asyncio.QueueFull:
            self._close_task = asyncio.create_task(self._close_once())
            self._close_task.add_done_callback(self._close_finished)
            return False

    async def get(self) -> Dict[str, Any]:
        return await self._queue.get()

    async def wait_closed(self) -> None:
        if self._close_task is not None:
            await asyncio.shield(self._close_task)

    async def _close_once(self) -> None:
        result = self._close_callback()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _close_finished(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "outbound queue close callback failed",
                exc_info=(type(error), error, error.__traceback__),
            )


@dataclass
class _ConsoleClient:
    session_id: str
    websocket: web.WebSocketResponse
    outbound: OutboundQueue
    lease: Optional[LeaseGrant] = None
    sender: Optional[asyncio.Task] = None
    closed: bool = False
    close_task: Optional[asyncio.Task] = None


class _ConsoleHub:
    def __init__(self, router: TargetRouter, outbound_capacity: int) -> None:
        self.router = router
        self.outbound_capacity = outbound_capacity
        self.clients: Dict[str, _ConsoleClient] = {}
        self._unsubscribe: Optional[Callable[[], None]] = None
        self._closed = False

    async def start(self) -> None:
        try:
            await self.router.start()
            self._unsubscribe = self.router.subscribe(self._on_router_event)
        except BaseException:
            self._detach()
            await self.router.close()
            self._closed = True
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._detach()
        await asyncio.gather(
            *(self.remove(client, close_websocket=True) for client in tuple(self.clients.values())),
            return_exceptions=True,
        )
        await self.router.close()

    def _detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def add(self, websocket: web.WebSocketResponse) -> _ConsoleClient:
        if self._closed:
            raise RuntimeError("console hub is closed")
        session_id = str(uuid4())
        grant = self.router.acquire_control(session_id)
        lease = grant if isinstance(grant, LeaseGrant) else None
        outbound = OutboundQueue(
            self.outbound_capacity,
            lambda: websocket.close(
                code=WSCloseCode.POLICY_VIOLATION, message=b"slow client"
            ),
        )
        client = _ConsoleClient(session_id, websocket, outbound, lease)
        self.clients[session_id] = client
        client.sender = asyncio.create_task(self._send(client))
        return client

    async def remove(
        self, client: _ConsoleClient, *, close_websocket: bool = False
    ) -> None:
        if client.close_task is None:
            client.close_task = asyncio.create_task(self._remove_core(client))
        await asyncio.shield(client.close_task)
        if close_websocket and not client.websocket.closed:
            await client.websocket.close()

    async def _remove_core(self, client: _ConsoleClient) -> None:
        client.closed = True
        self.clients.pop(client.session_id, None)
        if client.lease is not None:
            self.router.release_control(client.lease)
            client.lease = None
        if client.sender is not None:
            client.sender.cancel()
            await asyncio.gather(client.sender, return_exceptions=True)
            client.sender = None
        await client.outbound.wait_closed()

    async def _send(self, client: _ConsoleClient) -> None:
        while not client.websocket.closed:
            value = await client.outbound.get()
            await client.websocket.send_json(value)

    def offer(self, client: _ConsoleClient, value: Dict[str, Any]) -> None:
        if not client.closed:
            client.outbound.offer(value)

    def broadcast(self, value: Dict[str, Any]) -> None:
        for client in tuple(self.clients.values()):
            self.offer(client, value)

    def take_control(self, client: _ConsoleClient) -> LeaseResult:
        current_owner = self.router.lease_owner
        if current_owner == client.session_id and client.lease is not None:
            return client.lease
        if current_owner is not None:
            previous = self.clients.get(current_owner)
            if previous is None or previous.lease is None:
                return RouterError(
                    "control_lease_unavailable",
                    "control owner is no longer available",
                    True,
                )
            released = self.router.release_control(previous.lease)
            if isinstance(released, RouterError):
                return released
            previous.lease = None
            _send_control(self, previous)
        client.lease = None
        return self.router.acquire_control(client.session_id)

    def _on_router_event(self, routed: RouterEvent) -> None:
        value = {"type": "console.event", **routed.to_dict()}
        self.broadcast(value)

    @property
    def client_count(self) -> int:
        return len(self.clients)

    @property
    def pending_task_count(self) -> int:
        return sum(
            client.sender is not None and not client.sender.done()
            for client in self.clients.values()
        )


def create_app(
    *,
    router: TargetRouter,
    static_dir: Optional[Any] = None,
    outbound_capacity: int = 64,
) -> web.Application:
    """Create an application around an injected target router."""
    app = web.Application()
    app[APP_ROUTER] = router
    app[APP_HUB] = _ConsoleHub(router, outbound_capacity)
    app[APP_RUNTIME] = _RuntimeState()
    if static_dir is None:
        app[APP_STATIC_DIR] = resources.files("lefly_simulator").joinpath("static")
        app[APP_STATIC_FILESYSTEM] = False
    else:
        app[APP_STATIC_DIR] = Path(static_dir)
        app[APP_STATIC_FILESYSTEM] = True
    app[APP_OUTBOUND_CAPACITY] = outbound_capacity
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    app.router.add_get("/health", _health)
    app.router.add_get("/api/targets", _targets)
    app.router.add_get("/ws/console", _console_socket)
    app.router.add_get("/ws/device/simulator", _device_socket)
    app.router.add_get("/{path:.*}", _static_or_fallback)
    return app


async def _startup(app: web.Application) -> None:
    await app[APP_HUB].start()
    app[APP_RUNTIME].started = True


async def _cleanup(app: web.Application) -> None:
    app[APP_RUNTIME].started = False
    await app[APP_HUB].close()


async def _health(request: web.Request) -> web.Response:
    router = request.app[APP_ROUTER]
    started = request.app[APP_RUNTIME].started
    return web.json_response(
        {
            "ok": started,
            "service": "lefly-simulator",
            "started": started,
            "target": {
                "id": router.active_target_id,
                "epoch": router.target_epoch,
            },
        }
    )


async def _targets(request: web.Request) -> web.Response:
    router = request.app[APP_ROUTER]
    targets = []
    for target_id, target in router.targets.items():
        snapshot = target.snapshot()
        capabilities = snapshot.get("capabilities", {})
        targets.append(
            {
                "id": target_id,
                "kind": target.kind,
                "active": target_id == router.active_target_id,
                "status": target.status,
                "capabilities": dict(capabilities)
                if isinstance(capabilities, Mapping)
                else {},
            }
        )
    return web.json_response({"targets": targets})


def _lease_value(router: TargetRouter, client: _ConsoleClient) -> Dict[str, Any]:
    if client.lease is None:
        return {"role": "readonly"}
    return {
        "role": "controller",
        "expires_at": router.lease_expiry_epoch(client.lease),
    }


async def _console_socket(request: web.Request) -> web.WebSocketResponse:
    websocket = web.WebSocketResponse()
    await websocket.prepare(request)
    hub = request.app[APP_HUB]
    client = None
    try:
        client = hub.add(websocket)
        snapshot = hub.router.snapshot()
        hub.offer(
            client,
            {
                "type": "console.hello",
                "session_id": client.session_id,
                "lease": _lease_value(hub.router, client),
                **snapshot,
            },
        )
        async for message in websocket:
            if message.type == WSMsgType.TEXT:
                try:
                    value = json.loads(message.data)
                except json.JSONDecodeError as error:
                    _console_error(hub, client, "invalid_json", str(error))
                    continue
                await _handle_console_message(hub, client, value)
            elif message.type == WSMsgType.BINARY:
                _console_error(
                    hub, client, "invalid_message", "binary messages are not supported"
                )
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        if client is None:
            await websocket.close()
        else:
            await hub.remove(client)
    return websocket


async def _handle_console_message(
    hub: _ConsoleHub, client: _ConsoleClient, value: Any
) -> None:
    if not isinstance(value, Mapping):
        _console_error(hub, client, "invalid_message", "message must be an object")
        return
    message_type = value.get("type")
    if not isinstance(message_type, str):
        _console_error(hub, client, "invalid_message", "type must be a string")
        return
    request_id = _console_request_id(value)

    if message_type == "console.acquire_control":
        if not _exact_fields(value, {"type"}):
            _console_error(hub, client, "invalid_message", "unexpected fields")
            return
        result = hub.take_control(client)
        if isinstance(result, RouterError):
            _send_router_error(hub, client, result)
        else:
            client.lease = result
            _send_control(hub, client)
        return

    known_types = {
        "console.renew_control",
        "console.command",
        "console.select_target",
        "console.inject_sensor",
    }
    if message_type not in known_types:
        _console_error(hub, client, "unknown_message", "unsupported console message")
        return

    if client.lease is None:
        _console_error(
            hub,
            client,
            "read_only",
            "session does not own control",
            request_id=request_id,
        )
        return

    if message_type == "console.renew_control":
        if not _exact_fields(value, {"type"}):
            _console_error(hub, client, "invalid_message", "unexpected fields")
            return
        result = hub.router.renew_control(client.lease)
        if isinstance(result, RouterError):
            client.lease = None
            _send_router_error(hub, client, result)
        else:
            client.lease = result
            _send_control(hub, client)
        return

    if message_type == "console.command":
        if not _exact_fields(value, {"type", "target_epoch", "command"}):
            _console_error(
                hub,
                client,
                "invalid_message",
                "unexpected fields",
                request_id=request_id,
            )
            return
        epoch = value.get("target_epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            _console_error(
                hub,
                client,
                "invalid_message",
                "target_epoch must be an integer",
                request_id=request_id,
            )
            return
        try:
            command = DeviceCommand.from_dict(value.get("command"))
        except (ProtocolError, TypeError) as error:
            _console_error(
                hub,
                client,
                "invalid_command",
                str(error),
                request_id=request_id,
            )
            return
        request_id = command.message_id
        try:
            result = await hub.router.route(client.lease, epoch, command)
        except (
            RemoteDeviceError,
            RequestTimeoutError,
            DeviceDisconnectedError,
            ClientClosedError,
            TargetClosedError,
        ) as error:
            _send_router_error(
                hub,
                client,
                _command_exception_error(error),
                request_id=request_id,
            )
            return
        except Exception:
            logger.exception("unexpected console command failure")
            _console_error(
                hub,
                client,
                "internal_error",
                "unexpected command failure",
                True,
                request_id=request_id,
            )
            return
        if isinstance(result, RouterError):
            _send_router_error(hub, client, result, request_id=request_id)
        return

    if message_type == "console.select_target":
        if not _exact_fields(value, {"type", "target_id"}):
            _console_error(hub, client, "invalid_message", "unexpected fields")
            return
        target_id = value.get("target_id")
        if not isinstance(target_id, str) or not target_id.strip():
            _console_error(hub, client, "invalid_message", "target_id must be non-empty")
            return
        result = await hub.router.select_target(client.lease, target_id)
        if isinstance(result, RouterError):
            _send_router_error(hub, client, result)
        else:
            hub.broadcast({"type": "console.state", **hub.router.snapshot()})
        return

    if message_type == "console.inject_sensor":
        if not _exact_fields(value, {"type", "sensor_type", "payload"}):
            _console_error(hub, client, "invalid_message", "unexpected fields")
            return
        sensor_type = value.get("sensor_type")
        payload = value.get("payload")
        if not isinstance(sensor_type, str) or not isinstance(payload, Mapping):
            _console_error(hub, client, "invalid_sensor", "invalid sensor message")
            return
        result = await hub.router.inject_sensor(client.lease, sensor_type, payload)
        if isinstance(result, RouterError):
            _send_router_error(hub, client, result)
        return

def _exact_fields(value: Mapping[str, Any], expected: set) -> bool:
    return set(value) == expected


def _console_request_id(value: Mapping[str, Any]) -> Optional[str]:
    if value.get("type") != "console.command":
        return None
    command = value.get("command")
    if not isinstance(command, Mapping):
        return None
    request_id = command.get("id")
    if not isinstance(request_id, str) or not request_id.strip():
        return None
    return request_id


def _send_control(hub: _ConsoleHub, client: _ConsoleClient) -> None:
    hub.offer(client, {"type": "console.control", "lease": _lease_value(hub.router, client)})


def _send_router_error(
    hub: _ConsoleHub,
    client: _ConsoleClient,
    error: RouterError,
    *,
    request_id: Optional[str] = None,
) -> None:
    if error.code == "invalid_control_lease":
        client.lease = None
    _console_error(
        hub,
        client,
        error.code,
        error.message,
        error.recoverable,
        request_id=request_id,
    )


def _command_exception_error(error: Exception) -> RouterError:
    if isinstance(error, RemoteDeviceError):
        return RouterError(error.code, error.message, error.recoverable)
    if isinstance(error, RequestTimeoutError):
        return RouterError(
            "request_timeout", "remote command timed out", True
        )
    if isinstance(error, DeviceDisconnectedError):
        return RouterError(
            "device_disconnected", "remote device disconnected", True
        )
    if isinstance(error, ClientClosedError):
        return RouterError("client_closed", "remote device client is closed")
    if isinstance(error, TargetClosedError):
        return RouterError("target_closed", "target is closed")
    raise TypeError("unsupported command exception")


def _console_error(
    hub: _ConsoleHub,
    client: _ConsoleClient,
    code: str,
    message: str,
    recoverable: bool = True,
    *,
    request_id: Optional[str] = None,
) -> None:
    value = {
        "type": "console.error",
        "code": code,
        "message": message,
        "recoverable": recoverable,
    }
    if request_id is not None:
        value["request_id"] = request_id
    hub.offer(
        client,
        value,
    )


def _device_error(
    factory: EventFactory,
    code: str,
    message: str,
    recoverable: bool,
    *,
    correlation_id: Optional[str] = None,
) -> DeviceEvent:
    return factory.create(
        "device.error",
        {
            "code": code,
            "message": message,
            "recoverable": recoverable,
            "details": None,
        },
        correlation_id=correlation_id,
    )


def _device_exception_event(
    error: Any, device_id: str, *, correlation_id: Optional[str] = None
) -> DeviceEvent:
    factory = EventFactory(device_id)
    if isinstance(error, RouterError):
        return _device_error(
            factory,
            error.code,
            "device command was rejected",
            error.recoverable,
            correlation_id=correlation_id,
        )
    if isinstance(error, (ProtocolError, StateValidationError)):
        return _device_error(
            factory,
            "invalid_command",
            "device command is invalid",
            True,
            correlation_id=correlation_id,
        )
    if isinstance(error, TargetClosedError):
        return _device_error(
            factory,
            "target_closed",
            "simulator target is closed",
            False,
            correlation_id=correlation_id,
        )
    if isinstance(error, asyncio.QueueFull):
        return _device_error(
            factory,
            "queue_full",
            "device command queue is full",
            True,
            correlation_id=correlation_id,
        )
    raise TypeError("unsupported device exception")


class _DeviceSession:
    """Own one device WebSocket's sender, queue, and target subscription."""

    def __init__(self, target: Any, websocket: Any, *, capacity: int) -> None:
        self.target = target
        self.websocket = websocket
        self.outbound = OutboundQueue(
            capacity,
            lambda: websocket.close(
                code=WSCloseCode.POLICY_VIOLATION, message=b"slow client"
            ),
        )
        self._factory = EventFactory(target.device_id)
        self._sender: Optional[asyncio.Task] = None
        self._unsubscribe: Optional[Callable[[], None]] = None
        self._closed = False

    @property
    def pending_task_count(self) -> int:
        return int(self._sender is not None and not self._sender.done())

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("device session is closed")
        self._sender = asyncio.create_task(self._send())
        try:
            self._unsubscribe = self.target.subscribe(self._on_event)
        except BaseException:
            await self.close(close_websocket=True)
            raise

    async def close(self, *, close_websocket: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:
                logger.exception("device session unsubscribe failed")
            self._unsubscribe = None
        if self._sender is not None:
            self._sender.cancel()
            await asyncio.gather(self._sender, return_exceptions=True)
            self._sender = None
        await self.outbound.wait_closed()
        if close_websocket and not self.websocket.closed:
            await self.websocket.close()

    def _on_event(self, value: DeviceEvent) -> None:
        if not self._closed:
            self.outbound.offer(value.to_dict())

    async def _send(self) -> None:
        while not self.websocket.closed:
            await self.websocket.send_json(await self.outbound.get())

    def _offer_exception(
        self, error: Any, *, correlation_id: Optional[str] = None
    ) -> None:
        value = _device_exception_event(
            error, self.target.device_id, correlation_id=correlation_id
        )
        self.outbound.offer(value.to_dict())

    async def handle_text(self, value: str) -> None:
        try:
            command = DeviceCommand.from_json(value)
            if (
                command.device_id is not None
                and command.device_id != self.target.device_id
            ):
                raise ProtocolError("device_id does not match simulator")
        except ProtocolError as error:
            self._offer_exception(error)
            return

        try:
            result = await self.target.command(command)
        except asyncio.CancelledError:
            raise
        except (
            ProtocolError,
            StateValidationError,
            TargetClosedError,
            asyncio.QueueFull,
        ) as error:
            self._offer_exception(error, correlation_id=command.message_id)
        except Exception:
            logger.exception("unexpected device command failure")
            event = _device_error(
                self._factory,
                "internal_error",
                "unexpected device command failure",
                False,
                correlation_id=command.message_id,
            )
            self.outbound.offer(event.to_dict())
        else:
            if isinstance(result, RouterError):
                self._offer_exception(result, correlation_id=command.message_id)

    def handle_binary(self) -> None:
        event = _device_error(
            self._factory,
            "invalid_command",
            "binary messages are not supported",
            True,
        )
        self.outbound.offer(event.to_dict())


async def _device_socket(request: web.Request) -> web.StreamResponse:
    router = request.app[APP_ROUTER]
    target = router.targets.get("simulator")
    if target is None or target.kind != "simulator":
        raise web.HTTPNotFound()

    websocket = web.WebSocketResponse()
    session = None
    try:
        await websocket.prepare(request)
        session = _DeviceSession(
            target, websocket, capacity=request.app[APP_OUTBOUND_CAPACITY]
        )
        try:
            await session.start()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("device session initialization failed")
            return websocket

        async for message in websocket:
            if message.type == WSMsgType.TEXT:
                await session.handle_text(message.data)
            elif message.type == WSMsgType.BINARY:
                session.handle_binary()
            elif message.type == WSMsgType.ERROR:
                logger.warning(
                    "device websocket protocol error: %s",
                    websocket.exception() or message.data,
                )
                break
    finally:
        if session is not None:
            await session.close()
        elif websocket.prepared:
            await websocket.close()
    return websocket


def _decode_path_variants(value: str):
    variants = []
    current = value
    for _ in range(8):
        variants.append(current)
        decoded = unquote(current)
        if decoded == current:
            return variants
        current = decoded
    raise ValueError("path is encoded too deeply")


def _path_variants(raw_path: str, route_path: str):
    raw_target = raw_path.split("?", 1)[0]
    variants = _decode_path_variants(raw_target)
    variants.extend(_decode_path_variants("/" + route_path.lstrip("/")))
    for value in variants:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("path contains control characters")
    return variants


def _is_reserved_path(raw_path: str, route_path: str) -> bool:
    """Return whether any decoded layer targets a lowercase API/WS route."""
    for value in _path_variants(raw_path, route_path):
        normalized = "/" + value.lstrip("/")
        if normalized in {"/api", "/ws"} or normalized.startswith(
            ("/api/", "/ws/")
        ):
            return True
    return False


def _asset_parts(raw_path: str, route_path: str):
    variants = _path_variants(raw_path, route_path)

    def is_asset(value: str) -> bool:
        relative = value.lstrip("/")
        return relative == "assets" or relative.startswith("assets/")

    if not any(is_asset(value) for value in variants):
        return None
    for value in variants:
        if "\\" in value:
            raise ValueError("asset path contains unsafe characters")
        if any(segment in {".", ".."} for segment in value.split("/")):
            raise ValueError("asset path contains unsafe segments")

    decoded_assets = next(
        value.lstrip("/") for value in reversed(variants) if is_asset(value)
    )
    return tuple(decoded_assets.split("/")[1:])


def _resolve_asset_path(
    static_dir: Path, raw_path: str, route_path: str
) -> Optional[Path]:
    """Resolve an asset request without allowing encoded path traversal."""
    parts = _asset_parts(raw_path, route_path)
    if parts is None:
        return None
    assets_root = (static_dir / "assets").resolve()
    candidate = assets_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(assets_root)
    except ValueError as error:
        raise ValueError("asset path escapes assets root") from error
    return candidate


def _resource_response(static_root: Any, parts) -> web.Response:
    resource = static_root.joinpath(*parts)
    if not resource.is_file():
        raise web.HTTPNotFound()
    content_type = mimetypes.guess_type(parts[-1])[0] if parts else None
    return web.Response(body=resource.read_bytes(), content_type=content_type)


async def _static_or_fallback(request: web.Request) -> web.StreamResponse:
    path = request.match_info.get("path", "")
    raw_path = request.raw_path.split("?", 1)[0]
    try:
        reserved = _is_reserved_path(raw_path, path)
    except ValueError:
        raise web.HTTPNotFound()
    if reserved:
        return web.json_response(
            {"error": {"code": "not_found", "message": "route not found"}},
            status=404,
        )
    static_root = request.app[APP_STATIC_DIR]
    try:
        asset_parts = _asset_parts(raw_path, path)
    except ValueError:
        raise web.HTTPNotFound()

    if request.app[APP_STATIC_FILESYSTEM]:
        static_dir = static_root
        asset_path = _resolve_asset_path(static_dir, raw_path, path)
        index = static_dir / "index.html"
        index_exists = index.is_file()
    else:
        index = static_root.joinpath("index.html")
        index_exists = index.is_file()
    if not index_exists:
        return web.Response(
            status=503,
            text="LeFly console frontend build is unavailable.",
        )
    if asset_parts is not None:
        if request.app[APP_STATIC_FILESYSTEM]:
            if not asset_path.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(asset_path)
        return _resource_response(static_root, ("assets",) + asset_parts)
    if request.app[APP_STATIC_FILESYSTEM]:
        return web.FileResponse(index)
    return _resource_response(static_root, ("index.html",))
