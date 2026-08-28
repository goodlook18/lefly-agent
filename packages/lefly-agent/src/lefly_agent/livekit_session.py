"""Process-lifetime LiveKit text session adapter."""

from __future__ import annotations

import asyncio
import importlib.metadata
import logging
from collections.abc import Callable
from typing import Any

from livekit.agents import AgentSession

from .livekit_agent import LeFlyLiveKitAgent

logger = logging.getLogger(__name__)


class LiveKitTextSession:
    """Own one LLM-only AgentSession and serialize accepted text turns."""

    def __init__(
        self,
        agent: LeFlyLiveKitAgent,
        *,
        llm: Any,
        max_tool_steps: int = 3,
        session_factory: Callable[..., Any] = AgentSession,
    ) -> None:
        if not 1 <= max_tool_steps <= 10:
            raise ValueError("max_tool_steps must be between 1 and 10")
        self._agent = agent
        self._llm = llm
        self._max_tool_steps = max_tool_steps
        # LiveKit Agents 1.5.4 permits one more tool round than its documented
        # limit. Translate the project-level limit here instead of adding a
        # second runtime tool-loop counter.
        self._livekit_max_tool_steps = max_tool_steps - 1
        self._session_factory = session_factory
        self._session: Any | None = None
        self._turn_lock = asyncio.Lock()
        self._turn_active = False
        self._turn_error: Any | None = None
        self._turn_tool_error: str | None = None
        self._unsubscribe_agent = self._agent.subscribe(self._on_agent_event)
        self._closed = False

    @property
    def llm(self) -> Any:
        return self._llm

    def subscribe(self, observer):
        return self._agent.subscribe(observer)

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("LiveKit text session is closed")
        if self._session is not None:
            return
        versions = {}
        for distribution in ("livekit-agents", "livekit-plugins-openai"):
            try:
                versions[distribution] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                versions[distribution] = "not-installed"
        logger.info(
            "agent.livekit.dependencies",
            extra={"lefly_dependency_versions": versions},
        )
        self._session = await self._open_session()

    async def _open_session(self) -> Any:
        session = self._session_factory(
            llm=self._llm,
            max_tool_steps=self._livekit_max_tool_steps,
        )
        await session.start(self._agent, record=False)
        session.on("error", self._on_session_error)
        return session

    async def run_turn(self, text: str) -> str:
        normalized = self._validate_text(text)
        async with self._turn_lock:
            session = self._require_session()
            self._turn_error = None
            self._turn_tool_error = None
            self._turn_active = True
            turn_context = self._agent.chat_ctx.copy()
            try:
                await self._agent.refresh_instructions()
                result = session.run(user_input=normalized, input_modality="text")
                await result
                responses = [
                    event.item.text_content
                    for event in result.events
                    if getattr(event, "type", None) == "message"
                    and getattr(event.item, "role", None) == "assistant"
                    and getattr(event.item, "text_content", None)
                ]
                error = self._turn_error
                recovered = (
                    error is not None
                    and bool(getattr(error, "recoverable", False))
                    and bool(responses)
                )
                if error is not None and not recovered:
                    await self._agent.update_chat_ctx(turn_context)
                    await self._restart_session(session)
                    raise RuntimeError("LiveKit session error: %s" % error) from (
                        error if isinstance(error, BaseException) else None
                    )
                if recovered:
                    logger.warning(
                        "agent.livekit.retry_recovered",
                        extra={"lefly_error_type": type(error).__name__},
                    )
                if self._turn_tool_error is not None:
                    await self._agent.update_chat_ctx(turn_context)
                    raise RuntimeError(
                        "LiveKit tool failed: %s" % self._turn_tool_error
                    )
                return "\n".join(responses)
            finally:
                self._turn_active = False
                self._turn_error = None
                self._turn_tool_error = None

    async def _restart_session(self, failed_session: Any) -> None:
        if self._session is not failed_session:
            return
        failed_session.off("error", self._on_session_error)
        await failed_session.aclose()
        self._session = await self._open_session()

    async def sync_fast_exchange(self, user_text: str, assistant_text: str) -> None:
        user = self._validate_text(user_text)
        assistant = self._validate_text(assistant_text)
        async with self._turn_lock:
            self._require_session()
            chat_ctx = self._agent.chat_ctx.copy()
            chat_ctx.add_message(role="user", content=user)
            chat_ctx.add_message(role="assistant", content=assistant)
            await self._agent.update_chat_ctx(chat_ctx)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._unsubscribe_agent()
        async with self._turn_lock:
            session = self._session
            self._session = None
            if session is not None:
                session.off("error", self._on_session_error)
                await session.aclose()

    def _on_session_error(self, event: Any) -> None:
        if self._turn_active and self._turn_error is None:
            self._turn_error = getattr(event, "error", event)

    def _on_agent_event(self, event: Any) -> None:
        if (
            self._turn_active
            and self._turn_tool_error is None
            and getattr(event, "type", None) == "tool.failed"
        ):
            self._turn_tool_error = getattr(event, "tool_name", None) or "unknown"

    def _require_session(self) -> Any:
        if self._closed:
            raise RuntimeError("LiveKit text session is closed")
        if self._session is None:
            raise RuntimeError("LiveKit text session is not started")
        return self._session

    @staticmethod
    def _validate_text(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("text must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("text must be non-empty")
        if len(normalized) > 500:
            raise ValueError("text must not exceed 500 characters")
        return normalized
