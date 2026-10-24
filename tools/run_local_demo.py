#!/usr/bin/env python3
"""Start the local LeFly Simulator and Text Agent with one command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


HOST = "127.0.0.1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENT_CONFIG = REPOSITORY_ROOT / "packages" / "lefly-agent" / "agent.toml"
SIMULATOR_PORT = 8766
AGENT_PORT = 8767
DEVICE_URL = "ws://127.0.0.1:8766/ws/device/simulator"
MODEL_PROFILES = ("openai", "qwen", "deepseek", "huawei-maas", "openai-compatible")
SERVICE_PORTS = {"lefly-simulator": SIMULATOR_PORT, "lefly-agent": AGENT_PORT}
SERVICE_LABELS = {"lefly-simulator": "Simulator", "lefly-agent": "Text Agent"}


class DemoError(RuntimeError):
    """A local-demo service could not start or stay healthy."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-profile", choices=MODEL_PROFILES, help="enable model conversation with LEFLY_LLM_API_KEY")
    parser.add_argument("--config", type=Path, help="override the local Agent TOML configuration")
    return parser


def ensure_port_available(label: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, port))
        except OSError as error:
            raise DemoError("%s port %d is occupied; stop the existing service and retry" % (label, port)) from error


def probe_health(service: str) -> bool:
    url = "http://%s:%d/health" % (HOST, SERVICE_PORTS[service])
    try:
        with urlopen(url, timeout=0.4) as response:
            return response.status == 200 and json.load(response).get("service") == service
    except (OSError, URLError, ValueError):
        return False


def wait_healthy(service: str, process: subprocess.Popen, *, timeout: float = 15.0) -> None:
    label = SERVICE_LABELS[service]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise DemoError("%s exited during startup (code %d)" % (label, exit_code))
        if probe_health(service):
            return
        time.sleep(0.1)
    raise DemoError("%s did not become healthy at http://%s:%d/health" % (label, HOST, SERVICE_PORTS[service]))


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_demo(args: argparse.Namespace) -> int:
    if args.config is not None and not args.config.is_file():
        raise DemoError("Agent config file not found: %s" % args.config)
    if args.config is not None:
        agent_config = args.config.resolve()
    elif DEFAULT_AGENT_CONFIG.exists():
        if not DEFAULT_AGENT_CONFIG.is_file():
            raise DemoError("Default Agent config is not a file: %s" % DEFAULT_AGENT_CONFIG)
        agent_config = DEFAULT_AGENT_CONFIG
    else:
        agent_config = None
    if args.model_profile and not os.environ.get("LEFLY_LLM_API_KEY", "").strip():
        raise DemoError("--model-profile requires LEFLY_LLM_API_KEY in the environment")

    ensure_port_available("Simulator", SIMULATOR_PORT)
    ensure_port_available("Text Agent", AGENT_PORT)
    environment = os.environ.copy()
    if not args.model_profile:
        environment.pop("LEFLY_LLM_API_KEY", None)
    agent_environment = environment.copy()
    if args.model_profile:
        agent_environment["LEFLY_PROFILE_SOURCE_ROOT"] = str(REPOSITORY_ROOT)

    simulator_command = [sys.executable, "-m", "lefly_simulator", "--host", HOST, "--port", str(SIMULATOR_PORT)]
    agent_command = [
        sys.executable, "-m", "lefly_agent", "--host", HOST, "--port", str(AGENT_PORT),
        "--device-url", DEVICE_URL, "--device-id", "lefly-sim-01",
    ]
    if agent_config is not None:
        agent_command.extend(("--config", str(agent_config)))
    if args.model_profile:
        agent_command.extend(("--model-profile", args.model_profile))

    children: list[subprocess.Popen] = []
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def request_shutdown(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        print("Starting Simulator...", flush=True)
        simulator = subprocess.Popen(simulator_command, env=environment)
        children.append(simulator)
        wait_healthy("lefly-simulator", simulator)
        print("Simulator ready.", flush=True)

        print("Starting Text Agent...", flush=True)
        agent = subprocess.Popen(agent_command, env=agent_environment)
        children.append(agent)
        wait_healthy("lefly-agent", agent)
        print("Text Agent ready (%s)." % ("model: " + args.model_profile if args.model_profile else "offline"), flush=True)
        print("Open Console: http://127.0.0.1:8766/", flush=True)
        print("Press Ctrl-C to stop both services.", flush=True)
        while True:
            for service, process in (("lefly-simulator", simulator), ("lefly-agent", agent)):
                exit_code = process.poll()
                if exit_code is not None:
                    raise DemoError("%s exited (code %d)" % (SERVICE_LABELS[service], exit_code))
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("Stopping local demo...", flush=True)
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        for process in reversed(children):
            stop_process(process)


def main() -> int:
    if sys.version_info[:2] != (3, 12):
        print("Local demo requires Python 3.12", file=sys.stderr)
        return 2
    try:
        return run_demo(build_parser().parse_args())
    except (DemoError, OSError) as error:
        print("Local demo failed: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
