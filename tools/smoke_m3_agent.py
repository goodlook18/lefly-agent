#!/usr/bin/env python3
"""Verify the installed Offline Demo Agent-to-Simulator control chain."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATHS = (
    ROOT / "packages" / "lefly-protocol",
    ROOT / "packages" / "lefly-sdk-python",
    ROOT / "packages" / "lefly-simulator",
    ROOT / "packages" / "lefly-agent",
)


def phase(name: str) -> None:
    print("[m3-smoke] %s" % name, flush=True)


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_health(
    url: str,
    process: subprocess.Popen,
    service: str,
    timeout: float = 15,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError("installed %s exited during startup:\n%s" % (service, output))
        try:
            with urlopen(url, timeout=0.3) as response:
                payload = json.load(response)
                if response.status == 200 and payload.get("service") == service:
                    return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.05)
    raise TimeoutError("installed %s did not become healthy at %s" % (service, url))


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    if sys.version_info[:2] != (3, 12):
        print("M3 installed-package smoke requires Python 3.12", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="lefly-m3-install-") as temporary:
        temp = Path(temporary)
        install_root = temp / "site-packages"
        phase("install four workspace packages to an isolated non-editable target")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--target",
                str(install_root),
                *(str(path) for path in PACKAGE_PATHS),
            ],
            check=True,
            cwd=temp,
        )

        simulator_port = allocate_port()
        agent_port = allocate_port()
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(install_root),
                "PYTHONNOUSERSITE": "1",
            }
        )
        environment.pop("LEFLY_LLM_API_KEY", None)

        phase("start installed Simulator and Offline Agent outside repository")
        simulator = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "lefly_simulator",
                "--host",
                "127.0.0.1",
                "--port",
                str(simulator_port),
            ],
            cwd=temp,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        agent = None
        try:
            wait_for_health(
                "http://127.0.0.1:%d/health" % simulator_port,
                simulator,
                "lefly-simulator",
            )
            agent = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "lefly_agent",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(agent_port),
                    "--device-url",
                    "ws://127.0.0.1:%d/ws/device/simulator" % simulator_port,
                ],
                cwd=temp,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            wait_for_health(
                "http://127.0.0.1:%d/health" % agent_port,
                agent,
                "lefly-agent",
            )

            probe = temp / "agent_smoke.py"
            probe.write_text(
                textwrap.dedent(
                    """
                    import asyncio
                    from datetime import datetime, timezone
                    import os
                    from pathlib import Path

                    import aiohttp
                    import lefly_agent
                    import lefly_protocol
                    import lefly_sdk
                    import lefly_simulator
                    from lefly_sdk import DeviceClient


                    async def main():
                        expected = Path(os.environ["EXPECTED_INSTALL_ROOT"]).resolve()
                        for module in (lefly_agent, lefly_protocol, lefly_sdk, lefly_simulator):
                            path = Path(module.__file__).resolve()
                            if expected not in path.parents:
                                raise AssertionError("module was not loaded from isolated install: %s" % path)

                        device_events = []
                        device = DeviceClient(os.environ["DEVICE_URL"], request_timeout=2.0)
                        device.subscribe("*", device_events.append)
                        await device.start()
                        await device.wait_until_connected(timeout=5.0)
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.ws_connect(os.environ["AGENT_URL"]) as websocket:
                                    hello = await websocket.receive_json(timeout=5.0)
                                    if hello.get("type") != "agent.hello":
                                        raise AssertionError("expected agent.hello")
                                    connected = hello.get("state", {}).get("device_connected")
                                    deadline = asyncio.get_running_loop().time() + 5.0
                                    while not connected:
                                        remaining = deadline - asyncio.get_running_loop().time()
                                        if remaining <= 0:
                                            raise AssertionError("Agent did not report a connected device")
                                        state_event = await websocket.receive_json(timeout=remaining)
                                        if state_event.get("type") == "agent.state":
                                            connected = state_event.get("state", {}).get("device_connected")

                                    request_id = "m3-offline-smoke"
                                    await websocket.send_json(
                                        {
                                            "version": "1",
                                            "id": request_id,
                                            "type": "agent.submit_text",
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                            "text": "变蓝灯",
                                        }
                                    )
                                    accepted = False
                                    completed = False
                                    deadline = asyncio.get_running_loop().time() + 8.0
                                    while asyncio.get_running_loop().time() < deadline:
                                        event = await websocket.receive_json(timeout=8.0)
                                        if event.get("request_id") == request_id and event.get("type") == "agent.accepted":
                                            accepted = True
                                        if event.get("type") == "agent.message" and event.get("message", {}).get("role") == "agent":
                                            completed = True
                                            break
                                    if not accepted or not completed:
                                        raise AssertionError("Agent Control did not accept and complete the command")

                            deadline = asyncio.get_running_loop().time() + 5.0
                            while asyncio.get_running_loop().time() < deadline:
                                accepted_events = [
                                    event for event in device_events
                                    if event.message_type == "command.accepted" and event.correlation_id
                                ]
                                blue_states = [
                                    event for event in device_events
                                    if event.message_type == "device.state_changed"
                                    and event.payload["light"]["pixels"][0] == "#20A8B5"
                                ]
                                if accepted_events and blue_states:
                                    correlations = {event.correlation_id for event in accepted_events}
                                    if any(event.correlation_id in correlations for event in blue_states):
                                        return
                                await asyncio.sleep(0.05)
                            raise AssertionError("no correlated blue device state was observed")
                        finally:
                            await device.close()


                    asyncio.run(main())
                    """
                ),
                encoding="utf-8",
            )
            probe_environment = environment.copy()
            probe_environment.update(
                {
                    "EXPECTED_INSTALL_ROOT": str(install_root),
                    "DEVICE_URL": "ws://127.0.0.1:%d/ws/device/simulator" % simulator_port,
                    "AGENT_URL": "ws://127.0.0.1:%d/ws/agent" % agent_port,
                }
            )
            phase("send a fast command through Agent Control and observe correlated state")
            subprocess.run(
                [sys.executable, str(probe)],
                check=True,
                cwd=temp,
                env=probe_environment,
            )
        finally:
            phase("stop installed processes and remove isolated target")
            if agent is not None:
                stop_process(agent)
            stop_process(simulator)

    print("M3 INSTALLED AGENT SMOKE PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
