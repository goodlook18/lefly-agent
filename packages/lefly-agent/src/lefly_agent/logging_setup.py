"""Process-local debug logging for the LeFly Agent CLI."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_SAFE_EXTRA_FIELDS = (
    "error_type",
    "lefly_dependency_versions",
    "lefly_endpoint_host",
    "lefly_error_type",
    "lefly_latency",
    "lefly_resources",
)


class _SafeExtraFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        metadata = {}
        for name in _SAFE_EXTRA_FIELDS:
            if not hasattr(record, name):
                continue
            value = getattr(record, name)
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            metadata[name] = value
        if not metadata:
            return rendered
        encoded = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "%s metadata=%s" % (rendered, encoded)


@dataclass
class DebugLogSession:
    """Own handlers and logger state installed for one debug process."""

    path: Path
    handlers: tuple[logging.Handler, ...]
    _root_level: int
    _package_level: int
    _package_propagate: bool
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        root_logger = logging.getLogger()
        package_logger = logging.getLogger("lefly_agent")
        for handler in self.handlers:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.setLevel(self._root_level)
        package_logger.setLevel(self._package_level)
        package_logger.propagate = self._package_propagate


def configure_debug_logging(
    enabled: bool,
    *,
    working_directory: Path | None = None,
    now: datetime | None = None,
    pid: int | None = None,
) -> DebugLogSession | None:
    """Create one local debug log session, or leave logging unchanged."""
    if not enabled:
        return None

    base_directory = (
        Path.cwd() if working_directory is None else Path(working_directory)
    )
    timestamp = datetime.now().astimezone() if now is None else now
    process_id = os.getpid() if pid is None else pid
    log_directory = base_directory.resolve() / "logs"
    log_path = log_directory / (
        "lefly-agent.%s.%s.log" % (timestamp.strftime("%Y%m%d-%H%M%S"), process_id)
    )

    file_handler: logging.FileHandler | None = None
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_path,
            mode="x",
            encoding="utf-8",
        )
        console_handler = logging.StreamHandler(sys.stderr)
    except OSError as error:
        if file_handler is not None:
            file_handler.close()
        print(
            "LeFly debug log unavailable: %s" % type(error).__name__,
            file=sys.stderr,
            flush=True,
        )
        return None

    formatter = _SafeExtraFormatter(_FORMAT, datefmt=_DATE_FORMAT)
    handlers: tuple[logging.Handler, ...] = (console_handler, file_handler)
    for handler in handlers:
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    package_logger = logging.getLogger("lefly_agent")
    session = DebugLogSession(
        path=log_path,
        handlers=handlers,
        _root_level=root_logger.level,
        _package_level=package_logger.level,
        _package_propagate=package_logger.propagate,
    )
    root_logger.setLevel(logging.INFO)
    package_logger.setLevel(logging.DEBUG)
    package_logger.propagate = True
    for handler in handlers:
        root_logger.addHandler(handler)

    print("LeFly debug log: %s" % log_path, flush=True)
    logging.getLogger(__name__).debug("agent.debug_logging.enabled")
    return session
