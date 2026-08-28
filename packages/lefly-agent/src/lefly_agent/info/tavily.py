"""Narrow Tavily Search API client."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Mapping

import aiohttp

from .qweather import ProviderError

_CATEGORIES = {"general", "news"}
logger = logging.getLogger(__name__)


def _bounded(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str


class TavilyClient:
    """Call only Tavily's search endpoint with bounded result fields."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str,
        base_url: str = "https://api.tavily.com",
        timeout: float = 10.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Tavily API key is required")
        self._session = session
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def search(
        self,
        query: str,
        *,
        category: str = "general",
        max_results: int = 5,
    ) -> tuple[SearchResult, ...]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("search query is required")
        if category not in _CATEGORIES:
            raise ValueError("search category must be general or news")
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= 10
        ):
            raise ValueError("search max_results must be between 1 and 10")
        started_at = time.perf_counter()
        try:
            async with self._session.post(
                self._base_url + "/search",
                headers={
                    "Authorization": "Bearer " + self._api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "query": normalized_query,
                    "topic": category,
                    "search_depth": "basic",
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                },
                timeout=self._timeout,
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "tavily.request.failed category=%s http_status=%s elapsed_ms=%.1f",
                        category,
                        response.status,
                        (time.perf_counter() - started_at) * 1000,
                    )
                    raise ProviderError("Tavily HTTP error")
                payload = await response.json(content_type=None)
        except ProviderError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError, TypeError) as error:
            logger.warning(
                "tavily.request.failed category=%s error_type=%s elapsed_ms=%.1f",
                category,
                type(error).__name__,
                (time.perf_counter() - started_at) * 1000,
            )
            raise ProviderError("Tavily request failed") from error
        if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
            logger.warning(
                "tavily.request.failed category=%s error_type=InvalidResponse elapsed_ms=%.1f",
                category,
                (time.perf_counter() - started_at) * 1000,
            )
            raise ProviderError("Tavily returned an invalid response")

        results = []
        for raw in payload["results"][:max_results]:
            if not isinstance(raw, Mapping):
                continue
            title = _bounded(raw.get("title"), 160)
            url = _bounded(raw.get("url"), 512)
            content = _bounded(raw.get("content"), 600)
            if title and url:
                results.append(SearchResult(title=title, url=url, content=content))
        logger.debug(
            "tavily.request.completed category=%s result_count=%s elapsed_ms=%.1f",
            category,
            len(results),
            (time.perf_counter() - started_at) * 1000,
        )
        return tuple(results)
