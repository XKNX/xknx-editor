"""The tray icon in the macOS menu bar (an ``NSStatusItem``, through PyObjC).

AppKit runs on the main thread, which also runs the browser UI's frame loop: the icon is made there,
and the loop hands AppKit its events every frame (:meth:`MacTray.pump`) and while it waits
(:meth:`MacTray.wait`). The app stays out of the Dock (an accessory application)."""

from __future__ import annotations

import importlib
import sys
import threading
import time
from typing import Any

from editor_gui.web.tray import OPEN, STOP, TrayMenu

assert sys.platform == "darwin"

# Menu bar icons are 18 points high.
_ICON_POINTS = 18.0
# While waiting, AppKit's events are handled at least this often (seconds).
_SLICE = 0.02

_target_class: Any = None


def _appkit() -> tuple[Any, Any, Any]:
    return (
        importlib.import_module("objc"),
        importlib.import_module("AppKit"),
        importlib.import_module("Foundation"),
    )


def _target(objc: Any, foundation: Any) -> Any:
    """The Objective-C class whose instance receives the menu's actions (made once per process)."""
    global _target_class
    if _target_class is None:

        def open_browser(self: Any, _sender: Any) -> None:
            self.tray_menu.run(OPEN)

        def stop_app(self: Any, _sender: Any) -> None:
            self.tray_menu.run(STOP)

        _target_class = type(
            "XknxEditorTrayTarget",
            (foundation.NSObject,),
            {
                "openBrowser_": objc.selector(
                    open_browser, selector=b"openBrowser:", signature=b"v@:@"
                ),
                "stopApp_": objc.selector(
                    stop_app, selector=b"stopApp:", signature=b"v@:@"
                ),
            },
        )
    return _target_class


class MacTray:
    def __init__(self, menu: TrayMenu, png: bytes) -> None:
        objc, appkit, foundation = _appkit()
        self._appkit = appkit
        self._foundation = foundation
        self._app = appkit.NSApplication.sharedApplication()
        self._app.setActivationPolicy_(appkit.NSApplicationActivationPolicyAccessory)
        self._app.finishLaunching()
        self._mask = getattr(appkit, "NSEventMaskAny", None) or appkit.NSAnyEventMask
        self._mode = foundation.NSDefaultRunLoopMode

        self._target = _target(objc, foundation).alloc().init()
        self._target.tray_menu = menu
        bar = appkit.NSStatusBar.systemStatusBar()
        self._item = bar.statusItemWithLength_(appkit.NSVariableStatusItemLength)
        image = appkit.NSImage.alloc().initWithData_(
            foundation.NSData.dataWithBytes_length_(png, len(png))
        )
        image.setSize_((_ICON_POINTS, _ICON_POINTS))
        button = self._item.button()
        button.setImage_(image)
        button.setToolTip_(menu.title)

        ns_menu = appkit.NSMenu.alloc().init()
        ns_menu.setAutoenablesItems_(False)
        title = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            menu.title, None, ""
        )
        title.setEnabled_(False)
        ns_menu.addItem_(title)
        ns_menu.addItem_(appkit.NSMenuItem.separatorItem())
        for label, action in (
            (menu.open_label, "openBrowser:"),
            (menu.stop_label, "stopApp:"),
        ):
            entry = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                label, action, ""
            )
            entry.setTarget_(self._target)
            ns_menu.addItem_(entry)
        self._item.setMenu_(ns_menu)

    def pump(self) -> None:
        self._handle_events(None)

    def wait(self, event: threading.Event, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while not event.is_set():
            left = end - time.monotonic()
            if left <= 0:
                break
            self._handle_events(min(_SLICE, left))
        return event.is_set()

    def close(self) -> None:
        self._appkit.NSStatusBar.systemStatusBar().removeStatusItem_(self._item)

    def _handle_events(self, timeout: float | None) -> None:
        """Hand AppKit its pending events, waiting up to ``timeout`` seconds for the first one."""
        until = (
            self._foundation.NSDate.dateWithTimeIntervalSinceNow_(timeout)
            if timeout
            else self._foundation.NSDate.distantPast()
        )
        while True:
            event = self._app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                self._mask, until, self._mode, True
            )
            if event is None:
                return
            self._app.sendEvent_(event)
            until = self._foundation.NSDate.distantPast()
