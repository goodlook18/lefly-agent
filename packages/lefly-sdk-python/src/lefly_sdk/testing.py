"""Deterministic in-memory device endpoint for SDK and simulator tests."""

import asyncio
from typing import Any, Optional

from lefly_protocol import DeviceCommand, DeviceEvent

from .transport import Transport


_DISCONNECTED = object()


class _InMemoryTransport(Transport):
    def __init__(self, outbound: asyncio.Queue, inbound: asyncio.Queue):
        self._outbound = outbound
        self._inbound = inbound
        self._closed = False

    async def send(self, message: str) -> None:
        if self._closed:
            raise ConnectionError("in-memory transport is closed")
        await self._outbound.put(message)

    async def receive(self) -> str:
        value = await self._inbound.get()
        if value is _DISCONNECTED:
            raise ConnectionError("in-memory endpoint disconnected")
        return value

    async def close(self) -> None:
        self._closed = True


class InMemoryDeviceEndpoint:
    """Acts as a connector and exposes the server side of each connection."""

    def __init__(self):
        self._commands: Optional[asyncio.Queue] = None
        self._events: Optional[asyncio.Queue] = None
        self._connected = asyncio.Event()
        self.connection_count = 0

    async def connect(self, url: str) -> Transport:
        del url
        self._commands = asyncio.Queue()
        self._events = asyncio.Queue()
        self.connection_count += 1
        self._connected.set()
        return _InMemoryTransport(self._commands, self._events)

    async def wait_connected(self, timeout: float = 1.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def wait_for_connections(self, count: int, timeout: float = 1.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.connection_count < count:
            if loop.time() >= deadline:
                raise asyncio.TimeoutError(
                    f"expected {count} connections, got {self.connection_count}"
                )
            await asyncio.sleep(0.001)

    async def receive_command(self, timeout: float = 1.0) -> DeviceCommand:
        if self._commands is None:
            await self.wait_connected(timeout)
        assert self._commands is not None
        raw = await asyncio.wait_for(self._commands.get(), timeout)
        return DeviceCommand.from_json(raw)

    async def emit(self, event: DeviceEvent) -> None:
        if self._events is None:
            raise RuntimeError("no client is connected")
        await self._events.put(event.to_json())

    async def emit_raw(self, message: Any) -> None:
        if self._events is None:
            raise RuntimeError("no client is connected")
        await self._events.put(message)

    async def disconnect(self) -> None:
        if self._events is not None:
            await self._events.put(_DISCONNECTED)
        self._connected.clear()
