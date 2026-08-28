"""Queued execution runtime for the clean LeFly text agent."""

import asyncio
import inspect
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol
from uuid import uuid4

from .fast_intent import FastIntentDecision, FastIntentRouter, normalize_text
from .interpreter import TextInterpreter
from .models import (
    AgentAction,
    ChatMessage,
    response_completed,
    response_delta,
    response_failed,
    response_started,
    tool_completed,
    tool_failed,
    tool_started,
)
from .telemetry import LatencyRecorder, LatencyTrace


logger = logging.getLogger(__name__)
RuntimeHandler = Callable[[Dict[str, Any]], Any]

_OFFLINE_AGENT_TOOL_NAMES = {
    "motion.play": "play_motion",
    "light.solid": "set_head_light",
    "light.brightness": "set_head_light_brightness",
    "device.rest": "enter_rest_state",
}


class AgentQueueFullError(RuntimeError):
    pass


class RobotService(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def execute_action(self, action: AgentAction): ...


class M3RobotService(RobotService, Protocol):
    async def play_motion(self, name: str): ...
    async def set_head_light(self, color: str): ...
    async def set_head_light_brightness(self, value: float): ...
    async def enter_rest_state(self): ...


class LiveKitSession(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def run_turn(self, text: str) -> str: ...
    async def sync_fast_exchange(self, user_text: str, assistant_text: str) -> None: ...
    def subscribe(self, handler): ...


class InfoService(Protocol):
    def get_current_datetime(self) -> str: ...


class InternalStatusCoordinator(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def begin_inference(self): ...
    async def finish_inference(self, token, *, successful: bool) -> None: ...


class TouchDispatcher(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def submit(self, event: Any) -> str: ...
    def subscribe(self, handler): ...


@dataclass(frozen=True)
class _Submission:
    request_id: str
    text: str
    response_id: str
    trace: LatencyTrace


_LOCAL_DATETIME_PHRASES = frozenset(
    {"现在几点", "几点了", "现在时间", "当前时间", "今天几号", "今天日期", "今天星期几"}
)


class AgentRuntime:
    def __init__(
        self,
        interpreter: TextInterpreter,
        robot: RobotService,
        *,
        device_client: Optional[Any] = None,
        queue_capacity: int = 8,
        history_capacity: int = 100,
        connection_poll_interval: float = 0.25,
        fast_router: FastIntentRouter | None = None,
        livekit_session: LiveKitSession | None = None,
        info_service: InfoService | None = None,
        status_coordinator: InternalStatusCoordinator | None = None,
        telemetry: LatencyRecorder | None = None,
        touch_dispatcher: TouchDispatcher | None = None,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if history_capacity <= 0:
            raise ValueError("history_capacity must be positive")
        if connection_poll_interval <= 0:
            raise ValueError("connection_poll_interval must be positive")
        self.interpreter = interpreter
        self.robot = robot
        self.device_client = device_client
        self.queue_capacity = queue_capacity
        self.history_capacity = history_capacity
        self.connection_poll_interval = connection_poll_interval
        self.fast_router = fast_router
        self.livekit_session = livekit_session
        self.info_service = info_service
        self.status_coordinator = status_coordinator
        self.telemetry = telemetry or LatencyRecorder()
        self.touch_dispatcher = touch_dispatcher
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_capacity)
        self._history: Deque[ChatMessage] = deque(maxlen=history_capacity)
        self._handlers: List[RuntimeHandler] = []
        self._callback_tasks = set()
        self._worker: Optional[asyncio.Task] = None
        self._connection_monitor: Optional[asyncio.Task] = None
        self._last_connected: Optional[bool] = None
        self._unsubscribe_touch: Optional[Callable[[], None]] = None
        self._unsubscribe_livekit: Optional[Callable[[], None]] = None
        self._unsubscribe_touch_outcomes: Optional[Callable[[], None]] = None
        self._active_submission: _Submission | None = None
        self._phase = "idle"
        self._idle = asyncio.Event()
        self._idle.set()
        self._closed = False
        self._robot_started = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("agent runtime is closed")
        if self._worker is not None:
            return
        await self.robot.start()
        self._robot_started = True
        if self.status_coordinator is not None:
            await self.status_coordinator.start()
        if self.livekit_session is not None:
            self._unsubscribe_livekit = self.livekit_session.subscribe(
                self._on_livekit_event
            )
            await self.livekit_session.start()
        if self.touch_dispatcher is not None:
            self._unsubscribe_touch_outcomes = self.touch_dispatcher.subscribe(
                self._on_touch_dispatch_event
            )
            await self.touch_dispatcher.start()
        if self.device_client is not None:
            self._unsubscribe_touch = self.device_client.subscribe(
                "sensor.touch", self._on_touch
            )
        self._worker = asyncio.create_task(self._run(), name="lefly-agent-worker")
        if self.device_client is not None:
            self._last_connected = self._device_connected()
            self._connection_monitor = asyncio.create_task(
                self._monitor_connection(), name="lefly-agent-connection-monitor"
            )
        self._publish_state()
        logger.info(
            "agent.runtime.resources",
            extra={
                "lefly_resources": {
                    "robot_service": "reusable",
                    "livekit_session": self.livekit_session is not None,
                    "info_service": self.info_service is not None,
                    "device_client": self.device_client is not None,
                }
            },
        )
        if self.livekit_session is not None:
            logger.info("agent.runtime.prewarm_completed")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._unsubscribe_livekit is not None:
            self._unsubscribe_livekit()
            self._unsubscribe_livekit = None
        if self._unsubscribe_touch_outcomes is not None:
            self._unsubscribe_touch_outcomes()
            self._unsubscribe_touch_outcomes = None
        if self._unsubscribe_touch is not None:
            self._unsubscribe_touch()
            self._unsubscribe_touch = None
        if self._connection_monitor is not None:
            self._connection_monitor.cancel()
            await asyncio.gather(self._connection_monitor, return_exceptions=True)
            self._connection_monitor = None
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        if self.touch_dispatcher is not None:
            await self.touch_dispatcher.close()
        if self.livekit_session is not None:
            await self.livekit_session.close()
        if self.status_coordinator is not None:
            await self.status_coordinator.close()
        if self._callback_tasks:
            tasks = tuple(self._callback_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._callback_tasks.clear()
        if self._robot_started:
            await self.robot.close()
            self._robot_started = False
        self._idle.set()

    async def submit_text(self, request_id: str, text: str) -> None:
        if self._closed or self._worker is None:
            raise RuntimeError("agent runtime is not running")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be non-empty")
        normalized = self._validate_text(text)
        response_id = str(uuid4())
        trace = self.telemetry.begin(
            request_id.strip(),
            response_id=response_id,
            queue_depth=self._queue.qsize(),
            text_length=len(normalized),
        )
        submission = _Submission(request_id.strip(), normalized, response_id, trace)
        try:
            self._queue.put_nowait(submission)
        except asyncio.QueueFull as error:
            trace.stage(
                "response_failed", outcome="rejected", error_type="queue_full"
            )
            raise AgentQueueFullError("agent text queue is full") from error
        trace.stage("request_accepted", outcome="accepted")
        self._idle.clear()
        self._append_message(ChatMessage.create("user", normalized))
        self._publish_state()

    async def wait_until_idle(self, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self._idle.wait(), timeout)

    def subscribe(self, handler: RuntimeHandler) -> Callable[[], None]:
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    def snapshot(self) -> Dict[str, Any]:
        return {
            **self._state(),
            "messages": [message.to_dict() for message in self._history],
        }

    async def _run(self) -> None:
        while True:
            submission = await self._queue.get()
            try:
                submission.trace.stage("queue_wait_completed", outcome="dequeued")
                await self._process(submission)
            finally:
                self._queue.task_done()
                if self._queue.empty():
                    self._set_phase("idle")
                    self._idle.set()
                else:
                    self._publish_state()

    async def _process(self, submission: _Submission) -> None:
        try:
            if self.fast_router is not None and self.livekit_session is not None:
                await self._process_m3(submission)
                return
            self._set_phase("interpreting")
            plan = await self.interpreter.interpret(submission.text)
            if plan.actions:
                submission.trace.stage(
                    "route_decided", decision_path="fast_intent", outcome="matched"
                )
                self._set_phase("executing")
                for action in plan.actions:
                    tool_call_id = str(uuid4())
                    tool_name = _OFFLINE_AGENT_TOOL_NAMES.get(action.tool, action.tool)
                    submission.trace.stage(
                        "tool_started",
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        outcome="started",
                    )
                    try:
                        result = await self._execute(action)
                    except BaseException:
                        submission.trace.stage(
                            "tool_failed",
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            outcome="failed",
                        )
                        raise
                    correlation_id = getattr(result, "correlation_id", None)
                    disposition = getattr(result, "disposition", None)
                    if correlation_id is not None:
                        submission.trace.stage(
                            "sdk_acknowledged",
                            tool_call_id=tool_call_id,
                            protocol_correlation_id=correlation_id,
                            outcome=disposition or "acknowledged",
                        )
                    submission.trace.stage(
                        "tool_completed",
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        protocol_correlation_id=correlation_id,
                        outcome=disposition or "completed",
                    )
            else:
                submission.trace.stage(
                    "route_decided", decision_path="fast_intent", outcome="unmatched"
                )
            submission.trace.stage_once("response_first_delta", outcome="visible")
            self._append_message(ChatMessage.create("agent", plan.response))
            submission.trace.stage("response_completed", outcome="completed")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._set_phase("error")
            message = "执行失败：%s" % str(error)
            if self.fast_router is None or self.livekit_session is None:
                self._append_message(ChatMessage.create("agent", message))
            submission.trace.stage(
                "response_failed",
                outcome="failed",
                error_type=type(error).__name__,
            )
            self._publish(
                response_failed(submission.request_id, submission.response_id)
            )
            self._publish(
                {
                    "type": "agent.error",
                    "request_id": submission.request_id,
                    "code": "execution_failed",
                    "message": str(error),
                    "recoverable": True,
                }
            )

    async def _process_m3(self, submission: _Submission) -> None:
        self._active_submission = submission
        self._set_phase("interpreting")
        self._publish(response_started(submission.request_id, submission.response_id))
        try:
            local_response = self._local_datetime_response(submission.text)
            if local_response is not None:
                submission.trace.stage(
                    "route_decided", decision_path="fast_intent", outcome="matched"
                )
                response = await self._run_fast_info_tool(
                    submission, "get_current_datetime", local_response
                )
                await self._complete_fast_response(submission, response)
                return

            decision = self.fast_router.route(submission.text, source="typed")
            if decision.matched:
                submission.trace.stage(
                    "route_decided", decision_path="fast_intent", outcome="matched"
                )
                response = await self._run_fast_robot_tool(submission, decision)
                await self._complete_fast_response(submission, response)
                return

            submission.trace.stage(
                "route_decided", decision_path="livekit", outcome="fallback"
            )
            submission.trace.stage("llm_request_started", outcome="started")
            token = None
            if self.status_coordinator is not None:
                token = await self.status_coordinator.begin_inference()
            successful = False
            try:
                response = await self.livekit_session.run_turn(submission.text)
                successful = True
            finally:
                if self.status_coordinator is not None and token is not None:
                    await self.status_coordinator.finish_inference(
                        token, successful=successful
                    )
            if not response.strip():
                response = "模型没有返回可见文本。"
            if not submission.trace.has_stage("response_first_delta"):
                self._publish_response_delta(submission, response)
            self._commit_response(submission, response)
            self._publish_response_completed(submission)
        finally:
            self._active_submission = None

    def _local_datetime_response(self, text: str) -> str | None:
        if self.info_service is None or normalize_text(text) not in _LOCAL_DATETIME_PHRASES:
            return None
        return self.info_service.get_current_datetime()

    async def _run_fast_info_tool(
        self, submission: _Submission, tool_name: str, response: str
    ) -> str:
        tool_call_id = str(uuid4())
        self._publish_tool_started(submission, tool_name, tool_call_id)
        self._publish_tool_completed(submission, tool_name, tool_call_id, None, None)
        return response

    async def _run_fast_robot_tool(
        self, submission: _Submission, decision: FastIntentDecision
    ) -> str:
        assert decision.intent is not None
        tool_call_id = str(uuid4())
        self._set_phase("executing")
        self._publish_tool_started(submission, decision.intent, tool_call_id)
        operation = {
            "play_motion": lambda: self.robot.play_motion(decision.arguments["name"]),
            "set_head_light": lambda: self.robot.set_head_light(decision.arguments["color"]),
            "set_head_light_brightness": lambda: self.robot.set_head_light_brightness(
                decision.arguments["brightness"]
            ),
            "enter_rest_state": self.robot.enter_rest_state,
        }.get(decision.intent)
        if operation is None:
            raise RuntimeError("unsupported fast intent: %s" % decision.intent)
        try:
            result = await operation()
        except BaseException:
            submission.trace.stage(
                "tool_failed",
                tool_call_id=tool_call_id,
                tool_name=decision.intent,
                outcome="failed",
            )
            self._publish(
                tool_failed(
                    submission.request_id,
                    submission.response_id,
                    tool_call_id,
                    decision.intent,
                )
            )
            raise
        correlation_id = getattr(result, "correlation_id", None)
        disposition = getattr(result, "disposition", None)
        submission.trace.stage(
            "sdk_acknowledged",
            tool_call_id=tool_call_id,
            protocol_correlation_id=correlation_id,
            outcome=disposition or "acknowledged",
        )
        self._publish_tool_completed(
            submission,
            decision.intent,
            tool_call_id,
            correlation_id,
            disposition,
        )
        return decision.confirmation or "命令已提交。"

    async def _complete_fast_response(
        self, submission: _Submission, response: str
    ) -> None:
        self._publish_response_delta(submission, response)
        self._commit_response(submission, response)
        self._publish_response_completed(submission)
        await self.livekit_session.sync_fast_exchange(submission.text, response)

    def _publish_response_delta(self, submission: _Submission, text: str) -> None:
        submission.trace.stage_once("response_first_delta", outcome="visible")
        self._publish(response_delta(submission.request_id, submission.response_id, text))

    def _publish_response_completed(self, submission: _Submission) -> None:
        submission.trace.stage("response_completed", outcome="completed")
        self._publish(response_completed(submission.request_id, submission.response_id))

    def _commit_response(self, submission: _Submission, response: str) -> None:
        self._history.append(
            ChatMessage.create(
                "agent", response, message_id=submission.response_id
            )
        )

    def _publish_tool_started(
        self, submission: _Submission, tool_name: str, tool_call_id: str | None
    ) -> None:
        submission.trace.stage(
            "tool_started",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            outcome="started",
        )
        if tool_call_id is None:
            logger.error("LiveKit tool start omitted tool_call_id")
            return
        self._publish(
            tool_started(
                submission.request_id,
                submission.response_id,
                tool_call_id,
                tool_name,
            )
        )

    def _publish_tool_completed(
        self,
        submission: _Submission,
        tool_name: str,
        tool_call_id: str | None,
        correlation_id: str | None,
        disposition: str | None,
    ) -> None:
        if tool_call_id is None:
            logger.error("LiveKit tool completion omitted tool_call_id")
            return
        submission.trace.stage(
            "tool_completed",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            protocol_correlation_id=correlation_id,
            outcome=disposition or "completed",
        )
        self._publish(
            tool_completed(
                submission.request_id,
                submission.response_id,
                tool_call_id,
                tool_name,
                protocol_correlation_id=correlation_id,
                disposition=disposition,
            )
        )

    def _on_livekit_event(self, event: Any) -> None:
        submission = self._active_submission
        if submission is None:
            return
        event_type = getattr(event, "type", None)
        if event_type == "llm.chunk":
            submission.trace.stage_once("llm_first_chunk", outcome="received")
            return
        if event_type == "text.delta":
            submission.trace.stage_once("llm_first_chunk", outcome="received")
            text = getattr(event, "text", None)
            if isinstance(text, str) and text:
                self._publish_response_delta(submission, text)
            return
        tool_name = getattr(event, "tool_name", None) or "unknown"
        tool_call_id = getattr(event, "tool_call_id", None)
        if event_type == "tool.started":
            self._publish_tool_started(submission, tool_name, tool_call_id)
        elif event_type == "tool.completed":
            correlation_id = getattr(event, "correlation_id", None)
            disposition = getattr(event, "disposition", None)
            if correlation_id is not None:
                submission.trace.stage(
                    "sdk_acknowledged",
                    tool_call_id=tool_call_id,
                    protocol_correlation_id=correlation_id,
                    outcome=disposition or "acknowledged",
                )
            self._publish_tool_completed(
                submission,
                tool_name,
                tool_call_id,
                correlation_id,
                disposition,
            )
        elif event_type == "tool.failed":
            if tool_call_id is None:
                logger.error("LiveKit tool failure omitted tool_call_id")
                return
            submission.trace.stage(
                "tool_failed",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                outcome="failed",
            )
            self._publish(
                tool_failed(
                    submission.request_id,
                    submission.response_id,
                    tool_call_id,
                    tool_name,
                )
            )

    async def _execute(self, action: AgentAction) -> Any:
        if self.device_client is not None and not self._device_connected():
            raise RuntimeError("device is disconnected")
        return await self.robot.execute_action(action)

    def _on_touch(self, event: Any) -> None:
        if self.touch_dispatcher is not None:
            self.touch_dispatcher.submit(event)
            return
        payload = getattr(event, "payload", {})
        if payload.get("pressed") is not True:
            return
        labels = {"left": "左侧", "middle": "中间", "right": "右侧"}
        position = payload.get("position")
        label = labels.get(position, str(position))
        self._append_message(ChatMessage.create("system", "检测到%s触摸。" % label))

    def _on_touch_dispatch_event(self, event: Any) -> None:
        outcome = getattr(event, "outcome", None)
        if outcome not in {"completed", "failed", "dropped_queue_full"}:
            return
        labels = {"left": "左侧", "middle": "中间", "right": "右侧"}
        label = labels.get(getattr(event, "position", None), "未知位置")
        if outcome == "completed":
            text = "%s触摸联动已完成。" % label
        elif outcome == "dropped_queue_full":
            text = "%s触摸联动因队列已满被丢弃。" % label
        else:
            text = "%s触摸联动执行失败。" % label
        self._append_message(ChatMessage.create("system", text))

    def _append_message(self, message: ChatMessage) -> None:
        self._history.append(message)
        self._publish({"type": "agent.message", "message": message.to_dict()})

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        self._publish_state()

    def _publish_state(self) -> None:
        self._publish({"type": "agent.state", "state": self._state()})

    def _state(self) -> Dict[str, Any]:
        connected = True
        if self.device_client is not None:
            connected = self._device_connected()
        return {
            "phase": self._phase,
            "device_connected": connected,
            "queue": {
                "size": self._queue.qsize(),
                "capacity": self.queue_capacity,
            },
        }

    async def _monitor_connection(self) -> None:
        while True:
            await asyncio.sleep(self.connection_poll_interval)
            transport_connected = bool(
                getattr(self.device_client, "is_connected", False)
            )
            if not transport_connected:
                invalidate = getattr(self.robot, "invalidate_state", None)
                if callable(invalidate):
                    invalidate()
            elif not self._robot_ready():
                synchronize = getattr(self.robot, "synchronize_state", None)
                if callable(synchronize):
                    try:
                        await synchronize()
                    except Exception as error:
                        logger.warning(
                            "agent.device.state_resync_failed",
                            extra={"error_type": type(error).__name__},
                        )
            connected = self._device_connected()
            if connected == self._last_connected:
                continue
            self._last_connected = connected
            self._publish_state()

    def _device_connected(self) -> bool:
        return bool(
            getattr(self.device_client, "is_connected", False)
        ) and self._robot_ready()

    def _robot_ready(self) -> bool:
        ready = getattr(self.robot, "is_ready", None)
        return True if ready is None else bool(ready)

    def _publish(self, event: Dict[str, Any]) -> None:
        for handler in tuple(self._handlers):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    task = asyncio.create_task(result)
                    self._callback_tasks.add(task)
                    task.add_done_callback(self._callback_tasks.discard)
                    task.add_done_callback(self._callback_finished)
            except Exception:
                logger.error("agent runtime subscriber failed", exc_info=True)

    @staticmethod
    def _callback_finished(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("async agent runtime subscriber failed", exc_info=True)

    @staticmethod
    def _validate_text(text: str) -> str:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        normalized = text.strip()
        if not normalized:
            raise ValueError("text must be non-empty")
        if len(normalized) > 500:
            raise ValueError("text must not exceed 500 characters")
        return normalized
