"""Home Assistant plugin: the open project as the KNX project of Home Assistant's KNX integration.

Only inside Home Assistant (``host.home_assistant()``): a Home Assistant menu, a part of the status
bar, and the question whether to use a project that Home Assistant does not hold yet. The work is
done by :class:`~editor_gui.plugins.home_assistant.service.HomeAssistantService`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from imgui_bundle import imgui

from editor_gui import host
from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.home_assistant.areas import AreaRow, AreaState
from editor_gui.plugins.home_assistant.service import (
    HISTORY_HOURS,
    EntityRow,
    HomeAssistantService,
    LinkState,
)
from editor_gui.plugins.home_assistant.strings import S
from editor_gui.plugins.home_assistant.telegrams import link_info, telegram_frame
from editor_gui.settings_window import Setting
from editor_gui.strings import S as app_strings
from editor_gui.theme import status_color
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from xknxeditor.proj.core.addressing import format_ga

if TYPE_CHECKING:
    from editor_gui.plugins.monitor.service import MonitorService
    from editor_gui.plugins.project.service import ProjectService
    from editor_gui.web.host_link import HostLink

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
        if monitor is not None:
            # Without a connection of its own the editor sends over Home Assistant's.
            monitor.send_elsewhere = self._service.send_group
            monitor.elsewhere_available = lambda: (
                self.active and self._service.can_send_group
            )
        # Connects the editor's own connection like Home Assistant's: (transport, address).
        # Set by the app.
        self.connect_like: Callable[[str, str], None] | None = None
        # The project generation the question was shown for (once per opened project).
        self._asked: int | None = None
        self._question_requested = False
        # The floors and areas dialog: asked for, and the keys of the spaces chosen in it.
        self._areas_requested = False
        self._areas_chosen: set[str] = set()
        # Home Assistant's floors and areas (by id) and group addresses (raw) the user chose not
        # to add (all are chosen by default), and the building chosen for them.
        self._spaces_unchosen: set[str] = set()
        self._building_index = 0
        self._addresses_requested = False
        self._addresses_unchosen: set[int] = set()
        self._service.on_areas_applied = self._areas_applied
        # The entities dialog: asked for, and the candidates the user chose otherwise than
        # chosen by default.
        self._entities_requested = False
        self._entities_toggled: set[str] = set()
        # The entities of removed functions chosen to be removed (never by default).
        self._orphans_chosen: set[str] = set()
        self._service.on_entities_created = self._entities_created
        self._service.on_done = self._tell
        # The time server dialog: asked for, and the group address chosen for each kind.
        self._time_requested = False
        self._time_chosen: dict[str, str | None] = {}
        self._keys_requested = False
        self._project = project
        # Opens a page of the Settings window (set by the app).
        self.open_settings: Callable[[str], None] | None = None

    def _take_telegram(self, telegram: dict[str, Any], log: bool) -> None:
        frame = telegram_frame(telegram)
        if frame is not None and self._monitor is not None:
            raw, when = frame
            link = link_info(self._service.connection)
            self._monitor.on_foreign_cemi(raw, when, link, log=log)

    def _tell(self, text: str) -> None:
        if self._api.notify is not None:
            self._api.notify(text)

    def _entities_created(self, count: int) -> None:
        if self._api.notify is not None:
            self._api.notify(S.ENTITIES_DONE.format(count=count))

    def _areas_applied(self, changed: int) -> None:
        self._areas_chosen.clear()
        if self._api.notify is not None:
            self._api.notify(S.AREAS_APPLIED.format(count=changed))

    def _take_history(self, telegrams: list[dict[str, Any]]) -> None:
        if self._monitor is None:
            return
        frames = [frame for t in telegrams if (frame := telegram_frame(t)) is not None]
        count = self._monitor.load_foreign(frames, link_info(self._service.connection))
        if self._api.notify is not None:
            self._api.notify(S.HISTORY_LOADED(count).format(count=count))

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

    @property
    def menu_label(self) -> str:
        """The label of the Home Assistant menu, which the app draws in the menu bar while the
        editor runs inside Home Assistant (``active``)."""
        return S.MENU

    def render_menu(self) -> None:
        """The Home Assistant menu's content (the app draws the menu itself, see
        ``menu_label``)."""
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
            S.MENU_AREAS,
            "",
            False,
            service.connected and self._project.is_open,
        )[0]:
            self._areas_requested = True
        if imgui.menu_item(
            S.MENU_ENTITIES,
            "",
            False,
            service.connected and self._project.is_open,
        )[0]:
            self._entities_requested = True
        if imgui.menu_item(
            S.MENU_ADDRESSES,
            "",
            False,
            service.connected and self._project.is_open,
        )[0]:
            self._addresses_requested = True
        if imgui.menu_item(
            S.MENU_TIME_SERVER,
            "",
            False,
            service.connected and service.knx_loaded and self._project.is_open,
        )[0]:
            self._time_requested = True
        if imgui.menu_item(
            S.MENU_KEYS,
            "",
            False,
            service.connected and service.knx_loaded and self._project.is_open,
        )[0]:
            self._keys_requested = True
        target = service.connection_target()
        own = self._api.connection.xknx is not None
        if (
            imgui.menu_item(
                S.MENU_CONNECT_LIKE,
                "",
                False,
                target is not None and not own and self.connect_like is not None,
            )[0]
            and target is not None
            and self.connect_like is not None
        ):
            self.connect_like(*target)
        if target is not None and imgui.is_item_hovered(
            imgui.HoveredFlags_.for_tooltip
        ):
            imgui.set_tooltip(S.CONNECT_LIKE_HINT)
        elif target is None and imgui.is_item_hovered(
            imgui.HoveredFlags_.for_tooltip | imgui.HoveredFlags_.allow_when_disabled
        ):
            # Why it is not offered.
            imgui.set_tooltip(S.CONNECT_LIKE_UNAVAILABLE)
        if imgui.menu_item(
            S.MENU_LOAD_TELEGRAMS,
            "",
            False,
            service.connected and not service.history_loading,
        )[0]:
            service.request_history()
        if imgui.is_item_hovered(imgui.HoveredFlags_.for_tooltip):
            imgui.set_tooltip(
                S.LOAD_TELEGRAMS_HINT(HISTORY_HOURS).format(hours=HISTORY_HOURS)
            )
        if service.history_error is not None:
            imgui.text_colored(
                status_color("error"),
                S.HISTORY_FAILED.format(error=service.history_error),
            )
        if self.open_settings is not None:
            imgui.separator()
            if imgui.menu_item(app_strings.MENU_SETTINGS, "", False)[0]:
                self.open_settings(SETTINGS_PAGE)

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
        if self._areas_requested:
            self._areas_requested = False
            # Chosen by default: whatever is not in Home Assistant as it is here.
            self._areas_chosen = {
                row.planned.key
                for row in self._service.area_rows()
                if row.state is not AreaState.LINKED
            }
            self._spaces_unchosen.clear()
            self._building_index = 0
            imgui.open_popup(_areas_title())
        self._render_areas()
        if self._addresses_requested:
            self._addresses_requested = False
            self._addresses_unchosen.clear()
            imgui.open_popup(_addresses_title())
        self._render_addresses()
        if self._entities_requested:
            self._entities_requested = False
            self._entities_toggled.clear()
            imgui.open_popup(_entities_title())
        self._render_entities()
        if self._time_requested:
            self._time_requested = False
            served = self._service.time_server
            self._time_chosen = {kind: (served or {}).get(kind) for kind in _TIME_KINDS}
            imgui.open_popup(_time_title())
        self._render_time_server()
        if self._keys_requested:
            self._keys_requested = False
            imgui.open_popup(_keys_title())
        self._render_keys()

    def _render_time_server(self) -> None:
        if not begin_dialog(_time_title(), width=520.0):
            return
        service = self._service
        imgui.text_wrapped(S.TIME_INTRO)
        imgui.spacing()
        labels = {
            "time": S.TIME_TIME,
            "date": S.TIME_DATE,
            "datetime": S.TIME_DATETIME,
        }
        width = max(imgui.calc_text_size(text).x for text in labels.values())
        for kind in _TIME_KINDS:
            choices = [
                ga
                for ga in self._project.group_addresses
                if ga.datapoint_type in _TIME_TYPES[kind]
            ]
            names = [S.TIME_NONE, *(f"{ga.address} {ga.name}" for ga in choices)]
            addresses: list[str | None] = [None, *(ga.address for ga in choices)]
            current = self._time_chosen.get(kind)
            index = addresses.index(current) if current in addresses else 0
            imgui.align_text_to_frame_padding()
            imgui.text(labels[kind])
            imgui.same_line(width + px(16.0))
            imgui.set_next_item_width(-1)
            changed, index = combo(f"##time_{kind}", index, names, 16)
            if changed:
                self._time_chosen[kind] = addresses[index]
        if service.request_error:
            imgui.text_colored(
                status_color("error"),
                S.AREAS_FAILED.format(error=service.request_error),
            )
        clicked = dialog_buttons(
            S.BTN_APPLY,
            app_strings.BTN_CLOSE,
            enabled=[service.connected and not service.requesting, True],
        )
        if clicked == 0:
            service.set_time_server(dict(self._time_chosen))
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_keys(self) -> None:
        if not begin_dialog(_keys_title(), width=480.0):
            return
        service = self._service
        imgui.text_wrapped(S.KEYS_INTRO)
        imgui.spacing()
        secured = sum(1 for ga in self._project.group_addresses if ga.data_secure)
        imgui.text_disabled(S.KEYS_COUNT.format(count=secured))
        if service.keyring_refused is not None:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(status_color("warning"), S.KEYS_REFUSED)
            imgui.pop_text_wrap_pos()
        if service.request_error:
            imgui.text_colored(
                status_color("error"),
                S.AREAS_FAILED.format(error=service.request_error),
            )
        clicked = dialog_buttons(
            S.BTN_SEND,
            app_strings.BTN_CLOSE,
            enabled=[
                service.connected
                and service.keyring_refused is None
                and not service.requesting,
                True,
            ],
        )
        if (clicked == 0 and service.send_keys()) or clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _entity_chosen(self, row: EntityRow) -> bool:
        """Chosen by default: what Home Assistant takes and has no entity of yet, and created
        ones whose group addresses changed; the user's own choice otherwise."""
        if not _actionable(row):
            return False
        default = row.changed or (row.check == "" and not row.existing)
        return default != (row.candidate.key in self._entities_toggled)

    def _render_entities(self) -> None:
        if not begin_dialog(_entities_title(), width=760.0):
            return
        service = self._service
        rows = service.entity_rows()
        service.check_entities(rows)
        imgui.text_wrapped(S.ENTITIES_INTRO)
        imgui.spacing()
        area_names = _area_names(service.home_assistant_areas)
        if not rows:
            imgui.text_disabled(S.ENTITIES_NONE)
        elif begin_data_table(
            "##home_assistant_entities",
            6,
            imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y,
            imgui.ImVec2(0, px(340.0)),
        ):
            imgui.table_setup_column("##chosen", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column(S.ENTITIES_COLUMN_FUNCTION)
            imgui.table_setup_column(S.AREAS_COLUMN_KIND)
            imgui.table_setup_column(S.ENTITIES_COLUMN_ADDRESSES)
            imgui.table_setup_column(S.AREAS_AREA)
            imgui.table_setup_column(S.AREAS_COLUMN_STATE)
            data_table_headers()
            for row in rows:
                candidate = row.candidate
                imgui.push_id(candidate.key)
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.begin_disabled(not _actionable(row))
                changed, _chosen = checkbox("##chosen", self._entity_chosen(row))
                imgui.end_disabled()
                if changed:
                    self._entities_toggled ^= {candidate.key}
                imgui.table_next_column()
                imgui.text(candidate.path)
                imgui.table_next_column()
                imgui.text(candidate.platform)
                imgui.table_next_column()
                imgui.text(", ".join(candidate.group_addresses))
                imgui.table_next_column()
                if row.area_id is not None:
                    imgui.text(area_names.get(row.area_id, row.area_id))
                else:
                    imgui.text_disabled(S.ENTITIES_NO_AREA)
                imgui.table_next_column()
                imgui.text_wrapped(_entity_state_text(row))
                imgui.pop_id()
            imgui.end_table()
        orphans = service.orphans()
        if orphans:
            imgui.spacing()
            imgui.separator_text(S.ENTITIES_ORPHANS)
            for key, entity_id in orphans:
                changed, _ = checkbox(entity_id, key in self._orphans_chosen)
                if changed:
                    self._orphans_chosen ^= {key}
        busy = service.entities_creating or service.requesting
        if busy:
            imgui.text_disabled(S.AREAS_APPLYING)
        for error in (*service.entity_errors.values(), service.request_error):
            if error:
                imgui.text_colored(
                    status_color("error"), S.AREAS_FAILED.format(error=error)
                )
        chosen = [row for row in rows if self._entity_chosen(row)]
        new = [row for row in chosen if row.created is None]
        changed_rows = [row for row in chosen if row.created is not None]
        removed = [key for key, _entity in orphans if key in self._orphans_chosen]
        clicked = dialog_buttons(
            S.BTN_APPLY,
            app_strings.BTN_CLOSE,
            enabled=[
                bool(new or changed_rows or removed) and service.connected and not busy,
                True,
            ],
        )
        if clicked == 0:
            if new:
                service.create_entities(new)
            if changed_rows:
                service.update_entities(changed_rows)
            if removed:
                service.remove_entities(removed)
            self._entities_toggled.clear()
            self._orphans_chosen.clear()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_areas(self) -> None:
        if not begin_dialog(_areas_title(), width=640.0):
            return
        service = self._service
        rows = service.area_rows()
        imgui.text_wrapped(S.AREAS_INTRO)
        imgui.spacing()
        if not rows:
            imgui.text_disabled(S.AREAS_NONE)
        elif begin_data_table(
            "##home_assistant_areas",
            4,
            imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y,
            imgui.ImVec2(0, px(320.0)),
        ):
            imgui.table_setup_column("##chosen", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column(S.AREAS_COLUMN_SPACE)
            imgui.table_setup_column(S.AREAS_COLUMN_KIND)
            imgui.table_setup_column(S.AREAS_COLUMN_STATE)
            data_table_headers()
            for row in rows:
                planned = row.planned
                imgui.push_id(planned.key)
                imgui.table_next_row()
                imgui.table_next_column()
                linked = row.state is AreaState.LINKED
                imgui.begin_disabled(linked)
                changed, chosen = checkbox(
                    "##chosen", planned.key in self._areas_chosen and not linked
                )
                imgui.end_disabled()
                if changed:
                    if chosen:
                        self._areas_chosen.add(planned.key)
                    else:
                        self._areas_chosen.discard(planned.key)
                imgui.table_next_column()
                imgui.text(planned.path)
                imgui.table_next_column()
                imgui.text(S.AREAS_FLOOR if planned.kind == "floor" else S.AREAS_AREA)
                imgui.table_next_column()
                imgui.text(_area_state_text(row))
                imgui.pop_id()
            imgui.end_table()
        self._render_spaces_from_home_assistant()
        if service.areas_applying:
            imgui.text_disabled(S.AREAS_APPLYING)
        elif service.areas_error is not None:
            imgui.text_colored(
                status_color("error"), S.AREAS_FAILED.format(error=service.areas_error)
            )
        chosen = [
            row
            for row in rows
            if row.planned.key in self._areas_chosen
            and row.state is not AreaState.LINKED
        ]
        clicked = dialog_buttons(
            S.BTN_APPLY,
            app_strings.BTN_CLOSE,
            enabled=[
                bool(chosen) and service.connected and not service.areas_applying,
                True,
            ],
        )
        if clicked == 0:
            service.apply_areas(chosen)
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_spaces_from_home_assistant(self) -> None:
        """In the floors and areas dialog: Home Assistant's floors and areas the project has
        no space for, to add to a building of the project."""
        service = self._service
        spaces = service.spaces_to_add()
        if not spaces:
            return
        imgui.spacing()
        imgui.separator_text(S.FROM_HOME_ASSISTANT)
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.SPACES_FROM_INTRO)
        imgui.pop_text_wrap_pos()
        if begin_data_table(
            "##home_assistant_spaces_from",
            4,
            imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y,
            imgui.ImVec2(0, px(160.0)),
        ):
            imgui.table_setup_column("##chosen", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column(S.COLUMN_NAME)
            imgui.table_setup_column(S.AREAS_COLUMN_KIND)
            imgui.table_setup_column(S.AREAS_FLOOR)
            data_table_headers()
            for space in spaces:
                imgui.push_id(space.ha_id)
                imgui.table_next_row()
                imgui.table_next_column()
                changed, _chosen = checkbox(
                    "##chosen", space.ha_id not in self._spaces_unchosen
                )
                if changed:
                    self._spaces_unchosen ^= {space.ha_id}
                imgui.table_next_column()
                imgui.text(space.name)
                imgui.table_next_column()
                imgui.text(S.AREAS_FLOOR if space.kind == "floor" else S.AREAS_AREA)
                imgui.table_next_column()
                imgui.text(space.floor_name)
                imgui.pop_id()
            imgui.end_table()
        buildings = service.buildings()
        imgui.align_text_to_frame_padding()
        imgui.text(S.SPACES_BUILDING)
        imgui.same_line()
        if buildings:
            self._building_index = min(self._building_index, len(buildings) - 1)
            imgui.set_next_item_width(px(220.0))
            changed, index = combo(
                "##building", self._building_index, [b.name for b in buildings], 12
            )
            if changed:
                self._building_index = index
        else:
            imgui.text_disabled(S.SPACES_NEW_BUILDING)
        imgui.same_line()
        chosen = [s for s in spaces if s.ha_id not in self._spaces_unchosen]
        imgui.begin_disabled(not chosen or service.areas_applying)
        if imgui.button(S.BTN_ADD_TO_PROJECT):
            building = buildings[self._building_index].id if buildings else None
            added = service.add_spaces(chosen, building)
            self._spaces_unchosen.clear()
            self._tell(S.SPACES_ADDED.format(count=added))
        imgui.end_disabled()

    def _render_addresses(self) -> None:
        if not begin_dialog(_addresses_title(), width=720.0):
            return
        service = self._service
        addresses = service.addresses_to_add()
        imgui.text_wrapped(S.ADDRESSES_INTRO)
        imgui.spacing()
        style = self._project.group_address_style
        if not addresses:
            imgui.text_disabled(S.ADDRESSES_NONE)
        elif begin_data_table(
            "##home_assistant_addresses",
            5,
            imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y,
            imgui.ImVec2(0, px(340.0)),
        ):
            imgui.table_setup_column("##chosen", imgui.TableColumnFlags_.width_fixed)
            imgui.table_setup_column(S.COLUMN_ADDRESS)
            imgui.table_setup_column(S.COLUMN_NAME)
            imgui.table_setup_column(S.COLUMN_DATAPOINT_TYPE)
            imgui.table_setup_column(S.COLUMN_USED_BY)
            data_table_headers()
            for address in addresses:
                imgui.push_id(address.raw)
                imgui.table_next_row()
                imgui.table_next_column()
                changed, _chosen = checkbox(
                    "##chosen", address.raw not in self._addresses_unchosen
                )
                if changed:
                    self._addresses_unchosen ^= {address.raw}
                imgui.table_next_column()
                imgui.text(format_ga(address.raw, style))
                imgui.table_next_column()
                imgui.text(address.name)
                imgui.table_next_column()
                imgui.text(address.dpt or "")
                imgui.table_next_column()
                imgui.text_wrapped(", ".join(address.users))
                imgui.pop_id()
            imgui.end_table()
        chosen = [a for a in addresses if a.raw not in self._addresses_unchosen]
        clicked = dialog_buttons(
            S.BTN_ADD_TO_PROJECT, app_strings.BTN_CLOSE, enabled=[bool(chosen), True]
        )
        if clicked == 0:
            added = service.add_group_addresses(chosen)
            self._addresses_unchosen.clear()
            self._tell(S.ADDRESSES_ADDED.format(count=added))
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

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
        text, kind = {
            LinkState.UNAVAILABLE: (S.STATUS_UNAVAILABLE, "error"),
            LinkState.KNX_MISSING: (S.STATUS_KNX_MISSING, "warning"),
            LinkState.NO_PROJECT: (S.STATUS_NOT_LINKED, "warning"),
            LinkState.NOT_LINKED: (S.STATUS_NOT_LINKED, "warning"),
            LinkState.UP_TO_DATE: (S.STATUS_UP_TO_DATE, "success"),
            LinkState.PENDING: (S.STATUS_PENDING, "warning"),
            LinkState.SENDING: (S.STATUS_SENDING, "warning"),
            LinkState.FAILED: (S.STATUS_FAILED, "error"),
        }[state]
        return text, status_color(kind)

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


def _areas_title() -> str:
    return f"{S.AREAS_TITLE}###home_assistant_areas"


def _addresses_title() -> str:
    return f"{S.ADDRESSES_TITLE}###home_assistant_addresses"


def _entities_title() -> str:
    return f"{S.ENTITIES_TITLE}###home_assistant_entities"


def _time_title() -> str:
    return f"{S.TIME_TITLE}###home_assistant_time_server"


def _keys_title() -> str:
    return f"{S.KEYS_TITLE}###home_assistant_keys"


# What the time server sends, and the data types of the group addresses for each.
_TIME_KINDS = ("time", "date", "datetime")
_TIME_TYPES = {
    "time": ("DPST-10-1", "DPT-10"),
    "date": ("DPST-11-1", "DPT-11"),
    "datetime": ("DPST-19-1", "DPT-19"),
}


def _area_names(state: dict[str, Any] | None) -> dict[str, str]:
    """Home Assistant's areas by id: their names."""
    areas = (state or {}).get("areas")
    if not isinstance(areas, list):
        return {}
    names: dict[str, str] = {}
    for area in cast("list[object]", areas):
        if isinstance(area, dict) and "area_id" in area:
            entry = cast("dict[str, Any]", area)
            names[str(entry["area_id"])] = str(entry.get("name", ""))
    return names


def _actionable(row: EntityRow) -> bool:
    """Whether Apply can do something for ``row``: create it (Home Assistant takes it), or give
    its entity the new group addresses (its devices are downloaded)."""
    if row.created is not None:
        return row.changed and row.downloaded
    return row.check == ""


def _entity_state_text(row: EntityRow) -> str:
    if row.created is not None and row.changed:
        if not row.downloaded:
            return S.ENTITIES_CHANGED_WAIT
        return S.ENTITIES_CHANGED.format(entity=row.created)
    if row.created is not None:
        return S.ENTITIES_CREATED.format(entity=row.created)
    if row.existing:
        return S.ENTITIES_EXISTING.format(entities=", ".join(row.existing))
    if row.check is None:
        return S.ENTITIES_CHECKING
    if row.check:
        return S.ENTITIES_NOT_POSSIBLE.format(error=row.check)
    return S.AREAS_NEW


def _area_state_text(row: AreaRow) -> str:
    if row.state is AreaState.NEW:
        return S.AREAS_NEW
    if row.state is AreaState.MATCH:
        return S.AREAS_MATCH.format(name=row.current_name)
    if row.state is AreaState.RENAMED:
        return S.AREAS_RENAMED.format(name=row.current_name)
    return S.AREAS_LINKED


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
