import asyncio
import unittest
from types import MappingProxyType

from lefly_agent import AgentAction, AgentPlan, DeterministicChineseInterpreter
from lefly_agent.fast_intent import FastIntentRouter
from lefly_agent.runtime import AgentQueueFullError, AgentRuntime
from lefly_agent.telemetry import LatencyRecorder


class FakeController:
    def __init__(self):
        self.calls = []
        self.fail_on = None

    async def start(self):
        pass

    async def close(self):
        pass

    async def execute_action(self, action):
        arguments = action.arguments
        if action.tool == "motion.play":
            return await self.play_movement(arguments["name"])
        elif action.tool == "light.solid":
            return await self.set_light_color(arguments["color"])
        elif action.tool == "light.brightness":
            return await self.set_light_brightness(arguments["brightness"])
        elif action.tool == "device.rest":
            return await self.enter_rest_state()
        else:
            raise ValueError("unsupported agent tool: %s" % action.tool)

    async def play_movement(self, name):
        return await self._record("motion.play", {"name": name})

    async def set_light_color(self, color):
        return await self._record("light.solid", {"color": color})

    async def set_light_brightness(self, brightness):
        return await self._record("light.brightness", {"brightness": brightness})

    async def enter_rest_state(self):
        return await self._record("device.rest", {})

    async def _record(self, tool, arguments):
        if self.fail_on == tool:
            raise RuntimeError("controller failed")
        self.calls.append((tool, arguments))
        return FakeCommandResult()


class FakeDeviceClient:
    def __init__(self, connected=True):
        self.is_connected = connected
        self.subscriptions = {}

    def subscribe(self, message_type, handler):
        self.subscriptions[message_type] = handler

        def unsubscribe():
            self.subscriptions.pop(message_type, None)

        return unsubscribe


class FakeEvent:
    def __init__(self, payload):
        self.payload = MappingProxyType(payload)


class StaticInterpreter:
    def __init__(self, plan):
        self.plan = plan

    async def interpret(self, text):
        return self.plan


class BlockingInterpreter:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def interpret(self, text):
        self.entered.set()
        await self.release.wait()
        return AgentPlan((), "完成。")


class BlockingActionInterpreter(BlockingInterpreter):
    async def interpret(self, text):
        self.entered.set()
        await self.release.wait()
        return AgentPlan((AgentAction("motion.play", {"name": "nod"}, "点头"),), "完成。")


class ActionOnlyRobotService:
    def __init__(self):
        self.actions = []
        self.started = False
        self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def execute_action(self, action):
        self.actions.append(action)


class FakeCommandResult:
    correlation_id = "command-1"
    disposition = "queued"


class M3Robot(ActionOnlyRobotService):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail_motion = False

    async def play_motion(self, name):
        self.calls.append(("play_motion", {"name": name}))
        if self.fail_motion:
            raise RuntimeError("device is disconnected")
        return FakeCommandResult()

    async def set_head_light(self, color):
        self.calls.append(("set_head_light", {"color": color}))
        return FakeCommandResult()

    async def set_head_light_brightness(self, value):
        self.calls.append(("set_head_light_brightness", {"value": value}))
        return FakeCommandResult()

    async def enter_rest_state(self):
        self.calls.append(("enter_rest_state", {}))
        return FakeCommandResult()


class FakeInfo:
    def __init__(self):
        self.datetime_calls = 0

    def get_current_datetime(self):
        self.datetime_calls += 1
        return "2026年8月21日 星期五 09:30（Asia/Shanghai）"


class FakeLiveKitSession:
    def __init__(self, response="模型回答", *, emit_tool=False):
        self.response = response
        self.emit_tool = emit_tool
        self.start_calls = 0
        self.close_calls = 0
        self.run_calls = []
        self.sync_calls = []
        self.handlers = []

    async def start(self):
        self.start_calls += 1

    async def close(self):
        self.close_calls += 1

    async def run_turn(self, text):
        self.run_calls.append(text)
        if self.emit_tool:
            for handler in tuple(self.handlers):
                handler(type("Event", (), {"type": "llm.chunk"})())
                handler(
                    type(
                        "Event",
                        (),
                        {
                            "type": "tool.started",
                            "tool_name": "get_current_datetime",
                            "tool_call_id": "call-time",
                        },
                    )()
                )
                handler(
                    type(
                        "Event",
                        (),
                        {
                            "type": "tool.completed",
                            "tool_name": "get_current_datetime",
                            "tool_call_id": "call-time",
                            "correlation_id": None,
                            "disposition": None,
                        },
                    )()
                )
        for handler in tuple(self.handlers):
            handler(type("Event", (), {"type": "text.delta", "text": self.response})())
        return self.response

    async def sync_fast_exchange(self, user_text, assistant_text):
        self.sync_calls.append((user_text, assistant_text))

    def subscribe(self, handler):
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)


class FakeStatusCoordinator:
    def __init__(self):
        self.calls = []

    async def start(self):
        self.calls.append("start")

    async def close(self):
        self.calls.append("close")

    async def begin_inference(self):
        self.calls.append("thinking")
        return "token"

    async def finish_inference(self, token, *, successful):
        self.calls.append(("finish", token, successful))


class FailingLiveKitSession(FakeLiveKitSession):
    async def run_turn(self, text):
        self.run_calls.append(text)
        for handler in tuple(self.handlers):
            handler(type("Event", (), {"type": "text.delta", "text": "未完成"})())
        raise RuntimeError("provider stream failed")


class FakeTouchDispatcher:
    def __init__(self):
        self.events = []
        self.handlers = []

    async def start(self):
        pass

    async def close(self):
        pass

    def submit(self, event):
        self.events.append(event)
        return "enqueued"

    def subscribe(self, handler):
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    def emit(self, position, outcome, error_type=None):
        event = type(
            "TouchOutcome",
            (),
            {"position": position, "outcome": outcome, "error_type": error_type},
        )()
        for handler in tuple(self.handlers):
            handler(event)


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_delegates_execution_and_lifecycle_to_robot_service(self):
        robot = ActionOnlyRobotService()
        runtime = AgentRuntime(DeterministicChineseInterpreter(), robot)

        await runtime.start()
        await runtime.submit_text("req-service", "点头")
        await runtime.wait_until_idle()
        await runtime.close()

        self.assertTrue(robot.started)
        self.assertTrue(robot.closed)
        self.assertEqual(len(robot.actions), 1)
        self.assertEqual(robot.actions[0].tool, "motion.play")

    async def test_executes_strict_single_intent_and_records_chat(self):
        controller = FakeController()
        records = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            controller,
            telemetry=LatencyRecorder(sink=records.append),
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-1", "向左看")
        await runtime.wait_until_idle()

        self.assertEqual(
            controller.calls,
            [("motion.play", {"name": "look_left"})],
        )
        snapshot = runtime.snapshot()
        self.assertEqual([item["role"] for item in snapshot["messages"]], ["user", "agent"])
        self.assertEqual(snapshot["phase"], "idle")
        self.assertEqual(
            [
                record["stage"]
                for record in records
                if record["stage"]
                in {
                    "route_decided",
                    "tool_started",
                    "sdk_acknowledged",
                    "tool_completed",
                    "response_completed",
                }
            ],
            [
                "route_decided",
                "tool_started",
                "sdk_acknowledged",
                "tool_completed",
                "response_completed",
            ],
        )
        tool_records = [record for record in records if "tool_name" in record]
        self.assertTrue(all(record["tool_name"] == "play_motion" for record in tool_records))
        self.assertTrue(all("向左看" not in str(record) for record in records))

    async def test_unknown_text_does_not_call_controller(self):
        controller = FakeController()
        records = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            controller,
            telemetry=LatencyRecorder(sink=records.append),
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-unknown", "今天天气怎么样")
        await runtime.wait_until_idle()

        self.assertEqual(controller.calls, [])
        self.assertIn("还不理解", runtime.snapshot()["messages"][-1]["text"])
        route = next(record for record in records if record["stage"] == "route_decided")
        self.assertEqual(route["decision_path"], "fast_intent")
        self.assertEqual(route["outcome"], "unmatched")
        self.assertEqual(records[-1]["stage"], "response_completed")

    async def test_tool_failure_stops_later_actions_and_emits_error(self):
        plan = AgentPlan(
            (
                AgentAction("motion.play", {"name": "nod"}, "点头"),
                AgentAction("light.solid", {"color": "#FFFFFF"}, "白灯"),
            ),
            "执行完成。",
        )
        controller = FakeController()
        controller.fail_on = "motion.play"
        events = []
        runtime = AgentRuntime(StaticInterpreter(plan), controller)
        runtime.subscribe(events.append)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-fail", "执行")
        await runtime.wait_until_idle()

        self.assertEqual(controller.calls, [])
        self.assertTrue(any(event["type"] == "agent.error" for event in events))
        self.assertIn("执行失败", runtime.snapshot()["messages"][-1]["text"])

    async def test_queue_rejects_submission_instead_of_dropping_it(self):
        interpreter = BlockingInterpreter()
        runtime = AgentRuntime(interpreter, FakeController(), queue_capacity=1)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-1", "第一条")
        await interpreter.entered.wait()
        await runtime.submit_text("req-2", "第二条")
        with self.assertRaises(AgentQueueFullError):
            await runtime.submit_text("req-3", "第三条")
        interpreter.release.set()
        await runtime.wait_until_idle()

    async def test_history_is_bounded(self):
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            FakeController(),
            history_capacity=3,
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-1", "未知一")
        await runtime.wait_until_idle()
        await runtime.submit_text("req-2", "未知二")
        await runtime.wait_until_idle()

        messages = runtime.snapshot()["messages"]
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[-1]["role"], "agent")

    async def test_subscribes_only_to_pressed_touch_events(self):
        device = FakeDeviceClient()
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            FakeController(),
            device_client=device,
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        self.assertEqual(set(device.subscriptions), {"sensor.touch"})
        device.subscriptions["sensor.touch"](FakeEvent({"position": "left", "pressed": False}))
        device.subscriptions["sensor.touch"](FakeEvent({"position": "left", "pressed": True}))

        messages = runtime.snapshot()["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("左侧", messages[0]["text"])

    async def test_accepts_text_while_device_is_disconnected(self):
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            FakeController(),
            device_client=FakeDeviceClient(connected=False),
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-offline", "今天天气怎么样")
        await runtime.wait_until_idle()
        self.assertEqual(len(runtime.snapshot()["messages"]), 2)

    async def test_rechecks_connection_before_each_tool_call(self):
        device = FakeDeviceClient(connected=True)
        interpreter = BlockingActionInterpreter()
        controller = FakeController()
        runtime = AgentRuntime(interpreter, controller, device_client=device)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-disconnect", "点头")
        await interpreter.entered.wait()
        device.is_connected = False
        interpreter.release.set()
        await runtime.wait_until_idle()

        self.assertEqual(controller.calls, [])
        self.assertIn("disconnected", runtime.snapshot()["messages"][-1]["text"])

    async def test_cancels_async_subscriber_tasks_on_close(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def subscriber(_event):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        runtime = AgentRuntime(DeterministicChineseInterpreter(), FakeController())
        runtime.subscribe(subscriber)
        await runtime.start()
        await started.wait()
        await runtime.close()

        self.assertTrue(cancelled.is_set())

    async def test_publishes_state_when_device_connection_changes(self):
        device = FakeDeviceClient(connected=False)
        events = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            FakeController(),
            device_client=device,
            connection_poll_interval=0.01,
        )
        runtime.subscribe(events.append)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        device.is_connected = True
        await asyncio.sleep(0.03)

        states = [event["state"] for event in events if event["type"] == "agent.state"]
        self.assertTrue(any(state["device_connected"] is True for state in states))

    async def test_fast_path_skips_livekit_and_emits_correlated_lifecycle(self):
        robot = M3Robot()
        session = FakeLiveKitSession()
        events = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            robot,
            fast_router=FastIntentRouter(),
            livekit_session=session,
            info_service=FakeInfo(),
        )
        runtime.subscribe(events.append)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-fast", "点头")
        await runtime.wait_until_idle()

        self.assertEqual(session.run_calls, [])
        self.assertEqual(robot.calls, [("play_motion", {"name": "nod"})])
        self.assertEqual(len(session.sync_calls), 1)
        types = [event["type"] for event in events]
        self.assertIn("agent.response.started", types)
        self.assertIn("agent.tool.started", types)
        self.assertIn("agent.tool.completed", types)
        self.assertIn("agent.response.completed", types)
        correlated = [event for event in events if event["type"].startswith("agent.response")]
        self.assertTrue(all(event["request_id"] == "req-fast" for event in correlated))
        response_id = correlated[0]["response_id"]
        self.assertEqual(runtime.snapshot()["messages"][-1]["id"], response_id)
        assistant_messages = [
            event
            for event in events
            if event["type"] == "agent.message"
            and event["message"]["role"] == "agent"
        ]
        self.assertEqual(assistant_messages, [])

    async def test_complex_text_uses_one_persistent_livekit_turn(self):
        robot = M3Robot()
        session = FakeLiveKitSession("这是模型回答")
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            robot,
            fast_router=FastIntentRouter(),
            livekit_session=session,
            info_service=FakeInfo(),
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-1", "介绍一下你自己")
        await runtime.wait_until_idle()
        await runtime.submit_text("req-2", "再详细一点")
        await runtime.wait_until_idle()

        self.assertEqual(session.start_calls, 1)
        self.assertEqual(session.run_calls, ["介绍一下你自己", "再详细一点"])
        self.assertEqual(session.close_calls, 0)

    async def test_livekit_tool_uses_same_public_lifecycle_as_fast_tool(self):
        events = []
        records = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            M3Robot(),
            fast_router=FastIntentRouter(),
            livekit_session=FakeLiveKitSession(emit_tool=True),
            info_service=FakeInfo(),
            telemetry=LatencyRecorder(sink=records.append),
        )
        runtime.subscribe(events.append)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-tool", "帮我结合上下文回答当前时间")
        await runtime.wait_until_idle()

        lifecycle = [
            event["type"]
            for event in events
            if event["type"].startswith(("agent.response.", "agent.tool."))
        ]
        self.assertIn("agent.response.started", lifecycle)
        self.assertIn("agent.tool.started", lifecycle)
        self.assertIn("agent.tool.completed", lifecycle)
        self.assertIn("agent.response.delta", lifecycle)
        self.assertIn("agent.response.completed", lifecycle)
        tool_records = [
            record for record in records if record["stage"].startswith("tool_")
        ]
        self.assertEqual(
            [record["stage"] for record in tool_records],
            ["tool_started", "tool_completed"],
        )
        self.assertTrue(
            all(record["tool_name"] == "get_current_datetime" for record in tool_records)
        )

    async def test_local_datetime_fast_path_does_not_call_livekit(self):
        session = FakeLiveKitSession()
        info = FakeInfo()
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            M3Robot(),
            fast_router=FastIntentRouter(),
            livekit_session=session,
            info_service=info,
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-time", "现在几点")
        await runtime.wait_until_idle()

        self.assertEqual(session.run_calls, [])
        self.assertEqual(info.datetime_calls, 1)
        self.assertIn("09:30", runtime.snapshot()["messages"][-1]["text"])

    async def test_disconnected_complex_conversation_still_uses_livekit(self):
        session = FakeLiveKitSession()
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            M3Robot(),
            device_client=FakeDeviceClient(connected=False),
            fast_router=FastIntentRouter(),
            livekit_session=session,
            info_service=FakeInfo(),
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-talk", "给我讲个故事")
        await runtime.wait_until_idle()

        self.assertEqual(session.run_calls, ["给我讲个故事"])

    async def test_runtime_records_fast_path_latency_without_raw_text(self):
        records = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            M3Robot(),
            fast_router=FastIntentRouter(),
            livekit_session=FakeLiveKitSession(),
            info_service=FakeInfo(),
            telemetry=LatencyRecorder(sink=records.append),
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-latency", "点头")
        await runtime.wait_until_idle()

        stages = [record["stage"] for record in records]
        self.assertIn("route_decided", stages)
        self.assertIn("sdk_acknowledged", stages)
        self.assertEqual(records[-1]["decision_path"], "fast_intent")
        self.assertTrue(all("点头" not in str(record) for record in records))

    async def test_only_livekit_path_requests_internal_thinking_status(self):
        status = FakeStatusCoordinator()
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            M3Robot(),
            fast_router=FastIntentRouter(),
            livekit_session=FakeLiveKitSession(),
            info_service=FakeInfo(),
            status_coordinator=status,
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-fast", "点头")
        await runtime.wait_until_idle()
        self.assertEqual(status.calls, ["start"])

        await runtime.submit_text("req-llm", "介绍一下你自己")
        await runtime.wait_until_idle()
        self.assertEqual(
            status.calls,
            ["start", "thinking", ("finish", "token", True)],
        )

    async def test_failed_mutating_fast_command_is_not_retried(self):
        robot = M3Robot()
        robot.fail_motion = True
        events = []
        records = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            robot,
            fast_router=FastIntentRouter(),
            livekit_session=FakeLiveKitSession(),
            info_service=FakeInfo(),
            telemetry=LatencyRecorder(sink=records.append),
        )
        runtime.subscribe(events.append)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-fail-fast", "点头")
        await runtime.wait_until_idle()

        self.assertEqual(robot.calls, [("play_motion", {"name": "nod"})])
        self.assertTrue(any(event["type"] == "agent.tool.failed" for event in events))
        self.assertTrue(
            any(event["type"] == "agent.response.failed" for event in events)
        )
        failed = [record for record in records if record["stage"] == "tool_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["tool_name"], "play_motion")

    async def test_failed_stream_draft_is_not_promoted_to_committed_history(self):
        events = []
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            M3Robot(),
            fast_router=FastIntentRouter(),
            livekit_session=FailingLiveKitSession(),
            info_service=FakeInfo(),
        )
        runtime.subscribe(events.append)
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        await runtime.submit_text("req-stream-fail", "讲一个故事")
        await runtime.wait_until_idle()

        self.assertTrue(any(event["type"] == "agent.response.delta" for event in events))
        self.assertTrue(any(event["type"] == "agent.response.failed" for event in events))
        self.assertEqual(
            [message["role"] for message in runtime.snapshot()["messages"]],
            ["user"],
        )

    async def test_runtime_delegates_touch_to_dispatcher_without_llm(self):
        device = FakeDeviceClient()
        touch = FakeTouchDispatcher()
        session = FakeLiveKitSession()
        runtime = AgentRuntime(
            DeterministicChineseInterpreter(),
            M3Robot(),
            device_client=device,
            fast_router=FastIntentRouter(),
            livekit_session=session,
            info_service=FakeInfo(),
            touch_dispatcher=touch,
        )
        await runtime.start()
        self.addAsyncCleanup(runtime.close)

        event = FakeEvent({"position": "left", "pressed": True})
        device.subscriptions["sensor.touch"](event)
        touch.emit("left", "completed")

        self.assertEqual(touch.events, [event])
        self.assertEqual(session.run_calls, [])
        self.assertEqual(runtime.snapshot()["messages"][-1]["role"], "system")
        self.assertIn("左侧触摸", runtime.snapshot()["messages"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
