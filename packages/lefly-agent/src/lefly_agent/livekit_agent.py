"""LiveKit Agent exposing only the reviewed LeFly M3 tool vocabulary."""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from livekit.agents import Agent, RunContext, function_tool

from .prompts import build_agent_instructions
from .robot import RobotCommandResult

logger = logging.getLogger(__name__)


class LiveKitRobotService(Protocol):
    def advertised_motion_presets(self) -> tuple[str, ...]: ...
    async def play_motion(self, name: str) -> RobotCommandResult: ...
    async def set_head_light(self, color: str) -> RobotCommandResult: ...
    async def set_head_light_brightness(self, value: float) -> RobotCommandResult: ...
    async def enter_rest_state(self) -> RobotCommandResult: ...


class LiveKitInfoService(Protocol):
    def get_current_datetime(self) -> str: ...
    async def get_weather(self, location: str | None = None, *, days: int = 3) -> str: ...
    async def web_search(
        self,
        query: str,
        *,
        category: str = "general",
        max_results: int | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class LiveKitAgentEvent:
    type: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    text: str | None = None
    correlation_id: str | None = None
    disposition: str | None = None


Observer = Callable[[LiveKitAgentEvent], Any]


class LeFlyLiveKitAgent(Agent):
    """Persistent text Agent whose tools delegate to typed domain services."""

    def __init__(
        self,
        robot: LiveKitRobotService,
        info: LiveKitInfoService,
        *,
        observer: Observer | None = None,
    ) -> None:
        self._robot = robot
        self._info = info
        self._observers = [] if observer is None else [observer]
        super().__init__(instructions=self._build_instructions())

    def subscribe(self, observer: Observer) -> Callable[[], None]:
        if not callable(observer):
            raise TypeError("observer must be callable")
        self._observers.append(observer)

        def unsubscribe() -> None:
            if observer in self._observers:
                self._observers.remove(observer)

        return unsubscribe

    async def refresh_instructions(self) -> None:
        await self.update_instructions(self._build_instructions())

    def _build_instructions(self) -> str:
        return build_agent_instructions(
            current_datetime=self._info.get_current_datetime(),
            motion_presets=self._robot.advertised_motion_presets(),
        )

    async def observe_llm_stream(
        self, stream: AsyncIterable[Any]
    ) -> AsyncIterator[Any]:
        """Observe visible text while forwarding every LiveKit chunk unchanged."""
        async for chunk in stream:
            await self._emit(LiveKitAgentEvent(type="llm.chunk"))
            text = self._chunk_text(chunk)
            if text:
                await self._emit(LiveKitAgentEvent(type="text.delta", text=text))
            yield chunk

    async def llm_node(self, chat_ctx, tools, model_settings):
        stream = Agent.default.llm_node(self, chat_ctx, tools, model_settings)
        if inspect.isawaitable(stream):
            stream = await stream
        if stream is None:
            return
        async for chunk in self.observe_llm_stream(stream):
            yield chunk

    @function_tool
    async def play_motion(self, ctx: RunContext, name: str) -> str:
        """Play one currently advertised safe motion preset.

        Args:
            name: Exact advertised preset name.
        """
        result = await self._run_tool(
            ctx, "play_motion", lambda: self._robot.play_motion(name)
        )
        return self._robot_confirmation(name, result)

    @function_tool
    async def set_head_light(self, ctx: RunContext, color: str) -> str:
        """Set the user-controlled head RGB matrix to a solid color.

        Args:
            color: Color in #RRGGBB format.
        """
        result = await self._run_tool(
            ctx, "set_head_light", lambda: self._robot.set_head_light(color)
        )
        return self._robot_confirmation(color.upper(), result)

    @function_tool
    async def set_head_light_brightness(self, ctx: RunContext, value: float) -> str:
        """Set head RGB matrix brightness.

        Args:
            value: Brightness from 0 to 1.
        """
        result = await self._run_tool(
            ctx,
            "set_head_light_brightness",
            lambda: self._robot.set_head_light_brightness(value),
        )
        return self._robot_confirmation(str(value), result)

    @function_tool
    async def enter_rest_state(self, ctx: RunContext) -> str:
        """Enter the robot's safe rest lifecycle."""
        result = await self._run_tool(
            ctx, "enter_rest_state", self._robot.enter_rest_state
        )
        return self._robot_confirmation("resting", result)

    @function_tool
    async def get_current_datetime(self, ctx: RunContext) -> str:
        """Return the configured local date, weekday, and time."""
        return await self._run_tool(
            ctx, "get_current_datetime", self._info.get_current_datetime
        )

    @function_tool
    async def get_weather(
        self, ctx: RunContext, location: str | None = None, days: int = 3
    ) -> str:
        """Get a one-to-three-day weather forecast.

        Args:
            location: City, QWeather Location ID, coordinates, or empty for default city.
            days: Forecast length from 1 to 3 days.
        """
        return await self._run_tool(
            ctx,
            "get_weather",
            lambda: self._info.get_weather(location, days=days),
        )

    @function_tool
    async def web_search(
        self,
        ctx: RunContext,
        query: str,
        category: Literal["general", "news"] = "general",
        max_results: int | None = None,
    ) -> str:
        """Search current general information or news.

        Args:
            query: Focused search query.
            category: Either general or news.
            max_results: Optional bounded number of results.
        """
        return await self._run_tool(
            ctx,
            "web_search",
            lambda: self._info.web_search(
                query, category=category, max_results=max_results
            ),
        )

    async def _run_tool(
        self,
        ctx: RunContext,
        name: str,
        operation: Callable[[], Awaitable[Any] | Any],
    ) -> Any:
        call_id = getattr(getattr(ctx, "function_call", None), "call_id", None)
        await self._emit(
            LiveKitAgentEvent(
                type="tool.started", tool_name=name, tool_call_id=call_id
            )
        )
        try:
            result = operation()
            if inspect.isawaitable(result):
                result = await result
        except BaseException:
            await self._emit(
                LiveKitAgentEvent(
                    type="tool.failed", tool_name=name, tool_call_id=call_id
                )
            )
            raise
        await self._emit(
            LiveKitAgentEvent(
                type="tool.completed",
                tool_name=name,
                tool_call_id=call_id,
                correlation_id=getattr(result, "correlation_id", None),
                disposition=getattr(result, "disposition", None),
            )
        )
        return result

    async def _emit(self, event: LiveKitAgentEvent) -> None:
        for observer in tuple(self._observers):
            try:
                result = observer(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.error("LiveKit Agent observer failed", exc_info=True)

    @staticmethod
    def _robot_confirmation(value: str, result: RobotCommandResult) -> str:
        return "设备已接收：%s（%s）" % (value, result.disposition)

    @staticmethod
    def _chunk_text(chunk: Any) -> str | None:
        if isinstance(chunk, str):
            return chunk or None
        delta = getattr(chunk, "delta", None)
        content = getattr(delta, "content", None)
        return content if isinstance(content, str) and content else None
