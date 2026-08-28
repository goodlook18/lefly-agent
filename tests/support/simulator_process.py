import subprocess
import sys
import threading
import time
from typing import Optional
from urllib.error import URLError
from urllib.request import urlopen


class SimulatorProcess:
    def __init__(self, *, port: int, remote_url: Optional[str] = None):
        self.port = port
        self.remote_url = remote_url
        self._process = None
        self._output_lines = []
        self._reader_thread = None

    @property
    def health_url(self) -> str:
        return "http://127.0.0.1:%d/health" % self.port

    @property
    def console_url(self) -> str:
        return "ws://127.0.0.1:%d/ws/console" % self.port

    @property
    def device_url(self) -> str:
        return "ws://127.0.0.1:%d/ws/device/simulator" % self.port

    @property
    def output(self) -> str:
        return "".join(self._output_lines)

    def start(self, timeout: float = 8.0) -> None:
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError("simulator process is already running")
        command = [
            sys.executable,
            "-m",
            "lefly_simulator",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]
        if self.remote_url is not None:
            command.extend(["--remote", self.remote_url])
        self._output_lines = []
        self._process = subprocess.Popen(
            command,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
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
                raise RuntimeError(
                    "simulator exited during startup:\n%s" % self.output
                )
            try:
                with urlopen(self.health_url, timeout=0.2) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                pass
            time.sleep(0.05)

        self.stop()
        raise RuntimeError("simulator startup timed out:\n%s" % self.output)

    def stop(self, timeout: float = 5.0) -> None:
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
        self._process = None

    def restart(self, timeout: float = 8.0) -> None:
        self.stop()
        self.start(timeout=timeout)

    def _collect_output(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            return
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    def _read_output(self, process) -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._output_lines.append(line)
        finally:
            process.stdout.close()
