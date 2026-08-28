from __future__ import annotations

import unittest

from lefly_agent.config import ModelSettings
from lefly_agent.livekit_agent import LeFlyLiveKitAgent
from lefly_agent.livekit_session import LiveKitTextSession
from lefly_agent.llm_factory import build_llm
from lefly_agent.robot import RobotCommandResult

from fakes.fake_openai import FakeOpenAIServer


PROVIDERS = (
    "openai",
    "qwen",
    "deepseek",
    "huawei_maas",
    "openai_compatible",
)


class RecordingRobot:
    def __init__(self):
        self.calls = []

    def advertised_motion_presets(self):
        return ("nod",)

    async def play_motion(self, name):
        self.calls.append(("motion.play", name))
        return RobotCommandResult("motion.play", "corr-provider", "queued")

    async def set_head_light(self, color):
        return RobotCommandResult("light.solid", "corr-provider", "accepted")

    async def set_head_light_brightness(self, value):
        return RobotCommandResult(
            "light.brightness", "corr-provider", "accepted"
        )

    async def enter_rest_state(self):
        return RobotCommandResult("device.rest", "corr-provider", "accepted")


class FixedInfo:
    def get_current_datetime(self):
        return "2026年8月22日 星期六 12:00（Asia/Shanghai）"

    async def get_weather(self, location=None, *, days=3):
        return "天气"

    async def web_search(self, query, *, category="general", max_results=None):
        return "搜索"


def model_settings(provider: str, base_url: str) -> ModelSettings:
    return ModelSettings(
        provider=provider,
        model="lefly-fake",
        base_url=base_url,
        max_tool_steps=3,
    )


class LLMProviderContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_provider_sends_its_reviewed_request_policy(self):
        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                async with FakeOpenAIServer() as server:
                    model = build_llm(
                        model_settings(provider, server.base_url),
                        api_key="test-secret",
                    )
                    session = LiveKitTextSession(
                        LeFlyLiveKitAgent(RecordingRobot(), FixedInfo()),
                        llm=model,
                    )
                    await session.start()
                    try:
                        self.assertEqual(await session.run_turn("你好"), "兼容性通过")
                    finally:
                        await session.close()

                payload = server.requests[0]
                self.assertEqual(payload["model"], "lefly-fake")
                self.assertNotIn("api_key", payload)
                if provider == "qwen":
                    self.assertEqual(payload["enable_thinking"], False)
                else:
                    self.assertNotIn("enable_thinking", payload)
                if provider in {"deepseek", "huawei_maas"}:
                    self.assertEqual(payload["thinking"], {"type": "disabled"})
                else:
                    self.assertNotIn("thinking", payload)

    async def test_every_provider_completes_one_robot_tool_loop(self):
        def responder(payload, index):
            if index == 0:
                return FakeOpenAIServer.tool_chunks(
                    "play_motion",
                    '{"name":"nod"}',
                    call_id="call-provider-motion",
                )
            return FakeOpenAIServer._text_chunks("动作已经执行")

        for provider in PROVIDERS:
            with self.subTest(provider=provider):
                robot = RecordingRobot()
                async with FakeOpenAIServer(responder) as server:
                    model = build_llm(
                        model_settings(provider, server.base_url),
                        api_key="test-secret",
                    )
                    session = LiveKitTextSession(
                        LeFlyLiveKitAgent(robot, FixedInfo()),
                        llm=model,
                    )
                    await session.start()
                    try:
                        response = await session.run_turn("请决定是否点头")
                    finally:
                        await session.close()

                self.assertEqual(response, "动作已经执行")
                self.assertEqual(robot.calls, [("motion.play", "nod")])
                self.assertEqual(len(server.requests), 2)
                tool_messages = [
                    message
                    for message in server.requests[1].get("messages", [])
                    if message.get("role") == "tool"
                ]
                self.assertEqual(len(tool_messages), 1)
                self.assertEqual(
                    tool_messages[0].get("tool_call_id"), "call-provider-motion"
                )


if __name__ == "__main__":
    unittest.main()
