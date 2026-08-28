"""Small OpenAI-compatible streaming server used by LiveKit compatibility tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aiohttp import web


@dataclass(frozen=True)
class FailedResponse:
    status: int = 503


class FakeOpenAIServer:
    def __init__(
        self,
        responder: Callable[[dict[str, Any], int], list[dict[str, Any]]] | None = None,
        *,
        chunk_delay: float = 0.0,
    ) -> None:
        if chunk_delay < 0:
            raise ValueError("chunk_delay must not be negative")
        self.requests: list[dict[str, Any]] = []
        self._responder = responder
        self._chunk_delay = chunk_delay
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.base_url: str | None = None

    async def __aenter__(self) -> "FakeOpenAIServer":
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._chat_completions)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        server = getattr(self._site, "_server", None)
        sockets = () if server is None else tuple(server.sockets or ())
        if not sockets:
            raise RuntimeError("fake OpenAI server started without a socket")
        self.base_url = "http://127.0.0.1:%d/v1" % sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def _chat_completions(self, request: web.Request) -> web.StreamResponse:
        payload = await request.json()
        self.requests.append(payload)
        if self._responder is not None:
            chunks = self._responder(payload, len(self.requests) - 1)
            if isinstance(chunks, FailedResponse):
                return web.json_response(
                    {"error": {"message": "fake provider unavailable"}},
                    status=chunks.status,
                )
            return await self._stream_response(request, chunks)
        messages = payload.get("messages", [])
        has_tool_output = any(message.get("role") == "tool" for message in messages)
        user_text = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )

        if has_tool_output:
            chunks = self._text_chunks("工具调用完成")
        elif "调用探针" in user_text:
            chunks = self._tool_chunks()
        else:
            chunks = self._text_chunks("兼容性通过")

        return await self._stream_response(request, chunks)

    async def _stream_response(
        self,
        request: web.Request, chunks: list[dict[str, Any]]
    ) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await response.prepare(request)
        for index, chunk in enumerate(chunks):
            if index and self._chunk_delay:
                await asyncio.sleep(self._chunk_delay)
            await response.write(("data: %s\n\n" % json.dumps(chunk)).encode("utf-8"))
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    @staticmethod
    def _text_chunks(text: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "chatcmpl-text",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "lefly-fake",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": text},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-text",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "lefly-fake",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
            },
        ]

    @staticmethod
    def failed_response(status: int = 503) -> FailedResponse:
        return FailedResponse(status)

    @staticmethod
    def _tool_chunks() -> list[dict[str, Any]]:
        return FakeOpenAIServer.tool_chunks(
            "probe_tool", '{"value":"ok"}', call_id="call-probe"
        )

    @staticmethod
    def tool_chunks(
        name: str, arguments: str, *, call_id: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "chatcmpl-tool",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "lefly-fake",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": arguments,
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "chatcmpl-tool",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "lefly-fake",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                ],
            },
        ]
