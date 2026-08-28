"""Internal robot status coordination for model inference turns."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

logger = logging.getLogger(__name__)


class StatusController(Protocol):
    async def set_status(self, mode: str): ...


class StatusClient(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def subscribe(self, message_type: str, handler): ...


@dataclass(frozen=True)
class InferenceStatusToken:
    restore_active: bool = False


class StatusCoordinator:
    """Own status.set so it cannot become a public Agent tool."""

    def __init__(self, controller: StatusController, client: StatusClient) -> None:
        self._controller = controller
        self._client = client
        self._mode: str | None = None
        self._unsubscribe: Callable[[], None] | None = None

    async def start(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self._client.subscribe(
                "device.state_changed", self._on_state
            )

    async def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._mode = None

    async def begin_inference(self) -> InferenceStatusToken:
        if not self._client.is_connected or self._mode != "active":
            return InferenceStatusToken()
        try:
            await self._controller.set_status("thinking")
        except Exception as error:
            logger.warning(
                "unable to set inference status",
                extra={"error_type": type(error).__name__},
            )
            return InferenceStatusToken()
        return InferenceStatusToken(restore_active=True)

    async def finish_inference(
        self, token: InferenceStatusToken, *, successful: bool
    ) -> None:
        del successful
        if token.restore_active and self._client.is_connected:
            try:
                await self._controller.set_status("active")
            except Exception as error:
                logger.warning(
                    "unable to restore active status",
                    extra={"error_type": type(error).__name__},
                )

    def _on_state(self, event: Any) -> None:
        payload = getattr(event, "payload", {})
        connection = payload.get("connection")
        status = payload.get("status", {})
        mode = status.get("mode") if isinstance(status, Mapping) else None
        if connection not in {"ready", "degraded"} or not isinstance(mode, str):
            self._mode = None
            return
        self._mode = mode
