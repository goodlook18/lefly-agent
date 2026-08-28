import os
import subprocess
import sys
import threading
import time
from typing import Mapping, Optional
from urllib.error import URLError
from urllib.request import urlopen


class AgentProcess:
    def __init__(
        self,
        *,
        port: int,
        device_url: str,
        config_path: Optional[str] = None,
        environment: Optional[Mapping[str, str]] = None,
    ):
        self.port = port
        self.device_url = device_url
        self.config_path = config_path
        self.environment = dict(environment or {})
        self._process = None
        self._output_lines = []
        self._reader_thread = None

    @property
    def health_url(self) -> str:
        return "http://127.0.0.1:%d/health" % self.port

    @property
    def websocket_url(self) -> str:
        return "ws://127.0.0.1:%d/ws/agent" % self.port

    @property
    def output(self) -> str:
        return "".join(self._output_lines)

    @property
    def returncode(self):
        return None if self._process is None else self._process.poll()

    @property
    def reader_alive(self) -> bool:
        return self._reader_thread is not None and self._reader_thread.is_alive()

    def start(self, timeout: float = 15.0) -> None:
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError("agent process is already running")
        command = [
            sys.executable,
            "-m",
            "lefly_agent",
            "--device-url",
            self.device_url,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]
        if self.config_path is not None:
            command.extend(["--config", self.config_path])
        environment = os.environ.copy()
        environment.update(self.environment)
        self._output_lines = []
        self._process = subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
        self._reader_thread = threading.Thread(
            target=self._read_output,
            args=(self._process,),
            daemon=True,
        )
        self._reader_thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self._collect_output()
                raise RuntimeError("agent exited during startup:\n%s" % self.output)
            try:
                with urlopen(self.health_url, timeout=0.2) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                pass
            time.sleep(0.05)

        self.stop()
        raise RuntimeError("agent startup timed out:\n%s" % self.output)

    def stop(self, timeout: float = 8.0) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout)
        self._collect_output()

    def _collect_output(self) -> None:
        if self._process is None or self._process.poll() is None:
            return
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)

    def _read_output(self, process) -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._output_lines.append(line)
        finally:
            process.stdout.close()
