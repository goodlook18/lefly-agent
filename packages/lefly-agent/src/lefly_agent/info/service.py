"""Stable user-facing information tool results."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from .qweather import ProviderError, WeatherForecast
from .tavily import SearchResult

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_RELATIVE_YEAR = re.compile(r"今年|明年|去年|本年度|当前年份|最新")
_EXPLICIT_YEAR = re.compile(r"(?:19|20)\d{2}\s*年?")


class WeatherProvider(Protocol):
    async def get_daily_forecast(
        self, location: str, *, days: int
    ) -> WeatherForecast: ...


class SearchProvider(Protocol):
    async def search(
        self, query: str, *, category: str, max_results: int
    ) -> tuple[SearchResult, ...]: ...


class InfoService:
    """Format clock and provider data into bounded text for Agent tools."""

    def __init__(
        self,
        *,
        timezone: str,
        default_city: str,
        clock: Callable[[], datetime] | None = None,
        qweather: WeatherProvider | None = None,
        tavily: SearchProvider | None = None,
        search_max_results: int = 5,
    ) -> None:
        self._timezone_name = timezone
        self._timezone = ZoneInfo(timezone)
        self._default_city = default_city.strip()
        self._clock = clock or (lambda: datetime.now(self._timezone))
        self._qweather = qweather
        self._tavily = tavily
        self._search_max_results = search_max_results

    def get_current_datetime(self) -> str:
        now = self._now()
        return "%d年%d月%d日 %s %02d:%02d（%s）" % (
            now.year,
            now.month,
            now.day,
            _WEEKDAYS[now.weekday()],
            now.hour,
            now.minute,
            self._timezone_name,
        )

    async def get_weather(self, location: str | None = None, *, days: int = 3) -> str:
        if self._qweather is None:
            return "天气服务未配置。"
        requested = (location or "").strip() or self._default_city
        try:
            forecast = await self._qweather.get_daily_forecast(requested, days=days)
        except (ProviderError, asyncio.TimeoutError, ValueError, TypeError):
            return "天气服务暂时不可用，请稍后再试。"
        lines = ["%s未来%d天天气：" % (requested, len(forecast.days))]
        for day in forecast.days:
            condition = day.text_day
            if day.text_night != day.text_day:
                condition += "转" + day.text_night
            lines.append(
                "%s %s，%s~%s℃，%s%s级，湿度%s%%。"
                % (
                    day.date,
                    condition,
                    day.temp_min,
                    day.temp_max,
                    day.wind_direction,
                    day.wind_scale,
                    day.humidity,
                )
            )
        return "\n".join(lines)[:2000]

    async def web_search(
        self,
        query: str,
        *,
        category: str = "general",
        max_results: int | None = None,
    ) -> str:
        if self._tavily is None:
            return "搜索服务未配置。"
        limit = self._search_max_results if max_results is None else max_results
        grounded = self._ground_query(query)
        try:
            results = await self._tavily.search(
                grounded, category=category, max_results=limit
            )
        except (ProviderError, asyncio.TimeoutError, ValueError, TypeError):
            return "搜索服务暂时不可用，请稍后再试。"
        if not results:
            return "没有找到相关搜索结果。"
        lines = []
        for index, result in enumerate(results, start=1):
            summary = result.content or "无摘要"
            lines.append("%d. %s\n%s\n%s" % (index, result.title, summary, result.url))
        return "\n".join(lines)[:4000]

    def _ground_query(self, query: str) -> str:
        normalized = query.strip()
        if _RELATIVE_YEAR.search(normalized) and not _EXPLICIT_YEAR.search(normalized):
            return "%s（当前年份：%d年）" % (normalized, self._now().year)
        return normalized

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=self._timezone)
        return value.astimezone(self._timezone)
