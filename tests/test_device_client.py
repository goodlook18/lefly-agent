import asyncio
import unittest

from lefly_protocol import DeviceCommand, DeviceEvent
from lefly_sdk import (
    ClientClosedError,
    CommandOutcomeUnknownError,
    DeviceClient,
    DeviceDisconnectedError,
    RemoteDeviceError,
    RequestTimeoutError,
)
from lefly_sdk.testing import InMemoryDeviceEndpoint


COMMAND_1 = "10000000-0000-4000-8000-000000000031"
COMMAND_2 = "10000000-0000-4000-8000-000000000032"
OTHER_COMMAND = "10000000-0000-4000-8000-000000000039"


class HangingCloseTransport:
    def __init__(self, transport):
        self._transport = transport
        self.close_started = asyncio.Event()

    async def send(self, message):
        await self._transport.send(message)

    async def receive(self):
        return await self._transport.receive()

    async def close(self):
        self.close_started.set()
        await asyncio.Event().wait()


class HangingFirstCloseConnector:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.first_transport = None

    async def connect(self, url):
        transport = await self.endpoint.connect(url)
        if self.first_transport is None:
            self.first_transport = HangingCloseTransport(transport)
            return self.first_transport
        return transport


def command(message_id=COMMAND_1):
    return DeviceCommand(
        message_id=message_id,
        message_type="light.solid",
        timestamp="2026-08-17T08:00:00.000Z",
        device_id="lefly-001",
        payload={"target": "head_matrix", "color": "#FFFF00"},
    )


def accepted(correlation_id=COMMAND_1, event_suffix=1):
    return DeviceEvent(
        message_id=f"20000000-0000-4000-8000-{event_suffix:012d}",
        message_type="command.accepted",
        timestamp="2026-08-17T08:00:00.050Z",
        device_id="lefly-001",
        correlation_id=correlation_id,
        payload={"command_type": "light.solid", "disposition": "applied"},
    )


def touch(position, event_suffix):
    return DeviceEvent(
        message_id=f"20000000-0000-4000-8000-{event_suffix:012d}",
        message_type="sensor.touch",
        timestamp="2026-08-17T08:00:01.000Z",
        device_id="lefly-001",
        payload={"position": position, "pressed": True},
    )


class DeviceClientRequestTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.endpoint = InMemoryDeviceEndpoint()
        self.client = DeviceClient(
            "memory://device",
            connector=self.endpoint,
            request_timeout=0.2,
            reconnect_delay=0.01,
        )
        await self.client.start()
        await self.client.wait_until_connected(timeout=0.2)

    async def asyncTearDown(self):
        await self.client.close()

    async def test_request_sends_json_and_returns_correlated_acceptance(self):
        pending = asyncio.create_task(self.client.request(command()))
        self.assertEqual(await self.endpoint.receive_command(timeout=0.2), command())
        await self.endpoint.emit(accepted())
        self.assertEqual(await pending, accepted())

    async def test_unrelated_event_does_not_complete_request(self):
        pending = asyncio.create_task(self.client.request(command()))
        await self.endpoint.receive_command(timeout=0.2)
        await self.endpoint.emit(accepted(OTHER_COMMAND, 2))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        await self.endpoint.emit(accepted(COMMAND_1, 3))
        self.assertEqual((await pending).correlation_id, COMMAND_1)

    async def test_request_timeout_uses_sdk_error(self):
        pending = asyncio.create_task(self.client.request(command()))
        await self.endpoint.receive_command(timeout=0.2)
        with self.assertRaises(RequestTimeoutError):
            await pending

    async def test_request_while_disconnected_uses_sdk_error(self):
        disconnected = DeviceClient(
            "memory://disconnected",
            connector=InMemoryDeviceEndpoint(),
            request_timeout=0.01,
        )
        try:
            with self.assertRaises(DeviceDisconnectedError):
                await disconnected.request(command())
        finally:
            await disconnected.close()

    async def test_correlated_device_error_preserves_structured_details(self):
        pending = asyncio.create_task(self.client.request(command()))
        await self.endpoint.receive_command(timeout=0.2)
        await self.endpoint.emit(
            DeviceEvent(
                message_id="20000000-0000-4000-8000-000000000004",
                message_type="device.error",
                timestamp="2026-08-17T08:00:00.050Z",
                device_id="lefly-001",
                correlation_id=COMMAND_1,
                payload={
                    "code": "invalid_command",
                    "message": "Unsupported color",
                    "recoverable": True,
                    "details": {"field": "payload.color"},
                },
            )
        )
        with self.assertRaises(RemoteDeviceError) as raised:
            await pending
        self.assertEqual(raised.exception.code, "invalid_command")
        self.assertEqual(raised.exception.details, {"field": "payload.color"})

    async def test_malformed_message_does_not_disconnect_client(self):
        with self.assertLogs("lefly_sdk.client", level="WARNING"):
            await self.endpoint.emit_raw("not-json")
            await asyncio.sleep(0.01)
        self.assertTrue(self.client.is_connected)

    async def test_close_fails_pending_request_immediately(self):
        pending = asyncio.create_task(self.client.request(command()))
        await self.endpoint.receive_command(timeout=0.2)
        await self.client.close()
        with self.assertRaises(ClientClosedError):
            await pending

    async def test_disconnect_marks_sent_command_outcome_unknown(self):
        pending = asyncio.create_task(self.client.request(command()))
        await self.endpoint.receive_command(timeout=0.2)
        with self.assertLogs("lefly_sdk.client", level="WARNING"):
            await self.endpoint.disconnect()
            with self.assertRaises(CommandOutcomeUnknownError) as raised:
                await pending
        self.assertEqual(raised.exception.outcome, "outcome_unknown")


class DeviceClientSubscriptionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.endpoint = InMemoryDeviceEndpoint()
        self.client = DeviceClient(
            "memory://device",
            connector=self.endpoint,
            request_timeout=0.2,
            reconnect_delay=0.01,
        )
        await self.client.start()
        await self.client.wait_until_connected(timeout=0.2)

    async def asyncTearDown(self):
        await self.client.close()

    async def test_typed_and_wildcard_subscriptions_can_be_removed(self):
        typed = []
        wildcard = []
        unsubscribe = self.client.subscribe("sensor.touch", typed.append)
        self.client.subscribe("*", wildcard.append)
        first = touch("left", 10)
        second = touch("left", 11)
        await self.endpoint.emit(first)
        await asyncio.sleep(0.01)
        unsubscribe()
        await self.endpoint.emit(second)
        await asyncio.sleep(0.01)
        self.assertEqual(typed, [first])
        self.assertEqual(wildcard, [first, second])

    async def test_callback_failure_does_not_stop_event_delivery(self):
        received = []

        def broken_callback(event):
            del event
            raise RuntimeError("callback failed")

        self.client.subscribe("sensor.touch", broken_callback)
        self.client.subscribe("sensor.touch", received.append)
        event = touch("right", 12)
        with self.assertLogs("lefly_sdk.client", level="ERROR"):
            await self.endpoint.emit(event)
            await asyncio.sleep(0.01)
        self.assertEqual(received, [event])
        self.assertTrue(self.client.is_connected)

    async def test_client_reconnects_and_accepts_new_requests(self):
        await self.endpoint.disconnect()
        await self.endpoint.wait_for_connections(2, timeout=0.2)
        pending = asyncio.create_task(self.client.request(command(COMMAND_2)))
        received = await self.endpoint.receive_command(timeout=0.2)
        self.assertEqual(received.message_id, COMMAND_2)
        await self.endpoint.emit(accepted(COMMAND_2, 13))
        self.assertEqual((await pending).correlation_id, COMMAND_2)

    async def test_stalled_transport_close_does_not_block_reconnect(self):
        endpoint = InMemoryDeviceEndpoint()
        connector = HangingFirstCloseConnector(endpoint)
        client = DeviceClient(
            "memory://device",
            connector=connector,
            reconnect_delay=0.01,
            transport_close_timeout=0.02,
        )
        try:
            await client.start()
            await client.wait_until_connected(timeout=0.2)
            await endpoint.disconnect()
            await endpoint.wait_for_connections(2, timeout=0.2)
            self.assertTrue(connector.first_transport.close_started.is_set())
            self.assertTrue(client.is_connected)
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
