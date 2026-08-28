from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from livekit.agents import llm
from livekit.plugins import openai

from lefly_agent.livekit_agent import LeFlyLiveKitAgent
from lefly_agent.livekit_session import LiveKitTextSession
from lefly_agent.robot import RobotCommandResult

from fakes.fake_openai import FakeOpenAIServer


class FakeRobot:
    def __init__(self):
        self.calls = []

    def advertised_motion_presets(self):
        return ("nod",)

    async def play_motion(self, name):
        self.calls.append(("motion.play", name))
        return RobotCommandResult("motion.play", "corr", "queued")

    async def set_head_light(self, color):
        return RobotCommandResult("light.solid", "corr", "accepted")

    async def set_head_light_brightness(self, value):
        return RobotCommandResult("light.brightness", "corr", "accepted")

    async def enter_rest_state(self):
        return RobotCommandResult("device.rest", "corr", "accepted")


class FailingRobot(FakeRobot):
    async def play_motion(self, name):
        self.calls.append(("motion.play", name))
        raise RuntimeError("device is disconnected")


class FakeInfo:
    def __init__(self):
        self.calls = []

    def get_current_datetime(self):
        self.calls.append(("datetime",))
        return "2026年8月21日 星期五 09:07（Asia/Shanghai）"

    async def get_weather(self, location=None, *, days=3):
        self.calls.append(("weather", location, days))
        return "天气"

    async def web_search(self, query, *, category="general", max_results=None):
        return "搜索"


class FakeRunResult:
    def __init__(self, release, text="完成", before_complete=None):
        self.release = release
        self.before_complete = before_complete
        self.events = [
            SimpleNamespace(
                type="message",
                item=SimpleNamespace(role="assistant", text_content=text),
            )
        ]

    def __await__(self):
        async def wait():
            await self.release.wait()
            if self.before_complete is not None:
                self.before_complete()
            return self

        return wait().__await__()


class FakeAgentSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.agent = None
        self.start_calls = 0
        self.run_calls = []
        self.closed = False
        self.release = asyncio.Event()
        self.release.set()
        self.handlers = {}
        self.error_on_run = None

    async def start(self, agent, **kwargs):
        self.start_calls += 1
        self.agent = agent
        self.start_kwargs = kwargs

    def run(self, *, user_input, input_modality="text"):
        self.run_calls.append((user_input, input_modality, self._messages()))
        error = self.error_on_run
        self.error_on_run = None
        before_complete = None
        if error is not None:
            before_complete = lambda: self.emit(
                "error", SimpleNamespace(error=error)
            )
        return FakeRunResult(self.release, before_complete=before_complete)

    def on(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type, handler):
        handlers = self.handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event_type, event):
        for handler in tuple(self.handlers.get(event_type, [])):
            handler(event)

    async def aclose(self):
        self.closed = True

    def _messages(self):
        return [
            (item.role, item.text_content)
            for item in self.agent.chat_ctx.items
            if getattr(item, "type", None) == "message"
        ]


class RecordingFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        session = FakeAgentSession(**kwargs)
        self.calls.append(session)
        return session


class LiveKitTextSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.agent = LeFlyLiveKitAgent(FakeRobot(), FakeInfo())
        self.factory = RecordingFactory()
        self.adapter = LiveKitTextSession(
            self.agent,
            llm=object(),
            max_tool_steps=3,
            session_factory=self.factory,
        )
        self.addAsyncCleanup(self.adapter.close)

    async def test_constructs_and_starts_exactly_one_persistent_session(self):
        await self.adapter.start()
        await self.adapter.start()

        self.assertEqual(len(self.factory.calls), 1)
        session = self.factory.calls[0]
        self.assertEqual(session.start_calls, 1)
        self.assertEqual(session.kwargs["max_tool_steps"], 2)
        self.assertIs(session.kwargs["llm"], self.adapter.llm)
        self.assertEqual(session.start_kwargs, {"record": False})

    async def test_turns_are_serial_and_return_final_assistant_text(self):
        await self.adapter.start()
        session = self.factory.calls[0]
        session.release.clear()

        first = asyncio.create_task(self.adapter.run_turn("第一条"))
        await asyncio.sleep(0)
        second = asyncio.create_task(self.adapter.run_turn("第二条"))
        await asyncio.sleep(0)
        self.assertEqual(len(session.run_calls), 1)

        session.release.set()
        self.assertEqual(await first, "完成")
        self.assertEqual(await second, "完成")
        self.assertEqual([call[0] for call in session.run_calls], ["第一条", "第二条"])

    async def test_fast_exchange_syncs_history_without_generation(self):
        await self.adapter.start()
        session = self.factory.calls[0]

        await self.adapter.sync_fast_exchange("点头", "已点头。")
        self.assertEqual(session.run_calls, [])

        await self.adapter.run_turn("刚才做了什么？")
        context_seen = session.run_calls[0][2]
        self.assertIn(("user", "点头"), context_seen)
        self.assertIn(("assistant", "已点头。"), context_seen)

    async def test_close_is_clean_and_idempotent(self):
        await self.adapter.start()
        session = self.factory.calls[0]
        await self.adapter.close()
        await self.adapter.close()
        self.assertTrue(session.closed)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await self.adapter.run_turn("不能执行")

    async def test_session_error_fails_partial_turn_without_poisoning_next_turn(self):
        await self.adapter.start()
        session = self.factory.calls[0]
        session.error_on_run = RuntimeError("provider stream interrupted")

        with self.assertRaisesRegex(RuntimeError, "provider stream interrupted"):
            await self.adapter.run_turn("中断测试")

        self.assertEqual(await self.adapter.run_turn("下一轮"), "完成")

    async def test_recoverable_error_with_final_message_completes_turn(self):
        await self.adapter.start()
        session = self.factory.calls[0]
        session.error_on_run = SimpleNamespace(
            recoverable=True,
            error=RuntimeError("transient rate limit"),
        )

        self.assertEqual(await self.adapter.run_turn("重试成功"), "完成")
        self.assertEqual(len(self.factory.calls), 1)


class LiveKitRealSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_session_accepts_successful_retry_after_tool_rate_limit(self):
        def responder(payload, index):
            if index == 0:
                return FakeOpenAIServer.tool_chunks(
                    "get_current_datetime", "{}", call_id="call-retry-time"
                )
            if index == 1:
                return FakeOpenAIServer.failed_response(429)
            return FakeOpenAIServer._text_chunks("重试后完成")

        async with FakeOpenAIServer(responder) as server:
            model = openai.LLM(
                model="lefly-fake", api_key="test-key", base_url=server.base_url
            )
            adapter = LiveKitTextSession(
                LeFlyLiveKitAgent(FakeRobot(), FakeInfo()),
                llm=model,
                max_tool_steps=3,
            )
            await adapter.start()
            try:
                response = await adapter.run_turn("查询时间")
            finally:
                await adapter.close()

        self.assertEqual(response, "重试后完成")
        self.assertEqual(len(server.requests), 3)

    async def test_real_session_surfaces_robot_tool_failure(self):
        def responder(payload, index):
            if index == 0:
                return FakeOpenAIServer.tool_chunks(
                    "play_motion", '{"name":"nod"}', call_id="call-failed-motion"
                )
            return FakeOpenAIServer._text_chunks("动作已经执行")

        robot = FailingRobot()
        async with FakeOpenAIServer(responder) as server:
            model = openai.LLM(
                model="lefly-fake", api_key="test-key", base_url=server.base_url
            )
            adapter = LiveKitTextSession(
                LeFlyLiveKitAgent(robot, FakeInfo()), llm=model, max_tool_steps=3
            )
            await adapter.start()
            try:
                with self.assertRaisesRegex(RuntimeError, "play_motion"):
                    await adapter.run_turn("点头")
            finally:
                await adapter.close()

        self.assertEqual(robot.calls, [("motion.play", "nod")])

    async def test_real_session_recovers_after_partial_stream_error(self):
        def responder(payload, index):
            if index == 0:
                return FakeOpenAIServer._text_chunks("回复到一半")[:1] + [
                    {
                        "error": {
                            "message": "fake provider stream interrupted",
                            "type": "server_error",
                        }
                    }
                ]
            return FakeOpenAIServer._text_chunks("恢复成功")

        async with FakeOpenAIServer(responder) as server:
            model = openai.LLM(
                model="lefly-fake", api_key="test-key", base_url=server.base_url
            )
            adapter = LiveKitTextSession(
                LeFlyLiveKitAgent(FakeRobot(), FakeInfo()), llm=model, max_tool_steps=3
            )
            await adapter.start()
            try:
                with self.assertRaisesRegex(RuntimeError, "LiveKit session error"):
                    await asyncio.wait_for(adapter.run_turn("中断"), timeout=3)
                response = await asyncio.wait_for(adapter.run_turn("恢复"), timeout=3)
            finally:
                await adapter.close()

        self.assertEqual(response, "恢复成功")
        self.assertEqual(len(server.requests), 2)

    async def test_real_session_runs_multiple_sequential_tools_and_streams_text(self):
        def responder(payload, index):
            if index == 0:
                return FakeOpenAIServer.tool_chunks(
                    "get_current_datetime", "{}", call_id="call-time"
                )
            if index == 1:
                return FakeOpenAIServer.tool_chunks(
                    "get_weather",
                    '{"location":"宁波","days":2}',
                    call_id="call-weather",
                )
            return FakeOpenAIServer._text_chunks("查询完成")

        robot = FakeRobot()
        info = FakeInfo()
        events = []
        async with FakeOpenAIServer(responder) as server:
            model = openai.LLM(
                model="lefly-fake", api_key="test-key", base_url=server.base_url
            )
            agent = LeFlyLiveKitAgent(robot, info, observer=events.append)
            adapter = LiveKitTextSession(agent, llm=model, max_tool_steps=3)
            await adapter.start()
            try:
                response = await adapter.run_turn("查一下现在和宁波天气")
            finally:
                await adapter.close()

        self.assertEqual(response, "查询完成")
        self.assertIn(("weather", "宁波", 2), info.calls)
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(
            [event.type for event in events].count("tool.completed"), 2
        )
        self.assertIn("查询完成", "".join(event.text or "" for event in events))

    async def test_real_session_rejects_invalid_tool_arguments_before_robot(self):
        def responder(payload, index):
            if index == 0:
                return FakeOpenAIServer.tool_chunks(
                    "play_motion", "{}", call_id="call-invalid"
                )
            return FakeOpenAIServer._text_chunks("参数不完整")

        robot = FakeRobot()
        async with FakeOpenAIServer(responder) as server:
            model = openai.LLM(
                model="lefly-fake", api_key="test-key", base_url=server.base_url
            )
            adapter = LiveKitTextSession(
                LeFlyLiveKitAgent(robot, FakeInfo()), llm=model, max_tool_steps=3
            )
            await adapter.start()
            try:
                response = await adapter.run_turn("执行一个坏参数动作")
            finally:
                await adapter.close()

        self.assertEqual(response, "参数不完整")
        self.assertEqual(robot.calls, [])
        self.assertEqual(len(server.requests), 2)

    async def test_real_session_stops_tool_loop_at_three_steps(self):
        def responder(payload, index):
            if payload.get("tool_choice") == "none":
                return FakeOpenAIServer._text_chunks("已达到工具步数上限")
            return FakeOpenAIServer.tool_chunks(
                "get_current_datetime", "{}", call_id=f"call-{index}"
            )

        info = FakeInfo()
        events = []
        async with FakeOpenAIServer(responder) as server:
            model = openai.LLM(
                model="lefly-fake", api_key="test-key", base_url=server.base_url
            )
            adapter = LiveKitTextSession(
                LeFlyLiveKitAgent(FakeRobot(), info, observer=events.append),
                llm=model,
                max_tool_steps=3,
            )
            await adapter.start()
            try:
                response = await adapter.run_turn("重复调用工具")
            finally:
                await adapter.close()

        self.assertEqual(
            [event.type for event in events].count("tool.completed"), 3
        )
        self.assertEqual(response, "已达到工具步数上限")
        self.assertEqual(server.requests[-1].get("tool_choice"), "none")


if __name__ == "__main__":
    unittest.main()
