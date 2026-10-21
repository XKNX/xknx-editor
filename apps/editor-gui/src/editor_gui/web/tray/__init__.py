"""The browser UI's tray icon: shows that the editor runs in the background and offers "Open in
Browser" and "Stop XKNX Editor".

Each operating system has its own tray, so each has its own backend: the notification area on
Windows (``ctypes``), the menu bar on macOS (PyObjC) and a StatusNotifierItem over D-Bus on Linux
desktops (``dbus-fast``). Without a desktop session, or when the desktop has no tray, there is no
icon and the app runs as before."""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

ICON = Path(__file__).with_name("icon.png")

# Menu entries, by id.
OPEN = 1
STOP = 2


@dataclass(frozen=True)
class TrayMenu:
    """The tray icon's tooltip and menu: ``title`` (also the menu's first, disabled line), and the
    two commands, called on the tray's own thread (or the main thread on macOS)."""

    title: str
    open_label: str
    stop_label: str
    on_open: Callable[[], object]
    on_stop: Callable[[], object]

    def run(self, command: int) -> None:
        """Run the menu entry ``command`` (:data:`OPEN` or :data:`STOP`)."""
        try:
            if command == OPEN:
                self.on_open()
            elif command == STOP:
                self.on_stop()
        except Exception:
            logger.exception("tray command %d failed", command)


class Tray(Protocol):
    def pump(self) -> None:
        """Handle the tray's pending events (called every frame on the main thread)."""
        ...

    def wait(self, event: threading.Event, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for ``event`` while keeping the tray responsive; whether
        it was set."""
        ...

    def close(self) -> None:
        """Remove the icon."""
        ...


def has_desktop() -> bool:
    """Whether a desktop session shows windows and a tray (not a server, Docker, Home Assistant)."""
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def start_tray(menu: TrayMenu) -> Tray | None:
    """Show the tray icon, None when this desktop has no tray (or no desktop at all). On macOS it
    must be called on the main thread."""
    if not has_desktop():
        return None
    try:
        icon = ICON.read_bytes()
        if sys.platform == "win32":
            from editor_gui.web.tray.windows import WindowsTray

            return WindowsTray(menu, icon)
        if sys.platform == "darwin":
            from editor_gui.web.tray.macos import MacTray

            return MacTray(menu, icon)
        from editor_gui.web.tray.linux import start_linux_tray

        return start_linux_tray(menu, icon)
    except Exception:
        logger.warning("no tray icon", exc_info=True)
        return None
