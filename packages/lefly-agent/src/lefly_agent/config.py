"""Typed, secret-safe configuration for the LeFly Agent process."""

from __future__ import annotations

import os
import ipaddress
import logging
import re
import sys
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .fast_intent import DEFAULT_FAST_INTENT_ALIASES, INTENT_NAMES, normalize_text

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_URL = "ws://127.0.0.1:8766/ws/device/simulator"
DEFAULT_DEVICE_ID = "lefly-sim-01"
DEFAULT_LLM_PROVIDER = "openai"
SUPPORTED_LLM_PROVIDERS = frozenset(
    {"openai", "qwen", "deepseek", "huawei_maas", "openai_compatible"}
)
MODEL_PROFILE_FILES = MappingProxyType(
    {
        "openai": "agent.openai.toml",
        "qwen": "agent.qwen.toml",
        "deepseek": "agent.deepseek.toml",
        "huawei-maas": "agent.huawei-maas.toml",
        "openai-compatible": "agent.openai-compatible.toml",
    }
)
DEVICE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TOUCH_POSITIONS = ("left", "middle", "right")
SUPPORTED_FAST_INTENTS = INTENT_NAMES

_ENV_FIELDS = {
    "LEFLY_DEVICE_URL": ("agent", "device_url"),
    "LEFLY_DEVICE_ID": ("agent", "device_id"),
    "LEFLY_DEFAULT_CITY": ("agent", "default_city"),
    "LEFLY_TIMEZONE": ("agent", "timezone"),
    "LEFLY_REQUEST_TIMEOUT": ("agent", "request_timeout"),
    "LEFLY_QUEUE_CAPACITY": ("agent", "queue_capacity"),
    "LEFLY_HISTORY_CAPACITY": ("agent", "history_capacity"),
    "LEFLY_AGENT_HOST": ("server", "host"),
    "LEFLY_AGENT_PORT": ("server", "port"),
    "LEFLY_LLM_PROVIDER": ("model", "provider"),
    "LEFLY_MODEL": ("model", "model"),
    "LEFLY_MODEL_BASE_URL": ("model", "base_url"),
    "LEFLY_MAX_TOOL_STEPS": ("model", "max_tool_steps"),
    "LEFLY_SEARCH_MAX_RESULTS": ("search", "max_results"),
    "QWEATHER_API_HOST": ("search", "qweather_api_host"),
    "TAVILY_BASE_URL": ("search", "tavily_base_url"),
}
_INTEGER_PATHS = {
    ("agent", "queue_capacity"),
    ("agent", "history_capacity"),
    ("server", "port"),
    ("model", "max_tool_steps"),
    ("search", "max_results"),
}
_FLOAT_PATHS = {("agent", "request_timeout")}
_SECRET_KEYS = {"api_key", "llm_api_key", "qweather_api_key", "tavily_api_key"}


class ConfigError(ValueError):
    """Raised when Agent configuration cannot be loaded safely."""


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    model: str
    base_url: str | None
    max_tool_steps: int = 3


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int


@dataclass(frozen=True)
class SearchSettings:
    max_results: int = 5
    qweather_api_host: str | None = None
    tavily_base_url: str = "https://api.tavily.com"


@dataclass(frozen=True)
class TouchBehavior:
    motion: str | None = None
    light_color: str | None = None


@dataclass(frozen=True)
class AgentSettings:
    device_url: str
    device_id: str
    default_city: str
    timezone: str
    request_timeout: float
    queue_capacity: int
    history_capacity: int
    fast_intent_aliases: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class SecretSettings:
    llm_api_key: str | None = field(default=None, repr=False)
    qweather_api_key: str | None = field(default=None, repr=False)
    tavily_api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class LeFlyAgentConfig:
    model: ModelSettings
    server: ServerSettings
    search: SearchSettings
    agent: AgentSettings
    touch: Mapping[str, TouchBehavior]
    secrets: SecretSettings = field(repr=False)

    def health_summary(self) -> dict[str, object]:
        """Return non-secret configuration suitable for diagnostics."""
        return {
            "device_url": self.agent.device_url,
            "device_id": self.agent.device_id,
            "provider": self.model.provider,
            "model": self.model.model,
            "llm_configured": self.secrets.llm_api_key is not None,
            "qweather_configured": (
                self.secrets.qweather_api_key is not None
                and self.search.qweather_api_host is not None
            ),
            "tavily_configured": self.secrets.tavily_api_key is not None,
        }


def _defaults() -> dict[str, Any]:
    return {
        "agent": {
            "device_url": DEFAULT_DEVICE_URL,
            "device_id": DEFAULT_DEVICE_ID,
            "default_city": "Ningbo",
            "timezone": "Asia/Shanghai",
            "request_timeout": 30.0,
            "queue_capacity": 8,
            "history_capacity": 100,
        },
        "server": {"host": "127.0.0.1", "port": 8767},
        "model": {
            "provider": DEFAULT_LLM_PROVIDER,
            "model": "gpt-4o-mini",
            "base_url": None,
            "max_tool_steps": 3,
        },
        "search": {
            "max_results": 5,
            "qweather_api_host": None,
            "tavily_base_url": "https://api.tavily.com",
        },
        "fast_intent": {"aliases": {}},
        "touch": {position: {} for position in TOUCH_POSITIONS},
    }


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _coerce_environment(value: str, path: tuple[str, str]) -> object:
    try:
        if path in _INTEGER_PATHS:
            return int(value)
        if path in _FLOAT_PATHS:
            return float(value)
    except ValueError as error:
        raise ConfigError("invalid environment value for %s" % path[1]) from error
    return value


def _assert_no_stored_secrets(value: object, *, path: str = "") -> None:
    if not isinstance(value, Mapping):
        return
    for key, child in value.items():
        normalized = str(key).strip().lower()
        location = "%s.%s" % (path, key) if path else str(key)
        if normalized in _SECRET_KEYS or normalized.endswith("_api_key"):
            raise ConfigError(
                "%s must be supplied through environment variables only" % location
            )
        _assert_no_stored_secrets(child, path=location)


def _load_model_profile(name: str) -> Mapping[str, Any]:
    filename = MODEL_PROFILE_FILES.get(name)
    if filename is None:
        raise ConfigError("unknown model profile: %s" % name)
    try:
        resource = files("lefly_agent.model_profiles").joinpath(filename)
        with resource.open("rb") as profile_file:
            profile = tomllib.load(profile_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError("unable to load model profile: %s" % name) from error
    _assert_no_stored_secrets(profile)
    unknown_sections = sorted(set(profile) - {"model"})
    if unknown_sections:
        raise ConfigError(
            "model profile must contain only [model]: %s"
            % ", ".join(unknown_sections)
        )
    model = profile.get("model")
    if not isinstance(model, Mapping):
        raise ConfigError("model profile must contain a [model] table")
    unknown_fields = sorted(set(model) - {"provider", "model", "base_url"})
    if unknown_fields:
        raise ConfigError(
            "unknown model profile field: %s" % ", ".join(unknown_fields)
        )
    return model


def _aliases(configured: object) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(configured, Mapping):
        raise ConfigError("fast_intent.aliases must be a table")
    unknown = sorted(set(configured) - set(SUPPORTED_FAST_INTENTS))
    if unknown:
        raise ConfigError("unknown fast intent: %s" % ", ".join(unknown))

    result: dict[str, tuple[str, ...]] = dict(DEFAULT_FAST_INTENT_ALIASES)
    for intent, raw_aliases in configured.items():
        if not isinstance(raw_aliases, list) or not raw_aliases:
            raise ConfigError("aliases for %s must be a non-empty array" % intent)
        if any(not isinstance(alias, str) or not alias.strip() for alias in raw_aliases):
            raise ConfigError("aliases for %s must contain non-empty strings" % intent)
        result[intent] = result[intent] + tuple(alias.strip() for alias in raw_aliases)

    owners: dict[str, str] = {}
    for intent, values in result.items():
        for alias in values:
            normalized = normalize_text(alias)
            owner = owners.setdefault(normalized, intent)
            if owner != intent:
                raise ConfigError(
                    "conflicting fast intent alias %r: %s and %s"
                    % (alias, owner, intent)
                )
    return MappingProxyType(result)


def _positive_number(value: object, name: str, *, integer: bool = False):
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected) or value <= 0:
        raise ConfigError("%s must be positive" % name)
    return int(value) if integer else float(value)


def _nonempty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("%s must be a non-empty string" % name)
    return value.strip()


def _environment_secret(
    environment: Mapping[str, str], variable: str
) -> str | None:
    value = environment.get(variable)
    if not value:
        return None
    if any(not 33 <= ord(character) <= 126 for character in value):
        raise ConfigError(
            "%s must contain printable ASCII characters without whitespace"
            % variable
        )
    return value


def _http_endpoint(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    endpoint = _nonempty(value, name).rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("%s must be an HTTP endpoint" % name)
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ConfigError("%s must be an HTTP endpoint origin" % name)
    return endpoint


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _model_endpoint(value: object, name: str) -> str:
    endpoint = _nonempty(value, name).rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("%s must use HTTP or HTTPS" % name)
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError("%s must not contain credentials" % name)
    if parsed.query or parsed.fragment:
        raise ConfigError("%s must not contain a query or fragment" % name)
    if parsed.hostname is None:
        raise ConfigError("%s must contain a valid host" % name)
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        logger.warning(
            "model endpoint uses insecure HTTP",
            extra={"lefly_endpoint_host": parsed.hostname},
        )
    return endpoint


def _touch(value: object) -> Mapping[str, TouchBehavior]:
    if not isinstance(value, Mapping):
        raise ConfigError("touch must be a table")
    unknown = sorted(set(value) - set(TOUCH_POSITIONS))
    if unknown:
        raise ConfigError("unknown touch position: %s" % ", ".join(unknown))
    result = {}
    for position in TOUCH_POSITIONS:
        raw = value.get(position, {})
        if not isinstance(raw, Mapping):
            raise ConfigError("touch.%s must be a table" % position)
        unknown_fields = sorted(set(raw) - {"motion", "light_color"})
        if unknown_fields:
            raise ConfigError(
                "unknown touch.%s field: %s"
                % (position, ", ".join(unknown_fields))
            )
        motion = raw.get("motion")
        light_color = raw.get("light_color")
        if motion is not None:
            motion = _nonempty(motion, "touch.%s.motion" % position)
        if light_color is not None:
            light_color = _nonempty(
                light_color, "touch.%s.light_color" % position
            )
        result[position] = TouchBehavior(motion=motion, light_color=light_color)
    return MappingProxyType(result)


def load_agent_config(
    path: str | Path | None = None,
    *,
    model_profile: str | None = None,
    environ: Mapping[str, str] | None = None,
    cli: Mapping[str, object] | None = None,
    python_version: tuple[int, ...] | None = None,
) -> LeFlyAgentConfig:
    """Load immutable settings with CLI > env > profile > TOML > defaults."""
    version = python_version or tuple(sys.version_info[:3])
    if tuple(version[:2]) != (3, 12):
        raise ConfigError("LeFly Agent requires Python 3.12")

    values = _defaults()
    provider_explicit = False
    if path is not None:
        config_path = Path(path).expanduser()
        if not config_path.is_file():
            raise ConfigError("config file does not exist: %s" % config_path)
        try:
            with config_path.open("rb") as config_file:
                toml_values = tomllib.load(config_file)
        except tomllib.TOMLDecodeError as error:
            raise ConfigError("invalid TOML: %s" % error) from error
        _assert_no_stored_secrets(toml_values)
        raw_model = toml_values.get("model")
        provider_explicit = isinstance(raw_model, Mapping) and "provider" in raw_model
        _merge(values, toml_values)

    if model_profile is not None:
        profile_model = _load_model_profile(model_profile)
        values["model"]["base_url"] = None
        _merge(values["model"], profile_model)
        provider_explicit = True

    environment = os.environ if environ is None else environ
    if "LEFLY_LLM_PROVIDER" in environment:
        provider_explicit = True
    for variable, field_path in _ENV_FIELDS.items():
        if variable in environment:
            section, name = field_path
            values[section][name] = _coerce_environment(
                environment[variable], field_path
            )

    cli_fields = cli or {}
    for name in ("device_url", "device_id"):
        if cli_fields.get(name) is not None:
            values["agent"][name] = cli_fields[name]
    for name in ("host", "port"):
        if cli_fields.get(name) is not None:
            values["server"][name] = cli_fields[name]

    agent = values["agent"]
    server = values["server"]
    model = values["model"]
    search = values["search"]
    device_id = _nonempty(agent["device_id"], "device_id")
    if DEVICE_ID_PATTERN.fullmatch(device_id) is None:
        raise ConfigError("device ID must match ^[a-z][a-z0-9_-]{0,63}$")
    host = _nonempty(server["host"], "host").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigError("host must be a loopback address")
    port = server["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ConfigError("port must be between 0 and 65535")
    timezone = _nonempty(agent["timezone"], "timezone")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as error:
        raise ConfigError("unknown timezone: %s" % timezone) from error

    max_tool_steps = _positive_number(
        model["max_tool_steps"], "max_tool_steps", integer=True
    )
    if max_tool_steps > 10:
        raise ConfigError("max_tool_steps must not exceed 10")
    provider = _nonempty(model["provider"], "provider").lower()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise ConfigError("unsupported model provider: %s" % provider)
    base_url = model.get("base_url")
    if base_url is not None:
        base_url = _model_endpoint(base_url, "model.base_url")
    if provider in {"huawei_maas", "openai_compatible"} and base_url is None:
        raise ConfigError("%s requires model.base_url" % provider)

    llm_api_key = _environment_secret(environment, "LEFLY_LLM_API_KEY")
    if llm_api_key is not None and not provider_explicit:
        logger.warning(
            "model.provider was omitted; defaulting to openai for compatibility"
        )

    return LeFlyAgentConfig(
        model=ModelSettings(
            provider=provider,
            model=_nonempty(model["model"], "model"),
            base_url=base_url,
            max_tool_steps=max_tool_steps,
        ),
        server=ServerSettings(host=host, port=port),
        search=SearchSettings(
            max_results=_positive_number(
                search["max_results"], "search.max_results", integer=True
            ),
            qweather_api_host=_http_endpoint(
                search.get("qweather_api_host"),
                "search.qweather_api_host",
                optional=True,
            ),
            tavily_base_url=_http_endpoint(
                search["tavily_base_url"], "search.tavily_base_url"
            ),
        ),
        agent=AgentSettings(
            device_url=_nonempty(agent["device_url"], "device_url"),
            device_id=device_id,
            default_city=_nonempty(agent["default_city"], "default_city"),
            timezone=timezone,
            request_timeout=_positive_number(
                agent["request_timeout"], "request_timeout"
            ),
            queue_capacity=_positive_number(
                agent["queue_capacity"], "queue_capacity", integer=True
            ),
            history_capacity=_positive_number(
                agent["history_capacity"], "history_capacity", integer=True
            ),
            fast_intent_aliases=_aliases(values["fast_intent"]["aliases"]),
        ),
        touch=_touch(values["touch"]),
        secrets=SecretSettings(
            llm_api_key=llm_api_key,
            qweather_api_key=_environment_secret(
                environment, "QWEATHER_API_KEY"
            ),
            tavily_api_key=_environment_secret(environment, "TAVILY_API_KEY"),
        ),
    )
