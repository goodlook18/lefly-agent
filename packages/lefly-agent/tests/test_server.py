import asyncio
import unittest

from aiohttp import WSMsgType
from aiohttp.client_exceptions import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from lefly_agent.runtime import AgentQueueFullError
from lefly_agent.server import _AgentHub, _Client, create_app


class FakeRuntime:
    def __init__(self):
        self.submissions = []
        self.handlers = []
        self.queue_full = False
        self.emit_after_submit = None

    def snapshot(self):
        return {
            "phase": "idle",
            "device_connected": True,
            "queue": {"size": 0, "capacity": 8},
            "messages": [],
        }

    def subscribe(self, handler):
        self.handlers.append(handler)

        def unsubscribe():
            if handler in self.handlers:
                self.handlers.remove(handler)

        return unsubscribe

    async def submit_text(self, request_id, text):
        if self.queue_full:
            raise AgentQueueFullError("full")
        self.submissions.append((request_id, text))
        if self.emit_after_submit is not None:
            asyncio.get_running_loop().call_soon(self.emit, self.emit_after_submit)

    def emit(self, event):
        for handler in tuple(self.handlers):
            handler(event)


class AgentServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = FakeRuntime()
        self.client = TestClient(TestServer(create_app(runtime=self.runtime)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_websocket_starts_with_full_hello_snapshot(self):
        socket = await self.client.ws_connect("/ws/agent")

        hello = await socket.receive_json()

        self.assertEqual(hello["type"], "agent.hello")
        self.assertEqual(hello["version"], "1")
        self.assertEqual(hello["state"]["phase"], "idle")

    async def test_health_does_not_expose_chat_history(self):
        response = await self.client.get("/health")
        value = await response.json()

        self.assertNotIn("messages", value["state"])

    async def test_rejects_browser_origins_outside_loopback(self):
        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect(
                "/ws/agent", headers={"Origin": "https://example.com"}
            )

    async def test_valid_text_is_accepted_and_forwarded(self):
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()

        await socket.send_json(
            {
                "version": "1",
                "id": "req-1",
                "type": "agent.submit_text",
                "timestamp": "2026-08-15T12:00:00Z",
                "text": "向左看",
            }
        )
        accepted = await socket.receive_json()

        self.assertEqual(accepted, {"version": "1", "type": "agent.accepted", "request_id": "req-1"})
        self.assertEqual(self.runtime.submissions, [("req-1", "向左看")])

    async def test_acceptance_precedes_response_start(self):
        self.runtime.emit_after_submit = {
            "type": "agent.response.started",
            "request_id": "req-order",
            "response_id": "resp-order",
        }
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()

        await socket.send_json(
            {
                "version": "1",
                "id": "req-order",
                "type": "agent.submit_text",
                "timestamp": "2026-08-21T12:00:00Z",
                "text": "介绍一下自己",
            }
        )

        self.assertEqual((await socket.receive_json())["type"], "agent.accepted")
        self.assertEqual((await socket.receive_json())["type"], "agent.response.started")

    async def test_broadcasts_every_strict_stream_and_tool_lifecycle(self):
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()
        events = [
            {"type": "agent.response.started", "request_id": "req", "response_id": "resp"},
            {"type": "agent.response.delta", "request_id": "req", "response_id": "resp", "text": "你"},
            {
                "type": "agent.tool.started",
                "request_id": "req",
                "response_id": "resp",
                "tool_call_id": "call",
                "tool_name": "play_motion",
            },
            {
                "type": "agent.tool.completed",
                "request_id": "req",
                "response_id": "resp",
                "tool_call_id": "call",
                "tool_name": "play_motion",
                "protocol_correlation_id": "cmd",
                "disposition": "queued",
            },
            {"type": "agent.response.completed", "request_id": "req", "response_id": "resp"},
            {
                "type": "agent.tool.failed",
                "request_id": "req",
                "response_id": "resp-2",
                "tool_call_id": "call-2",
                "tool_name": "get_weather",
                "code": "tool_failed",
                "message": "工具执行失败。",
                "recoverable": True,
            },
            {
                "type": "agent.response.failed",
                "request_id": "req",
                "response_id": "resp-2",
                "code": "response_failed",
                "message": "处理请求失败。",
                "recoverable": True,
            },
        ]

        for event in events:
            self.runtime.emit(event)

        received = [await socket.receive_json() for _ in events]
        self.assertEqual(
            received,
            [{"version": "1", **event} for event in events],
        )

    async def test_invalid_lifecycle_is_not_broadcast_and_next_event_survives(self):
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()

        self.runtime.emit(
            {
                "type": "agent.response.delta",
                "request_id": "req",
                "response_id": "resp",
                "text": "x",
                "secret": "must-not-leak",
            }
        )
        self.runtime.emit(
            {
                "type": "agent.response.started",
                "request_id": " req ",
                "response_id": "resp",
            }
        )
        self.runtime.emit(
            {"type": "agent.response.completed", "request_id": "req", "response_id": "resp"}
        )

        message = await asyncio.wait_for(socket.receive_json(), 0.5)
        self.assertEqual(message["type"], "agent.response.completed")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(socket.receive_json(), 0.05)

    async def test_reconnect_snapshot_does_not_promote_incomplete_draft(self):
        first = await self.client.ws_connect("/ws/agent")
        await first.receive_json()
        self.runtime.emit(
            {
                "type": "agent.response.delta",
                "request_id": "req",
                "response_id": "resp",
                "text": "未完成",
            }
        )
        await first.receive_json()

        second = await self.client.ws_connect("/ws/agent")
        hello = await second.receive_json()

        self.assertEqual(hello["state"]["messages"], [])

    async def test_malformed_input_returns_recoverable_error(self):
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()

        await socket.send_json(
            {
                "version": "1",
                "id": "req-bad",
                "type": "agent.submit_text",
                "timestamp": "2026-08-15T12:00:00Z",
                "text": "向左看",
                "unexpected": True,
            }
        )
        error = await socket.receive_json()

        self.assertEqual(error["type"], "agent.error")
        self.assertEqual(error["code"], "invalid_message")
        self.assertTrue(error["recoverable"])
        self.assertEqual(self.runtime.submissions, [])

    async def test_queue_full_returns_explicit_error(self):
        self.runtime.queue_full = True
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()

        await socket.send_json(
            {
                "version": "1",
                "id": "req-full",
                "type": "agent.submit_text",
                "timestamp": "2026-08-15T12:00:00Z",
                "text": "点头",
            }
        )
        error = await socket.receive_json()

        self.assertEqual(error["code"], "queue_full")
        self.assertEqual(error["request_id"], "req-full")

    async def test_runtime_events_are_broadcast_to_connected_clients(self):
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()

        self.runtime.emit(
            {
                "type": "agent.message",
                "message": {
                    "id": "msg-1",
                    "role": "agent",
                    "text": "好的。",
                    "timestamp": "2026-08-15T12:00:01Z",
                },
            }
        )
        message = await asyncio.wait_for(socket.receive_json(), 0.5)

        self.assertEqual(message["type"], "agent.message")
        self.assertEqual(message["message"]["text"], "好的。")

    async def test_binary_messages_are_rejected(self):
        socket = await self.client.ws_connect("/ws/agent")
        await socket.receive_json()
        await socket.send_bytes(b"not-json")

        message = await socket.receive()

        self.assertEqual(message.type, WSMsgType.TEXT)
        self.assertEqual(message.json()["code"], "binary_not_supported")

    async def test_slow_client_overflow_closes_only_that_client(self):
        class SlowSocket:
            closed = False

            def __init__(self):
                self.close_calls = []

            async def close(self, **kwargs):
                self.closed = True
                self.close_calls.append(kwargs)

        hub = _AgentHub(self.runtime, capacity=1)
        socket = SlowSocket()
        hub.clients["slow"] = _Client(socket, asyncio.Queue(maxsize=1))

        hub.offer("slow", {"version": "1", "type": "one"})
        hub.offer("slow", {"version": "1", "type": "two"})
        await asyncio.sleep(0)

        self.assertTrue(socket.closed)
        self.assertEqual(socket.close_calls[0]["code"], 1008)


if __name__ == "__main__":
    unittest.main()
