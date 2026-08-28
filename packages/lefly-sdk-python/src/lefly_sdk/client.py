"""Asynchronous client for LeFly Device Protocol endpoints."""

import asyncio
import inspect
import logging
from collections import defaultdict
from typing import Any, Callable, DefaultDict, Dict, List, Optional

from lefly_protocol import DeviceCommand, DeviceEvent, ProtocolError

from .transport import Connector, Transport
from .errors import (
    ClientClosedError,
    CommandOutcomeUnknownError,
    DeviceDisconnectedError,
    RemoteDeviceError,
    RequestTimeoutError,
)


logger = logging.getLogger(__name__)
EventHandler = Callable[[DeviceEvent], Any]


class DeviceClient:
    def __init__(
        self,
        url: str,
        *,
        connector: Optional[Connector] = None,
        request_timeout: float = 5.0,
        reconnect_delay: float = 0.5,
        transport_close_timeout: float = 1.0,
    ):
        self.url = url
        if connector is None:
            from .websocket import WebSocketConnector

            connector = WebSocketConnector()
        self._connector = connector
        self._request_timeout = request_timeout
        self._reconnect_delay = reconnect_delay
        if transport_close_timeout <= 0:
            raise ValueError("transport_close_timeout must be positive")
        self._transport_close_timeout = transport_close_timeout
        self._transport: Optional[Transport] = None
        self._runner: Optional[asyncio.Task] = None
        self._connected = asyncio.Event()
        self._closing = False
        self._pending: Dict[str, asyncio.Future] = {}
        self._subscribers: DefaultDict[str, List[EventHandler]] = defaultdict(list)
        self._callback_tasks: set = set()
        self._send_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def start(self) -> None:
        if self._runner is None:
            self._closing = False
            self._runner = asyncio.create_task(self._run())
            self._runner.add_done_callback(self._runner_finished)

    async def close(self) -> None:
        self._closing = True
        self._connected.clear()
        for response in tuple(self._pending.values()):
            if not response.done():
                response.set_exception(ClientClosedError("device client closed"))
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
            self._runner = None
        if self._transport is not None:
            await self._close_transport(self._transport)
            self._transport = None
        if self._callback_tasks:
            await asyncio.gather(*tuple(self._callback_tasks), return_exceptions=True)

    async def wait_until_connected(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def request(self, command: DeviceCommand) -> DeviceEvent:
        try:
            await self.wait_until_connected(self._request_timeout)
        except asyncio.TimeoutError as exc:
            raise DeviceDisconnectedError("device is not connected") from exc
        loop = asyncio.get_running_loop()
        response = loop.create_future()
        self._pending[command.message_id] = response
        try:
            async with self._send_lock:
                if self._transport is None:
                    raise ConnectionError("device is disconnected")
                await self._transport.send(command.to_json())
            try:
                return await asyncio.wait_for(response, self._request_timeout)
            except asyncio.TimeoutError as exc:
                raise RequestTimeoutError(
                    f"command {command.message_id} was not acknowledged"
                ) from exc
        finally:
            self._pending.pop(command.message_id, None)

    def subscribe(self, message_type: str, handler: EventHandler) -> Callable[[], None]:
        subscribers = self._subscribers[message_type]
        subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in subscribers:
                subscribers.remove(handler)

        return unsubscribe

    async def _run(self) -> None:
        while not self._closing:
            transport: Optional[Transport] = None
            try:
                transport = await self._connector.connect(self.url)
                self._transport = transport
                self._connected.set()
                while not self._closing:
                    raw = await transport.receive()
                    self._handle_message(raw)
            except asyncio.CancelledError:
                raise
            except (
                ConnectionError,
                OSError,
                ProtocolError,
                TypeError,
                ValueError,
            ) as error:
                logger.warning(
                    "Device connection lost (%s): %s",
                    type(error).__name__,
                    error,
                )
                was_connected = self._connected.is_set()
                self._connected.clear()
                self._transport = None
                if was_connected:
                    detail = str(error).strip()
                    message = "device disconnected before acknowledgement"
                    if detail:
                        message = "%s: %s" % (message, detail)
                    for response in tuple(self._pending.values()):
                        if not response.done():
                            response.set_exception(CommandOutcomeUnknownError(message))
            finally:
                if transport is not None:
                    await self._close_transport(transport)
            if not self._closing:
                await asyncio.sleep(self._reconnect_delay)

    async def _close_transport(self, transport: Transport) -> None:
        try:
            await asyncio.wait_for(
                transport.close(), timeout=self._transport_close_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out closing device transport after %.2fs",
                self._transport_close_timeout,
            )
        except Exception:
            logger.warning("Failed to close device transport", exc_info=True)

    def _handle_message(self, raw: str) -> None:
        try:
            event = DeviceEvent.from_json(raw)
        except (ProtocolError, TypeError, ValueError):
            logger.warning("Ignoring malformed device event")
            return

        if event.correlation_id is not None:
            pending = self._pending.get(event.correlation_id)
            if pending is not None and not pending.done():
                if event.message_type == "command.accepted":
                    pending.set_result(event)
                elif event.message_type == "device.error":
                    pending.set_exception(
                        RemoteDeviceError(
                            str(event.payload.get("code", "DEVICE_ERROR")),
                            str(event.payload.get("message", "Device command failed")),
                            bool(event.payload.get("recoverable", False)),
                            event.payload.get("details"),
                        )
                    )

        for handler in tuple(self._subscribers[event.message_type]):
            self._invoke_handler(handler, event)
        for handler in tuple(self._subscribers["*"]):
            self._invoke_handler(handler, event)

    def _invoke_handler(self, handler: EventHandler, event: DeviceEvent) -> None:
        try:
            result = handler(event)
        except Exception:
            logger.error("Device event subscriber failed", exc_info=True)
            return
        if inspect.isawaitable(result):
            task = asyncio.create_task(result)
            self._callback_tasks.add(task)
            task.add_done_callback(self._callback_finished)

    def _callback_finished(self, task: asyncio.Task) -> None:
        self._callback_tasks.discard(task)
        try:
            task.result()
        except Exception:
            logger.error("Async device event subscriber failed", exc_info=True)

    def _runner_finished(self, task: asyncio.Task) -> None:
        if task.cancelled() or self._closing:
            return
        try:
            task.result()
        except Exception:
            logger.error("Device client runner stopped unexpectedly", exc_info=True)
