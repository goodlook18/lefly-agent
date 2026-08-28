"""Transport-independent models for LeFly agent planning and chat."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Tuple
from uuid import uuid4


AGENT_TOOL_NAMES = frozenset(
    {
        "play_motion",
        "set_head_light",
        "set_head_light_brightness",
        "enter_rest_state",
        "get_current_datetime",
        "get_weather",
        "web_search",
    }
)
AGENT_LIFECYCLE_TYPES = frozenset(
    {
        "agent.response.started",
        "agent.response.delta",
        "agent.response.completed",
        "agent.response.failed",
        "agent.tool.started",
        "agent.tool.completed",
        "agent.tool.failed",
    }
)


@dataclass(frozen=True)
class AgentAction:
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or not self.tool.strip():
            raise ValueError("tool must be non-empty")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True)
class AgentPlan:
    actions: Tuple[AgentAction, ...]
    response: str

    def __post_init__(self) -> None:
        if not isinstance(self.response, str) or not self.response.strip():
            raise ValueError("response must be non-empty")


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    role: str
    text: str
    timestamp: str

    @classmethod
    def create(
        cls, role: str, text: str, *, message_id: str | None = None
    ) -> "ChatMessage":
        if role not in {"user", "agent", "system"}:
            raise ValueError("unsupported chat role")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("chat text must be non-empty")
        if message_id is not None:
            message_id = _identifier("message_id", message_id)
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return cls(
            message_id=message_id or "msg-%s" % uuid4(),
            role=role,
            text=text.strip(),
            timestamp=now.replace("+00:00", "Z"),
        )

    def to_dict(self):
        return {
            "id": self.message_id,
            "role": self.role,
            "text": self.text,
            "timestamp": self.timestamp,
        }


def response_started(request_id: str, response_id: str) -> dict[str, Any]:
    return _lifecycle_base("agent.response.started", request_id, response_id)


def response_delta(request_id: str, response_id: str, text: str) -> dict[str, Any]:
    value = _lifecycle_base("agent.response.delta", request_id, response_id)
    if not isinstance(text, str) or not text or len(text) > 4000:
        raise ValueError("response delta text must contain 1 to 4000 characters")
    value["text"] = text
    return value


def response_completed(request_id: str, response_id: str) -> dict[str, Any]:
    return _lifecycle_base("agent.response.completed", request_id, response_id)


def response_failed(
    request_id: str,
    response_id: str,
    *,
    code: str = "response_failed",
    message: str = "处理请求失败，请稍后重试。",
    recoverable: bool = True,
) -> dict[str, Any]:
    value = _lifecycle_base("agent.response.failed", request_id, response_id)
    value.update(_failure_fields(code, message, recoverable))
    return value


def tool_started(
    request_id: str,
    response_id: str,
    tool_call_id: str,
    tool_name: str,
) -> dict[str, Any]:
    value = _tool_base(
        "agent.tool.started", request_id, response_id, tool_call_id, tool_name
    )
    return value


def tool_completed(
    request_id: str,
    response_id: str,
    tool_call_id: str,
    tool_name: str,
    *,
    protocol_correlation_id: str | None = None,
    disposition: str | None = None,
) -> dict[str, Any]:
    value = _tool_base(
        "agent.tool.completed", request_id, response_id, tool_call_id, tool_name
    )
    if protocol_correlation_id is not None:
        value["protocol_correlation_id"] = _identifier(
            "protocol_correlation_id", protocol_correlation_id
        )
    if disposition is not None:
        if disposition not in {"applied", "queued"}:
            raise ValueError("tool disposition must be applied or queued")
        value["disposition"] = disposition
    return value


def tool_failed(
    request_id: str,
    response_id: str,
    tool_call_id: str,
    tool_name: str,
    *,
    code: str = "tool_failed",
    message: str = "工具执行失败。",
    recoverable: bool = True,
) -> dict[str, Any]:
    value = _tool_base(
        "agent.tool.failed", request_id, response_id, tool_call_id, tool_name
    )
    value.update(_failure_fields(code, message, recoverable))
    return value


def validate_lifecycle_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy one runtime lifecycle event at the server boundary."""
    if not isinstance(event, Mapping):
        raise TypeError("Agent lifecycle event must be a mapping")
    message_type = event.get("type")
    if message_type not in AGENT_LIFECYCLE_TYPES:
        raise ValueError("unsupported Agent lifecycle event")
    builders = {
        "agent.response.started": lambda: response_started(
            event.get("request_id"), event.get("response_id")
        ),
        "agent.response.delta": lambda: response_delta(
            event.get("request_id"), event.get("response_id"), event.get("text")
        ),
        "agent.response.completed": lambda: response_completed(
            event.get("request_id"), event.get("response_id")
        ),
        "agent.response.failed": lambda: response_failed(
            event.get("request_id"),
            event.get("response_id"),
            code=event.get("code"),
            message=event.get("message"),
            recoverable=event.get("recoverable"),
        ),
        "agent.tool.started": lambda: tool_started(
            event.get("request_id"),
            event.get("response_id"),
            event.get("tool_call_id"),
            event.get("tool_name"),
        ),
        "agent.tool.completed": lambda: tool_completed(
            event.get("request_id"),
            event.get("response_id"),
            event.get("tool_call_id"),
            event.get("tool_name"),
            protocol_correlation_id=event.get("protocol_correlation_id"),
            disposition=event.get("disposition"),
        ),
        "agent.tool.failed": lambda: tool_failed(
            event.get("request_id"),
            event.get("response_id"),
            event.get("tool_call_id"),
            event.get("tool_name"),
            code=event.get("code"),
            message=event.get("message"),
            recoverable=event.get("recoverable"),
        ),
    }
    normalized = builders[message_type]()
    if set(event) != set(normalized):
        raise ValueError("Agent lifecycle event contains unknown or missing fields")
    return normalized


def _lifecycle_base(
    message_type: str, request_id: str, response_id: str
) -> dict[str, Any]:
    return {
        "type": message_type,
        "request_id": _identifier("request_id", request_id),
        "response_id": _identifier("response_id", response_id),
    }


def _tool_base(
    message_type: str,
    request_id: str,
    response_id: str,
    tool_call_id: str,
    tool_name: str,
) -> dict[str, Any]:
    value = _lifecycle_base(message_type, request_id, response_id)
    value["tool_call_id"] = _identifier("tool_call_id", tool_call_id)
    if tool_name not in AGENT_TOOL_NAMES:
        raise ValueError("unsupported public Agent tool")
    value["tool_name"] = tool_name
    return value


def _failure_fields(code: str, message: str, recoverable: bool) -> dict[str, Any]:
    value = {
        "code": _identifier("error code", code),
        "message": _identifier("error message", message, max_length=500),
    }
    if not isinstance(recoverable, bool):
        raise TypeError("recoverable must be a boolean")
    value["recoverable"] = recoverable
    return value


def _identifier(name: str, value: str, *, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be non-empty" % name)
    normalized = value.strip()
    if normalized != value:
        raise ValueError("%s must not contain surrounding whitespace" % name)
    if len(normalized) > max_length:
        raise ValueError("%s is too long" % name)
    return normalized
