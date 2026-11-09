"""Monitor plugin: Group Monitor, Bus Monitor, Bus Activity and the group objects' values."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from imgui_bundle import imgui

from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.monitor import frames, persistence
from editor_gui.plugins.monitor.formatting import (
    DptFormat,
    ProjectIndex,
    build_index,
    master_formats,
)
from editor_gui.plugins.monitor.frames import individual_address_text
from editor_gui.plugins.monitor.records import TelegramLog
from editor_gui.plugins.monitor.service import MonitorKind, MonitorService
from editor_gui.plugins.monitor.strings import S
from editor_gui.plugins.monitor.ui import MonitorPanel
from editor_gui.regional import regional_format
from editor_gui.settings import load_settings, save_settings
from editor_gui.settings_window import Setting

_SETTINGS = "monitor"
# Seconds between periodic autosaves of the persisted Group Monitor while it records.
_AUTOSAVE_SECONDS = 10.0


class MonitorPlugin:
    name = "monitor"

    def __init__(self, api: PluginAPI) -> None:
        # Secured group telegrams are shown decrypted with the connection's group keys.
        frames.secure_group_keys = api.connection.secure_group_keys
        self._api = api
        self._service = MonitorService(api.connection)
        self._service.set_logger(Logger(api.log, "monitor"))
        # Restore the persisted caps (each set in its monitor's toolbar); one number is the
        # Group Monitor's, from when the monitors shared one cap.
        stored = load_settings(_SETTINGS).get("max_records")
        if isinstance(stored, int):
            stored = {MonitorKind.GROUP.value: stored}
        if isinstance(stored, dict):
            for kind in MonitorKind:
                value = cast(dict[str, Any], stored).get(kind.value)
                # 0 is a valid cap (unlimited); only a negative or non-int value is ignored.
                if isinstance(value, int) and value >= 0:
                    self._service.set_max_records(kind, value)
        self._service.on_max_records_changed = self._save_max_records
        # Persist the Group Monitor across restarts (per project), bounded by a size in MB.
        settings = load_settings(_SETTINGS)
        self._persist = settings.get("persist") is True
        mb = settings.get("max_file_mb")
        self._max_file_mb = mb if isinstance(mb, int) and mb >= 0 else 10
        # The project whose telegrams the log currently holds (its file is the flush target).
        self._owner_guid = ""
        self._owner_name = ""
        self._flushed_generation = -1
        self._last_autosave = 0.0
        self._unsubscribe_closing = api.project.subscribe(
            "closing", self._on_project_closing
        )
        self._index = ProjectIndex()
        self._formats: tuple[int, dict[str, DptFormat]] = (0, {})
        self._panel = MonitorPanel(
            service=self._service,
            connection=api.connection,
            get_group_addresses=lambda: api.project.group_addresses,
            get_index=self._project_index,
            notify=api.tell_refused,
            on_select_device=self._select_device,
            persist_get=lambda: self._persist,
            persist_set=self.set_persist,
        )
        connection = api.connection
        connection.add_raw_cemi_listener(self._service.on_raw_cemi)
        connection.add_sent_cemi_listener(self._service.on_sent_cemi)
        connection.add_connected_listener(self._service.on_connected)
        connection.add_disconnected_listener(self._service.on_disconnected)
        connection.add_link_state_listener(self._service.on_link_state)
        connection.add_busmonitor_listener(self._service.on_busmonitor_cemi)
        connection.add_busmonitor_state_listener(self._service.on_busmonitor_state)

    @property
    def service(self) -> MonitorService:
        """The live monitor service (shared with the embedded MCP server)."""
        return self._service

    @property
    def monitor_extended(self) -> bool:
        return self._panel.monitor_extended

    def set_monitor_extended(self, value: bool) -> None:
        self._panel.set_monitor_extended(value)

    def set_layout_toggler(self, toggle: Callable[[], None]) -> None:
        self._panel.set_layout_toggler(toggle)

    def toggle_monitor_extended(self) -> None:
        self._panel.toggle_monitor_extended()

    def _select_device(self, address: int) -> None:
        """Select the project device with individual address ``address``, if there is one."""
        device = self._api.project.find_device_by_address(
            individual_address_text(address)
        )
        if device is not None:
            self._api.project.selected_device = device

    def _save_max_records(self, kind: MonitorKind, value: int) -> None:
        data = load_settings(_SETTINGS)
        stored = data.get("max_records")
        caps = dict(cast(dict[str, Any], stored)) if isinstance(stored, dict) else {}
        caps[kind.value] = value
        data["max_records"] = caps
        save_settings(_SETTINGS, data)

    # --- persisting the Group Monitor per project -------------------------

    @property
    def persist(self) -> bool:
        return self._persist

    @property
    def max_file_mb(self) -> int:
        return self._max_file_mb

    def set_persist(self, value: bool) -> None:
        value = bool(value)
        if value == self._persist:
            return
        if not value:
            # Turn off: write a final time to the current owner, while still persisting.
            current = self._current_project()
            if current is not None:
                self._owner_guid, self._owner_name = current
            self.flush_current()
            self._persist = False
            self._save_persist_settings()
            return
        # Turn on: bind to the open project and keep what is recorded. An empty log is seeded
        # from the project's persisted file; a non-empty one (telegrams recorded before the user
        # switched persistence on) is written out, not discarded.
        self._persist = True
        self._save_persist_settings()
        current = self._current_project()
        if current is None:
            self._owner_guid = ""
            return
        self._owner_guid, self._owner_name = current
        log = self._group_log()
        if len(log) == 0:
            log.replace(persistence.load(persistence.group_file(*current)))
        else:
            self.flush_current()
        self._flushed_generation = log.generation
        self._last_autosave = time.monotonic()

    def set_max_file_mb(self, value: int) -> None:
        value = max(int(value), 0)
        if value == self._max_file_mb:
            return
        self._max_file_mb = value
        self._save_persist_settings()

    def _save_persist_settings(self) -> None:
        data = load_settings(_SETTINGS)
        data["persist"] = self._persist
        data["max_file_mb"] = self._max_file_mb
        save_settings(_SETTINGS, data)

    def settings(self, open_folder: Callable[[Any], None]) -> list[Setting]:
        """The Monitor page of the Settings window (also reached by MCP settings_get/set).
        ``open_folder`` opens a folder the web-aware way (the app's folder opener)."""

        def open_monitor_folder() -> None:
            folder = persistence.monitor_dir()
            folder.mkdir(parents=True, exist_ok=True)
            open_folder(folder)

        return [
            Setting(
                "max_telegrams",
                lambda: S.SETTING_MAX_TELEGRAMS,
                "int",
                get=lambda: self._service.max_records(MonitorKind.GROUP),
                set=lambda value: self._service.set_max_records(
                    MonitorKind.GROUP, int(value)
                ),
                bounds=(0, 1_000_000),
                hint=lambda: S.SETTING_MAX_TELEGRAMS_HINT,
            ),
            Setting(
                "persist_telegrams",
                lambda: S.SETTING_PERSIST,
                "bool",
                get=lambda: self._persist,
                set=lambda value: self.set_persist(bool(value)),
                hint=lambda: S.SETTING_PERSIST_HINT,
            ),
            Setting(
                "max_telegram_file_mb",
                lambda: S.SETTING_MAX_FILE_MB,
                "int",
                get=lambda: self._max_file_mb,
                set=lambda value: self.set_max_file_mb(int(value)),
                bounds=(0, 10_000),
                hint=lambda: S.SETTING_MAX_FILE_MB_HINT,
            ),
            Setting(
                "monitor_folder",
                lambda: S.SETTING_OPEN_FOLDER,
                "custom",
                render=lambda: self._render_open_folder(open_monitor_folder),
            ),
        ]

    @staticmethod
    def _render_open_folder(action: Callable[[], None]) -> None:
        if imgui.button(S.SETTING_OPEN_FOLDER):
            action()

    def _group_log(self) -> TelegramLog:
        return self._service.channels[MonitorKind.GROUP].log

    def _current_project(self) -> tuple[str, str] | None:
        """The open project's ``(guid, name)`` while persisting, else None."""
        project = self._api.project
        if not self._persist or not project.is_open or project.loading:
            return None
        info = project.get_project_metadata()
        return project.ensure_guid(), (
            info.name if info is not None else ""
        ) or "Project"

    def flush_current(self) -> None:
        """Write the Group Monitor's log to its owning project's file, if persisting. Flushes to
        the owner the log currently holds (not the project that happens to be open now), so a quick
        close right after a switch cannot write one project's telegrams into another's file."""
        if not self._persist or not self._owner_guid:
            return
        log = self._group_log()
        if persistence.flush(
            log,
            persistence.group_file(self._owner_guid, self._owner_name),
            self._max_file_mb,
        ):
            self._flushed_generation = log.generation

    def _on_project_closing(self) -> None:
        self.flush_current()

    def autosave_tick(self) -> None:
        """Called each frame from the app loop: load a newly-opened project's file, flush
        periodically. Does nothing while a project is loading, so a transient import does not make
        the log reload and drop telegrams recorded since."""
        current = self._current_project()
        if current is None:
            return
        guid, name = current
        if guid != self._owner_guid:
            # A different project is open now: adopt it and load its persisted telegrams.
            self._owner_guid, self._owner_name = guid, name
            log = self._group_log()
            log.replace(persistence.load(persistence.group_file(guid, name)))
            self._flushed_generation = log.generation
            self._last_autosave = time.monotonic()
            return
        self._owner_name = name  # keep the file name current across a rename
        log = self._group_log()
        now = time.monotonic()
        dirty = log.has_unsaved or log.generation != self._flushed_generation
        if dirty and now - self._last_autosave >= _AUTOSAVE_SECONDS:
            self.flush_current()
            self._last_autosave = now

    def _project_index(self) -> ProjectIndex:
        """Names, DPTs and building structure of the open project, rebuilt when it changes."""
        project = self._api.project
        master = self._api.connection.master
        decimal = regional_format().decimal
        key = (
            project.revision,
            str(project.path),
            project.is_open,
            id(master),
            decimal,
        )
        if self._index.revision == key or project.loading:
            return self._index
        if self._formats[0] != id(master):
            self._formats = (id(master), master_formats(master))
        style: Any = None
        devices: list[Any] = []
        spaces: list[Any] = []
        if project.is_open:
            try:
                style = project.group_address_style
            except Exception:
                style = None
            devices = [*project.devices, *project.unloaded_devices]
            spaces = list(project.get_space_tree())
        self._index = build_index(
            revision=key,
            group_addresses=project.group_addresses,
            devices=devices,
            spaces=spaces,
            ga_style=style,
            formats=self._formats[1],
            decimal=decimal,
        )
        return self._index

    @property
    def panels(self) -> list[PanelDefinition]:
        return [
            PanelDefinition(
                name="monitor",
                label=S.PANEL_MONITOR,
                dock="BottomSpace",
                render=self._panel.render,
            )
        ]

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        self.flush_current()
        self._unsubscribe_closing()
        self._service.stop_replay()
