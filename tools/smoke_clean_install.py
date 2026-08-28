#!/usr/bin/env python3
"""Verify a non-editable Python 3.12 install outside the repository."""

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
)


def phase(name: str) -> None:
    print("[clean-install] %s" % name, flush=True)


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for_health(url: str, process: subprocess.Popen, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError("installed simulator exited during startup:\n%s" % output)
        try:
            with urlopen(url, timeout=0.3) as response:
                payload = json.load(response)
                if response.status == 200 and payload.get("service") == "lefly-simulator":
                    return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(0.05)
    raise TimeoutError("installed simulator did not become healthy at %s" % url)


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
        print("clean-install smoke requires Python 3.12", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="lefly-clean-install-") as temporary:
        temp = Path(temporary)
        install_root = temp / "site-packages"

        phase("install local packages to an isolated target without editable mode")
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

        port = allocate_port()
        clean_environment = os.environ.copy()
        clean_environment["PYTHONPATH"] = str(install_root)
        clean_environment["PYTHONNOUSERSITE"] = "1"
        phase("start installed simulator outside repository")
        server = subprocess.Popen(
            [sys.executable, "-m", "lefly_simulator", "--host", "127.0.0.1", "--port", str(port)],
            cwd=temp,
            env=clean_environment,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_health("http://127.0.0.1:%d/health" % port, server)
            client_script = temp / "sdk_smoke.py"
            client_script.write_text(
                textwrap.dedent(
                    """
                    import asyncio
                    import os
                    from pathlib import Path

                    import lefly_sdk
                    from lefly_sdk import DeviceClient, RemoteHardwareController


                    async def main():
                        package_path = Path(lefly_sdk.__file__).resolve()
                        expected_root = Path(os.environ["EXPECTED_INSTALL_ROOT"]).resolve()
                        if expected_root not in package_path.parents:
                            raise AssertionError("SDK did not resolve from isolated install: %s" % package_path)

                        state_ready = asyncio.Event()
                        observed = {}
                        observed_correlation = [None]
                        client = DeviceClient(os.environ["DEVICE_URL"])

                        def on_state(event):
                            state = dict(event.payload)
                            pixels = state.get("light", {}).get("pixels", ())
                            if len(pixels) == 64 and set(pixels) == {"#00FF00"}:
                                observed.update(state)
                                observed_correlation[0] = event.correlation_id
                                state_ready.set()

                        client.subscribe("device.state_changed", on_state)
                        await client.start()
                        try:
                            await client.wait_until_connected(timeout=5)
                            robot = RemoteHardwareController(client, device_id="lefly-sim-01")
                            acknowledgement = await robot.set_light_color("green")
                            if acknowledgement.message_type != "command.accepted":
                                raise AssertionError("expected command.accepted")
                            if not acknowledgement.correlation_id:
                                raise AssertionError("acknowledgement lacks correlation_id")
                            await asyncio.wait_for(state_ready.wait(), timeout=5)
                            if observed_correlation[0] != acknowledgement.correlation_id:
                                raise AssertionError("state correlation does not match acknowledgement")
                            if observed.get("device_id") != "lefly-sim-01":
                                raise AssertionError("complete state was not observed")
                            if "capabilities" not in observed or "revision" not in observed:
                                raise AssertionError("state lacks capabilities or revision")
                        finally:
                            await client.close()


                    asyncio.run(main())
                    """
                ),
                encoding="utf-8",
            )
            child_environment = clean_environment.copy()
            child_environment.update({
                "DEVICE_URL": "ws://127.0.0.1:%d/ws/device/simulator" % port,
                "EXPECTED_INSTALL_ROOT": str(install_root),
            })
            phase("exercise public SDK against installed simulator")
            subprocess.run(
                [sys.executable, str(client_script)],
                check=True,
                cwd=temp,
                env=child_environment,
            )
        finally:
            phase("stop simulator and remove temporary environment")
            stop_process(server)

    print("CLEAN INSTALL SMOKE PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
