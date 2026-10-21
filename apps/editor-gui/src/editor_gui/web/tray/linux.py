"""The tray icon on Linux desktops: a StatusNotifierItem with a ``com.canonical.dbusmenu`` menu on
the session D-Bus (``dbus-fast``), as KDE Plasma, GNOME (with the AppIndicator extension most
distributions ship), Xfce, Cinnamon and others show it. A click opens the browser; the menu comes
with a right click. Without a StatusNotifierWatcher on the bus, there is no icon."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Any

from editor_gui.web.tray import OPEN, STOP, TrayMenu
from editor_gui.web.tray.png import argb_pixels

assert sys.platform == "linux"

from dbus_fast import BusType, Message, MessageType, Variant  # noqa: E402
from dbus_fast.aio import MessageBus  # noqa: E402

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
_ITEM = "org.kde.StatusNotifierItem"
_MENU = "com.canonical.dbusmenu"
_PROPERTIES = "org.freedesktop.DBus.Properties"
_INTROSPECTABLE = "org.freedesktop.DBus.Introspectable"
_WATCHER = "org.kde.StatusNotifierWatcher"
_UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"
# Menu ids besides OPEN and STOP: the title line and the separator.
_TITLE, _SEPARATOR = 10, 11

_INTROSPECTION = {
    ITEM_PATH: f"""<node><interface name="{_ITEM}">
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/><property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
</interface></node>""",
    MENU_PATH: f"""<node><interface name="{_MENU}">
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
<arg type="as" direction="in"/><arg type="u" direction="out"/>
<arg type="(ia{{sv}}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
<arg type="a(ia{{sv}})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/>
<arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/>
<arg type="v" direction="in"/><arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/>
<arg type="ai" direction="out"/></method>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
<property name="Version" type="u" access="read"/><property name="Status" type="s" access="read"/>
<property name="TextDirection" type="s" access="read"/>
</interface></node>""",
}


def _label(text: str) -> str:
    """A menu label: an underscore marks a mnemonic, so a real one is doubled."""
    return text.replace("_", "__")


class StatusNotifierItem:
    """Answers the D-Bus calls for the icon and its menu (on the bus connection's loop)."""

    def __init__(self, menu: TrayMenu, png: bytes) -> None:
        self.menu = menu
        width, height, pixels = argb_pixels(png)
        pixmap = [[width, height, pixels]]
        self.item_properties: dict[str, Variant] = {
            "Category": Variant("s", "ApplicationStatus"),
            "Id": Variant("s", "xknx-editor"),
            "Title": Variant("s", menu.title),
            "Status": Variant("s", "Active"),
            "WindowId": Variant("i", 0),
            "IconName": Variant("s", ""),
            "IconPixmap": Variant("a(iiay)", pixmap),
            "ToolTip": Variant("(sa(iiay)ss)", ["", pixmap, menu.title, ""]),
            "ItemIsMenu": Variant("b", False),
            "Menu": Variant("o", MENU_PATH),
        }
        self.menu_properties: dict[str, Variant] = {
            "Version": Variant("u", 3),
            "Status": Variant("s", "normal"),
            "TextDirection": Variant("s", "ltr"),
        }
        self.entries: dict[int, dict[str, Variant]] = {
            _TITLE: {
                "label": Variant("s", _label(menu.title)),
                "enabled": Variant("b", False),
            },
            _SEPARATOR: {"type": Variant("s", "separator")},
            OPEN: {"label": Variant("s", _label(menu.open_label))},
            STOP: {"label": Variant("s", _label(menu.stop_label))},
        }

    def layout(self, parent: int) -> list[Any]:
        """The menu from ``parent`` down, as ``(ia{sv}av)``."""
        if parent in self.entries:
            return [parent, self.entries[parent], []]
        children = [
            Variant("(ia{sv}av)", [entry, props, []])
            for entry, props in self.entries.items()
        ]
        return [0, {"children-display": Variant("s", "submenu")}, children]

    def handle(self, msg: Message) -> Message | None:
        """The reply to a method call on the icon or its menu, None when not ours."""
        if msg.message_type is not MessageType.METHOD_CALL or msg.path not in (
            ITEM_PATH,
            MENU_PATH,
        ):
            return None
        member, body = msg.member, msg.body
        properties = (
            self.item_properties if msg.path == ITEM_PATH else self.menu_properties
        )
        if msg.interface == _INTROSPECTABLE and member == "Introspect":
            return Message.new_method_return(msg, "s", [_INTROSPECTION[msg.path]])
        if msg.interface == _PROPERTIES and member == "GetAll":
            return Message.new_method_return(msg, "a{sv}", [properties])
        if msg.interface == _PROPERTIES and member == "Get" and body[1] in properties:
            return Message.new_method_return(msg, "v", [properties[body[1]]])
        if msg.path == ITEM_PATH:
            if member in ("Activate", "SecondaryActivate"):
                self.menu.run(OPEN)
                return Message.new_method_return(msg)
            if member in ("ContextMenu", "Scroll", "ProvideXdgActivationToken"):
                return Message.new_method_return(msg)
        else:
            reply = self._menu_call(msg)
            if reply is not None:
                return reply
        return Message.new_error(msg, _UNKNOWN_METHOD, f"{member} is not supported")

    def _menu_call(self, msg: Message) -> Message | None:
        member, body = msg.member, msg.body
        if member == "GetLayout":
            return Message.new_method_return(
                msg, "u(ia{sv}av)", [1, self.layout(body[0])]
            )
        if member == "GetGroupProperties":
            ids = body[0] or list(self.entries)
            found = [[i, self.entries[i]] for i in ids if i in self.entries]
            return Message.new_method_return(msg, "a(ia{sv})", [found])
        if member == "GetProperty":
            value = self.entries.get(body[0], {}).get(body[1], Variant("s", ""))
            return Message.new_method_return(msg, "v", [value])
        if member == "Event":
            if body[1] == "clicked":
                self.menu.run(body[0])
            return Message.new_method_return(msg)
        if member == "EventGroup":
            for entry, event, _data, _time in body[0]:
                if event == "clicked":
                    self.menu.run(entry)
            return Message.new_method_return(msg, "ai", [[]])
        if member == "AboutToShow":
            return Message.new_method_return(msg, "b", [False])
        if member == "AboutToShowGroup":
            return Message.new_method_return(msg, "aiai", [[], []])
        return None


class LinuxTray:
    """The icon's bus connection, on its own thread and event loop."""

    def __init__(
        self, menu: TrayMenu, png: bytes, bus_address: str | None = None
    ) -> None:
        self.item = StatusNotifierItem(menu, png)
        self._bus_address = bus_address
        self._loop = asyncio.new_event_loop()
        self._bus: MessageBus | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-{id(self)}"
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0):
            raise OSError("the tray icon did not start in time")
        if self._error is not None:
            raise self._error

    def pump(self) -> None:
        pass

    def wait(self, event: threading.Event, timeout: float) -> bool:
        return event.wait(timeout)

    def close(self) -> None:
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not threading.current_thread():
            self._thread.join(2.0)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._register())
        except BaseException as error:
            self._error = error
            self._ready.set()
            self._loop.close()
            return
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            if self._bus is not None:
                self._bus.disconnect()
            self._loop.close()

    async def _register(self) -> None:
        bus = MessageBus(bus_address=self._bus_address, bus_type=BusType.SESSION)
        self._bus = await bus.connect()
        self._bus.add_message_handler(self.item.handle)
        await self._bus.request_name(self.name)
        reply = await self._bus.call(
            Message(
                destination=_WATCHER,
                path="/StatusNotifierWatcher",
                interface=_WATCHER,
                member="RegisterStatusNotifierItem",
                signature="s",
                body=[self.name],
            )
        )
        if reply.message_type is MessageType.ERROR:
            raise OSError(f"no StatusNotifierWatcher: {reply.error_name}")


def start_linux_tray(menu: TrayMenu, png: bytes) -> LinuxTray:
    return LinuxTray(menu, png)
