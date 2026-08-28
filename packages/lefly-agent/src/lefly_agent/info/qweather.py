"""Narrow QWeather GeoAPI and three-day forecast client."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Mapping

import aiohttp

logger = logging.getLogger(__name__)

_LOCATION_ID = re.compile(r"^\d{6,}$")
_COORDINATES = re.compile(
    r"^-?(?:\d{1,3})(?:\.\d{1,2})?,-?(?:\d{1,2})(?:\.\d{1,2})?$"
)
_CITY_CACHE_CAPACITY = 10


class ProviderError(RuntimeError):
    """A sanitized upstream provider failure."""


@dataclass(frozen=True)
class WeatherDay:
    date: str
    text_day: str
    text_night: str
    temp_min: str
    temp_max: str
    wind_direction: str
    wind_scale: str
    humidity: str


@dataclass(frozen=True)
class WeatherForecast:
    location_name: str
    location_id: str
    update_time: str
    days: tuple[WeatherDay, ...]


class QWeatherClient:
    """Query only city lookup and the QWeather three-day endpoint."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        credential: str,
        api_host: str,
        timeout: float = 10.0,
    ) -> None:
        if not credential.strip():
            raise ValueError("QWeather credential is required")
        if not api_host.strip():
            raise ValueError("QWeather API host is required")
        self._session = session
        self._credential = credential.strip()
        self._api_host = api_host.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._city_cache: OrderedDict[str, tuple[str, str]] = OrderedDict()

    async def get_daily_forecast(
        self, location: str, *, days: int = 3
    ) -> WeatherForecast:
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3:
            raise ValueError("weather days must be between 1 and 3")
        requested = location.strip()
        if not requested:
            raise ValueError("weather location is required")

        location_id = requested
        location_name = requested
        if not self._is_direct_location(requested):
            cache_key = " ".join(requested.split()).casefold()
            cached = self._city_cache.get(cache_key)
            if cached is not None:
                self._city_cache.move_to_end(cache_key)
                location_id, location_name = cached
            else:
                payload = await self._get_json(
                    "/geo/v2/city/lookup",
                    params={"location": requested, "number": 1, "lang": "zh"},
                )
                locations = payload.get("location")
                if not isinstance(locations, list) or not locations:
                    raise ProviderError("QWeather location was not found")
                first = locations[0]
                if not isinstance(first, Mapping):
                    raise ProviderError("QWeather returned an invalid location")
                location_id = self._required_text(first, "id", "location")
                location_name = self._required_text(first, "name", "location")
                self._city_cache[cache_key] = (location_id, location_name)
                if len(self._city_cache) > _CITY_CACHE_CAPACITY:
                    self._city_cache.popitem(last=False)

        payload = await self._get_json(
            "/v7/weather/3d",
            params={"location": location_id, "lang": "zh"},
        )
        raw_days = payload.get("daily")
        if not isinstance(raw_days, list):
            raise ProviderError("QWeather returned an invalid forecast")
        parsed = tuple(self._parse_day(value) for value in raw_days[:days])
        if not parsed:
            raise ProviderError("QWeather returned no forecast")
        return WeatherForecast(
            location_name=location_name,
            location_id=location_id,
            update_time=str(payload.get("updateTime") or ""),
            days=parsed,
        )

    async def _get_json(self, path: str, *, params: Mapping[str, object]) -> Mapping[str, Any]:
        stage = "city_lookup" if path == "/geo/v2/city/lookup" else "forecast"
        started_at = time.perf_counter()
        try:
            async with self._session.get(
                self._api_host + path,
                params=dict(params),
                headers=self._auth_headers(),
                timeout=self._timeout,
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "qweather.request.failed stage=%s http_status=%s",
                        stage,
                        response.status,
                    )
                    raise ProviderError("QWeather HTTP error")
                payload = await response.json(content_type=None)
        except ProviderError:
            raise
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError, TypeError) as error:
            logger.warning(
                "qweather.request.failed stage=%s error_type=%s",
                stage,
                type(error).__name__,
            )
            raise ProviderError("QWeather request failed") from error
        if not isinstance(payload, Mapping) or payload.get("code") != "200":
            provider_code = payload.get("code") if isinstance(payload, Mapping) else "invalid"
            logger.warning(
                "qweather.request.failed stage=%s provider_code=%s",
                stage,
                provider_code,
            )
            raise ProviderError("QWeather returned an error")
        logger.debug(
            "qweather.request.completed stage=%s elapsed_ms=%.1f",
            stage,
            (time.perf_counter() - started_at) * 1000,
        )
        return payload

    def _auth_headers(self) -> dict[str, str]:
        if self._credential.count(".") == 2:
            return {"Authorization": "Bearer " + self._credential}
        return {"X-QW-Api-Key": self._credential}

    @staticmethod
    def _is_direct_location(location: str) -> bool:
        return bool(_LOCATION_ID.fullmatch(location) or _COORDINATES.fullmatch(location))

    @classmethod
    def _parse_day(cls, value: object) -> WeatherDay:
        if not isinstance(value, Mapping):
            raise ProviderError("QWeather returned an invalid forecast day")
        return WeatherDay(
            date=cls._required_text(value, "fxDate", "forecast"),
            text_day=str(value.get("textDay") or "未知"),
            text_night=str(value.get("textNight") or "未知"),
            temp_min=str(value.get("tempMin") or "未知"),
            temp_max=str(value.get("tempMax") or "未知"),
            wind_direction=str(value.get("windDirDay") or "未知"),
            wind_scale=str(value.get("windScaleDay") or "未知"),
            humidity=str(value.get("humidity") or "未知"),
        )

    @staticmethod
    def _required_text(value: Mapping[str, object], key: str, context: str) -> str:
        result = value.get(key)
        if not isinstance(result, str) or not result.strip():
            raise ProviderError("QWeather returned an invalid %s" % context)
        return result.strip()
