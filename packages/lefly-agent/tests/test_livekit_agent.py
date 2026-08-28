from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from livekit.agents.llm.utils import prepare_function_arguments
from pydantic import ValidationError

from lefly_agent.livekit_agent import LeFlyLiveKitAgent
from lefly_agent.prompts import PUBLISHED_TOOL_NAMES
from lefly_agent.robot import RobotCommandResult, RobotPolicyError


class FakeRobot:
    def __init__(self):
        self.calls = []
        self.fail = False

    def advertised_motion_presets(self):
        return ("nod", "look_left")

    async def _call(self, name, value=None):
        self.calls.append((name, value))
        if self.fail:
            raise RobotPolicyError("blocked by policy")
        return RobotCommandResult(name, "corr-1", "queued")

    async def play_motion(self, name):
        return await self._call("motion.play", name)

    async def set_head_light(self, color):
        return await self._call("light.solid", color)

    async def set_head_light_brightness(self, value):
        return await self._call("light.brightness", value)

    async def enter_rest_state(self):
        return await self._call("device.rest")


class FakeInfo:
    def __init__(self):
        self.calls = []

    def get_current_datetime(self):
        self.calls.append(("datetime",))
        return "2026年8月21日 星期五 09:07（Asia/Shanghai）"

    async def get_weather(self, location=None, *, days=3):
        self.calls.append(("weather", location, days))
        return "宁波未来3天天气"

    async def web_search(self, query, *, category="general", max_results=None):
        self.calls.append(("search", query, category, max_results))
        return "搜索结果"


class FakeRunContext:
    def __init__(self, call_id):
        self.function_call = SimpleNamespace(call_id=call_id)


def tool(agent, name):
    return next(item for item in agent.tools if item.info.name == name)


class LiveKitAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.robot = FakeRobot()
        self.info = FakeInfo()
        self.events = []
        self.agent = LeFlyLiveKitAgent(
            self.robot,
            self.info,
            observer=self.events.append,
        )

    async def invoke(self, name, *args):
        wrapped = tool(self.agent, name)
        return await wrapped._func(self.agent, FakeRunContext("call-" + name), *args)

    async def test_exposes_only_the_seven_reviewed_tools(self):
        names = tuple(item.info.name for item in self.agent.tools)
        self.assertEqual(set(names), set(PUBLISHED_TOOL_NAMES))
        self.assertEqual(len(names), 7)
        self.assertNotIn("status.set", names)

    async def test_robot_and_information_tools_use_focused_services(self):
        results = [
            await self.invoke("play_motion", "nod"),
            await self.invoke("set_head_light", "#FFFFFF"),
            await self.invoke("set_head_light_brightness", 0.4),
            await self.invoke("enter_rest_state"),
            await self.invoke("get_current_datetime"),
            await self.invoke("get_weather", "宁波", 3),
            await self.invoke("web_search", "机器人新闻", "news", 2),
        ]

        self.assertEqual(len(results), 7)
        self.assertEqual(
            [name for name, _ in self.robot.calls],
            ["motion.play", "light.solid", "light.brightness", "device.rest"],
        )
        self.assertIn(("weather", "宁波", 3), self.info.calls)
        self.assertIn(("search", "机器人新闻", "news", 2), self.info.calls)
        event_types = [event.type for event in self.events]
        self.assertEqual(event_types.count("tool.started"), 7)
        self.assertEqual(event_types.count("tool.completed"), 7)
        self.assertFalse(any(event.type == "tool.failed" for event in self.events))

    async def test_invalid_arguments_fail_before_service_call(self):
        wrapped = tool(self.agent, "play_motion")
        with self.assertRaises((ValidationError, ValueError, TypeError)):
            prepare_function_arguments(
                fnc=wrapped,
                json_arguments="{}",
                call_ctx=FakeRunContext("invalid"),
            )
        self.assertEqual(self.robot.calls, [])

    async def test_tool_failure_is_observed_and_propagated(self):
        self.robot.fail = True
        with self.assertRaisesRegex(RobotPolicyError, "blocked"):
            await self.invoke("play_motion", "nod")
        self.assertEqual(
            [event.type for event in self.events],
            ["tool.started", "tool.failed"],
        )
        self.assertNotIn("blocked by policy", repr(self.events[-1]))

    async def test_incremental_chunks_are_forwarded_unchanged(self):
        first = SimpleNamespace(delta=SimpleNamespace(content="你"))
        second = SimpleNamespace(delta=SimpleNamespace(content="好"))

        async def source():
            yield first
            yield second

        forwarded = [chunk async for chunk in self.agent.observe_llm_stream(source())]

        self.assertEqual(forwarded, [first, second])
        self.assertEqual(
            [(event.type, event.text) for event in self.events if event.type == "text.delta"],
            [("text.delta", "你"), ("text.delta", "好")],
        )
        self.assertEqual(
            [event.type for event in self.events].count("llm.chunk"), 2
        )

    async def test_provider_failure_before_and_after_delta_is_not_swallowed(self):
        async def fail_before():
            if False:
                yield None
            raise RuntimeError("provider failed")

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            _ = [chunk async for chunk in self.agent.observe_llm_stream(fail_before())]
        self.assertEqual(self.events, [])

        async def fail_after():
            yield "partial"
            raise RuntimeError("provider failed")

        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            _ = [chunk async for chunk in self.agent.observe_llm_stream(fail_after())]
        self.assertEqual(self.events[-1].text, "partial")


if __name__ == "__main__":
    unittest.main()
