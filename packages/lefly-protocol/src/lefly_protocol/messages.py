"""Typed envelopes for LeFly Device Protocol version 1."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Dict, Optional, Type, TypeVar

from .validation import ProtocolError, validate_command, validate_envelope, validate_event


PROTOCOL_VERSION = "1"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


EnvelopeType = TypeVar("EnvelopeType", bound="_Envelope")


@dataclass(frozen=True)
class _Envelope:
    message_id: str
    message_type: str
    timestamp: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    device_id: Optional[str] = None
    correlation_id: Optional[str] = None
    version: str = PROTOCOL_VERSION

    _WIRE_FIELDS: ClassVar[set] = {
        "version",
        "id",
        "type",
        "timestamp",
        "payload",
        "device_id",
        "correlation_id",
    }

    def __post_init__(self) -> None:
        validate_envelope(
            version=self.version,
            message_id=self.message_id,
            message_type=self.message_type,
            timestamp=self.timestamp,
            device_id=self.device_id,
            correlation_id=self.correlation_id,
            payload=self.payload,
        )
        self._validate_specific()
        object.__setattr__(self, "payload", _freeze(self.payload))

    def _validate_specific(self) -> None:
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        message = {
            "version": self.version,
            "id": self.message_id,
            "type": self.message_type,
            "timestamp": self.timestamp,
            "payload": _thaw(self.payload),
        }
        if self.device_id is not None:
            message["device_id"] = self.device_id
        if self.correlation_id is not None:
            message["correlation_id"] = self.correlation_id
        return message

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls: Type[EnvelopeType], data: Mapping[str, Any]) -> EnvelopeType:
        if not isinstance(data, Mapping):
            raise ProtocolError("message must be an object")
        unknown = sorted(set(data) - cls._WIRE_FIELDS)
        if unknown:
            raise ProtocolError(f"Unknown envelope field: {unknown[0]}")
        missing = [
            name
            for name in ("version", "id", "type", "timestamp", "device_id", "payload")
            if name not in data
        ]
        if missing:
            raise ProtocolError(f"Missing envelope field: {missing[0]}")
        return cls(
            version=data["version"],
            message_id=data["id"],
            message_type=data["type"],
            timestamp=data["timestamp"],
            payload=data["payload"],
            device_id=data.get("device_id"),
            correlation_id=data.get("correlation_id"),
        )

    @classmethod
    def from_json(cls: Type[EnvelopeType], value: str) -> EnvelopeType:
        try:
            data = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"Invalid JSON: {exc}") from exc
        return cls.from_dict(data)


@dataclass(frozen=True)
class DeviceCommand(_Envelope):
    """Command sent by an agent or SDK to a LeFly device endpoint."""

    def _validate_specific(self) -> None:
        validate_command(self.message_type, self.payload, self.correlation_id)


@dataclass(frozen=True)
class DeviceEvent(_Envelope):
    """Event emitted by a LeFly device endpoint toward an agent or SDK."""

    def _validate_specific(self) -> None:
        validate_event(self.message_type, self.payload, self.correlation_id, self.device_id)
