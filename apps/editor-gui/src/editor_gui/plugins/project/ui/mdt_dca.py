"""Custom symbol editor shared by desktop and browser clients."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.checkbox import checkbox, wrapped_checkbox
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tree import collapsing_header
from xknxeditor.vendor.mdt.layout import TILE_SIZES, TRANSPARENCY, Tile
from xknxeditor.vendor.mdt.symbols import export_symbol_image, import_symbol_image

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService


@dataclass
class _SymbolState:
    slot: int = 0
    pixels: bytearray = field(default_factory=lambda: bytearray(b"\xff" * 4096))
    source: tuple[int, int] | None = None
    drawing: bool = False
    ink: bool = True
    error: str = ""
    pending: bool = False
    include_defaults: bool = True
    invert_library: bool = False
    operation: Any = None
    read_prompt: tuple[str, str] | None = None
    confirm_read: bool = False
    operation_source: tuple[tuple[int, str], ...] = ()
    comparison: dict[int, bool] = field(default_factory=dict[int, bool])


@lru_cache(maxsize=128)
def _symbol_runs(grayscale_hex: str) -> tuple[tuple[int, int, int], ...]:
    pixels = bytes.fromhex(grayscale_hex)
    runs: list[tuple[int, int, int]] = []
    for y in range(64):
        x = 0
        while x < 64:
            if pixels[y * 64 + x] >= 128:
                x += 1
                continue
            start = x
            while x < 64 and pixels[y * 64 + x] < 128:
                x += 1
            runs.append((y, start, x))
    return tuple(runs)


class MdtDcaPanel:
    def __init__(
        self,
        queue: Callable[..., Any] | None = None,
        *,
        connected: Callable[[], bool] | None = None,
    ) -> None:
        self._queue = queue
        self._connected = connected or (lambda: False)
        self._states: dict[int, _SymbolState] = {}
        self._generation: int | None = None
        self._open: file_dialogs.OpenFileDialog | None = None
        self._save: file_dialogs.SaveFileDialog | None = None
        self._target: tuple[int, int, int, str] | None = None
        self._export = b""
        self._layouts: dict[int, dict[str, Any]] = {}
        self._import_kind = "symbol"
        self._import_revision: int | None = None
        self._import_invert = False

    def render(self, device: Device, project: ProjectService) -> None:
        if self._generation != project.generation:
            self._states.clear()
            self._layouts.clear()
            self._generation = project.generation
        self._poll(project)
        imgui.begin_disabled(self._open is not None or self._save is not None)
        try:
            self._render_device(device, project)
        finally:
            imgui.end_disabled()

    def _render_device(self, device: Device, project: ProjectService) -> None:
        profile = project.mdt_dca_profile(device)
        if profile is not None and profile.kind == "visucontrol":
            self._visucontrol(device, project)
            return
        if profile is not None and profile.kind == "layout":
            self._layout(device, project)
            return
        if profile is None or profile.kind != "symbols":
            return
        state = self._states.setdefault(device.node_id, _SymbolState())
        if state.drawing and not imgui.is_mouse_down(0):
            self._finish_drawing(state, device.node_id, project)
        try:
            view = project.mdt_symbols(device.node_id)
        except ValueError as exc:
            imgui.text_wrapped(str(exc))
            return
        state.slot = min(state.slot, profile.slots - 1)
        self._offer_symbol_read(device, state, view)
        imgui.begin_disabled(
            self._queue is None
            or state.operation is not None
            or not device.individual_address
        )
        if imgui.button(S.MDT_READ_SYMBOLS_BUTTON):
            self._request_symbol_read(device, state)
        imgui.end_disabled()
        self._confirm_symbol_read(device, state)
        imgui.text_wrapped(S.MDT_READ_SYMBOLS_HELP)
        missing = not view["defaults"] and len(view["symbols"]) != view["slots"]
        if missing:
            imgui.text_wrapped(S.MDT_LIBRARY_MISSING)
        self._add_default_library(device, project)
        if collapsing_header(S.MDT_MANAGE_LIBRARY + "###symbol_library"):
            imgui.push_id("symbol_library")
            try:
                if profile.app_id == "M0083-A0041" and view["new_library"] is not None:
                    changed, library = checkbox(S.MDT_NEW_LIBRARY, view["new_library"])
                    if changed:
                        project.set_mdt_symbol_library(device.node_id, library)
                        view = project.mdt_symbols(device.node_id)
                self._symbol_library(device, project, state, view)
            finally:
                imgui.pop_id()
        view = project.mdt_symbols(device.node_id)
        wide = imgui.get_content_region_avail().x >= px(700)
        if imgui.begin_table("##symbols", 2 if wide else 1):
            imgui.table_next_column()
            self._symbol_overview(state, view, wide)
            imgui.table_next_column()
            self._symbol_editor(device, project, state, view)
            imgui.end_table()
        self._symbol_operations(device, project, state, view)
        if state.error:
            imgui.text_wrapped(state.error)

    def _offer_symbol_read(
        self, device: Device, state: _SymbolState, view: dict[str, Any]
    ) -> None:
        identity = (
            device.individual_address or "",
            device.app.id if device.app else "",
        )
        if (
            not view["symbols"]
            and not view["defaults"]
            and identity != state.read_prompt
            and not state.confirm_read
            and self._queue is not None
            and state.operation is None
            and self._connected()
            and self._open is None
            and self._save is None
            and identity[0]
            and identity[1]
        ):
            self._request_symbol_read(device, state)

    @staticmethod
    def _request_symbol_read(device: Device, state: _SymbolState) -> None:
        if not device.individual_address or device.app is None:
            return
        state.read_prompt = (
            device.individual_address or "",
            device.app.id if device.app else "",
        )
        state.confirm_read = True

    def _confirm_symbol_read(self, device: Device, state: _SymbolState) -> None:
        title = f"{S.MDT_READ_SYMBOLS}###mdt_read_symbols_{device.node_id}"
        if state.confirm_read and not imgui.is_popup_open(title):
            imgui.open_popup(title)
        if begin_dialog(title, width=480):
            identity = (
                device.individual_address or "",
                device.app.id if device.app else "",
            )
            imgui.text_wrapped(
                S.MDT_READ_SYMBOLS_CONFIRM.format(address=device.individual_address)
            )
            clicked = dialog_buttons(S.MDT_READ_SYMBOLS, S.BTN_CANCEL)
            if identity != state.read_prompt or clicked is not None:
                state.confirm_read = False
                imgui.close_current_popup()
                if clicked == 0 and identity == state.read_prompt:
                    self._read_symbols(device, state)
            end_dialog()

    def _read_symbols(self, device: Device, state: _SymbolState) -> None:
        if self._queue is None:
            return
        state.error = ""
        state.comparison.clear()
        try:
            state.operation = self._queue(device.node_id, "read_mdt_symbols")
        except ValueError as exc:
            state.error = str(exc)

    def _add_default_library(self, device: Device, project: ProjectService) -> None:
        if imgui.button(S.MDT_ADD_DEFAULT_LIBRARY):
            self._import_kind = "default_library"
            self._import_revision = project.revision
            self._target = (
                project.generation,
                device.node_id,
                0,
                device.app.id if device.app else "",
            )
            self._open = file_dialogs.open_file(
                title=S.MDT_ADD_DEFAULT_LIBRARY,
                filters=["MDT DCA / Symbol Resources", "*.etsapp *.zip"],
            )
        imgui.text_wrapped(S.MDT_ADD_DEFAULT_LIBRARY_HELP)

    def _symbol_library(
        self,
        device: Device,
        project: ProjectService,
        state: _SymbolState,
        view: dict[str, Any],
    ) -> None:
        choices = [(S.MDT_LOAD_DEFAULTS, False)]
        if view["app_id"] == "M0083-A0041":
            choices = [
                (S.MDT_LOAD_FIRST_LIBRARY, False),
                (S.MDT_LOAD_SECOND_LIBRARY, True),
            ]
        try:
            for index, (label, newer) in enumerate(choices):
                if index:
                    same_line_if_fits(button_width(label))
                imgui.begin_disabled(newer not in view["default_libraries"])
                if imgui.button(label):
                    project.load_mdt_default_symbols(device.node_id, new_library=newer)
                    state.include_defaults = True
                    state.error = ""
                imgui.end_disabled()
            imgui.text_wrapped(S.MDT_LIBRARY_HELP)
            _, state.invert_library = checkbox(
                S.MDT_INVERT_LIBRARY, state.invert_library
            )
            if imgui.button(S.MDT_IMPORT_LIBRARY):
                self._import_kind = "library"
                self._import_revision = project.revision
                self._import_invert = state.invert_library
                self._target = (
                    project.generation,
                    device.node_id,
                    0,
                    device.app.id if device.app else "",
                )
                self._open = file_dialogs.open_file(
                    title=S.MDT_IMPORT_LIBRARY, filters=["ZIP", "*.zip"]
                )
            same_line_if_fits(button_width(S.MDT_EXPORT_LIBRARY))
            if imgui.button(S.MDT_EXPORT_LIBRARY):
                self._export = project.export_mdt_symbol_library(device.node_id)
                self._target = (
                    project.generation,
                    device.node_id,
                    0,
                    device.app.id if device.app else "",
                )
                self._save = file_dialogs.save_file(
                    title=S.MDT_EXPORT_LIBRARY,
                    default_path="symbols.zip",
                    filters=["ZIP", "*.zip"],
                )
            imgui.text_wrapped(
                S.MDT_ZIP_HELP.format(
                    first=view["first_custom_slot"] + 1, last=view["slots"]
                )
            )
        except ValueError as exc:
            state.error = str(exc)

    @staticmethod
    def _symbol_overview(state: _SymbolState, view: dict[str, Any], wide: bool) -> None:
        images = {s["slot"]: s for s in (*view["defaults"], *view["symbols"])}
        if imgui.begin_child(
            "##symbol_overview", imgui.ImVec2(0, px(420 if wide else 240))
        ):
            for group, title in view["groups"].items():
                imgui.separator_text(title)
                width = imgui.get_content_region_avail().x
                columns = max(1, int(width / px(220)))
                if not imgui.begin_table(f"##{group}", columns):
                    continue
                for slot in view["slot_info"]:
                    if slot["group"] != group:
                        continue
                    imgui.table_next_column()
                    origin = imgui.get_cursor_screen_pos()
                    width = imgui.get_content_region_avail().x
                    image_size = px(48)
                    text_x = image_size + px(8)
                    wrap_width = max(px(20), width - text_x)
                    height = max(
                        image_size,
                        imgui.calc_text_size(slot["name"], wrap_width=wrap_width).y,
                    ) + px(8)
                    visible = imgui.is_rect_visible(imgui.ImVec2(width, height))
                    selected, _ = imgui.selectable(
                        f"##symbol_{slot['slot']}",
                        state.slot == slot["slot"],
                        size=imgui.ImVec2(width, height),
                    )
                    if selected and not state.drawing:
                        state.slot = slot["slot"]
                    if not visible:
                        continue
                    draw = imgui.get_window_draw_list()
                    item = images.get(slot["slot"])
                    if item:
                        draw.add_rect_filled(
                            origin,
                            imgui.ImVec2(origin.x + image_size, origin.y + image_size),
                            0xFFFFFFFF,
                        )
                        scale = image_size / 64
                        for y, start, end in _symbol_runs(item["grayscale_hex"]):
                            draw.add_rect_filled(
                                imgui.ImVec2(
                                    round(origin.x + start * scale),
                                    round(origin.y + y * scale),
                                ),
                                imgui.ImVec2(
                                    round(origin.x + end * scale),
                                    round(origin.y + (y + 1) * scale),
                                ),
                                0xFF000000,
                            )
                    else:
                        draw.add_rect(
                            origin,
                            imgui.ImVec2(origin.x + image_size, origin.y + image_size),
                            imgui.get_color_u32(imgui.Col_.text_disabled),
                        )
                        draw.add_text(origin, imgui.get_color_u32(imgui.Col_.text), "?")
                    draw.add_text(
                        imgui.get_font(),
                        imgui.get_font_size(),
                        imgui.ImVec2(origin.x + text_x, origin.y),
                        imgui.get_color_u32(imgui.Col_.text),
                        slot["name"],
                        wrap_width,
                    )
                imgui.end_table()
        imgui.end_child()

    def _symbol_editor(
        self,
        device: Device,
        project: ProjectService,
        state: _SymbolState,
        view: dict[str, Any],
    ) -> None:
        slot = view["slot_info"][state.slot]
        imgui.text_wrapped(slot["name"])
        custom = next((s for s in view["symbols"] if s["slot"] == state.slot), None)
        default = next((s for s in view["defaults"] if s["slot"] == state.slot), None)
        item = custom or default
        if state.source != (project.revision, state.slot) and not state.drawing:
            state.pixels = (
                bytearray.fromhex(item["grayscale_hex"])
                if item
                else bytearray(b"\xff" * 4096)
            )
            state.source = (project.revision, state.slot)
        if not slot["editable"]:
            imgui.text_wrapped(S.MDT_SYMBOL_PROTECTED)
        elif custom is None:
            imgui.text_wrapped(S.MDT_DEFAULT_SYMBOL)
        if item is None:
            imgui.text_wrapped(S.MDT_SYMBOL_UNAVAILABLE)
        if not slot["editable"] and item is None:
            return
        imgui.begin_disabled(not slot["editable"])
        _, state.ink = checkbox(S.MDT_DRAW_SYMBOL, state.ink)
        imgui.end_disabled()
        if item is not None or slot["editable"]:
            self._canvas(state, editable=slot["editable"])
        self._symbol_format_help()
        imgui.begin_disabled(not slot["editable"])
        if imgui.button(S.MDT_IMPORT_SYMBOL):
            self._import_kind = "symbol"
            self._target = (
                project.generation,
                device.node_id,
                state.slot,
                device.app.id if device.app else "",
            )
            self._open = file_dialogs.open_file(
                title=S.MDT_IMPORT_SYMBOL,
                filters=["PNG / BMP / DATA", "*.png *.bmp *.data"],
            )
        imgui.end_disabled()
        imgui.begin_disabled(item is None)
        if imgui.button(S.MDT_EXPORT_SYMBOL):
            self._export = export_symbol_image(bytes(state.pixels))
            self._save = file_dialogs.save_file(
                title=S.MDT_EXPORT_SYMBOL,
                default_path=f"symbol-{state.slot + 1}.png",
                filters=["PNG", "*.png"],
            )
        imgui.end_disabled()
        imgui.begin_disabled(not slot["editable"] or custom is None)
        if imgui.button(S.PLUGIN_PARAMS_REMOVE):
            project.set_mdt_symbol(device.node_id, state.slot, None)
        imgui.end_disabled()
        imgui.begin_disabled(not slot["editable"] or default is None)
        if imgui.button(S.MDT_RESTORE_SYMBOL):
            project.restore_mdt_symbol(device.node_id, state.slot)
        imgui.end_disabled()

    @staticmethod
    def _symbol_format_help() -> None:
        imgui.text_wrapped(S.MDT_SYMBOL_IMPORT_HINT)
        if collapsing_header(S.MDT_SYMBOL_FORMAT_DETAILS + "###symbol_format"):
            for text in (
                S.MDT_SYMBOL_IMAGE_HELP,
                S.MDT_SYMBOL_COLOR_HELP,
                S.MDT_SYMBOL_DATA_HELP,
                S.MDT_SYMBOL_EXPORT_HELP,
                S.MDT_SYMBOL_IMPORT_PROJECT_HELP.format(button=S.MDT_PROGRAM_SYMBOLS),
            ):
                imgui.text_wrapped(text)

    def _visucontrol(self, device: Device, project: ProjectService) -> None:
        state = self._states.setdefault(device.node_id, _SymbolState())
        if imgui.button(S.PLUGIN_NATIVE_IMPORT):
            self._import_kind = "visucontrol"
            self._target = (
                project.generation,
                device.node_id,
                0,
                device.app.id if device.app else "",
            )
            self._open = file_dialogs.open_file(
                title=S.PLUGIN_NATIVE_IMPORT, filters=["VisuControl", "*.etsvcp"]
            )
        if imgui.button(S.PLUGIN_NATIVE_EXPORT):
            try:
                self._export = project.export_mdt_visucontrol(device.node_id)
                self._target = (
                    project.generation,
                    device.node_id,
                    0,
                    device.app.id if device.app else "",
                )
                self._save = file_dialogs.save_file(
                    title=S.PLUGIN_NATIVE_EXPORT,
                    default_path="configuration.etsvcp",
                    filters=["VisuControl", "*.etsvcp"],
                )
                state.error = ""
            except ValueError as exc:
                state.error = str(exc)
        if state.error:
            imgui.text_wrapped(state.error)
        for link in device.last_lost_links:
            imgui.text_wrapped(str(link["address"]))

    def _symbol_operations(
        self,
        device: Device,
        project: ProjectService,
        state: _SymbolState,
        view: dict[str, Any],
    ) -> None:
        current = tuple(
            sorted(
                (s["slot"], s["data_hex"])
                for s in {
                    s["slot"]: s for s in (*view["defaults"], *view["symbols"])
                }.values()
            )
        )
        if current != state.operation_source:
            state.comparison.clear()
        if state.operation is not None and state.operation.ended.done():
            result = state.operation.ended.result()
            state.error = str(result.message or "")
            payload: object = result.result
            if isinstance(payload, dict) and current == state.operation_source:
                state.comparison = cast(dict[int, bool], payload)
            state.operation = None
        complete = (
            len({s["slot"] for s in (*view["defaults"], *view["symbols"])})
            == view["slots"]
        )
        _, state.include_defaults = wrapped_checkbox(
            S.MDT_INCLUDE_DEFAULTS, state.include_defaults
        )
        imgui.begin_disabled(self._queue is None or state.operation is not None)
        imgui.begin_disabled(not complete)
        if imgui.button(S.MDT_COMPARE_SYMBOLS):
            self._start_symbols(
                device, project, state, view, False, include_defaults=True
            )
        same_line_if_fits(button_width(S.MDT_SYNCHRONIZE_SYMBOLS))
        if imgui.button(S.MDT_SYNCHRONIZE_SYMBOLS):
            self._start_symbols(
                device,
                project,
                state,
                view,
                False,
                include_defaults=True,
                synchronize=True,
            )
        imgui.end_disabled()
        imgui.text_wrapped(S.MDT_SYNC_HELP)
        imgui.begin_disabled(
            not complete if state.include_defaults else not view["symbols"]
        )
        if imgui.button(S.MDT_PROGRAM_SYMBOLS):
            state.pending = True
        imgui.end_disabled()
        imgui.end_disabled()
        title = S.MDT_PROGRAM_SYMBOLS + "###mdt_symbols_confirm"
        if state.pending:
            imgui.open_popup(title)
        if begin_dialog(title):
            imgui.text_wrapped(
                S.MDT_PROGRAM_ALL_CONFIRM
                if state.include_defaults
                else S.MDT_PROGRAM_CONFIRM
            )
            clicked = dialog_buttons(S.DALI_CONTINUE, S.BTN_CANCEL)
            if clicked == 0:
                self._start_symbols(
                    device,
                    project,
                    state,
                    view,
                    True,
                    include_defaults=state.include_defaults,
                )
            if clicked is not None:
                state.pending = False
                imgui.close_current_popup()
            end_dialog()
        for slot, match in state.comparison.items():
            imgui.text(
                f"{view['slot_info'][slot]['name']}: {S.GENERIC_PLUGIN_MATCH if match else S.GENERIC_PLUGIN_MISMATCH}"
            )

    def _start_symbols(
        self,
        device: Device,
        project: ProjectService,
        state: _SymbolState,
        view: dict[str, Any],
        writes: bool,
        *,
        include_defaults: bool,
        synchronize: bool = False,
    ) -> None:
        if self._queue is not None:
            try:
                symbols = project.mdt_symbol_payload(
                    device.node_id, include_defaults=include_defaults
                )
                state.error = ""
                state.comparison.clear()
                effective = {
                    s["slot"]: s for s in (*view["defaults"], *view["symbols"])
                }
                state.operation_source = tuple(
                    sorted((s["slot"], s["data_hex"]) for s in effective.values())
                )
                state.operation = self._queue(
                    device.node_id,
                    "mdt_symbols",
                    writes=writes,
                    symbols=symbols,
                    include_defaults=include_defaults,
                    synchronize=synchronize,
                )
            except ValueError as exc:
                state.error = str(exc)

    def _layout(self, device: Device, project: ProjectService) -> None:
        import copy

        node_id = device.node_id
        try:
            view = project.mdt_layout(node_id)
        except ValueError as exc:
            imgui.text_wrapped(str(exc))
            return
        state = self._layouts.setdefault(
            node_id, {"revision": -1, "page": 0, "tile": 0, "error": ""}
        )
        if not view["pages"]:
            imgui.text_wrapped(S.MDT_NO_PAGES)
            return
        if state["revision"] != project.revision:
            state["pages"] = copy.deepcopy(view["pages"])
            state["page"] = min(state["page"], len(view["pages"]) - 1)
            state["revision"] = project.revision
        labels = S.MDT_LAYOUT_FIELDS
        _, page_number = imgui.slider_int(
            labels["page"], state["page"] + 1, 1, len(view["pages"])
        )
        state["page"] = page_number - 1
        page = state["pages"][state["page"]]
        if page["status"]:
            imgui.text(S.MDT_STATUS_NIGHT if page["number"] == -1 else S.MDT_STATUS_DAY)
        else:
            _, page["name"] = imgui.input_text(S.PLUGIN_PARAMS_NAME, page["name"])
        tiles = cast(list[dict[str, int]], list(page["tiles"]))
        page["tiles"] = tiles
        if imgui.button(S.BTN_ADD) and len(tiles) < 12:
            tiles.append(
                {
                    "x": 19,
                    "y": 29,
                    "kind": 2,
                    "function": 0,
                    "transparency": 255,
                    "color": 0,
                }
            )
            state["tile"] = len(tiles) - 1
        if tiles:
            state["tile"] = min(state["tile"], len(tiles) - 1)
            _, selected = imgui.slider_int(
                labels["tile"], state["tile"] + 1, 1, len(tiles)
            )
            state["tile"] = selected - 1
            tile = tiles[state["tile"]]
            _, tile["kind"] = combo(
                S.PREFLIGHT_COL_SIZE,
                tile["kind"],
                [f"{w} x {h}" for w, h in TILE_SIZES],
            )
            _, opacity_index = combo(
                labels["transparency"],
                TRANSPARENCY.index(tile["transparency"]),
                [S.MDT_DEFAULT_VALUE, *(f"{n}%" for n in range(0, 101, 10))],
            )
            tile["transparency"] = TRANSPARENCY[opacity_index]
            for key in ("x", "y") if page["status"] else ("x", "y", "color"):
                _, tile[key] = imgui.input_int(labels.get(key, key.upper()), tile[key])
            key = "status_functions" if page["status"] else "functions"
            choices = [{"number": 0, "name": ""}, *view[key]]
            index = next(
                (i for i, f in enumerate(choices) if f["number"] == tile["function"]), 0
            )
            changed, index = combo(
                S.PANEL_FUNCTION,
                index,
                [f"{f['number']}: {f['name']}" for f in choices],
            )
            if changed:
                tile["function"] = int(choices[index]["number"])
            if imgui.button(S.PLUGIN_PARAMS_REMOVE):
                tiles.pop(state["tile"])
        self._layout_preview(page, state, view["height"])
        if imgui.button(S.BTN_SAVE):
            try:
                project.set_mdt_layout(
                    node_id, page["number"], page["name"], page["orientation"], tiles
                )
                state["error"] = ""
            except ValueError as exc:
                state["error"] = str(exc)
        if state["error"]:
            imgui.text_wrapped(state["error"])

    @staticmethod
    def _layout_preview(
        page: dict[str, Any], state: dict[str, Any], height: int
    ) -> None:
        width, height = (720, height) if page["orientation"] == "V" else (height, 720)
        scale = min(px(300), imgui.get_content_region_avail().x) / width
        origin = imgui.get_cursor_screen_pos()
        imgui.invisible_button("##layout", imgui.ImVec2(width * scale, height * scale))
        draw = imgui.get_window_draw_list()
        draw.add_rect_filled(
            origin,
            imgui.ImVec2(origin.x + width * scale, origin.y + height * scale),
            0xFF202020,
        )
        mouse = imgui.get_mouse_pos()
        for i, tile in enumerate(cast(list[dict[str, int]], page["tiles"])):
            if not 0 <= tile["kind"] < len(TILE_SIZES):
                continue
            w, h = TILE_SIZES[tile["kind"]]
            x, y = Tile(**tile).position(
                page["orientation"], height if page["orientation"] == "V" else width
            )
            a = imgui.ImVec2(origin.x + x * scale, origin.y + y * scale)
            b = imgui.ImVec2(a.x + w * scale, a.y + h * scale)
            draw.add_rect_filled(a, b, 0xFF908050 if i == state["tile"] else 0xFF605040)
            draw.add_text(a, 0xFFFFFFFF, str(tile["function"]))
            if (
                imgui.is_item_clicked()
                and a.x <= mouse.x <= b.x
                and a.y <= mouse.y <= b.y
            ):
                state["tile"] = i

    @staticmethod
    def _canvas(state: _SymbolState, *, editable: bool) -> None:
        size = min(px(320), max(px(64), imgui.get_content_region_avail().x))
        origin = imgui.get_cursor_screen_pos()
        imgui.invisible_button("##symbol", imgui.ImVec2(size, size))
        draw = imgui.get_window_draw_list()
        scale = size / 64
        for y in range(64):
            for x in range(64):
                black = state.pixels[y * 64 + x] < 128
                draw.add_rect_filled(
                    imgui.ImVec2(origin.x + x * scale, origin.y + y * scale),
                    imgui.ImVec2(
                        origin.x + (x + 1) * scale, origin.y + (y + 1) * scale
                    ),
                    0xFF000000 if black else 0xFFFFFFFF,
                )
        if editable and imgui.is_item_active() and imgui.is_mouse_down(0):
            mouse = imgui.get_mouse_pos()
            x, y = int((mouse.x - origin.x) / scale), int((mouse.y - origin.y) / scale)
            if 0 <= x < 64 and 0 <= y < 64:
                state.pixels[y * 64 + x] = 0 if state.ink else 255
                state.drawing = True

    @staticmethod
    def _finish_drawing(
        state: _SymbolState, node_id: int, project: ProjectService
    ) -> None:
        state.drawing = False
        try:
            project.set_mdt_symbol(node_id, state.slot, state.pixels.hex())
            state.error = ""
        except ValueError as exc:
            state.error = str(exc)

    def _poll(self, project: ProjectService) -> None:
        if self._open is not None and self._open.ready():
            result = self._open.result()
            self._open = None
            if (
                result
                and self._target is not None
                and self._target[0] == project.generation
            ):
                _, node_id, slot, application_id = self._target
                device = project.find_device_by_node_id(node_id)
                if (
                    device is None
                    or device.app is None
                    or device.app.id != application_id
                ):
                    return
                state = self._states.setdefault(node_id, _SymbolState())
                try:
                    if self._import_kind == "default_library":
                        from xknxeditor.vendor.mdt.symbol_resources import MAX_APP_SIZE

                        with Path(result[0]).open("rb") as stream:
                            data = stream.read(MAX_APP_SIZE + 1)
                    else:
                        data = Path(result[0]).read_bytes()
                    if self._import_kind in ("library", "default_library"):
                        if self._import_revision != project.revision:
                            raise ValueError(S.MDT_IMPORT_STALE)
                        if self._import_kind == "default_library":
                            project.import_mdt_default_library(node_id, data)
                        else:
                            project.import_mdt_symbol_library(
                                node_id, data, invert=self._import_invert
                            )
                    elif self._import_kind == "visucontrol":
                        project.import_mdt_visucontrol(node_id, data)
                    else:
                        pixels = import_symbol_image(data)
                        project.set_mdt_symbol(node_id, slot, pixels.hex())
                    state.error = ""
                except (OSError, ValueError) as exc:
                    state.error = str(exc)
        if self._save is not None and self._save.ready():
            result = self._save.result()
            self._save = None
            if result:
                try:
                    Path(result).write_bytes(self._export)
                except OSError as exc:
                    if self._target is not None:
                        self._states.setdefault(
                            self._target[1], _SymbolState()
                        ).error = str(exc)
