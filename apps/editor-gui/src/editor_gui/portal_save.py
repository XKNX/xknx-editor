"""Save dialog through the XDG desktop portal.

The native file dialog's file-type list is the ETS 5 / ETS 6 choice. Zenity and
portable-file-dialogs draw that list but return only the path. The portal's
``SaveFile`` response includes the selected filter, so the exporter can tell the
two ``*.knxproj`` types apart.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlparse

# A D-Bus variant arrives as ``(signature, value)``. A signature has no spaces;
# a filter name such as "ETS 6 Project (*.knxproj)" does, so this does not
# unwrap the filter tuple itself.
_SIGNATURE = re.compile(r"^[a-z(){}]+$")


def _variant_value(value: object) -> object:
    if not isinstance(value, tuple):
        return value
    pair = cast(tuple[object, ...], value)
    if len(pair) == 2 and isinstance(pair[0], str) and _SIGNATURE.fullmatch(pair[0]):
        return pair[1]
    return cast(object, value)


def _file_uri_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    return uri


def parse_save_response(body: object) -> tuple[str, str] | None:
    """``(path, filter name)`` from a portal ``Response`` body, or ``None`` on cancel.

    ``filter name`` is empty when the portal omits ``current_filter``.
    """
    if not isinstance(body, tuple):
        raise ValueError("unexpected portal save response")
    items = cast(tuple[object, ...], body)
    if len(items) < 2:
        raise ValueError("unexpected portal save response")
    code, results = items[0], items[1]
    if code != 0:
        return None
    if not isinstance(results, dict):
        raise ValueError("unexpected portal save results")
    fields = cast(dict[object, object], results)
    uris = _variant_value(fields.get("uris"))
    chosen = _variant_value(fields.get("current_filter"))
    if not isinstance(uris, list) or not uris or not isinstance(uris[0], str):
        return None
    name = ""
    if isinstance(chosen, tuple) and chosen and isinstance(chosen[0], str):
        name = chosen[0]
    return _file_uri_path(uris[0]), name


class PortalSaveDialog:
    """A portal save dialog polled like ``portable_file_dialogs.save_file``.

    ``filters`` is ``(label, glob pattern)`` pairs. ``current`` is the label
    selected when the dialog opens. ``ready`` and ``result`` match the portable
    file dialog: ``result`` is the chosen path, or ``""`` when the user cancels
    or the portal call fails. ``selected_filter`` is the label they confirmed.
    """

    def __init__(
        self,
        title: str,
        default_name: str,
        filters: list[tuple[str, str]],
        *,
        current: str,
    ) -> None:
        self.title = title
        self.default_name = default_name
        self._filters = filters
        self._current = current
        self._path = ""
        self._selected_filter = ""
        self._error = ""
        self._done = threading.Event()
        threading.Thread(target=self._run, name="portal-save", daemon=True).start()

    @property
    def selected_filter(self) -> str:
        return self._selected_filter

    @property
    def error(self) -> str:
        return self._error

    def ready(self, timeout: int = 20) -> bool:
        del timeout  # the UI polls; blocking here would freeze the frame
        return self._done.is_set()

    def result(self) -> str:
        return self._path

    def _run(self) -> None:
        try:
            choice = _save(self.title, self.default_name, self._filters, self._current)
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
        else:
            if choice is not None:
                self._path, self._selected_filter = choice
        finally:
            self._done.set()


def _save(
    title: str,
    default_name: str,
    filters: list[tuple[str, str]],
    current: str,
) -> tuple[str, str] | None:
    import importlib
    import secrets
    from typing import Any

    # jeepney ships without type stubs and is only installed on Linux. Its names are
    # looked up as Any, so the rest of this module stays strictly typed on every OS.
    jeepney: Any = importlib.import_module("jeepney")
    DBusAddress = jeepney.DBusAddress
    MessageType = jeepney.MessageType
    MatchRule = importlib.import_module("jeepney.bus_messages").MatchRule
    open_dbus_connection = importlib.import_module(
        "jeepney.io.blocking"
    ).open_dbus_connection

    token = "xknx" + secrets.token_hex(8)
    patterns = [(label, [(0, pattern)]) for label, pattern in filters]
    selected = next((item for item in patterns if item[0] == current), patterns[0])
    options: list[tuple[str, tuple[str, object]]] = [
        ("handle_token", ("s", token)),
        ("current_name", ("s", default_name)),
        ("current_folder", ("ay", os.fsencode(Path.cwd()))),
        ("filters", ("a(sa(us))", patterns)),
        ("current_filter", ("(sa(us))", selected)),
    ]
    conn: Any = open_dbus_connection(bus="SESSION")
    try:
        unique = str(cast(object, conn.unique_name))
        sender = unique[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        rule = MatchRule(
            type="signal",
            sender="org.freedesktop.portal.Desktop",
            interface="org.freedesktop.portal.Request",
            member="Response",
            path=path,
        )
        conn.bus_proxy.AddMatch(rule)
        call = jeepney.new_method_call(
            DBusAddress(
                "/org/freedesktop/portal/desktop",
                bus_name="org.freedesktop.portal.Desktop",
                interface="org.freedesktop.portal.FileChooser",
            ),
            "SaveFile",
            "ssa{sv}",
            ("", title, options),
        )
        with conn.filter(rule, bufsize=2) as queue:
            reply: Any = conn.send_and_get_reply(call)
            if reply.header.message_type is MessageType.error:
                raise RuntimeError(str(cast(object, reply.body)))
            response: Any = conn.recv_until_filtered(queue)
            return parse_save_response(cast(object, response.body))
    finally:
        conn.close()
