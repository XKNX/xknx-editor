"""The Configured Connections dialog: the KNXnet/IP tunnelling interfaces entered by their address
(see :mod:`.configured`), edited as a draft and taken over with OK; one of them can be the open
project's connection (its backbone's Bus Connection), set with OK too."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugins.connection.configured import (
    PROTOCOLS,
    ConfiguredConnection,
    Protocol,
    export_xml,
    find_same,
    host_valid,
    import_xml,
    merge_imported,
    unique_name,
)
from editor_gui.plugins.connection.strings import S
from editor_gui.strings import S as app_strings
from editor_gui.theme import status_color
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo, end_combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.flow import button_width, same_line_if_fits

# Applied with OK: the connections, the one marked as the project's connection (None: none), and
# whether that marking changed.
OnApply = Callable[
    [list[ConfiguredConnection], ConfiguredConnection | None, bool], None
]

_TITLE_ID = "###configured_connections"
_LIST_ROWS = 6


@dataclass
class _Entry:
    connection: ConfiguredConnection
    project: bool = False


def protocol_label(protocol: Protocol) -> str:
    """The protocol as offered: "Automatic", "UDP" or "TCP"."""
    return S.CONFIGURED_PROTOCOL_AUTO if protocol == "Auto" else protocol.upper()


def connection_label(connection: ConfiguredConnection) -> str:
    """A configured connection as listed: its name, its address and a chosen protocol."""
    protocol = (
        "" if connection.protocol == "Auto" else f" {connection.protocol.upper()}"
    )
    where = connection.endpoint or "-"
    name = connection.name or where
    return f"{name} ({where}{protocol})" if connection.name else f"{name}{protocol}"


class ConfiguredConnectionsDialog:
    """The dialog's draft and its drawing. ``on_apply`` takes the result over (OK); ``notify``
    and ``warn`` show a toast."""

    def __init__(
        self,
        on_apply: OnApply,
        notify: Callable[[str], None] = lambda _t: None,
        warn: Callable[[str], None] = lambda _t: None,
    ) -> None:
        self._on_apply = on_apply
        self._notify = notify
        self._warn = warn
        self._entries: list[_Entry] = []
        self._selected: int | None = None
        self._project_open = False
        self._initial_project: ConfiguredConnection | None = None
        self._open = False
        self._shown = False
        self._import_dialog: file_dialogs.OpenFileDialog | None = None
        self._export_dialog: file_dialogs.SaveFileDialog | None = None

    # --- the draft --------------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def connections(self) -> list[ConfiguredConnection]:
        return [entry.connection for entry in self._entries]

    @property
    def selected(self) -> int | None:
        return self._selected

    @property
    def project_connection(self) -> ConfiguredConnection | None:
        """The connection marked as the project's connection, None for none."""
        return next((e.connection for e in self._entries if e.project), None)

    def open(
        self,
        connections: Sequence[ConfiguredConnection],
        project_connection: ConfiguredConnection | None,
        *,
        project_open: bool,
    ) -> None:
        """Start editing ``connections``; the first that is the same interface as the project's
        ``project_connection`` is marked as it. A project connection that is none of them is
        listed too (OK adds it)."""
        self._entries = [_Entry(c) for c in connections]
        self._project_open = project_open
        if project_open and project_connection is not None:
            index = find_same(self.connections, project_connection)
            if index is None:
                self._entries.append(_Entry(project_connection, project=True))
            else:
                self._entries[index].project = True
        self._initial_project = self.project_connection
        self._selected = 0 if self._entries else None
        self._open = True

    def select(self, index: int | None) -> None:
        self._selected = (
            index if index is None or 0 <= index < len(self._entries) else None
        )

    def new(self) -> None:
        """Add a connection with the next free default name, and select it."""
        name = unique_name(
            S.CONFIGURED_DEFAULT_NAME, (c.name for c in self.connections)
        )
        self._entries.append(_Entry(ConfiguredConnection(name, "")))
        self._selected = len(self._entries) - 1

    def delete(self) -> None:
        """Delete the selected connection."""
        if self._selected is None:
            return
        del self._entries[self._selected]
        self._selected = (
            min(self._selected, len(self._entries) - 1) if self._entries else None
        )

    def update(self, **fields: object) -> None:
        """Change fields of the selected connection (name, host, port, protocol, use_nat)."""
        if self._selected is None:
            return
        entry = self._entries[self._selected]
        entry.connection = replace(entry.connection, **fields)  # pyright: ignore[reportArgumentType]

    def can_be_project_connection(self) -> bool:
        if self._selected is None or not self._project_open:
            return False
        return host_valid(self._entries[self._selected].connection.host)

    def toggle_project(self) -> None:
        """Make the selected connection the project's connection, or none when it is it."""
        if not self.can_be_project_connection():
            return
        assert self._selected is not None
        entry = self._entries[self._selected]
        marked = not entry.project
        for other in self._entries:
            other.project = False
        entry.project = marked

    def valid(self) -> bool:
        """Whether every connection has an address an interface can have."""
        return all(host_valid(c.host) for c in self.connections)

    def import_data(self, data: bytes) -> int:
        """Add the connections of a ``BusConnections`` file (see :func:`merge_imported`); how
        many were added. Raises ``ValueError`` for a file that is no XML."""
        imported = import_xml(data)
        merged, added = merge_imported(self.connections, imported)
        marked = {id(e.connection) for e in self._entries if e.project}
        self._entries = [_Entry(c, project=id(c) in marked) for c in merged]
        if self._selected is None and self._entries:
            self._selected = 0
        return added

    def export_data(self) -> bytes:
        return export_xml(self.connections)

    def apply(self) -> None:
        """Take the draft over (OK)."""
        project = self.project_connection
        changed = project != self._initial_project
        self._on_apply(
            [replace(c, host=c.host.strip()) for c in self.connections],
            project,
            changed,
        )
        self._open = False

    def cancel(self) -> None:
        self._open = False

    # --- drawing ----------------------------------------------------------------------------

    def render(self) -> None:
        self._poll_file_dialogs()
        title = f"{S.CONFIGURED_TITLE}{_TITLE_ID}"
        if self._open and not self._shown:
            imgui.open_popup(title)
            self._shown = True
        if not self._shown:
            return
        if not begin_dialog(title, width=520.0):
            self._shown = False
            self._open = False
            return
        if not self._open:
            imgui.close_current_popup()
            self._shown = False
            end_dialog()
            return
        self._render_list()
        self._render_actions()
        if self._selected is not None:
            imgui.separator()
            self._render_fields()
        choice = dialog_buttons(
            app_strings.BTN_OK, app_strings.BTN_CANCEL, enabled=[self.valid(), True]
        )
        if choice == 0:
            self.apply()
        elif choice == 1:
            self.cancel()
        end_dialog()

    def _render_list(self) -> None:
        if not self._entries:
            imgui.text_disabled(S.CONFIGURED_NONE)
            return
        rows = min(len(self._entries), _LIST_ROWS)
        height = rows * imgui.get_text_line_height_with_spacing() + px(8)
        if imgui.begin_child("##configured_list", imgui.ImVec2(-1, height)):
            for index, entry in enumerate(self._entries):
                label = connection_label(entry.connection)
                if entry.project:
                    label = f"{label}  [{S.CONFIGURED_PROJECT_TAG}]"
                if imgui.selectable(
                    f"{label}##configured_{index}", index == self._selected
                )[0]:
                    self._selected = index
        imgui.end_child()

    def _render_actions(self) -> None:
        if imgui.button(f"{S.CONFIGURED_NEW}##configured_new"):
            self.new()
        same_line_if_fits(button_width(S.CONFIGURED_DELETE))
        imgui.begin_disabled(self._selected is None)
        if imgui.button(f"{S.CONFIGURED_DELETE}##configured_delete"):
            self.delete()
        imgui.end_disabled()
        same_line_if_fits(button_width(S.CONFIGURED_IMPORT))
        if imgui.button(f"{S.CONFIGURED_IMPORT}##configured_import"):
            self._import_dialog = file_dialogs.open_file(
                S.CONFIGURED_IMPORT_TITLE, "", [S.CONFIGURED_FILE_FILTER, "*.xml"]
            )
        same_line_if_fits(button_width(S.CONFIGURED_EXPORT))
        imgui.begin_disabled(not self._entries)
        if imgui.button(f"{S.CONFIGURED_EXPORT}##configured_export"):
            self._export_dialog = file_dialogs.save_file(
                S.CONFIGURED_EXPORT_TITLE,
                "BusConnections.xml",
                [S.CONFIGURED_FILE_FILTER, "*.xml"],
            )
        imgui.end_disabled()

    def _render_fields(self) -> None:
        assert self._selected is not None
        connection = self._entries[self._selected].connection
        width = -1.0
        imgui.text(S.CONFIGURED_NAME)
        imgui.set_next_item_width(width)
        changed, name = imgui.input_text("##configured_name", connection.name)
        if changed:
            self.update(name=name)
        imgui.text(S.CONFIGURED_HOST)
        imgui.set_next_item_width(width)
        changed, host = imgui.input_text("##configured_host", connection.host)
        if changed:
            self.update(host=host.strip())
        if not host_valid(connection.host):
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(status_color("error"), S.CONFIGURED_HOST_INVALID)
            imgui.pop_text_wrap_pos()
        imgui.text(S.CONFIGURED_PORT)
        imgui.set_next_item_width(px(140))
        changed, port = imgui.input_int("##configured_port", connection.port)
        if changed:
            self.update(port=min(max(port, 1), 65535))
        imgui.text(S.CONFIGURED_PROTOCOL)
        imgui.set_next_item_width(px(200))
        if begin_combo("##configured_protocol", protocol_label(connection.protocol)):
            for protocol in PROTOCOLS:
                if imgui.selectable(
                    f"{protocol_label(protocol)}##protocol_{protocol}",
                    protocol == connection.protocol,
                )[0]:
                    self.update(protocol=protocol)
            end_combo()
        if connection.protocol != "Tcp":
            changed, nat = checkbox(S.CONFIGURED_USE_NAT, connection.use_nat)
            if changed:
                self.update(use_nat=nat)
        marked = self._entries[self._selected].project
        allowed = self.can_be_project_connection()
        imgui.begin_disabled(not allowed and not marked)
        changed, _value = checkbox(S.CONFIGURED_USE_AS_PROJECT, marked)
        if changed:
            self.toggle_project()
        imgui.end_disabled()
        if not self._project_open:
            # why it is off, also on a touch screen
            imgui.push_text_wrap_pos(0.0)
            imgui.text_disabled(S.CONFIGURED_NO_PROJECT)
            imgui.pop_text_wrap_pos()

    def _poll_file_dialogs(self) -> None:
        if self._import_dialog is not None and self._import_dialog.ready():
            chosen = self._import_dialog.result()
            self._import_dialog = None
            for path in chosen[:1]:
                try:
                    added = self.import_data(Path(path).read_bytes())
                except (OSError, ValueError) as error:
                    self._warn(S.CONFIGURED_IMPORT_FAILED.format(error=error))
                else:
                    self._notify(S.CONFIGURED_IMPORTED(added))
        if self._export_dialog is not None and self._export_dialog.ready():
            dest = self._export_dialog.result()
            self._export_dialog = None
            if dest:
                path = Path(dest)
                path = path if path.suffix else path.with_suffix(".xml")
                try:
                    path.write_bytes(self.export_data())
                except OSError as error:
                    self._warn(S.CONFIGURED_EXPORT_FAILED.format(error=error))
                else:
                    self._notify(S.CONFIGURED_EXPORTED.format(name=path.name))
