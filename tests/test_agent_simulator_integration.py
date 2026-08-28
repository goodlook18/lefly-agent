import asyncio
import json
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import aiohttp
from lefly_sdk import DeviceClient

from tests.support.agent_process import AgentProcess
from tests.support.simulator_process import SimulatorProcess


ROOT = Path(__file__).resolve().parents[1]
AGENT_TESTS = ROOT / "packages" / "lefly-agent" / "tests"
if str(AGENT_TESTS) not in sys.path:
    sys.path.insert(0, str(AGENT_TESTS))

from fakes.fake_openai import FakeOpenAIServer  # noqa: E402


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def fake_response(payload, _index):
    messages = payload.get("messages", [])
    latest_user_index = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
        ),
        default=-1,
    )
    has_tool_output = any(
        message.get("role") == "tool" for message in messages[latest_user_index + 1 :]
    )
    user_text = next(
        (
            str(message.get("content", ""))
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    if "中断" in user_text:
        return FakeOpenAIServer._text_chunks("回复到一半")[:1] + [
            {
                "error": {
                    "message": "fake provider stream interrupted",
                    "type": "server_error",
                }
            }
        ]
    if has_tool_output:
        return FakeOpenAIServer._text_chunks("模型动作已经执行")
    if "复杂" in user_text:
        return FakeOpenAIServer.tool_chunks(
            "play_motion", '{"name":"happy_wiggle"}', call_id="call-complex-motion"
        )
    return FakeOpenAIServer._text_chunks("模型正常回复")


class AgentSimulatorIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.simulator = SimulatorProcess(port=free_port())
        self.simulator.start()
        self.fake_openai = await FakeOpenAIServer(fake_response).__aenter__()
        self.temporary = tempfile.TemporaryDirectory(prefix="lefly-agent-integration-")
        config_path = Path(self.temporary.name) / "agent.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[model]",
                    'provider = "openai_compatible"',
                    'model = "lefly-fake"',
                    'base_url = "%s"' % self.fake_openai.base_url,
                    "max_tool_steps = 3",
                    "",
                    "[touch.left]",
                    'motion = "nod"',
                    'light_color = "#F1A22E"',
                ]
            ),
            encoding="utf-8",
        )
        self.agent = AgentProcess(
            port=free_port(),
            device_url=self.simulator.device_url,
            config_path=str(config_path),
            environment={"LEFLY_LLM_API_KEY": "integration-test-key"},
        )
        self.agent.start()
        self.device_events = []
        self.device = DeviceClient(
            self.simulator.device_url,
            request_timeout=2.0,
            reconnect_delay=0.05,
        )
        self.device.subscribe("*", self.device_events.append)
        await self.device.start()
        await self.device.wait_until_connected(timeout=3.0)
        await self._wait_agent_device_connected()
        self.session = aiohttp.ClientSession()
        self.agent_socket = await self.session.ws_connect(self.agent.websocket_url)
        hello = await self.agent_socket.receive_json(timeout=3.0)
        self.assertEqual(hello["type"], "agent.hello")

    async def asyncTearDown(self):
        if hasattr(self, "agent_socket"):
            await self.agent_socket.close()
        if hasattr(self, "session"):
            await self.session.close()
        if hasattr(self, "device"):
            await self.device.close()
        if hasattr(self, "agent"):
            self.agent.stop()
        if hasattr(self, "fake_openai"):
            await self.fake_openai.__aexit__(None, None, None)
        if hasattr(self, "simulator"):
            self.simulator.stop()
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    async def test_real_fast_livekit_touch_and_shutdown_chain(self):
        nod = await self._submit("request-fast-nod", "点头")
        self._assert_completed_lifecycle(nod, "request-fast-nod")
        nod_tool = self._one(nod, "agent.tool.completed")
        self.assertEqual(nod_tool["tool_name"], "play_motion")
        self.assertTrue(nod_tool.get("protocol_correlation_id"))
        await self._wait_device_event("motion.started")

        light = await self._submit("request-fast-light", "蓝灯")
        self._assert_completed_lifecycle(light, "request-fast-light")
        await self._wait_state(
            lambda state: state["light"]["pixels"][0] == "#20A8B5"
        )

        complex_turn = await self._submit(
            "request-livekit-motion", "请根据现在的动作库完成一个复杂动作"
        )
        self._assert_completed_lifecycle(complex_turn, "request-livekit-motion")
        complex_tool = self._one(complex_turn, "agent.tool.completed")
        self.assertEqual(complex_tool["tool_name"], "play_motion")
        self.assertEqual(complex_tool["tool_call_id"], "call-complex-motion")
        self.assertEqual(len(self.fake_openai.requests), 2)
        first_request, second_request = self.fake_openai.requests
        self.assertNotIn("thinking", first_request)
        self.assertNotIn("enable_thinking", first_request)
        tool_results = [
            message
            for message in second_request.get("messages", [])
            if message.get("role") == "tool"
        ]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(
            tool_results[0].get("tool_call_id"), "call-complex-motion"
        )

        await self._inject_touch("left")
        await self._wait_device_event("sensor.touch")
        await self._wait_agent_message("左侧触摸联动已完成。")
        await self._wait_state(
            lambda state: state["light"]["pixels"][0] == "#F1A22E"
        )

        accepted_correlations = {
            event.correlation_id
            for event in self.device_events
            if event.message_type == "command.accepted"
        }
        for event in (nod_tool, complex_tool):
            self.assertIn(event["protocol_correlation_id"], accepted_correlations)

        self.agent.stop()
        self.assertIsNotNone(self.agent.returncode)
        self.assertFalse(self.agent.reader_alive)
        self.assertNotIn("Task was destroyed", self.agent.output)

    async def test_stream_and_device_failures_are_recoverable(self):
        interrupted = await self._submit("request-stream-failure", "请做一次中断测试")
        self.assertIn("agent.response.delta", [event["type"] for event in interrupted])
        self.assertIn("回复到一半", "".join(
            event.get("text", "") for event in interrupted
        ))
        self.assertEqual(
            self._one(interrupted, "agent.response.failed")["recoverable"], True
        )

        recovered_text = await self._submit("request-after-stream", "蓝灯")
        self._assert_completed_lifecycle(recovered_text, "request-after-stream")

        self.simulator.stop()
        await self._wait_agent_device_connected(expected=False)
        disconnected = await self._submit(
            "request-device-failure", "请根据动作库完成一个复杂动作"
        )
        self.assertEqual(self._one(disconnected, "agent.tool.started")["tool_name"], "play_motion")
        self.assertEqual(self._one(disconnected, "agent.tool.failed")["recoverable"], True)
        self.assertEqual(self._one(disconnected, "agent.response.failed")["recoverable"], True)
        self.assertEqual(self._one(disconnected, "agent.error")["recoverable"], True)

        self.simulator.start()
        await self._wait_agent_device_connected(expected=True)
        recovered_device = await self._submit("request-device-recovered", "黄灯")
        self._assert_completed_lifecycle(recovered_device, "request-device-recovered")
        await self._wait_state(
            lambda state: state["light"]["pixels"][0] == "#F1A22E"
        )

    async def _submit(self, request_id: str, text: str):
        await self.agent_socket.send_json(
            {
                "version": "1",
                "id": request_id,
                "type": "agent.submit_text",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "text": text,
            }
        )
        events = []
        while True:
            try:
                event = await self.agent_socket.receive_json(timeout=8.0)
            except TimeoutError:
                self.fail(
                    "timed out waiting for request %s\nevents: %r\n"
                    "model requests: %r\nagent output:\n%s"
                    % (
                        request_id,
                        events,
                        self.fake_openai.requests,
                        self.agent.output,
                    )
                )
            if event.get("request_id") != request_id:
                continue
            events.append(event)
            if event["type"] == "agent.response.completed":
                return events
            if event["type"] == "agent.response.failed":
                while True:
                    detail = await self.agent_socket.receive_json(timeout=3.0)
                    if detail.get("request_id") == request_id:
                        events.append(detail)
                    if detail.get("type") == "agent.error" and detail.get("request_id") == request_id:
                        return events

    async def _inject_touch(self, position: str):
        async with self.session.ws_connect(self.simulator.console_url) as console:
            hello = await console.receive_json(timeout=3.0)
            self.assertEqual(hello["type"], "console.hello")
            await console.send_json(
                {
                    "type": "console.inject_sensor",
                    "sensor_type": "touch",
                    "payload": {"position": position, "pressed": True},
                }
            )
            while True:
                event = await console.receive_json(timeout=3.0)
                if (
                    event.get("type") == "console.event"
                    and event.get("event", {}).get("type") == "sensor.touch"
                ):
                    return

    async def _wait_agent_message(self, text: str):
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            event = await self.agent_socket.receive_json(timeout=max(0.05, remaining))
            if event.get("type") == "agent.message" and text in event["message"]["text"]:
                return event
        self.fail(
            "timed out waiting for Agent message %r\nagent output:\n%s"
            % (text, self.agent.output)
        )

    async def _wait_agent_device_connected(self, expected: bool = True):
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            with urlopen(self.agent.health_url, timeout=0.5) as response:
                payload = json.load(response)
            if payload["state"]["device_connected"] is expected:
                return
            await asyncio.sleep(0.05)
        self.fail(
            "Agent device_connected did not become %s:\n%s"
            % (expected, self.agent.output)
        )

    async def _wait_device_event(self, message_type: str):
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if any(event.message_type == message_type for event in self.device_events):
                return
            await asyncio.sleep(0.02)
        self.fail("timed out waiting for %s" % message_type)

    async def _wait_state(self, predicate):
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            states = [
                event.payload
                for event in self.device_events
                if event.message_type == "device.state_changed"
            ]
            if any(predicate(state) for state in states):
                return
            await asyncio.sleep(0.02)
        self.fail("timed out waiting for matching device state")

    def _assert_completed_lifecycle(self, events, request_id):
        detail = "events=%r\nagent output:\n%s" % (events, self.agent.output)
        self.assertEqual(events[0]["type"], "agent.accepted", detail)
        self.assertEqual(events[1]["type"], "agent.response.started", detail)
        self.assertEqual(events[-1]["type"], "agent.response.completed", detail)
        response_ids = {
            event["response_id"] for event in events if "response_id" in event
        }
        self.assertEqual(len(response_ids), 1)
        self.assertTrue(all(event["request_id"] == request_id for event in events))

    def _one(self, events, message_type):
        matches = [event for event in events if event["type"] == message_type]
        self.assertEqual(len(matches), 1, events)
        return matches[0]


if __name__ == "__main__":
    unittest.main()
