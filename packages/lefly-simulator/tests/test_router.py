import asyncio
import copy
import unittest
from datetime import datetime, timezone

from lefly_protocol import DeviceCommand, DeviceEvent
from lefly_sdk import DeviceDisconnectedError
from lefly_simulator.models import SimulatorState
from lefly_simulator.router import ControlLease, RouterError, TargetRouter
from lefly_simulator.target import RemoteTarget


def snapshot(device_id="remote", revision=1):
    state = SimulatorState.default(device_id)
    result = state.observe()
    for _ in range(revision):
        result = state.publish_snapshot()
    return result


def command(message_type="light.solid", suffix=1, device_id="simulator"):
    payloads = {
        "light.solid": {"target": "head_matrix", "color": "#FFFF00"},
        "status.set": {"mode": "speaking"},
        "device.get_state": {},
    }
    return DeviceCommand(
        message_id=f"10000000-0000-4000-8000-{suffix:012d}",
        message_type=message_type,
        timestamp="2026-08-17T08:00:00.000Z",
        device_id=device_id,
        payload=payloads.get(message_type, {}),
    )


def event(message_type, payload, suffix=1, device_id="remote", correlation_id=None):
    return DeviceEvent(
        message_id=f"20000000-0000-4000-8000-{suffix:012d}",
        message_type=message_type,
        timestamp="2026-08-17T08:00:00.010Z",
        device_id=device_id,
        correlation_id=correlation_id,
        payload=payload,
    )


def accepted(item, suffix=90):
    return event(
        "command.accepted",
        {"command_type": item.message_type, "disposition": "applied"},
        suffix,
        item.device_id,
        item.message_id,
    )


class FakeDeviceClient:
    def __init__(self, state):
        self.is_connected = False
        self.state = state
        self.handlers = []
        self.requests = []
        self.close_calls = 0
        self.event_suffix = 10
        self.fail_requests = False

    def subscribe(self, message_type, handler):
        self.handlers.append(handler)

        def unsubscribe():
            if handler in self.handlers:
                self.handlers.remove(handler)

        return unsubscribe

    async def start(self):
        pass

    async def wait_until_connected(self, timeout):
        del timeout
        self.is_connected = True

    async def request(self, item):
        if self.fail_requests:
            raise DeviceDisconnectedError("device is not connected")
        self.requests.append(item)
        if item.message_type == "device.get_state":
            self.event_suffix += 1
            self.emit(
                event(
                    "device.state_changed",
                    self.state,
                    self.event_suffix,
                    item.device_id,
                    item.message_id,
                )
            )
        return accepted(item, self.event_suffix + 100)

    async def close(self):
        self.close_calls += 1
        self.is_connected = False

    def emit(self, item):
        for handler in tuple(self.handlers):
            handler(item)


class RemoteTargetProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_requests_and_caches_complete_canonical_state(self):
        client = FakeDeviceClient(snapshot("remote", 1))
        target = RemoteTarget(
            "remote",
            client=client,
            id_factory=lambda: "10000000-0000-4000-8000-000000000099",
            utc_now=lambda: datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc),
        )

        await target.start()

        request = client.requests[0]
        self.assertEqual(request.timestamp, "2026-08-17T08:00:00.000Z")
        self.assertEqual(dict(request.payload), {})
        self.assertEqual(target.snapshot()["revision"], 1)
        self.assertEqual(target.snapshot()["connection"], "ready")
        self.assertNotIn("stale", target.snapshot())
        await target.close()

    async def test_contiguous_state_replaces_instead_of_merging(self):
        client = FakeDeviceClient(snapshot("remote", 1))
        target = RemoteTarget("remote", client=client)
        await target.start()
        replacement = snapshot("remote", 2)
        replacement["status"] = {"mode": "thinking"}

        client.emit(event("device.state_changed", replacement, 20, "remote"))

        self.assertEqual(target.snapshot(), replacement)
        await target.close()

    async def test_revision_gap_triggers_resync_before_recovery(self):
        client = FakeDeviceClient(snapshot("remote", 1))
        target = RemoteTarget(
            "remote",
            client=client,
            resync_base_delay=0.001,
            resync_max_delay=0.002,
            snapshot_timeout=0.1,
        )
        await target.start()
        client.state = snapshot("remote", 3)

        client.emit(event("device.state_changed", client.state, 30, "remote"))
        for _ in range(100):
            if not target.snapshot_stale:
                break
            await asyncio.sleep(0)

        self.assertFalse(target.snapshot_stale)
        self.assertEqual(target.snapshot()["revision"], 3)
        self.assertGreaterEqual(len(client.requests), 2)
        await target.close()

    async def test_unknown_event_is_forwarded_without_mutating_state(self):
        client = FakeDeviceClient(snapshot("remote", 1))
        target = RemoteTarget("remote", client=client)
        received = []
        target.subscribe(received.append)
        await target.start()
        before = target.snapshot()
        unknown = event("future.telemetry", {"value": 1}, 40, "remote")

        client.emit(unknown)
        await asyncio.sleep(0)

        self.assertIn(unknown, received)
        self.assertEqual(target.snapshot(), before)
        await target.close()

    async def test_lost_transport_maps_snapshot_to_offline(self):
        client = FakeDeviceClient(snapshot("remote", 1))
        target = RemoteTarget("remote", client=client)
        await target.start()

        client.is_connected = False

        self.assertEqual(target.snapshot()["connection"], "offline")
        await target.close()

    async def test_transport_recovery_accepts_device_revision_reset(self):
        client = FakeDeviceClient(snapshot("remote", 5))
        target = RemoteTarget("remote", client=client)
        await target.start()

        client.is_connected = False
        client.fail_requests = True
        with self.assertRaises(DeviceDisconnectedError):
            await target.command(command(device_id="remote"))

        client.fail_requests = False
        client.is_connected = True
        restarted = snapshot("remote", 0)
        client.emit(event("device.state_changed", restarted, 50, "remote"))

        self.assertFalse(target.snapshot_stale)
        self.assertEqual(target.snapshot()["revision"], 0)
        await target.close()


class ControlLeaseTest(unittest.TestCase):
    def test_acquire_renew_release_and_expiry(self):
        now = [10.0]
        lease = ControlLease(
            ttl=5,
            monotonic=lambda: now[0],
            token_factory=lambda: "lease-token",
        )
        grant = lease.acquire("console-a")
        self.assertNotIsInstance(grant, RouterError)
        self.assertIsInstance(lease.acquire("console-b"), RouterError)
        now[0] = 12
        renewed = lease.renew(grant)
        self.assertEqual(renewed.expires_at, 17)
        self.assertTrue(lease.release(renewed))
        expired = lease.acquire("console-b")
        now[0] = 18
        self.assertIsNotNone(lease.validate(expired))


class FakeTarget:
    kind = "simulator"

    def __init__(self, target_id="simulator", kind="simulator"):
        self.target_id = target_id
        self.device_id = target_id
        self.kind = kind
        self.is_connected = False
        self.status = "stopped"
        self.snapshot_stale = False
        self._snapshot = snapshot(target_id, 1)
        self.handlers = []
        self.commands = []
        self.injected = []

    async def start(self):
        self.is_connected = True
        self.status = "ready"

    async def close(self):
        self.is_connected = False
        self.status = "closed"

    async def command(self, item):
        self.commands.append(item)
        return accepted(item)

    async def inject_sensor(self, sensor_type, payload):
        self.injected.append((sensor_type, payload))
        return event(
            "sensor.touch",
            {"position": payload["position"], "pressed": True},
            70,
            self.device_id,
        )

    def subscribe(self, handler):
        self.handlers.append(handler)
        return lambda: self.handlers.remove(handler) if handler in self.handlers else None

    def snapshot(self):
        return copy.deepcopy(self._snapshot)

    async def emit(self, item):
        for handler in tuple(self.handlers):
            handler(item)
        await asyncio.sleep(0)


class TargetRouterProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.target = FakeTarget()
        self.router = TargetRouter([self.target], active_target_id="simulator")
        await self.router.start()
        self.lease = self.router.acquire_control("console")

    async def asyncTearDown(self):
        await self.router.close()

    async def test_routes_control_capability_and_blocks_system_scope(self):
        allowed = await self.router.route(
            self.lease, self.router.target_epoch, command("light.solid")
        )
        blocked = await self.router.route(
            self.lease, self.router.target_epoch, command("status.set", 2)
        )

        self.assertEqual(allowed.message_type, "command.accepted")
        self.assertEqual(blocked.code, "system_command_forbidden")

    async def test_simulator_sensor_injection_is_outside_device_capabilities(self):
        self.assertNotIn(
            "sensor.inject",
            self.router.current_state["capabilities"]["commands"],
        )

        result = await self.router.inject_sensor(
            self.lease, "touch", {"position": "left"}
        )

        self.assertEqual(result.message_type, "sensor.touch")

    async def test_state_event_replaces_complete_snapshot(self):
        replacement = snapshot("simulator", 2)
        replacement["status"] = {"mode": "listening"}
        await self.target.emit(
            event("device.state_changed", replacement, 80, "simulator")
        )

        self.assertEqual(self.router.current_state, replacement)

    async def test_snapshot_projects_live_target_connection_without_replacing_state(self):
        self.target._snapshot["connection"] = "offline"

        projected = self.router.snapshot()

        self.assertEqual(projected["state"]["connection"], "offline")
        self.assertEqual(projected["state"]["revision"], 1)
        self.assertEqual(self.router.current_state["connection"], "ready")

    async def test_transport_recovery_accepts_a_valid_revision_reset(self):
        self.target.snapshot_stale = True
        blocked = await self.router.route(
            self.lease, self.router.target_epoch, command("light.solid")
        )
        self.assertEqual(blocked.code, "state_stale")

        restarted = snapshot("simulator", 0)
        self.target._snapshot = restarted
        self.target.snapshot_stale = False
        await self.target.emit(
            event("device.state_changed", restarted, 83, "simulator")
        )

        self.assertFalse(self.router.state_stale)
        self.assertEqual(self.router.current_state["revision"], 0)

    async def test_gap_marks_stale_until_authoritative_snapshot_arrives(self):
        gap = snapshot("simulator", 3)
        await self.target.emit(event("device.state_changed", gap, 81, "simulator"))

        self.assertTrue(self.router.state_stale)
        blocked = await self.router.route(
            self.lease, self.router.target_epoch, command("light.solid", 3)
        )
        self.assertEqual(blocked.code, "state_stale")

        self.target._snapshot = gap
        await self.target.emit(event("device.state_changed", gap, 82, "simulator"))
        self.assertFalse(self.router.state_stale)
        self.assertEqual(self.router.current_state["revision"], 3)

    async def test_target_mismatch_is_rejected(self):
        result = await self.router.route(
            self.lease,
            self.router.target_epoch,
            command("light.solid", device_id="other"),
        )
        self.assertEqual(result.code, "target_mismatch")


if __name__ == "__main__":
    unittest.main()
