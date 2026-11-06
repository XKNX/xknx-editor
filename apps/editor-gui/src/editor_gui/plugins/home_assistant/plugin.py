"""Home Assistant plugin: the open project as the KNX project of Home Assistant's KNX integration.

Only inside Home Assistant (``host.home_assistant()``): a Home Assistant menu, a part of the status
bar, and the question whether to use a project that Home Assistant does not hold yet. The work is
done by :class:`~editor_gui.plugins.home_assistant.service.HomeAssistantService`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui import host
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.home_assistant.service import (
    HISTORY_HOURS,
    HomeAssistantService,
    LinkState,
)
from editor_gui.plugins.home_assistant.strings import S
from editor_gui.plugins.home_assistant.telegrams import link_info, telegram_frame
from editor_gui.settings_window import Setting
from editor_gui.strings import S as app_strings
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.tables import begin_data_table, data_table_headers

if TYPE_CHECKING:
    from editor_gui.plugins.monitor.service import MonitorService
    from editor_gui.plugins.project.service import ProjectService
    from editor_gui.web.host_link import HostLink

_GREEN = imgui.ImVec4(0.4, 0.85, 0.45, 1.0)
_AMBER = imgui.ImVec4(0.95, 0.75, 0.2, 1.0)
_RED = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
# The Home Assistant page of the Settings window.
SETTINGS_PAGE = "home_assistant"


def _question_title() -> str:
    return f"{S.QUESTION_TITLE}###home_assistant_question"


def _host_link() -> HostLink | None:
    from editor_gui import web

    hub = web.active_hub
    return hub.host_link if hub is not None else None


class HomeAssistantPlugin:
    name = "home_assistant"

    def __init__(
        self,
        api: PluginAPI,
        project: ProjectService,
        monitor: MonitorService | None = None,
    ) -> None:
        """``monitor``: the Group Monitor and latest values Home Assistant's telegrams go to."""
        self._api = api
        self._service = HomeAssistantService(project, _host_link)
        self._service.set_logger(Logger(api.log, "home_assistant"))
        self._monitor = monitor
        self._service.on_telegram = self._take_telegram
        self._service.on_history = self._take_history
        # The project generation the question was shown for (once per opened project).
        self._asked: int | None = None
        self._question_requested = False
        self._project = project
        # Opens a page of the Settings window (set by the app).
        self.open_settings: Callable[[str], None] | None = None

    def _take_telegram(self, telegram: dict[str, Any], log: bool) -> None:
        frame = telegram_frame(telegram)
        if frame is not None and self._monitor is not None:
            raw, when = frame
            link = link_info(self._service.connection)
            self._monitor.on_foreign_cemi(raw, when, link, log=log)

    def _take_history(self, telegrams: list[dict[str, Any]]) -> None:
        if self._monitor is None:
            return
        frames = [frame for t in telegrams if (frame := telegram_frame(t)) is not None]
        count = self._monitor.load_foreign(frames, link_info(self._service.connection))
        if self._api.notify is not None:
            self._api.notify(S.HISTORY_LOADED.format(count=count))

    @property
    def service(self) -> HomeAssistantService:
        """The live service (shared with the embedded MCP server)."""
        return self._service

    @property
    def active(self) -> bool:
        return host.home_assistant()

    @property
    def panels(self) -> list[PanelDefinition]:
        return []

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def poll(self) -> None:
        """Each frame: the integration's messages, and a changed project sent."""
        if self.active:
            self._service.poll()

    # --- UI ---

    def render_menu(self) -> None:
        if not self.active or not imgui.begin_menu(S.MENU):
            return
        service = self._service
        state = service.state
        text, color = self._status(state)
        imgui.text_colored(color, text)
        imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + px(360.0))
        imgui.text_disabled(self._detail(state))
        imgui.pop_text_wrap_pos()
        imgui.separator()
        can_send = state in (
            LinkState.NOT_LINKED,
            LinkState.PENDING,
            LinkState.UP_TO_DATE,
            LinkState.FAILED,
        )
        label = S.MENU_UPDATE_NOW if service.linked else S.MENU_USE_PROJECT
        if imgui.menu_item(label, "", False, can_send)[0]:
            if service.linked:
                service.send_now()
            else:
                self._open_question()
        if imgui.menu_item(
            S.MENU_LOAD_TELEGRAMS,
            "",
            False,
            service.connected and not service.history_loading,
        )[0]:
            service.request_history()
        if imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip):
            imgui.set_tooltip(S.LOAD_TELEGRAMS_HINT.format(hours=HISTORY_HOURS))
        if service.history_error is not None:
            imgui.text_colored(
                _RED, S.HISTORY_FAILED.format(error=service.history_error)
            )
        if self.open_settings is not None:
            imgui.separator()
            if imgui.menu_item(app_strings.MENU_SETTINGS, "", False)[0]:
                self.open_settings(SETTINGS_PAGE)
        imgui.end_menu()

    def settings(self) -> list[Setting]:
        """The settings of the Home Assistant page of the Settings window (and MCP)."""
        return [
            Setting(
                "home_assistant_auto_update",
                lambda: S.MENU_AUTO_UPDATE,
                "bool",
                get=lambda: self._service.auto_update,
                set=lambda value: self._service.set_auto_update(bool(value)),
                hint=lambda: S.AUTO_UPDATE_HINT,
            )
        ]

    def render_group_address(self, raw: int) -> None:
        """Below a group address's details: what Home Assistant does with it."""
        if not self.active:
            return
        imgui.spacing()
        imgui.separator_text(S.MENU)
        service = self._service
        if not service.connected:
            imgui.text_disabled(S.USAGE_UNKNOWN)
            return
        usage = service.usage.get(raw)
        if usage is not None and usage.sends:
            imgui.text_wrapped(S.USAGE_SENDS)
        if usage is None or not usage.users:
            imgui.text_disabled(S.USAGE_NONE)
            return
        if not begin_data_table(
            "##home_assistant_usage", 3, imgui.TableFlags_.borders_inner_h
        ):
            return
        imgui.table_setup_column(S.COLUMN_NAME)
        imgui.table_setup_column(S.COLUMN_USE)
        imgui.table_setup_column(S.COLUMN_STATE)
        data_table_headers()
        for index, user in enumerate(usage.users):
            imgui.push_id(index)
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(user.get("name") or user.get("entity_id") or "?"))
            imgui.table_next_column()
            imgui.text(_use_text(user))
            imgui.table_next_column()
            state = user.get("state")
            if user.get("disabled"):
                imgui.text_disabled(S.USER_DISABLED)
            elif state is not None:
                imgui.text(str(state))
            imgui.pop_id()
        imgui.end_table()

    def render_status(self, part: Callable[..., Any]) -> None:
        """The status bar's part: ``part(text, color, tooltip=...)`` of the app's status bar."""
        if not self.active:
            return
        state = self._service.state
        if state is LinkState.NO_PROJECT:
            return
        text, color = self._status(state)
        part(text, color, tooltip=self._detail(state))

    def render_window(self) -> None:
        """The question whether to use the open project in Home Assistant: asked once per opened
        project, after the other dialogs (e.g. the progress of opening it) have closed."""
        if not self.active:
            return
        generation = self._project.generation
        if self._asked != generation and self._service.wants_question():
            self._asked = generation
            self._question_requested = True
        other_dialog = imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id)
        if self._question_requested and not other_dialog:
            imgui.open_popup(_question_title())
            self._question_requested = False
        self._render_question()

    def _open_question(self) -> None:
        self._question_requested = True

    def _render_question(self) -> None:
        if not begin_dialog(_question_title(), width=460.0):
            return
        service = self._service
        held = service.knx_project
        name = service.project_name()
        if held is None:
            imgui.text_wrapped(S.QUESTION_NONE.format(name=name))
        else:
            imgui.text_wrapped(
                S.QUESTION_OTHER.format(
                    other=held.get("name") or "?",
                    tool=held.get("tool_version") or "?",
                    date=_date(held.get("last_modified")),
                    name=name,
                )
            )
        imgui.spacing()
        imgui.text_wrapped(S.QUESTION_EFFECT)
        imgui.spacing()
        imgui.text_wrapped(
            S.QUESTION_AUTO if service.auto_update else S.QUESTION_MANUAL
        )
        clicked = dialog_buttons(
            S.BTN_USE if held is None else S.BTN_REPLACE, S.BTN_NOT_NOW
        )
        if clicked == 0:
            imgui.close_current_popup()
            service.send_now()
        elif clicked == 1:
            imgui.close_current_popup()
            service.decline()
        end_dialog()

    def _status(self, state: LinkState) -> tuple[str, imgui.ImVec4]:
        return {
            LinkState.UNAVAILABLE: (S.STATUS_UNAVAILABLE, _RED),
            LinkState.KNX_MISSING: (S.STATUS_KNX_MISSING, _AMBER),
            LinkState.NO_PROJECT: (S.STATUS_NOT_LINKED, _AMBER),
            LinkState.NOT_LINKED: (S.STATUS_NOT_LINKED, _AMBER),
            LinkState.UP_TO_DATE: (S.STATUS_UP_TO_DATE, _GREEN),
            LinkState.PENDING: (S.STATUS_PENDING, _AMBER),
            LinkState.SENDING: (S.STATUS_SENDING, _AMBER),
            LinkState.FAILED: (S.STATUS_FAILED, _RED),
        }[state]

    def _detail(self, state: LinkState) -> str:
        service = self._service
        if state is LinkState.UP_TO_DATE or state is LinkState.SENDING:
            return S.DETAIL_UP_TO_DATE
        if state is LinkState.PENDING:
            return (
                S.DETAIL_PENDING_AUTO
                if service.auto_update
                else S.DETAIL_PENDING_MANUAL
            )
        if state is LinkState.KNX_MISSING:
            return S.DETAIL_KNX_MISSING
        if state is LinkState.FAILED:
            return S.DETAIL_FAILED.format(error=service.error or "?")
        if state is LinkState.UNAVAILABLE:
            return S.DETAIL_UNAVAILABLE
        held = service.knx_project
        if held is None:
            return S.DETAIL_NO_PROJECT
        return S.DETAIL_OTHER_PROJECT.format(
            name=held.get("name") or "?",
            tool=held.get("tool_version") or "?",
            date=_date(held.get("last_modified")),
        )


def _use_text(user: dict[str, Any]) -> str:
    """How a user of a group address uses it: an entity (its id, from YAML or the UI), an
    expose or the time server."""
    kind = user.get("kind")
    if kind == "expose":
        return S.USER_EXPOSE
    if kind == "time":
        return S.USER_TIME_SERVER
    entity = str(user.get("entity_id") or user.get("platform") or "")
    return S.USER_YAML.format(entity=entity) if user.get("ui") is False else entity


def _date(value: object) -> str:
    """The date part of an ISO timestamp, as the project info gives it."""
    if not isinstance(value, str) or not value:
        return S.UNKNOWN_DATE
    return value.split("T", 1)[0]
