"""Production WebSocket transport for the LeFly SDK."""

from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .transport import Transport


class _WebSocketTransport(Transport):
    def __init__(self, websocket: Any):
        self._websocket = websocket

    async def send(self, message: str) -> None:
        try:
            await self._websocket.send(message)
        except ConnectionClosed as exc:
            raise ConnectionError(
                f"WebSocket connection closed while sending: {exc}"
            ) from exc

    async def receive(self) -> str:
        try:
            message = await self._websocket.recv()
        except ConnectionClosed as exc:
            raise ConnectionError(
                f"WebSocket connection closed while receiving: {exc}"
            ) from exc
        if not isinstance(message, str):
            raise TypeError("LeFly Device Protocol requires WebSocket text frames")
        return message

    async def close(self) -> None:
        await self._websocket.close()


class WebSocketConnector:
    def __init__(
        self,
        *,
        open_timeout: float = 10.0,
        close_timeout: float = 0.5,
        ping_interval: float = 20.0,
    ):
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout
        self._ping_interval = ping_interval

    async def connect(self, url: str) -> Transport:
        try:
            websocket = await connect(
                url,
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
                ping_interval=self._ping_interval,
                compression=None,
            )
        except InvalidHandshake as exc:
            raise ConnectionError(f"WebSocket handshake failed: {exc}") from exc
        return _WebSocketTransport(websocket)
