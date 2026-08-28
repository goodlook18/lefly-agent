"""Provider-aware construction for the LiveKit OpenAI-compatible LLM."""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

from .config import ModelSettings, SUPPORTED_LLM_PROVIDERS

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URLS = MappingProxyType(
    {
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "deepseek": "https://api.deepseek.com",
    }
)


def resolve_llm_options(
    settings: ModelSettings,
    *,
    api_key: str,
) -> dict[str, object]:
    """Return reviewed LiveKit OpenAI-plugin constructor options."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("LLM API key must be non-empty")
    if settings.provider not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError("unsupported model provider: %s" % settings.provider)

    options: dict[str, object] = {
        "model": settings.model,
        "api_key": api_key,
    }
    base_url = settings.base_url or _DEFAULT_BASE_URLS.get(settings.provider)
    if settings.provider in {"huawei_maas", "openai_compatible"} and base_url is None:
        raise ValueError("%s requires model.base_url" % settings.provider)
    if base_url is not None:
        options["base_url"] = base_url

    if settings.provider == "qwen":
        options.update(
            {
                "extra_body": {"enable_thinking": False},
                "_strict_tool_schema": False,
            }
        )
    elif settings.provider in {"deepseek", "huawei_maas"}:
        options.update(
            {
                "extra_body": {"thinking": {"type": "disabled"}},
                "tool_choice": "auto",
                "_strict_tool_schema": False,
            }
        )
    elif settings.provider == "openai_compatible":
        options["_strict_tool_schema"] = False

    return options


def build_llm(
    settings: ModelSettings,
    *,
    api_key: str,
    llm_type: Callable[..., Any] | None = None,
) -> Any:
    """Construct one provider-aware process-lifetime LiveKit LLM."""
    options = resolve_llm_options(settings, api_key=api_key)
    endpoint = options.get("base_url")
    endpoint_host = (
        urlparse(str(endpoint)).hostname if endpoint is not None else "api.openai.com"
    )
    logger.info(
        "agent.llm.provider provider=%s model=%s endpoint_host=%s",
        settings.provider,
        settings.model,
        endpoint_host,
    )

    constructor = llm_type
    if constructor is None:
        from livekit.plugins import openai

        constructor = openai.LLM
    return constructor(**options)
