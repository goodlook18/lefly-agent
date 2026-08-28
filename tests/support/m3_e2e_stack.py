"""Run the local fake-model M3 stack used by Playwright."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
from pathlib import Path

from tests.support.agent_process import AgentProcess
from tests.support.simulator_process import SimulatorProcess


ROOT = Path(__file__).resolve().parents[2]
AGENT_TESTS = ROOT / "packages" / "lefly-agent" / "tests"
if str(AGENT_TESTS) not in sys.path:
    sys.path.insert(0, str(AGENT_TESTS))

from fakes.fake_openai import FakeOpenAIServer  # noqa: E402


def _text_chunks(*parts: str):
    chunks = []
    for part in parts:
        chunks.append(
            {
                "id": "chatcmpl-m3-e2e",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "lefly-fake",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": part},
                        "finish_reason": None,
                    }
                ],
            }
        )
    chunks.append(
        {
            "id": "chatcmpl-m3-e2e",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "lefly-fake",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    return chunks


def _response(payload, _index):
    messages = payload.get("messages", [])
    latest_user_index = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
        ),
        default=-1,
    )
    user_text = next(
        (
            str(message.get("content", ""))
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    has_tool_output = any(
        message.get("role") == "tool" for message in messages[latest_user_index + 1 :]
    )
    if "中断测试" in user_text:
        return _text_chunks("回复到一半")[:1] + [
            {
                "error": {
                    "message": "fake provider stream interrupted",
                    "type": "server_error",
                }
            }
        ]
    if has_tool_output:
        return _text_chunks("流式", "动作完成")
    if "流式动作" in user_text:
        return FakeOpenAIServer.tool_chunks(
            "play_motion", '{"name":"nod"}', call_id="call-m3-e2e-motion"
        )
    return _text_chunks("模型", "恢复成功")


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass

    simulator_port = int(os.environ.get("LEFLY_E2E_SIMULATOR_PORT", "18766"))
    if simulator_port < 1 or simulator_port > 65535:
        raise ValueError("LEFLY_E2E_SIMULATOR_PORT must be between 1 and 65535")
    simulator = SimulatorProcess(port=simulator_port)
    agent = None
    temporary = tempfile.TemporaryDirectory(prefix="lefly-m3-e2e-")
    try:
        simulator.start()
        async with FakeOpenAIServer(_response, chunk_delay=0.8) as fake_openai:
            config_path = Path(temporary.name) / "agent.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[model]",
                        'provider = "openai_compatible"',
                        'model = "lefly-fake"',
                        'base_url = "%s"' % fake_openai.base_url,
                        "max_tool_steps = 3",
                    ]
                ),
                encoding="utf-8",
            )
            agent = AgentProcess(
                port=8767,
                device_url=simulator.device_url,
                config_path=str(config_path),
                environment={"LEFLY_LLM_API_KEY": "m3-e2e-key"},
            )
            agent.start()
            print("LeFly M3 E2E stack ready", flush=True)
            await stop.wait()
    finally:
        if agent is not None:
            agent.stop()
            if agent.output:
                print(agent.output, end="", flush=True)
        simulator.stop()
        temporary.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
