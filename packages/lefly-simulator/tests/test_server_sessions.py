import asyncio
import unittest

from lefly_protocol import DeviceCommand, DeviceEvent, ProtocolError
from lefly_simulator import (
    RouterError,
    SimulatorState,
    StateValidationError,
    TargetClosedError,
)
from lefly_simulator.server import (
    _ConsoleHub,
    _DeviceSession,
    _device_exception_event,
)



COMMAND_1 = "10000000-0000-4000-8000-000000000001"
COMMAND_2 = "10000000-0000-4000-8000-000000000002"


def command(message_id=COMMAND_1):
    return DeviceCommand(
        message_id=message_id,
        message_type="device.get_state",
        timestamp="2026-08-17T08:00:00.000Z",
        payload={},
        device_id="simulator",
    )


def event(message_type, message_id, correlation_id=None):
    payload = {}
    if message_type == "command.accepted":
        payload = {"command_type": "device.get_state", "disposition": "applied"}
    elif message_type == "device.state_changed":
        payload = SimulatorState.default("simulator").publish_snapshot()
    elif message_type == "sensor.touch":
        payload = {"position": "left", "pressed": True}
    return DeviceEvent(
        message_id=message_id,
        message_type=message_type,
        timestamp="2026-08-17T08:00:00.010Z",
        payload=payload,
        device_id="simulator",
        correlation_id=correlation_id,
    )


class FakeWebSocket:
    def __init__(self, *, block_sends=False):
        self.closed = False
        self.sent = []
        self._changed = asyncio.Condition()
        self._block_sends = block_sends
        self.send_entered = asyncio.Event()
        self.release_send = asyncio.Event()

    async def send_json(self, value):
        self.send_entered.set()
        if self._block_sends:
            await self.release_send.wait()
        async with self._changed:
            self.sent.append(value)
            self._changed.notify_all()

    async def wait_for_count(self, count):
        async with self._changed:
            await self._changed.wait_for(lambda: len(self.sent) >= count)

    async def close(self, **_kwargs):
        self.closed = True
        self.release_send.set()


class RecordingOutboundQueue:
    def __init__(self, delegate):
        self.delegate = delegate
        self.offered = []

    def offer(self, value):
        self.offered.append(value)
        return self.delegate.offer(value)

    async def get(self):
        return await self.delegate.get()

    async def wait_closed(self):
        await self.delegate.wait_closed()

    @property
    def empty(self):
        return self.delegate._queue.empty()


class FakeDeviceTarget:
    kind = "simulator"
    target_id = "simulator"
    device_id = "simulator"

    def __init__(self):
        self.handlers = []
        self.command_errors = []
        self.subscribe_error = None
        self.events_published = asyncio.Event()

    def subscribe(self, handler):
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.handlers.append(handler)

        def unsubscribe():
            if handler in self.handlers:
                self.handlers.remove(handler)

        return unsubscribe

    async def command(self, item):
        if self.command_errors:
            error = self.command_errors.pop(0)
            if error is not None:
                raise error
        accepted = event(
            "command.accepted",
            "20000000-0000-4000-8000-000000000001",
            item.message_id,
        )
        changed = event(
            "device.state_changed",
            "20000000-0000-4000-8000-000000000002",
            item.message_id,
        )
        for handler in tuple(self.handlers):
            handler(accepted)
            handler(changed)
        self.events_published.set()
        return accepted

    def emit(self, value):
        for handler in tuple(self.handlers):
            handler(value)


class FakeConsoleRouter:
    def __init__(self):
        self.owner = None

    @property
    def lease_owner(self):
        return self.owner

    def acquire_control(self, owner):
        if self.owner is not None:
            return RouterError("control_lease_unavailable", "busy", True)
        self.owner = owner
        return RouterError("control_lease_unavailable", "readonly", True)

    def release_control(self, _credential):
        self.owner = None
        return True


class FailingSubscribeRouter(FakeConsoleRouter):
    def __init__(self):
        super().__init__()
        self.closed = 0

    async def start(self):
        return None

    def subscribe(self, _handler):
        raise RuntimeError("subscribe failed")

    async def close(self):
        self.closed += 1


class DeviceSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_command_error_is_redacted_and_session_continues(self):
        target = FakeDeviceTarget()
        target.command_errors.append(RuntimeError("SECRET /private/path"))
        websocket = FakeWebSocket()
        session = _DeviceSession(target, websocket, capacity=8)
        await session.start()

        with self.assertLogs("lefly_simulator.server", level="ERROR"):
            await session.handle_text(command().to_json())
        await websocket.wait_for_count(1)
        error = websocket.sent[0]
        self.assertEqual(error["type"], "device.error")
        self.assertEqual(error["payload"]["code"], "internal_error")
        self.assertFalse(error["payload"]["recoverable"])
        self.assertNotIn("SECRET", str(error))

        await session.handle_text(command(message_id=COMMAND_2).to_json())
        await websocket.wait_for_count(3)
        self.assertEqual(
            [item["type"] for item in websocket.sent[1:]],
            ["command.accepted", "device.state_changed"],
        )
        await session.close()

    async def test_subscribe_failure_cleans_sender_queue_and_websocket(self):
        target = FakeDeviceTarget()
        target.subscribe_error = RuntimeError("subscribe failed")
        websocket = FakeWebSocket()
        session = _DeviceSession(target, websocket, capacity=1)

        with self.assertRaisesRegex(RuntimeError, "subscribe failed"):
            await session.start()

        self.assertTrue(websocket.closed)
        self.assertEqual(session.pending_task_count, 0)
        self.assertEqual(target.handlers, [])

    async def test_disconnect_unsubscribes_and_drops_later_events(self):
        target = FakeDeviceTarget()
        websocket = FakeWebSocket()
        session = _DeviceSession(target, websocket, capacity=4)
        await session.start()
        await session.close()

        target.emit(
            event(
                "sensor.touch",
                "20000000-0000-4000-8000-000000000003",
            )
        )

        self.assertEqual(target.handlers, [])
        self.assertEqual(websocket.sent, [])

    async def test_command_ack_is_sent_once_only_from_subscription(self):
        target = FakeDeviceTarget()
        websocket = FakeWebSocket()
        session = _DeviceSession(target, websocket, capacity=4)
        outbound = RecordingOutboundQueue(session.outbound)
        session.outbound = outbound
        await session.start()

        await session.handle_text(command().to_json())
        await target.events_published.wait()
        await websocket.wait_for_count(2)

        self.assertEqual(
            [item["type"] for item in outbound.offered],
            ["command.accepted", "device.state_changed"],
        )
        self.assertEqual(
            sum(item["type"] == "command.accepted" for item in outbound.offered), 1
        )
        self.assertTrue(outbound.empty)
        await session.close()

    async def test_cancelled_command_propagates(self):
        target = FakeDeviceTarget()
        target.command_errors.append(asyncio.CancelledError())
        session = _DeviceSession(target, FakeWebSocket(), capacity=4)
        await session.start()
        with self.assertRaises(asyncio.CancelledError):
            await session.handle_text(command().to_json())
        await session.close()

    def test_known_device_errors_have_stable_payloads(self):
        known = (
            (ProtocolError("details"), "invalid_command", True),
            (StateValidationError("details"), "invalid_command", True),
            (TargetClosedError("details"), "target_closed", False),
            (asyncio.QueueFull(), "queue_full", True),
            (RouterError("stale_target_epoch", "details", True), "stale_target_epoch", True),
        )
        for error, code, recoverable in known:
            with self.subTest(error=type(error).__name__):
                mapped = _device_exception_event(error, "simulator")
                self.assertEqual(mapped.payload["code"], code)
                self.assertEqual(mapped.payload["recoverable"], recoverable)
                self.assertNotIn("details", mapped.payload["message"])


class ConsoleHubConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_router_subscribe_failure_rolls_back_hub(self):
        router = FailingSubscribeRouter()
        hub = _ConsoleHub(router, outbound_capacity=1)

        with self.assertRaisesRegex(RuntimeError, "subscribe failed"):
            await hub.start()

        self.assertEqual(router.closed, 1)
        self.assertEqual(hub.client_count, 0)
        self.assertEqual(hub.pending_task_count, 0)

    async def test_slow_client_closes_without_affecting_ordered_fast_client(self):
        hub = _ConsoleHub(FakeConsoleRouter(), outbound_capacity=1)
        slow_socket = FakeWebSocket(block_sends=True)
        fast_socket = FakeWebSocket()
        slow = hub.add(slow_socket)
        fast = hub.add(fast_socket)

        hub.broadcast({"sequence": 1})
        await slow_socket.send_entered.wait()
        await fast_socket.wait_for_count(1)
        hub.broadcast({"sequence": 2})
        await fast_socket.wait_for_count(2)
        hub.broadcast({"sequence": 3})
        await fast_socket.wait_for_count(3)
        await slow.outbound.wait_closed()

        self.assertTrue(slow_socket.closed)
        self.assertFalse(fast_socket.closed)
        self.assertEqual(
            [item["sequence"] for item in fast_socket.sent], [1, 2, 3]
        )
        await hub.remove(slow)
        await hub.remove(fast)

    async def test_fanout_racing_client_close_is_nonblocking(self):
        hub = _ConsoleHub(FakeConsoleRouter(), outbound_capacity=2)
        websocket = FakeWebSocket()
        client = hub.add(websocket)
        closing = asyncio.create_task(hub.remove(client))

        hub.broadcast({"sequence": 1})
        await closing

        self.assertEqual(hub.client_count, 0)
        self.assertEqual(hub.pending_task_count, 0)


if __name__ == "__main__":
    unittest.main()
