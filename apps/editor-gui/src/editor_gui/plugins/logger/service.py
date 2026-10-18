from __future__ import annotations

import logging
import sys
import time
from collections import deque
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from editor_gui.log_file import PlainTextFile, session_log

_MAX_RECORDS = 10_000

# Standard attributes present on every stdlib LogRecord; anything else came from ``extra=`` and is
# surfaced as payload. Computed once from a probe record so it tracks the running Python version.
_STDLIB_RECORD_ATTRS: set[str] = set(logging.makeLogRecord({}).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}

# Library package segment -> user-facing panel label (the library dir names differ from the label).
_PACKAGE_LABELS: dict[str, str] = {
    "proj": "project",
    "prod": "product",
    "datasecure": "keyring",
    "namespaces": "models",
}


class _TeeStream:
    def __init__(self, *streams: Any) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


class _StdlibBridgeHandler(logging.Handler):
    """Forwards stdlib log records from the ``xknxeditor.*`` packages into a :class:`LogService`."""

    def __init__(self, service: LogService) -> None:
        super().__init__(level=logging.DEBUG)
        self._service = service

    def retarget(self, service: LogService) -> None:
        """Point the (single, shared) bridge at the given service instance."""
        self._service = service

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._service.ingest_stdlib(record)
        except Exception:  # logging must never raise into the emitting call site
            self.handleError(record)


@dataclass
class LogRecord:
    timestamp: float
    level: str
    plugin: str
    event: str
    payload: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def timestamp_str(self) -> str:
        t = time.localtime(self.timestamp)
        return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}"


class LogService:
    def __init__(self) -> None:
        self._records: deque[LogRecord] = deque(maxlen=_MAX_RECORDS)
        # Monotonic version bumped on every append/clear. get_records() hands out a fresh list each
        # call, so its id/length are not a safe change signal (a bounded deque keeps a constant length
        # while its contents roll over); the UI caches its filtered view against this instead.
        self._version = 0
        self._configure_structlog()
        self._install_stdlib_bridge()

    def _store(self, record: LogRecord) -> None:
        self._records.append(record)
        self._version += 1

    def _wrap_payload(
        self, logger: Any, method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        plugin = str(event_dict.pop("plugin", "app"))
        payload_keys = [k for k in event_dict if k != "event"]
        payload = {k: event_dict.pop(k) for k in payload_keys}
        event_dict["plugin"] = plugin
        if payload:
            event_dict["payload"] = payload
        return event_dict

    def _capture(
        self, logger: Any, method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        payload = event_dict.get("payload", {})
        self._store(
            LogRecord(
                timestamp=time.time(),
                level=str(event_dict.get("level", method)),
                plugin=str(event_dict.get("plugin", "app")),
                event=str(event_dict.get("event", "")),
                payload={k: str(v) for k, v in payload.items()},
            )
        )
        return event_dict

    def _log_stream(self) -> Any:
        """A console-and-file stream for structlog's PrintLogger: the console as is, the session log
        file without colour codes. The frozen app's console is that file already."""
        log_file = session_log()
        stream = sys.stdout if sys.stdout is not None else sys.stderr
        if stream is None or stream is log_file:
            return PlainTextFile(log_file)
        return _TeeStream(stream, PlainTextFile(log_file))

    def _configure_structlog(self) -> None:
        # Before the renderer: on Windows it wraps sys.stdout for colours, which would hide that the
        # frozen app's console is the log file and write each line to it twice.
        self._stream = self._log_stream()
        self._renderer = structlog.dev.ConsoleRenderer()
        structlog.configure(
            processors=[
                self._wrap_payload,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="%H:%M:%S", utc=False),
                self._capture,
                self._renderer,
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=self._stream),
            cache_logger_on_first_use=False,
        )

    def _install_stdlib_bridge(self) -> None:
        """Route the packages' stdlib ``logging`` (``xknxeditor.*`` - programming, download, myknx,
        export, import) into the same in-app log so their debug output shows in the Logger panel.

        We capture at DEBUG unconditionally (the panel filters by level client-side) and stop
        propagation so the records neither hit the root last-resort handler nor get double-printed.
        """
        pkg_logger = logging.getLogger("xknxeditor")
        pkg_logger.setLevel(logging.DEBUG)
        pkg_logger.propagate = False
        # Idempotent: reuse the single bridge on the shared package logger, re-pointing it at this
        # instance (the app has one LogService; tests create several — the newest is the sink).
        for h in pkg_logger.handlers:
            if isinstance(h, _StdlibBridgeHandler):
                h.retarget(self)
                return
        pkg_logger.addHandler(_StdlibBridgeHandler(self))

    def ingest_stdlib(self, record: logging.LogRecord) -> None:
        """Convert a stdlib ``LogRecord`` into our ``LogRecord`` and store it."""
        parts = record.name.split(".")
        # "xknxeditor.download.procedure" -> "download"; anything else keeps its own short name.
        plugin = parts[1] if len(parts) > 1 and parts[0] == "xknxeditor" else parts[0]
        # Map library package segments to their user-facing panel label.
        plugin = _PACKAGE_LABELS.get(plugin, plugin)
        payload = {
            k: str(v)
            for k, v in record.__dict__.items()
            if k not in _STDLIB_RECORD_ATTRS
        }
        level = record.levelname.lower()
        event = record.getMessage()
        self._store(
            LogRecord(
                timestamp=record.created,
                level=level,
                plugin=plugin,
                event=event,
                payload=payload,
            )
        )
        # Also into the console and the log file, in the app's own format, so a reported log has
        # the libraries' steps too (download, MyKNX signing, export, import).
        line: dict[str, Any] = {
            "event": event,
            "level": level,
            "timestamp": time.strftime("%H:%M:%S", time.localtime(record.created)),
            "plugin": plugin,
        }
        if payload:
            line["payload"] = payload
        if record.exc_info:
            line["exc_info"] = record.exc_info
        self._stream.write(self._renderer(None, level, line) + "\n")

    def debug(self, event: str, plugin: str, **kwargs: Any) -> None:
        structlog.get_logger().debug(event, plugin=plugin, **kwargs)

    def info(self, event: str, plugin: str, **kwargs: Any) -> None:
        structlog.get_logger().info(event, plugin=plugin, **kwargs)

    def warning(self, event: str, plugin: str, **kwargs: Any) -> None:
        structlog.get_logger().warning(event, plugin=plugin, **kwargs)

    def error(self, event: str, plugin: str, **kwargs: Any) -> None:
        structlog.get_logger().error(event, plugin=plugin, **kwargs)

    def get_records(self) -> list[LogRecord]:
        return list(self._records)

    def records_version(self) -> int:
        return self._version

    def clear(self) -> None:
        self._records.clear()
        self._version += 1
