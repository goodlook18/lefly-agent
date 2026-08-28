import asyncio
import unittest

from lefly_protocol import validate_state
from lefly_simulator.clock import ManualClock, RealClock
from lefly_simulator.models import (
    DEFAULT_JOINTS,
    SimulatorState,
    StateValidationError,
)


class SimulatorStateTest(unittest.TestCase):
    def test_default_observation_is_a_complete_canonical_state(self):
        state = SimulatorState.default("lefly-sim-01")

        snapshot = state.observe()
        validate_state(snapshot, "lefly-sim-01")

        self.assertEqual(snapshot["device_id"], "lefly-sim-01")
        self.assertEqual(snapshot["revision"], 0)
        self.assertEqual(snapshot["connection"], "ready")
        self.assertEqual(snapshot["motion"]["state"], "idle")
        self.assertIsNone(snapshot["motion"]["action"])
        self.assertEqual(
            list(snapshot["motion"]["joints"]),
            [profile.name for profile in DEFAULT_JOINTS],
        )
        self.assertEqual(
            {
                name: joint["pos"]
                for name, joint in snapshot["motion"]["joints"].items()
            },
            {
                "base_yaw": 0,
                "base_pitch": -45,
                "elbow_pitch": 105,
                "wrist_roll": 0,
                "wrist_pitch": 45,
            },
        )
        self.assertLessEqual(snapshot["motion"]["joints"]["base_yaw"]["min"], -60)
        self.assertGreaterEqual(snapshot["motion"]["joints"]["base_yaw"]["max"], 60)
        self.assertLessEqual(snapshot["motion"]["joints"]["wrist_roll"]["min"], -180)
        self.assertGreaterEqual(snapshot["motion"]["joints"]["wrist_roll"]["max"], 180)
        self.assertEqual(snapshot["status"], {"mode": "active"})
        self.assertEqual(snapshot["status_strip"], {"effect": "solid", "color": "#FFF0D0"})
        self.assertEqual(snapshot["light"]["matrix"], {"width": 8, "height": 8})
        self.assertEqual(len(snapshot["light"]["pixels"]), 64)
        self.assertEqual(set(snapshot["light"]["pixels"]), {"#FFF0D0"})
        self.assertEqual(snapshot["command_queue"], {"size": 0, "capacity": 8})

    def test_capabilities_are_structured_and_match_installed_features(self):
        capabilities = SimulatorState.default("lefly-sim-01").observe()["capabilities"]

        self.assertEqual(
            set(capabilities["commands"]),
            {
                "motion.play",
                "motion.relative_move",
                "motion.absolute_move",
                "light.solid",
                "light.paint",
                "light.brightness",
                "status.set",
                "device.rest",
                "device.get_state",
            },
        )
        self.assertEqual(capabilities["commands"]["status.set"], {"scope": "system"})
        self.assertNotIn("sensor.inject", capabilities["commands"])
        self.assertEqual(
            capabilities["motion"]["joints"],
            [profile.name for profile in DEFAULT_JOINTS],
        )
        self.assertTrue(capabilities["motion"]["presets"])
        self.assertEqual(
            capabilities["lights"],
            [{"target": "head_matrix", "kind": "rgb_matrix", "width": 8, "height": 8}],
        )

    def test_default_rejects_empty_device_id(self):
        for device_id in ("", "   ", None):
            with self.subTest(device_id=device_id):
                with self.assertRaises(StateValidationError):
                    SimulatorState.default(device_id)  # type: ignore[arg-type]

    def test_internal_mutation_does_not_publish_a_revision(self):
        state = SimulatorState.default("lefly-sim-01")

        state.set_joint("base_yaw", 60)
        self.assertEqual(state.joints["base_yaw"].position, 60)
        self.assertEqual(state.revision, 0)

        state.set_joint("base_yaw", 60.0)
        self.assertEqual(state.revision, 0)

        with self.assertRaises(StateValidationError):
            state.set_joint("base_yaw", 120)
        self.assertEqual(state.joints["base_yaw"].position, 60)
        self.assertEqual(state.revision, 0)

    def test_publication_increments_once_and_observation_does_not(self):
        state = SimulatorState.default("lefly-sim-01")

        self.assertEqual(state.observe()["revision"], 0)
        self.assertEqual(state.observe()["revision"], 0)
        self.assertEqual(state.publish_snapshot()["revision"], 1)
        self.assertEqual(state.observe()["revision"], 1)
        self.assertEqual(state.publish_snapshot()["revision"], 2)

    def test_joint_update_rejects_unknown_and_non_numeric_values(self):
        state = SimulatorState.default("lefly-sim-01")

        invalid_updates = (
            ("missing", 0),
            ("base_yaw", True),
            ("base_yaw", "10"),
            ("base_yaw", float("nan")),
            ("base_yaw", float("inf")),
        )
        for name, position in invalid_updates:
            with self.subTest(name=name, position=position):
                with self.assertRaises(StateValidationError):
                    state.set_joint(name, position)  # type: ignore[arg-type]

        self.assertEqual(state.revision, 0)

    def test_joint_batch_is_atomic_and_does_not_publish(self):
        state = SimulatorState.default("lefly-sim-01")

        state.set_joints({"base_yaw": 10, "wrist_roll": 20})

        self.assertEqual(state.joints["base_yaw"].position, 10)
        self.assertEqual(state.joints["wrist_roll"].position, 20)
        self.assertEqual(state.revision, 0)

        snapshot = state.observe()
        with self.assertRaises(StateValidationError):
            state.set_joints({"base_yaw": 20, "wrist_roll": 999})
        self.assertEqual(state.observe(), snapshot)

    def test_motion_and_queue_batch_is_atomic(self):
        state = SimulatorState.default("lefly-sim-01")

        state.set_motion_and_queue("moving", "nod", 2)

        self.assertEqual(state.motion_state, "moving")
        self.assertEqual(state.motion_action, "nod")
        self.assertEqual(state.command_queue_size, 2)
        self.assertEqual(state.revision, 0)

    def test_status_mode_owns_its_fixed_strip_rendering(self):
        state = SimulatorState.default("lefly-sim-01")

        state.set_status("speaking")

        self.assertEqual(state.observe()["status"], {"mode": "speaking"})
        self.assertEqual(
            state.observe()["status_strip"],
            {"effect": "level_sweep", "color": "#2F9D68"},
        )
        self.assertEqual(state.revision, 0)

    def test_lifecycle_status_uses_canonical_rest_and_warm_white_active(self):
        state = SimulatorState.default("lefly-sim-01")

        self.assertEqual(
            state.observe()["status_strip"],
            {"effect": "solid", "color": "#FFF0D0"},
        )
        state.set_status("starting")
        self.assertEqual(
            state.observe()["status_strip"],
            {"effect": "fade", "color": "#FFF0D0"},
        )
        state.set_status("resting")
        self.assertEqual(
            state.observe()["status_strip"],
            {"effect": "breath", "color": "#FFD33D"},
        )
        with self.assertRaises(StateValidationError):
            state.set_status("sleeping")

    def test_head_light_normalizes_color_and_tracks_actual_changes(self):
        state = SimulatorState.default("lefly-sim-01")

        state.set_head_light("#ffd33d", 0.4)
        light = state.observe()["light"]
        self.assertEqual(light["brightness"], 0.4)
        self.assertEqual(set(light["pixels"]), {"#FFD33D"})
        self.assertEqual(state.revision, 0)

        state.set_head_light("#FFD33D", 0.4)
        self.assertEqual(state.revision, 0)

        state.set_head_light("#FFD33D", 1)
        self.assertEqual(state.revision, 0)

    def test_complete_pixel_frame_replaces_the_matrix_atomically(self):
        state = SimulatorState.default("lefly-sim-01")
        frame = ["#000000"] * 63 + ["#FFFFFF"]

        state.set_head_pixels(frame)

        self.assertEqual(state.observe()["light"]["pixels"], frame)
        before = state.observe()
        for invalid in (["#000000"], ["#000000"] * 63 + ["white"]):
            with self.assertRaises(StateValidationError):
                state.set_head_pixels(invalid)
        self.assertEqual(state.observe(), before)

    def test_head_light_rejects_invalid_values_without_partial_mutation(self):
        state = SimulatorState.default("lefly-sim-01")
        original = state.observe()
        invalid_values = (
            ("FFD33D", 0.5),
            ("#GGD33D", 0.5),
            ("#FFD33D", True),
            ("#FFD33D", -0.01),
            ("#FFD33D", 1.01),
            ("#FFD33D", float("nan")),
            ("#FFD33D", float("inf")),
            ("#FFD33D", float("-inf")),
        )

        for color, brightness in invalid_values:
            with self.subTest(color=color, brightness=brightness):
                with self.assertRaises(StateValidationError):
                    state.set_head_light(color, brightness)  # type: ignore[arg-type]

        self.assertEqual(state.observe(), original)

    def test_observation_is_detached(self):
        state = SimulatorState.default("lefly-sim-01")

        first = state.observe()
        first["capabilities"]["commands"].pop("motion.play")
        first["motion"]["joints"]["base_yaw"]["pos"] = 999
        first["status_strip"]["color"] = "#000000"
        first["light"]["pixels"][0] = "#000000"
        first["command_queue"]["size"] = 7

        second = state.observe()
        self.assertEqual(second["revision"], 0)
        self.assertIn("motion.play", second["capabilities"]["commands"])
        self.assertEqual(second["motion"]["joints"]["base_yaw"]["pos"], 0)
        self.assertEqual(second["status_strip"]["color"], "#FFF0D0")
        self.assertEqual(second["light"]["pixels"][0], "#FFF0D0")
        self.assertEqual(second["command_queue"]["size"], 0)

    def test_public_state_views_cannot_bypass_validation_or_revision(self):
        state = SimulatorState.default("lefly-sim-01")

        with self.assertRaises(AttributeError):
            state.revision = 99
        with self.assertRaises(TypeError):
            state.capabilities["commands"] = {}
        with self.assertRaises(TypeError):
            state.joints["other"] = state.joints["base_yaw"]
        with self.assertRaises(AttributeError):
            state.joints["base_yaw"].position = 99
        for attribute, value in (
            ("motion_state", "moving"),
            ("motion_action", "nod"),
            ("command_queue_size", 4),
            ("status_mode", "error"),
        ):
            with self.subTest(attribute=attribute):
                with self.assertRaises(AttributeError):
                    setattr(state, attribute, value)

        self.assertEqual(state.revision, 0)
        self.assertEqual(state.observe()["motion"]["joints"]["base_yaw"]["pos"], 0)

    def test_task3_state_mutators_reject_invalid_values_atomically(self):
        state = SimulatorState.default("lefly-sim-01")
        original = state.observe()

        invalid_calls = (
            lambda: state.set_motion("unknown", None),
            lambda: state.set_motion("moving", True),
            lambda: state.set_command_queue(True),
            lambda: state.set_command_queue(-1),
            lambda: state.set_command_queue(9),
            lambda: state.set_status("idle"),
            lambda: state.set_head_pixels(["#000000"] * 63),
        )
        for invalid_call in invalid_calls:
            with self.subTest(call=invalid_call):
                with self.assertRaises(StateValidationError):
                    invalid_call()

        self.assertEqual(state.observe(), original)


class ManualClockTest(unittest.IsolatedAsyncioTestCase):
    async def test_sleep_releases_only_after_deadline(self):
        clock = ManualClock(initial=10.0)
        sleeper = asyncio.create_task(clock.sleep(2.0))
        await asyncio.sleep(0)

        clock.advance(1.99)
        await asyncio.sleep(0)
        self.assertFalse(sleeper.done())
        self.assertEqual(clock.now(), 11.99)

        clock.advance(0.01)
        await sleeper
        self.assertEqual(clock.now(), 12.0)

    async def test_multiple_waiters_release_at_their_own_deadlines(self):
        clock = ManualClock()
        first = asyncio.create_task(clock.sleep(1))
        second = asyncio.create_task(clock.sleep(2))
        third = asyncio.create_task(clock.sleep(1))
        await asyncio.sleep(0)

        clock.advance(1)
        await asyncio.sleep(0)
        self.assertTrue(first.done())
        self.assertTrue(third.done())
        self.assertFalse(second.done())

        clock.advance(1)
        await second

    async def test_cancelled_waiter_does_not_affect_other_waiters(self):
        clock = ManualClock()
        cancelled = asyncio.create_task(clock.sleep(1))
        survivor = asyncio.create_task(clock.sleep(1))
        await asyncio.sleep(0)
        self.assertEqual(clock.pending_sleep_count, 2)

        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertEqual(clock.pending_sleep_count, 1)
        clock.advance(1)
        await survivor
        self.assertEqual(clock.pending_sleep_count, 0)

    async def test_zero_sleep_is_immediate_and_negative_advance_is_rejected(self):
        clock = ManualClock(initial=3)

        await clock.sleep(0)
        with self.assertRaises(ValueError):
            clock.advance(-0.1)
        self.assertEqual(clock.now(), 3)

    async def test_sleep_duration_validation_matches_real_clock(self):
        invalid_values = (
            (False, TypeError),
            ("1", TypeError),
            (float("nan"), ValueError),
            (float("inf"), ValueError),
            (-0.1, ValueError),
        )

        for clock in (ManualClock(), RealClock()):
            for value, error in invalid_values:
                with self.subTest(clock=type(clock).__name__, value=value):
                    with self.assertRaises(error):
                        await clock.sleep(value)

            await clock.sleep(0)


if __name__ == "__main__":
    unittest.main()
