from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lefly_agent.info.qweather import ProviderError, QWeatherClient
from lefly_agent.info.service import InfoService
from lefly_agent.info.tavily import TavilyClient


class FakeResponse:
    def __init__(self, status: int, payload: object = None, *, json_error=None):
        self.status = status
        self._payload = payload
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, *, content_type=None):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self._next()

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self._next()

    def _next(self):
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def weather_payload(days=3):
    return {
        "code": "200",
        "updateTime": "2026-08-21T10:00+08:00",
        "daily": [
            {
                "fxDate": f"2026-08-{21 + index:02d}",
                "textDay": "晴",
                "textNight": "多云",
                "tempMin": str(20 + index),
                "tempMax": str(30 + index),
                "windDirDay": "东风",
                "windScaleDay": "1-3",
                "humidity": "60",
            }
            for index in range(days)
        ],
    }


class QWeatherClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_city_name_is_resolved_and_forecast_is_bounded_to_three_days(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "code": "200",
                        "location": [
                            {"id": "101210401", "name": "宁波", "adm1": "浙江省"}
                        ],
                    },
                ),
                FakeResponse(200, weather_payload(5)),
            ]
        )
        client = QWeatherClient(
            session,
            credential="api-secret",
            api_host="https://weather.example",
        )

        forecast = await client.get_daily_forecast("宁波", days=3)

        self.assertEqual(forecast.location_name, "宁波")
        self.assertEqual(len(forecast.days), 3)
        method, url, options = session.requests[0]
        self.assertEqual((method, url), ("GET", "https://weather.example/geo/v2/city/lookup"))
        self.assertEqual(options["params"]["location"], "宁波")
        self.assertEqual(options["headers"], {"X-QW-Api-Key": "api-secret"})
        self.assertEqual(session.requests[1][2]["params"]["location"], "101210401")

    async def test_location_id_and_coordinates_skip_lookup(self):
        for location in ("101210401", "121.55,29.87"):
            with self.subTest(location=location):
                session = FakeSession([FakeResponse(200, weather_payload())])
                client = QWeatherClient(
                    session,
                    credential="a.b.c",
                    api_host="https://weather.example/",
                )

                await client.get_daily_forecast(location, days=1)

                self.assertEqual(len(session.requests), 1)
                self.assertEqual(
                    session.requests[0][2]["headers"],
                    {"Authorization": "Bearer a.b.c"},
                )
                self.assertEqual(session.requests[0][2]["params"]["location"], location)

    async def test_successful_city_lookups_are_cached(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {"code": "200", "location": [{"id": "101210401", "name": "宁波"}]},
                ),
                FakeResponse(200, weather_payload()),
                FakeResponse(200, weather_payload()),
            ]
        )
        client = QWeatherClient(
            session,
            credential="key",
            api_host="https://weather.example",
        )

        await client.get_daily_forecast(" 宁波 ")
        await client.get_daily_forecast("宁波")

        geo_requests = [request for request in session.requests if "/geo/" in request[1]]
        self.assertEqual(len(geo_requests), 1)
        self.assertEqual(len(session.requests), 3)

    async def test_success_logs_sanitized_request_stage_and_elapsed_time(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {"code": "200", "location": [{"id": "101270101", "name": "成都"}]},
                ),
                FakeResponse(200, weather_payload()),
            ]
        )
        client = QWeatherClient(
            session,
            credential="secret-key",
            api_host="https://weather.example",
        )

        with self.assertLogs("lefly_agent.info.qweather", level="DEBUG") as captured:
            await client.get_daily_forecast("成都")

        rendered = "\n".join(captured.output)
        self.assertRegex(
            rendered,
            r"qweather\.request\.completed stage=city_lookup elapsed_ms=\d+\.\d+",
        )
        self.assertRegex(
            rendered,
            r"qweather\.request\.completed stage=forecast elapsed_ms=\d+\.\d+",
        )
        self.assertNotIn("成都", rendered)
        self.assertNotIn("secret-key", rendered)

    async def test_city_cache_is_lru_bounded_to_ten_entries(self):
        responses = []
        for index in range(11):
            responses.extend(
                [
                    FakeResponse(
                        200,
                        {
                            "code": "200",
                            "location": [
                                {"id": str(101000000 + index), "name": f"城市{index}"}
                            ],
                        },
                    ),
                    FakeResponse(200, weather_payload()),
                ]
            )
        responses.extend(
            [
                FakeResponse(
                    200,
                    {"code": "200", "location": [{"id": "101000000", "name": "城市0"}]},
                ),
                FakeResponse(200, weather_payload()),
            ]
        )
        session = FakeSession(responses)
        client = QWeatherClient(
            session,
            credential="key",
            api_host="https://weather.example",
        )

        for index in range(11):
            await client.get_daily_forecast(f"城市{index}")
        await client.get_daily_forecast("城市0")

        geo_requests = [request for request in session.requests if "/geo/" in request[1]]
        self.assertEqual(len(geo_requests), 12)

    async def test_rejects_days_outside_one_to_three(self):
        client = QWeatherClient(
            FakeSession([]),
            credential="key",
            api_host="https://weather.example",
        )
        for days in (0, 4):
            with self.subTest(days=days):
                with self.assertRaises(ValueError):
                    await client.get_daily_forecast("宁波", days=days)

    async def test_http_and_schema_errors_are_provider_errors(self):
        cases = [
            FakeResponse(429, {"code": "429"}),
            FakeResponse(200, json_error=json.JSONDecodeError("bad", "x", 0)),
            FakeResponse(200, {"code": "200", "daily": "not-a-list"}),
            asyncio.TimeoutError(),
        ]
        for response in cases:
            with self.subTest(response=type(response).__name__):
                client = QWeatherClient(
                    FakeSession([response]),
                    credential="key",
                    api_host="https://weather.example",
                )
                with self.assertRaises(ProviderError):
                    await client.get_daily_forecast("101210401", days=3)

    async def test_failures_log_sanitized_request_stage_and_status(self):
        cases = (
            (
                "成都",
                asyncio.TimeoutError(),
                "stage=city_lookup error_type=TimeoutError",
            ),
            (
                "101270101",
                FakeResponse(429, {"code": "429"}),
                "stage=forecast http_status=429",
            ),
            (
                "101270101",
                FakeResponse(200, {"code": "401"}),
                "stage=forecast provider_code=401",
            ),
        )

        for location, response, expected in cases:
            with self.subTest(expected=expected):
                client = QWeatherClient(
                    FakeSession([response]),
                    credential="secret-key",
                    api_host="https://weather.example",
                )
                with self.assertLogs(
                    "lefly_agent.info.qweather", level="WARNING"
                ) as captured:
                    with self.assertRaises(ProviderError):
                        await client.get_daily_forecast(location, days=3)
                rendered = "\n".join(captured.output)
                self.assertIn(expected, rendered)
                self.assertNotIn(location, rendered)
                self.assertNotIn("secret-key", rendered)


class TavilyClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_bearer_auth_and_supported_topics(self):
        for category in ("general", "news"):
            with self.subTest(category=category):
                session = FakeSession(
                    [
                        FakeResponse(
                            200,
                            {
                                "results": [
                                    {
                                        "title": "结果一",
                                        "url": "https://example.com/one",
                                        "content": "摘要",
                                        "score": 0.9,
                                    }
                                ]
                            },
                        )
                    ]
                )
                client = TavilyClient(
                    session,
                    api_key="tvly-secret",
                    base_url="https://search.example/",
                )

                with self.assertLogs(
                    "lefly_agent.info.tavily", level="DEBUG"
                ) as captured:
                    results = await client.search(
                        "机器人", category=category, max_results=2
                    )

                self.assertEqual(len(results), 1)
                rendered = "\n".join(captured.output)
                self.assertIn(
                    "tavily.request.completed category=%s result_count=1" % category,
                    rendered,
                )
                self.assertIn("elapsed_ms=", rendered)
                self.assertNotIn("机器人", rendered)
                self.assertNotIn("tvly-secret", rendered)
                method, url, options = session.requests[0]
                self.assertEqual((method, url), ("POST", "https://search.example/search"))
                self.assertEqual(options["headers"]["Authorization"], "Bearer tvly-secret")
                self.assertEqual(options["json"]["topic"], category)
                self.assertEqual(options["json"]["max_results"], 2)
                self.assertFalse(options["json"]["include_raw_content"])

    async def test_search_rejects_unknown_category_and_invalid_limit(self):
        client = TavilyClient(FakeSession([]), api_key="key")
        with self.assertRaises(ValueError):
            await client.search("query", category="finance")
        with self.assertRaises(ValueError):
            await client.search("query", max_results=0)

    async def test_results_are_limited_and_fields_are_bounded(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "results": [
                            {
                                "title": "标题" * 200,
                                "url": "https://example.com/" + "x" * 800,
                                "content": "摘要" * 1000,
                            }
                            for _ in range(8)
                        ]
                    },
                )
            ]
        )
        results = await TavilyClient(session, api_key="key").search(
            "query", max_results=2
        )
        self.assertEqual(len(results), 2)
        self.assertLessEqual(len(results[0].title), 160)
        self.assertLessEqual(len(results[0].url), 512)
        self.assertLessEqual(len(results[0].content), 600)

    async def test_http_malformed_json_and_timeout_are_provider_errors(self):
        cases = [
            (FakeResponse(429, {"detail": "rate limited"}), "http_status=429"),
            (
                FakeResponse(200, json_error=json.JSONDecodeError("bad", "x", 0)),
                "error_type=JSONDecodeError",
            ),
            (FakeResponse(200, {"results": "not-a-list"}), "error_type=InvalidResponse"),
            (asyncio.TimeoutError(), "error_type=TimeoutError"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                client = TavilyClient(FakeSession([response]), api_key="secret-key")
                with self.assertLogs(
                    "lefly_agent.info.tavily", level="WARNING"
                ) as captured:
                    with self.assertRaises(ProviderError):
                        await client.search("private query")
                rendered = "\n".join(captured.output)
                self.assertIn(expected, rendered)
                self.assertNotIn("private query", rendered)
                self.assertNotIn("secret-key", rendered)


class InfoServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 21, 9, 7, tzinfo=ZoneInfo("Asia/Shanghai"))

    async def test_datetime_uses_fixed_clock_timezone_and_weekday(self):
        service = InfoService(
            timezone="Asia/Shanghai",
            default_city="宁波",
            clock=lambda: self.now,
        )
        result = service.get_current_datetime()
        self.assertIn("2026年8月21日", result)
        self.assertIn("星期五", result)
        self.assertIn("09:07", result)
        self.assertIn("Asia/Shanghai", result)

    async def test_default_city_is_used_and_weather_is_formatted(self):
        class WeatherProvider:
            def __init__(self):
                self.calls = []

            async def get_daily_forecast(self, location, *, days):
                self.calls.append((location, days))
                session = FakeSession([FakeResponse(200, weather_payload())])
                return await QWeatherClient(
                    session,
                    credential="key",
                    api_host="https://weather.example",
                ).get_daily_forecast("101210401", days=days)

        provider = WeatherProvider()
        service = InfoService(
            timezone="Asia/Shanghai",
            default_city="宁波",
            clock=lambda: self.now,
            qweather=provider,
        )
        result = await service.get_weather(days=2)
        self.assertEqual(provider.calls, [("宁波", 2)])
        self.assertIn("宁波", result)
        self.assertEqual(result.count("2026-08-"), 2)

    async def test_missing_provider_disables_only_corresponding_tool(self):
        service = InfoService(
            timezone="Asia/Shanghai",
            default_city="宁波",
            clock=lambda: self.now,
        )
        self.assertIn("天气服务未配置", await service.get_weather())
        self.assertIn("搜索服务未配置", await service.web_search("机器人"))
        self.assertIn("2026年", service.get_current_datetime())

    async def test_relative_year_search_is_grounded_and_category_is_forwarded(self):
        class SearchProvider:
            def __init__(self):
                self.calls = []

            async def search(self, query, *, category, max_results):
                self.calls.append((query, category, max_results))
                return []

        provider = SearchProvider()
        service = InfoService(
            timezone="Asia/Shanghai",
            default_city="宁波",
            clock=lambda: self.now,
            tavily=provider,
            search_max_results=4,
        )
        await service.web_search("今年机器人新闻", category="news", max_results=2)
        self.assertEqual(provider.calls[0][1:], ("news", 2))
        self.assertIn("2026", provider.calls[0][0])

        await service.web_search("2025年机器人新闻", category="general")
        self.assertEqual(provider.calls[1], ("2025年机器人新闻", "general", 4))

    async def test_provider_failures_become_short_stable_results(self):
        class BrokenWeather:
            async def get_daily_forecast(self, location, *, days):
                raise ProviderError("credential=secret " + "x" * 1000)

        class BrokenSearch:
            async def search(self, query, *, category, max_results):
                raise asyncio.TimeoutError()

        service = InfoService(
            timezone="Asia/Shanghai",
            default_city="宁波",
            clock=lambda: self.now,
            qweather=BrokenWeather(),
            tavily=BrokenSearch(),
        )
        weather = await service.get_weather()
        search = await service.web_search("新闻")
        self.assertEqual(weather, "天气服务暂时不可用，请稍后再试。")
        self.assertEqual(search, "搜索服务暂时不可用，请稍后再试。")
        self.assertLess(len(weather), 80)
        self.assertNotIn("secret", weather)


if __name__ == "__main__":
    unittest.main()
