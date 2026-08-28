import asyncio
import unittest
from types import MappingProxyType

from lefly_agent.config import TouchBehavior
from lefly_agent.touch import TouchBehaviorDispatcher


class FakeEvent:
    def __init__(self, position, pressed=True):
        self.payload = MappingProxyType({"position": position, "pressed": pressed})


class RecordingRobot:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def play_motion(self, name):
        self.calls.append(("motion", name))
        self.entered.set()
        await self.release.wait()

    async def set_head_light(self, color):
        self.calls.append(("light", color))


class TouchBehaviorDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_additional_observer_can_subscribe_for_runtime_history(self):
        robot = RecordingRobot()
        dispatcher = TouchBehaviorDispatcher(
            robot,
            {
                "left": TouchBehavior("look_left"),
                "middle": TouchBehavior(),
                "right": TouchBehavior(),
            },
        )
        observed = []
        unsubscribe = dispatcher.subscribe(observed.append)
        await dispatcher.start()
        self.addAsyncCleanup(dispatcher.close)

        dispatcher.submit(FakeEvent("left"))
        await dispatcher.wait_until_idle()
        unsubscribe()

        self.assertEqual(observed[-1].outcome, "completed")

    async def test_pressed_mapping_runs_motion_then_light_without_chat_or_llm(self):
        robot = RecordingRobot()
        events = []
        dispatcher = TouchBehaviorDispatcher(
            robot,
            {
                "left": TouchBehavior("look_left", "#F1A22E"),
                "middle": TouchBehavior(),
                "right": TouchBehavior(),
            },
            observer=events.append,
        )
        await dispatcher.start()
        self.addAsyncCleanup(dispatcher.close)

        self.assertEqual(dispatcher.submit(FakeEvent("left", pressed=False)), "ignored")
        self.assertEqual(dispatcher.submit(FakeEvent("left")), "enqueued")
        await dispatcher.wait_until_idle()

        self.assertEqual(
            robot.calls,
            [("motion", "look_left"), ("light", "#F1A22E")],
        )
        self.assertEqual(events[-1].outcome, "completed")

    async def test_queue_drops_later_events_when_capacity_is_exhausted(self):
        robot = RecordingRobot()
        robot.release.clear()
        events = []
        dispatcher = TouchBehaviorDispatcher(
            robot,
            {
                "left": TouchBehavior("look_left"),
                "middle": TouchBehavior("nod"),
                "right": TouchBehavior("look_right"),
            },
            queue_capacity=1,
            observer=events.append,
        )
        await dispatcher.start()
        self.addAsyncCleanup(dispatcher.close)

        dispatcher.submit(FakeEvent("left"))
        await robot.entered.wait()
        self.assertEqual(dispatcher.submit(FakeEvent("middle")), "enqueued")
        self.assertEqual(dispatcher.submit(FakeEvent("right")), "dropped")
        robot.release.set()
        await dispatcher.wait_until_idle()

        self.assertTrue(any(event.outcome == "dropped_queue_full" for event in events))

    def test_rejects_incomplete_or_unknown_mapping_at_startup(self):
        with self.assertRaisesRegex(ValueError, "positions"):
            TouchBehaviorDispatcher(RecordingRobot(), {"left": TouchBehavior()})


if __name__ == "__main__":
    unittest.main()
