"""Running editors on this computer.

Each editor registers itself in the data folder (``instances/<pid>.json``: window or browser UI, the
browser UI's address, the open project) and listens on a local port for requests from another
editor. A second start can so offer switching to an editor that runs already (bringing it to the
front, or opening a project there), and opening a project another editor has open can warn first.

A request is one JSON line with the editor's token (only readable from its file, so only the same
user can send one) and gets one JSON line back."""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

from editor_gui.ports import bind_port

logger = logging.getLogger(__name__)

DESKTOP = "desktop"
BROWSER = "browser"
# How long a request to another editor may take (seconds).
_TIMEOUT = 2.0


@dataclass(frozen=True)
class Instance:
    """A running editor: its process, window or browser UI (with its address), control port and
    token, the open project ("" without one) and when it started."""

    pid: int
    mode: str
    url: str
    port: int
    token: str
    project: str
    started: float


def same_file(a: str, b: str) -> bool:
    """Whether two paths name the same file (case and links as the file system sees them)."""
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(
            os.path.abspath(b)
        )


def _folder() -> Path:
    from editor_gui.settings import config_dir

    return config_dir() / "instances"


def allow_foreground(pid: int) -> None:
    """Let editor ``pid`` bring its window to the front: Windows only lets the app in front hand that
    on (elsewhere the window manager allows it anyway)."""
    if sys.platform == "win32":
        ctypes.windll.user32.AllowSetForegroundWindow(pid)


def request(instance: Instance, command: dict[str, Any]) -> dict[str, Any] | None:
    """Send ``command`` to ``instance``; its answer, None when it does not answer."""
    message = json.dumps({**command, "token": instance.token}) + "\n"
    try:
        with socket.create_connection(
            ("127.0.0.1", instance.port), timeout=_TIMEOUT
        ) as sock:
            sock.sendall(message.encode("utf-8"))
            with sock.makefile("r", encoding="utf-8") as reply:
                line = reply.readline()
    except OSError:
        return None
    try:
        answer: object = json.loads(line)
    except ValueError:
        return None
    return cast("dict[str, Any]", answer) if isinstance(answer, dict) else None


def others(folder: Path | None = None) -> list[Instance]:
    """The other editors running for this user, newest first. Entries of editors that ended without
    removing theirs are removed."""
    folder = folder or _folder()
    found: list[Instance] = []
    for path in folder.glob("*.json"):
        try:
            instance = Instance(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
        if instance.pid == os.getpid():
            continue
        if request(instance, {"cmd": "ping"}) is None:
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        found.append(instance)
    return sorted(found, key=lambda i: i.started, reverse=True)


def with_project(project: str, running: list[Instance]) -> list[Instance]:
    """The editors of ``running`` that have ``project`` open."""
    return [i for i in running if same_file(i.project, project)]


class Registry:
    """This editor's entry and the port it answers other editors on. ``on_request`` gets each
    request (``{"cmd": "activate", "file": ...}``) on the listening thread."""

    def __init__(
        self,
        mode: str,
        url: str,
        on_request: Callable[[dict[str, Any]], None],
        folder: Path | None = None,
    ) -> None:
        self._folder = folder or _folder()
        self._on_request = on_request
        sock = bind_port("127.0.0.1", 0)
        if sock is None:
            raise OSError("no local port for the editor's requests")
        sock.listen()
        self._sock = sock
        self.entry = Instance(
            os.getpid(),
            mode,
            url,
            sock.getsockname()[1],
            secrets.token_hex(16),
            "",
            time.time(),
        )
        self._path = self._folder / f"{self.entry.pid}.json"
        self._lock = threading.Lock()
        self._write()
        threading.Thread(target=self._serve, name="instances", daemon=True).start()

    def set_project(self, project: str) -> None:
        """Record the project now open ("" for none)."""
        with self._lock:
            if project == self.entry.project:
                return
            self.entry = replace(self.entry, project=project)
        self._write()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._path.unlink()
        # On Linux, closing alone leaves the listening thread's accept() waiting and the port
        # answering; shutting it down first ends the accept().
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self._sock.close()

    def _write(self) -> None:
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            partial = self._path.with_suffix(".tmp")
            partial.write_text(json.dumps(asdict(self.entry)), encoding="utf-8")
            partial.replace(self._path)
        except OSError:
            logger.warning("editor registry not written", exc_info=True)

    def _serve(self) -> None:
        while True:
            try:
                conn, _address = self._sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(_TIMEOUT)
                try:
                    with conn.makefile("r", encoding="utf-8") as lines:
                        received: object = json.loads(lines.readline())
                except (OSError, ValueError):
                    continue
                command: dict[str, Any] = (
                    cast("dict[str, Any]", received)
                    if isinstance(received, dict)
                    else {}
                )
                ok = secrets.compare_digest(
                    str(command.get("token", "")), self.entry.token
                )
                if ok and command.get("cmd") != "ping":
                    try:
                        self._on_request(command)
                    except Exception:
                        logger.exception("editor request failed")
                answer = {"ok": ok, "project": self.entry.project}
                with contextlib.suppress(OSError):
                    conn.sendall((json.dumps(answer) + "\n").encode("utf-8"))
