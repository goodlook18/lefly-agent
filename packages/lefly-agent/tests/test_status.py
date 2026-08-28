import unittest
from types import MappingProxyType

from lefly_agent.status import StatusCoordinator


class FakeEvent:
    def __init__(self, mode):
        self.payload = MappingProxyType(
            {"connection": "ready", "status": {"mode": mode}}
        )


class FakeClient:
    def __init__(self):
        self.handler = None
        self.is_connected = True

    def subscribe(self, message_type, handler):
        self.handler = handler
        return lambda: setattr(self, "handler", None)


class FakeController:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def set_status(self, mode):
        self.calls.append(mode)
        if self.fail:
            raise RuntimeError("device disconnected")


class StatusCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_inference_uses_thinking_then_restores_active(self):
        client = FakeClient()
        controller = FakeController()
        coordinator = StatusCoordinator(controller, client)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        client.handler(FakeEvent("active"))

        token = await coordinator.begin_inference()
        await coordinator.finish_inference(token, successful=True)

        self.assertEqual(controller.calls, ["thinking", "active"])

    async def test_resting_robot_is_preserved(self):
        client = FakeClient()
        controller = FakeController()
        coordinator = StatusCoordinator(controller, client)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        client.handler(FakeEvent("resting"))

        token = await coordinator.begin_inference()
        await coordinator.finish_inference(token, successful=True)

        self.assertEqual(controller.calls, [])

    async def test_failed_inference_does_not_latch_robot_error(self):
        client = FakeClient()
        controller = FakeController()
        coordinator = StatusCoordinator(controller, client)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        client.handler(FakeEvent("active"))

        token = await coordinator.begin_inference()
        await coordinator.finish_inference(token, successful=False)

        self.assertEqual(controller.calls, ["thinking", "active"])

    async def test_status_transport_failure_does_not_block_inference(self):
        client = FakeClient()
        controller = FakeController()
        controller.fail = True
        coordinator = StatusCoordinator(controller, client)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        client.handler(FakeEvent("active"))

        token = await coordinator.begin_inference()
        await coordinator.finish_inference(token, successful=True)

        self.assertFalse(token.restore_active)
        self.assertEqual(controller.calls, ["thinking"])

    async def test_disconnected_device_skips_inference_status_immediately(self):
        client = FakeClient()
        client.is_connected = False
        controller = FakeController()
        coordinator = StatusCoordinator(controller, client)
        await coordinator.start()
        self.addAsyncCleanup(coordinator.close)
        client.handler(FakeEvent("active"))

        token = await coordinator.begin_inference()

        self.assertFalse(token.restore_active)
        self.assertEqual(controller.calls, [])


if __name__ == "__main__":
    unittest.main()
