"""Compatibility gate for LiveKit Agents 1.5.4.

The proven sequence is intentionally explicit: construct an LLM-only
``AgentSession``, call ``start(agent)`` without a Room, process text through
``run(user_input=...)``, execute a function tool, and close the session. M3 must
stop if a future dependency version breaks this sequence or requires audio I/O.
"""

from __future__ import annotations

import importlib.metadata
import unittest

from livekit.agents import Agent, AgentSession, function_tool
from livekit.plugins import openai

from fakes.fake_openai import FakeOpenAIServer


class ProbeAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="Follow the user's request exactly.")
        self.probe_calls: list[str] = []

    @function_tool
    async def probe_tool(self, value: str) -> str:
        """Record a compatibility probe value.

        Args:
            value: Probe value supplied by the model.
        """
        self.probe_calls.append(value)
        return "probe recorded"


def _assistant_text(agent: Agent) -> list[str]:
    return [
        text
        for item in agent.chat_ctx.items
        if getattr(item, "role", None) == "assistant"
        if (text := getattr(item, "text_content", None))
    ]


class LiveKitCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_and_function_tool_run_without_room_or_audio(self) -> None:
        self.assertEqual(importlib.metadata.version("livekit-agents"), "1.5.4")
        self.assertEqual(importlib.metadata.version("livekit-plugins-openai"), "1.5.4")

        async with FakeOpenAIServer() as server:
            model = openai.LLM(
                model="lefly-fake",
                api_key="test-only-key",
                base_url=server.base_url,
            )
            agent = ProbeAgent()
            async with AgentSession(llm=model) as session:
                await session.start(agent)
                await session.run(user_input="只回复兼容性通过")
                await session.run(user_input="调用探针，参数使用 ok")

            self.assertIn("兼容性通过", _assistant_text(agent))
            self.assertIn("工具调用完成", _assistant_text(agent))
            self.assertEqual(agent.probe_calls, ["ok"])
            self.assertEqual(len(server.requests), 3)
            self.assertTrue(all(request.get("stream") is True for request in server.requests))


if __name__ == "__main__":
    unittest.main()
