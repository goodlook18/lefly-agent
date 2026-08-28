"""Secret-safe monotonic latency records for Agent request stages."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any


logger = logging.getLogger(__name__)

STAGES = frozenset(
    {
        "request_received",
        "request_accepted",
        "queue_wait_completed",
        "route_decided",
        "llm_request_started",
        "llm_first_chunk",
        "tool_started",
        "tool_completed",
        "tool_failed",
        "sdk_acknowledged",
        "response_first_delta",
        "response_completed",
        "response_failed",
    }
)


class LatencyRecorder:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.perf_counter,
        sink: Callable[[dict[str, Any]], None] | None = None,
        capacity: int = 200,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._clock = clock
        self._sink = sink
        self._records = deque(maxlen=capacity)

    def begin(
        self,
        request_id: str,
        *,
        response_id: str,
        queue_depth: int,
        text_length: int | None = None,
    ) -> "LatencyTrace":
        trace = LatencyTrace(
            self,
            request_id=request_id,
            response_id=response_id,
            queue_depth=queue_depth,
            started_at=self._clock(),
        )
        trace._emit(
            "request_received",
            now=trace.started_at,
            outcome="received",
            text_length=text_length,
        )
        return trace

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(record) for record in self._records)

    def _record(self, value: dict[str, Any]) -> None:
        self._records.append(value)
        if self._sink is not None:
            self._sink(dict(value))
        logger.info("agent.latency.stage", extra={"lefly_latency": value})


class LatencyTrace:
    def __init__(
        self,
        recorder: LatencyRecorder,
        *,
        request_id: str,
        response_id: str,
        queue_depth: int,
        started_at: float,
    ) -> None:
        self._recorder = recorder
        self.request_id = request_id
        self.response_id = response_id
        self.queue_depth = queue_depth
        self.started_at = started_at
        self._previous_at = started_at
        self._once = set()
        self.decision_path: str | None = None

    def stage(self, stage: str, **fields: Any) -> None:
        self._emit(stage, now=self._recorder._clock(), **fields)

    def stage_once(self, stage: str, **fields: Any) -> None:
        if stage in self._once:
            return
        self._once.add(stage)
        self.stage(stage, **fields)

    def has_stage(self, stage: str) -> bool:
        return stage in self._once

    def _emit(self, stage: str, *, now: float, **fields: Any) -> None:
        if stage not in STAGES:
            raise ValueError("unknown latency stage: %s" % stage)
        decision_path = fields.pop("decision_path", None)
        if decision_path is not None:
            if decision_path not in {"fast_intent", "livekit"}:
                raise ValueError("invalid decision_path")
            self.decision_path = decision_path
        value: dict[str, Any] = {
            "event": "agent.latency.stage",
            "stage": stage,
            "request_id": self.request_id,
            "response_id": self.response_id,
            "decision_path": self.decision_path,
            "stage_elapsed_ms": round(max(0.0, now - self._previous_at) * 1000, 3),
            "total_elapsed_ms": round(max(0.0, now - self.started_at) * 1000, 3),
            "queue_depth": self.queue_depth,
            "outcome": fields.pop("outcome", "pending"),
            "error_type": fields.pop("error_type", None),
        }
        for key in (
            "tool_call_id",
            "tool_name",
            "protocol_correlation_id",
            "text_length",
        ):
            item = fields.pop(key, None)
            if item is not None:
                value[key] = item
        if fields:
            raise ValueError("unsupported latency fields: %s" % sorted(fields))
        self._previous_at = max(self._previous_at, now)
        self._recorder._record(value)
