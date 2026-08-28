"""Agent Control WebSocket server, separate from LeFly Device Protocol."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlsplit
from uuid import uuid4

from aiohttp import WSMsgType, WSCloseCode, web

from .models import AGENT_LIFECYCLE_TYPES, validate_lifecycle_event
from .runtime import AgentQueueFullError, AgentRuntime

logger = logging.getLogger(__name__)

APP_RUNTIME = web.AppKey("lefly.agent_runtime", AgentRuntime)
APP_HUB = web.AppKey("lefly.agent_hub", object)


@dataclass
class _Client:
    websocket: web.WebSocketResponse
    outbound: asyncio.Queue
    sender: Optional[asyncio.Task] = None


class _AgentHub:
    def __init__(self, runtime: AgentRuntime, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("outbound capacity must be positive")
        self.runtime = runtime
        self.capacity = capacity
        self.clients: Dict[str, _Client] = {}
        self._unsubscribe: Optional[Callable[[], None]] = None

    async def start(self) -> None:
        self._unsubscribe = self.runtime.subscribe(self.broadcast)

    async def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for client_id in tuple(self.clients):
            await self.remove(client_id, close_socket=True)

    def add(self, websocket: web.WebSocketResponse) -> str:
        client_id = str(uuid4())
        client = _Client(websocket, asyncio.Queue(maxsize=self.capacity))
        self.clients[client_id] = client
        client.sender = asyncio.create_task(self._send(client))
        return client_id

    async def remove(self, client_id: str, *, close_socket: bool = False) -> None:
        client = self.clients.pop(client_id, None)
        if client is None:
            return
        if client.sender is not None:
            client.sender.cancel()
            await asyncio.gather(client.sender, return_exceptions=True)
        if close_socket and not client.websocket.closed:
            await client.websocket.close()

    def offer(self, client_id: str, value: Dict[str, Any]) -> None:
        client = self.clients.get(client_id)
        if client is None:
            return
        try:
            client.outbound.put_nowait(value)
        except asyncio.QueueFull:
            asyncio.create_task(
                client.websocket.close(
                    code=WSCloseCode.POLICY_VIOLATION,
                    message=b"slow agent client",
                )
            )

    def broadcast(self, event: Dict[str, Any]) -> None:
        if event.get("type") in AGENT_LIFECYCLE_TYPES:
            try:
                event = validate_lifecycle_event(event)
            except (TypeError, ValueError):
                logger.error("invalid Agent lifecycle event was not broadcast")
                return
        value = {"version": "1", **event}
        for client_id in tuple(self.clients):
            self.offer(client_id, value)

    async def _send(self, client: _Client) -> None:
        while not client.websocket.closed:
            value = await client.outbound.get()
            await client.websocket.send_json(value)


def create_app(
    *,
    runtime: AgentRuntime,
    outbound_capacity: int = 64,
) -> web.Application:
    app = web.Application()
    app[APP_RUNTIME] = runtime
    app[APP_HUB] = _AgentHub(runtime, outbound_capacity)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    app.router.add_get("/health", _health)
    app.router.add_get("/ws/agent", _agent_socket)
    return app


async def _startup(app: web.Application) -> None:
    await app[APP_HUB].start()


async def _cleanup(app: web.Application) -> None:
    await app[APP_HUB].close()


async def _health(request: web.Request) -> web.Response:
    runtime = request.app[APP_RUNTIME]
    state = dict(runtime.snapshot())
    state.pop("messages", None)
    return web.json_response(
        {"ok": True, "service": "lefly-agent", "state": state}
    )


async def _agent_socket(request: web.Request) -> web.WebSocketResponse:
    if not _allowed_browser_origin(request.headers.get("Origin")):
        raise web.HTTPForbidden(text="Agent Control accepts local browser origins only")
    runtime = request.app[APP_RUNTIME]
    hub = request.app[APP_HUB]
    websocket = web.WebSocketResponse(max_msg_size=16 * 1024)
    await websocket.prepare(request)
    client_id = hub.add(websocket)
    hub.offer(
        client_id,
        {
            "version": "1",
            "type": "agent.hello",
            "session_id": client_id,
            "state": runtime.snapshot(),
        },
    )
    try:
        async for message in websocket:
            if message.type == WSMsgType.TEXT:
                await _handle_text(runtime, hub, client_id, message.data)
            elif message.type == WSMsgType.BINARY:
                hub.offer(
                    client_id,
                    _error("binary_not_supported", "binary messages are not supported"),
                )
            elif message.type == WSMsgType.ERROR:
                break
    finally:
        await hub.remove(client_id)
    return websocket


def _allowed_browser_origin(origin: Optional[str]) -> bool:
    if origin is None:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


async def _handle_text(
    runtime: AgentRuntime,
    hub: _AgentHub,
    client_id: str,
    raw: str,
) -> None:
    request_id: Optional[str] = None
    try:
        decoded = json.loads(raw)
        request_id, text = _parse_submission(decoded)
        await runtime.submit_text(request_id, text)
    except json.JSONDecodeError:
        hub.offer(client_id, _error("invalid_json", "message must be valid JSON"))
    except AgentQueueFullError as error:
        hub.offer(
            client_id,
            _error("queue_full", str(error), request_id=request_id),
        )
    except (TypeError, ValueError) as error:
        hub.offer(
            client_id,
            _error("invalid_message", str(error), request_id=request_id),
        )
    except RuntimeError as error:
        hub.offer(
            client_id,
            _error("agent_unavailable", str(error), request_id=request_id),
        )
    else:
        hub.offer(
            client_id,
            {
                "version": "1",
                "type": "agent.accepted",
                "request_id": request_id,
            },
        )


def _parse_submission(value: Any):
    if not isinstance(value, Mapping):
        raise TypeError("message must be an object")
    expected = {"version", "id", "type", "timestamp", "text"}
    unknown = set(value) - expected
    if unknown:
        raise ValueError("unknown field: %s" % sorted(unknown)[0])
    missing = expected - set(value)
    if missing:
        raise ValueError("missing field: %s" % sorted(missing)[0])
    if value["version"] != "1":
        raise ValueError("unsupported version")
    if value["type"] != "agent.submit_text":
        raise ValueError("unsupported message type")
    request_id = value["id"]
    timestamp = value["timestamp"]
    text = value["text"]
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
        or len(request_id) > 128
    ):
        raise ValueError("id must contain 1 to 128 characters without surrounding whitespace")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError("timestamp must be non-empty")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be non-empty")
    if len(text.strip()) > 500:
        raise ValueError("text must not exceed 500 characters")
    return request_id, text.strip()


def _error(
    code: str,
    message: str,
    *,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    value = {
        "version": "1",
        "type": "agent.error",
        "code": code,
        "message": message,
        "recoverable": True,
    }
    if request_id is not None:
        value["request_id"] = request_id
    return value
