import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from lefly_protocol import DeviceCommand, DeviceEvent
from lefly_sdk import DeviceClient
from lefly_sdk.websocket import WebSocketConnector
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidMessage


class WebSocketTransportIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_connector_disables_optional_websocket_compression(self):
        websocket = AsyncMock()
        connector = WebSocketConnector(close_timeout=0.25)

        with patch(
            "lefly_sdk.websocket.connect",
            new=AsyncMock(return_value=websocket),
        ) as connect:
            transport = await connector.connect("ws://127.0.0.1:8766/ws/device/simulator")

        connect.assert_awaited_once_with(
            "ws://127.0.0.1:8766/ws/device/simulator",
            open_timeout=10.0,
            close_timeout=0.25,
            ping_interval=20.0,
            compression=None,
        )
        await transport.close()

    async def test_connector_normalizes_transient_handshake_failures(self):
        connector = WebSocketConnector()

        with patch(
            "lefly_sdk.websocket.connect",
            new=AsyncMock(side_effect=InvalidMessage("incomplete response")),
        ):
            with self.assertRaisesRegex(ConnectionError, "WebSocket handshake failed"):
                await connector.connect("ws://127.0.0.1:8766/ws/device/simulator")

    async def test_command_receives_correlated_acknowledgement(self):
        received = []

        async def handler(websocket):
            command = DeviceCommand.from_json(await websocket.recv())
            received.append(command)
            event = DeviceEvent(
                message_id="20000000-0000-4000-8000-000000000021",
                message_type="command.accepted",
                timestamp="2026-08-13T08:00:00.050Z",
                device_id=command.device_id,
                correlation_id=command.message_id,
                payload={
                    "command_type": command.message_type,
                    "disposition": "applied",
                },
            )
            await websocket.send(event.to_json())
            await asyncio.sleep(0.05)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = DeviceClient(
                f"ws://127.0.0.1:{port}",
                connector=WebSocketConnector(open_timeout=0.2),
                request_timeout=0.2,
            )
            await client.start()
            await client.wait_until_connected(timeout=0.2)
            command = DeviceCommand(
                message_id="10000000-0000-4000-8000-000000000021",
                message_type="device.get_state",
                timestamp="2026-08-13T08:00:00.000Z",
                device_id="lefly-sim-01",
                payload={},
            )

            response = await client.request(command)
            await client.close()

        self.assertEqual(received, [command])
        self.assertEqual(response.correlation_id, command.message_id)


if __name__ == "__main__":
    unittest.main()
