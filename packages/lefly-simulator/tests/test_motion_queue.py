import asyncio
import unittest

from lefly_protocol import DeviceCommand
from lefly_simulator import ManualClock, SimulatorEngine
from lefly_simulator.queue import PRESET_POSES


def command(message_type, payload=None, suffix=1, device_id="lefly-sim-01"):
    return DeviceCommand(
        message_id=f"10000000-0000-4000-8000-{suffix:012d}",
        message_type=message_type,
        timestamp="2026-08-17T08:00:00.000Z",
        device_id=device_id,
        payload={} if payload is None else payload,
    )


class EventRecorder:
    def __init__(self):
        self.events = []
        self.changed = asyncio.Condition()

    async def __call__(self, event):
        async with self.changed:
            self.events.append(event)
            self.changed.notify_all()

    async def wait_for(self, message_type, correlation_id, count=1):
        def matches():
            return [
                event
                for event in self.events
                if event.message_type == message_type
                and event.correlation_id == correlation_id
            ]

        async with self.changed:
            await asyncio.wait_for(
                self.changed.wait_for(lambda: len(matches()) >= count), 1
            )
        return matches()


async def wait_for_sleep(clock, count=1):
    async def ready():
        while clock.pending_sleep_count != count:
            await asyncio.sleep(0)

    await asyncio.wait_for(ready(), 1)


async def finish_motion(clock, recorder, correlation_id, steps=4, step=0.025):
    for _ in range(steps):
        await wait_for_sleep(clock)
        clock.advance(step)
    await recorder.wait_for("motion.finished", correlation_id)


class FailOnceClock:
    def __init__(self):
        self.manual = ManualClock()
        self.failed = False

    def now(self):
        return self.manual.now()

    async def sleep(self, seconds):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated clock failure")
        await self.manual.sleep(seconds)

    @property
    def pending_sleep_count(self):
        return self.manual.pending_sleep_count

    def advance(self, seconds):
        self.manual.advance(seconds)


class MotionQueueLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = ManualClock()
        self.engine = SimulatorEngine(
            "lefly-sim-01", clock=self.clock, motion_steps=4
        )
        self.recorder = EventRecorder()
        self.engine.subscribe(self.recorder)

    async def asyncTearDown(self):
        await self.engine.close()

    def related(self, suffix):
        correlation_id = f"10000000-0000-4000-8000-{suffix:012d}"
        return [
            event
            for event in self.recorder.events
            if event.correlation_id == correlation_id
        ]

    async def test_completed_motion_has_one_canonical_lifecycle(self):
        accepted = await self.engine.execute(
            command(
                "motion.absolute_move",
                {"joints": {"base_yaw": 20}, "duration_ms": 100},
            )
        )
        correlation_id = command("device.get_state").message_id
        await self.recorder.wait_for("motion.started", correlation_id)
        await finish_motion(self.clock, self.recorder, correlation_id)

        self.assertEqual([event.message_type for event in accepted], ["command.accepted"])
        self.assertEqual(
            dict(accepted[0].payload),
            {"command_type": "motion.absolute_move", "disposition": "queued"},
        )
        related = self.related(1)
        semantic = [
            event.message_type
            for event in related
            if event.message_type != "device.state_changed"
        ]
        self.assertEqual(
            semantic,
            [
                "command.accepted",
                "motion.started",
                "motion.progress",
                "motion.progress",
                "motion.progress",
                "motion.progress",
                "motion.finished",
            ],
        )
        progress = [event for event in related if event.message_type == "motion.progress"]
        self.assertEqual([event.payload["progress"] for event in progress], [0.25, 0.5, 0.75, 1.0])
        self.assertTrue(all(len(event.payload["joints"]) == 5 for event in progress))
        finished = [event for event in related if event.message_type == "motion.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(
            {key: finished[0].payload[key] for key in ("status", "reason", "error")},
            {"status": "completed", "reason": None, "error": None},
        )
        revisions = [
            event.payload["revision"]
            for event in related
            if event.message_type == "device.state_changed"
        ]
        self.assertEqual(revisions, list(range(1, len(revisions) + 1)))

    async def test_queue_size_counts_waiting_jobs_only(self):
        engine = SimulatorEngine(
            "lefly-sim-01", clock=self.clock, queue_capacity=1, motion_steps=2
        )
        recorder = EventRecorder()
        engine.subscribe(recorder)
        try:
            first = await engine.execute(
                command(
                    "motion.absolute_move",
                    {"joints": {"base_yaw": 10}, "duration_ms": 100},
                    1,
                )
            )
            await recorder.wait_for("motion.started", first[0].correlation_id)
            self.assertEqual(engine.state.command_queue_size, 0)

            second = await engine.execute(
                command(
                    "motion.absolute_move",
                    {"joints": {"base_yaw": 20}, "duration_ms": 100},
                    2,
                )
            )
            third = await engine.execute(
                command(
                    "motion.absolute_move",
                    {"joints": {"base_yaw": 30}, "duration_ms": 100},
                    3,
                )
            )

            self.assertEqual(second[0].message_type, "command.accepted")
            self.assertEqual(engine.state.command_queue_size, 1)
            self.assertEqual(third[0].message_type, "device.error")
            self.assertEqual(third[0].payload["code"], "queue_full")
        finally:
            await engine.close()

    async def test_invalid_and_duplicate_commands_are_rejected_before_acceptance(self):
        invalid = await self.engine.execute(
            command(
                "motion.absolute_move",
                {"joints": {"missing_joint": 1}, "duration_ms": 100},
            )
        )
        valid_command = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": 10}, "duration_ms": 100},
            2,
        )
        first = await self.engine.execute(valid_command)
        duplicate = await self.engine.execute(valid_command)

        self.assertEqual([event.message_type for event in invalid], ["device.error"])
        self.assertEqual(first[0].message_type, "command.accepted")
        self.assertEqual(duplicate[0].message_type, "device.error")
        self.assertEqual(duplicate[0].payload["code"], "duplicate_command")
        self.assertEqual(set(duplicate[0].payload), {"code", "message", "recoverable", "details"})

    async def test_close_cancels_active_and_waiting_once_without_starting_waiter(self):
        first = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": 10}, "duration_ms": 100},
            1,
        )
        second = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": 20}, "duration_ms": 100},
            2,
        )
        await self.engine.execute(first)
        await self.recorder.wait_for("motion.started", first.message_id)
        await self.engine.execute(second)

        await self.engine.close()

        first_finished = await self.recorder.wait_for("motion.finished", first.message_id)
        second_finished = await self.recorder.wait_for("motion.finished", second.message_id)
        self.assertEqual(len(first_finished), 1)
        self.assertEqual(len(second_finished), 1)
        self.assertEqual(first_finished[0].payload["status"], "cancelled")
        self.assertEqual(second_finished[0].payload["status"], "cancelled")
        self.assertFalse(
            any(
                event.message_type == "motion.started"
                and event.correlation_id == second.message_id
                for event in self.recorder.events
            )
        )
        self.assertEqual(self.engine.state.command_queue_size, 0)

    async def test_completed_rest_enters_resting_status(self):
        rest = command("device.rest", {}, 1)
        await self.engine.execute(rest)
        await self.recorder.wait_for("motion.started", rest.message_id)
        await finish_motion(self.clock, self.recorder, rest.message_id, step=0.15)

        self.assertEqual(self.engine.state.status_mode, "resting")
        self.assertEqual(self.engine.state.motion_state, "idle")
        self.assertEqual(
            {name: joint.position for name, joint in self.engine.state.joints.items()},
            {"base_yaw": 0, "base_pitch": -45, "elbow_pitch": 105, "wrist_roll": 0, "wrist_pitch": 45},
        )

    async def test_rest_preempts_active_and_waiting_motion_in_order(self):
        active = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": 30}, "duration_ms": 4_000},
            1,
        )
        waiting = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": -30}, "duration_ms": 100},
            2,
        )
        rest = command("device.rest", {}, 3)

        await self.engine.execute(active)
        await self.recorder.wait_for("motion.started", active.message_id)
        await self.engine.execute(waiting)
        await self.engine.execute(rest)

        waiting_finished = await self.recorder.wait_for(
            "motion.finished", waiting.message_id
        )
        active_finished = await self.recorder.wait_for(
            "motion.finished", active.message_id
        )
        await self.recorder.wait_for("motion.started", rest.message_id)

        self.assertEqual(waiting_finished[0].payload["status"], "cancelled")
        self.assertEqual(waiting_finished[0].payload["reason"], "device_rest")
        self.assertEqual(active_finished[0].payload["status"], "cancelled")
        self.assertEqual(active_finished[0].payload["reason"], "device_rest")
        self.assertFalse(
            any(
                event.message_type == "motion.started"
                and event.correlation_id == waiting.message_id
                for event in self.recorder.events
            )
        )

        semantic = [
            (event.message_type, event.correlation_id)
            for event in self.recorder.events
            if event.message_type in {"command.accepted", "motion.started", "motion.finished"}
        ]
        rest_accepted = semantic.index(("command.accepted", rest.message_id))
        self.assertEqual(
            semantic[rest_accepted : rest_accepted + 4],
            [
                ("command.accepted", rest.message_id),
                ("motion.finished", waiting.message_id),
                ("motion.finished", active.message_id),
                ("motion.started", rest.message_id),
            ],
        )

        await finish_motion(self.clock, self.recorder, rest.message_id, step=0.15)
        self.assertEqual(self.engine.state.status_mode, "resting")

    async def test_rest_transition_rejects_new_motion_and_second_rest(self):
        active = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": 30}, "duration_ms": 4_000},
            1,
        )
        rest = command("device.rest", {}, 2)
        await self.engine.execute(active)
        await self.recorder.wait_for("motion.started", active.message_id)
        await self.engine.execute(rest)

        rejected_motion = await self.engine.execute(
            command(
                "motion.absolute_move",
                {"joints": {"base_yaw": -20}, "duration_ms": 100},
                3,
            )
        )
        rejected_rest = await self.engine.execute(command("device.rest", {}, 4))

        self.assertEqual(rejected_motion[0].message_type, "device.error")
        self.assertEqual(rejected_motion[0].payload["code"], "device_resting")
        self.assertEqual(rejected_rest[0].message_type, "device.error")
        self.assertEqual(rejected_rest[0].payload["code"], "device_resting")

        await self.recorder.wait_for("motion.finished", active.message_id)
        await self.recorder.wait_for("motion.started", rest.message_id)
        await finish_motion(self.clock, self.recorder, rest.message_id, step=0.15)

        accepted_after_rest = await self.engine.execute(
            command(
                "motion.absolute_move",
                {"joints": {"base_yaw": 10}, "duration_ms": 100},
                5,
            )
        )
        self.assertEqual(accepted_after_rest[0].message_type, "command.accepted")

    async def test_rest_from_latched_error_preserves_error_status(self):
        self.engine.state.set_status("error")
        rest = command("device.rest", {}, 1)

        await self.engine.execute(rest)
        await self.recorder.wait_for("motion.started", rest.message_id)
        await finish_motion(self.clock, self.recorder, rest.message_id, step=0.15)

        self.assertEqual(self.engine.state.status_mode, "error")

    async def test_motion_from_resting_transitions_through_starting_to_active(self):
        self.engine.state.set_status("resting")
        wake = command("motion.play", {"name": "wake_up"}, 1)

        await self.engine.execute(wake)
        await self.recorder.wait_for("motion.started", wake.message_id)

        self.assertEqual(self.engine.state.status_mode, "starting")
        await finish_motion(self.clock, self.recorder, wake.message_id, step=0.15)
        self.assertEqual(self.engine.state.status_mode, "active")

    async def test_failed_motion_enters_error_status(self):
        clock = FailOnceClock()
        engine = SimulatorEngine("lefly-sim-01", clock=clock, motion_steps=2)
        recorder = EventRecorder()
        engine.subscribe(recorder)
        movement = command(
            "motion.absolute_move",
            {"joints": {"base_yaw": 10}, "duration_ms": 100},
            1,
        )
        try:
            with self.assertLogs("lefly_simulator.queue", level="ERROR"):
                await engine.execute(movement)
                await recorder.wait_for("motion.finished", movement.message_id)

            self.assertEqual(engine.state.status_mode, "error")
        finally:
            await engine.close()

    async def test_failed_rest_does_not_claim_resting(self):
        clock = FailOnceClock()
        engine = SimulatorEngine("lefly-sim-01", clock=clock, motion_steps=2)
        recorder = EventRecorder()
        engine.subscribe(recorder)
        rest = command("device.rest", {}, 1)
        try:
            with self.assertLogs("lefly_simulator.queue", level="ERROR"):
                await engine.execute(rest)
                finished = await recorder.wait_for("motion.finished", rest.message_id)

            self.assertEqual(finished[0].payload["status"], "failed")
            self.assertIsNotNone(finished[0].payload["error"])
            self.assertNotEqual(engine.state.status_mode, "resting")
        finally:
            await engine.close()

    def test_installed_presets_match_capabilities_and_joint_ranges(self):
        state = self.engine.state.observe()
        advertised = {
            item["name"] for item in state["capabilities"]["motion"]["presets"]
        }

        self.assertTrue({"turn_left", "center", "turn_right"}.isdisjoint(advertised))
        self.assertEqual(advertised, set(PRESET_POSES))
        for pose in PRESET_POSES.values():
            for joint, value in pose.items():
                profile = self.engine.state.joints[joint].profile
                self.assertLessEqual(profile.minimum, value)
                self.assertLessEqual(value, profile.maximum)


if __name__ == "__main__":
    unittest.main()
