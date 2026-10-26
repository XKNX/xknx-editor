"""Mass Linker: link many communication objects at once, without ordering two parallel lists.

Instead of two positional lists, each object is a table row and its target is set explicitly
per row, so the order of rows never matters:

- **Object <> GA**: rows are com-objects; each row picks an existing group address (inline combo).
  Helpers fill the targets fast: "assign sequential from <address>" and "match by name".
- **Object <> Object**: rows are source com-objects; each row picks a target com-object. Every
  pair gets a freshly created group address (name template + optional start address).
- **Log**: chronological record of every mass operation.

The panel is imgui-only glue: the assignment logic is the pure :func:`sequential_targets` /
:func:`match_by_name`, and the create/link work is injected callbacks (implemented on the project
plugin over ``ProjectService``), so the logic is testable without a UI.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.device import address_order, device_label
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from editor_gui.widgets.tabs import begin_tab_bar
from editor_gui.widgets.x_button import x_button

if TYPE_CHECKING:
    from editor_gui.device import ComObject, Device
    from editor_gui.plugins.project.service import Assignment
    from xknxeditor.proj.core.addressing import GroupAddressStyle
    from xknxeditor.proj.core.service import GroupRangeInfo

LinkResult = tuple[int, list[str]]  # (linked count, error messages)
# How long a note under the row buttons stays.
_NOTE_SECONDS = 4.0


def _dpt_label(co: ComObject) -> str:
    """Compact datapoint-type label for the table, e.g. '1.1' (or '-' when unset)."""
    dpt = getattr(co, "dpt", None)
    major = getattr(dpt, "major", None)
    if major is None:
        return getattr(dpt, "name", "") or "-"
    minor = getattr(dpt, "minor", None)
    return f"{major}.{minor}" if minor else str(major)


def _obj_major(co: ComObject) -> int | None:
    """The com-object's datapoint-type major number (for DPT compatibility), or None."""
    return getattr(getattr(co, "dpt", None), "major", None)


def _co_text(co: ComObject) -> str:
    """Distinguishable com-object label: number, name, function, DPT and size — so objects that
    share a channel name (e.g. several 'G: Schlafzimmer') can still be told apart by their function
    ('Switch', 'Status', ...) in lists/combos."""
    parts = [f"#{co.number}", co.name or "?"]
    function = getattr(co, "function_text", "")
    if function and function != co.name:
        parts.append(function)
    dpt = _dpt_label(co)
    if dpt != "-":
        parts.append(dpt)
    if getattr(co, "object_size", ""):
        parts.append(co.object_size)
    return "  ".join(parts)


_STATUS_STYLE: dict[str, tuple[imgui.ImVec4, str]] = {
    "ready": (imgui.ImVec4(0.45, 0.80, 0.50, 1.0), "OK"),
    "ambiguous": (imgui.ImVec4(0.90, 0.75, 0.35, 1.0), "!"),
    "incompatible": (imgui.ImVec4(0.90, 0.45, 0.45, 1.0), "x"),
    "unmatched": (imgui.ImVec4(0.55, 0.55, 0.55, 1.0), "-"),
}


def _status_of(
    target_ga: int | None, obj_major: int | None, ga_major: int | None
) -> str:
    """Status of a chosen target: unmatched (no target), incompatible (DPT major differs),
    ambiguous (a DPT major is unknown), or ready (majors match)."""
    if target_ga is None:
        return "unmatched"
    if obj_major is None or ga_major is None:
        return "ambiguous"
    return "ready" if obj_major == ga_major else "incompatible"


def _dpt_label_from_token(token: str | None) -> str:
    """Compact label of a group-address DPT token ('DPST-10-1' -> '10.1', 'DPT-1' -> '1'), as the
    objects' DPT column shows it; empty when unknown."""
    if not token:
        return ""
    parts = token.replace("DPST-", "").replace("DPT-", "").split("-")
    return ".".join(parts) if all(part.isdigit() for part in parts) else ""


def _dpt_major_from_token(token: str | None) -> int | None:
    """Major number from a group-address DPT token ('DPST-1-1'/'DPT-1' -> 1); None if unknown."""
    if not token:
        return None
    parts = token.replace("DPST-", "").replace("DPT-", "").split("-")
    return int(parts[0]) if parts and parts[0].isdigit() else None


def autopair(
    obj_name: str, obj_major: int | None, gas: list[tuple[int, str, int | None]]
) -> tuple[int | None, str]:
    """Best existing group address for one object by name, constrained by DPT major.

    ``gas`` is ``(ga_id, ga_name, ga_major)``. Returns ``(ga_id, status)`` where status is:
    ``ready`` (name match + compatible/known-equal DPT), ``ambiguous`` (matched but DPT unknown on
    a side), ``incompatible`` (only wrong-major matches exist — not assigned, must be fixed), or
    ``unmatched`` (no name match). Exact name wins over substring; first match in order wins.
    """
    key = obj_name.strip().lower()
    if not key:
        return None, "unmatched"
    named = [
        (gid, gname.strip().lower(), gmaj) for gid, gname, gmaj in gas if gname.strip()
    ]
    exact = [(gid, gmaj) for gid, low, gmaj in named if low == key]
    # Substring matching only for names long enough to be meaningful (avoids "on"/"1" matching
    # half the project); a blank GA name is already excluded above.
    contains = (
        [(gid, gmaj) for gid, low, gmaj in named if key in low or low in key]
        if len(key) >= 3
        else []
    )
    candidates = exact or contains
    if not candidates:
        return None, "unmatched"
    if obj_major is None:
        return candidates[0][0], "ambiguous"  # can't verify DPT
    compatible = [gid for gid, gmaj in candidates if gmaj == obj_major]
    if len(compatible) == 1:
        return compatible[0], "ready"
    if len(compatible) > 1:
        return compatible[0], "ambiguous"  # several equally valid targets -> confirm
    unknown = [gid for gid, gmaj in candidates if gmaj is None]
    if unknown:
        return unknown[0], "ambiguous"
    return None, "incompatible"  # only different-major matches -> block


def sequential_targets(
    existing: list[tuple[int, int]], start_value: int, count: int
) -> list[int | None]:
    """Assign the first ``count`` existing group addresses with value >= ``start_value``.

    ``existing`` is ``(raw_value, ga_id)`` pairs (any order). Returns a list of ``count`` ga-ids
    (``None`` once the existing addresses run out), taken in ascending raw-value order — so N
    objects link to the next N existing addresses from a chosen start, no manual ordering needed.
    """
    ordered = sorted(v for v in existing if v[0] >= start_value)
    ids = [ga_id for _value, ga_id in ordered]
    return [ids[i] if i < len(ids) else None for i in range(count)]


GA_MAX_VALUE = 65535  # a KNX group address is a 16-bit value; 0 (0/0/0) is reserved.


def first_free_values(
    used: set[int], start: int, count: int, *, limit: int = GA_MAX_VALUE
) -> list[int]:
    """The next ``count`` group-address raw values >= ``max(start, 1)``, skipping ``used`` and 0.

    Mirrors the bulk linker's suggestion for object<->object links ("first available"
    address). ``used`` is not mutated; already-suggested values are skipped so a batch never
    proposes the same address twice. Never returns a value above ``limit`` (the 16-bit maximum):
    if the address space is exhausted, fewer than ``count`` values are returned.
    """
    taken = set(used)
    out: list[int] = []
    value = max(start, 1)
    while len(out) < count and value <= limit:
        if value not in taken:
            out.append(value)
            taken.add(value)
        value += 1
    return out


def match_by_name(name: str, existing: list[tuple[int, str]]) -> int | None:
    """Pick the existing group address whose name best matches ``name`` (or ``None``).

    Prefers a case-insensitive exact match, then a two-way substring containment. ``existing`` is
    ``(ga_id, ga_name)`` pairs; the first best match in list order wins.
    """
    key = name.strip().lower()
    if not key:
        return None
    contains: int | None = None
    for ga_id, ga_name in existing:
        low = ga_name.strip().lower()
        if low == key:
            return ga_id
        if contains is None and (key in low or low in key):
            contains = ga_id
    return contains


def _hint(text: str) -> None:
    """A greyed hint, wrapped to the panel so it stays readable in narrow layouts."""
    imgui.push_style_color(
        imgui.Col_.text, imgui.get_style_color_vec4(imgui.Col_.text_disabled)
    )
    imgui.text_wrapped(text)
    imgui.pop_style_color()


@dataclass
class _Row:
    """One source com-object with its chosen target (a group-address id or a target com-object)."""

    device_name: str
    co: ComObject
    target_ga: int | None = None
    target_co: ComObject | None = None
    status: str = "unmatched"  # unmatched | ready | ambiguous | incompatible
    ga_addr: str = (
        ""  # object<->object: per-pair GA address override (empty = suggested)
    )
    ga_name: str = ""  # object<->object: per-pair GA name override (empty = suggested)
    target_device: str = (
        ""  # object<->object: the target object's device (address and name)
    )

    @property
    def label(self) -> str:
        return f"{self.device_name}  {_co_text(self.co)}"


@dataclass
class _LogEntry:
    time: str
    text: str


@dataclass
class _FlatGa:
    ga_id: int
    value: int
    text: str
    name: str
    major: int | None = None
    dpt: str = ""  # compact DPT label ('10.1'), empty when the address has none


class MassLinkerPanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_range_tree: Callable[[], list[GroupRangeInfo]],
        is_open: Callable[[], bool],
        on_link_ga_co: Callable[[list[tuple[ComObject, int]]], LinkResult],
        on_link_co_co: Callable[
            [list[tuple[ComObject, ComObject, str, str]]], LinkResult
        ],
        get_selected_node_id: Callable[[], int | None],
        group_style: Callable[[], GroupAddressStyle],
        get_links_for_co: Callable[[int], list[Assignment]] | None = None,
    ) -> None:
        self._get_devices = get_devices
        self._get_range_tree = get_range_tree
        self._is_open = is_open
        self._on_link_ga_co = on_link_ga_co
        self._on_link_co_co = on_link_co_co
        self._get_selected_node_id = get_selected_node_id
        self._group_style = group_style
        # Resolves a com-object's existing links (id, group_address_id, is_sending) so a freshly
        # added object shows the group address it is ALREADY linked to instead of "no target".
        self._get_links_for_co = get_links_for_co

        self._rows1: list[_Row] = []  # Object <> GA rows
        self._rows2: list[_Row] = []  # Object <> Object rows
        self._seq_start = ""  # "assign sequential from" address (tab 1)
        self._name_template = "{source}"
        self._co_start = ""  # start address for created GAs (tab 2)

        self._log_entries: list[_LogEntry] = []
        self._result: _LogEntry | None = None
        self._result_errors: list[str] = []
        self._was_open = False

        # Object picker state (adds rows to whichever list is active).
        self._pick_into: list[_Row] | None = None
        self._pick_obj_selected: set[int] = set()
        self._pick_filter = ""
        self._tgt_filter = ""  # shared search filter for the per-row target combo
        # The target combo lists only group addresses of the object's DPT (or without one).
        self._dpt_only = True
        self._problems_only = False  # table filter: hide rows that are already ready
        # Defer open_popup() to render()'s root scope (must match begin_popup_modal's id scope).
        self._open_obj_pending = False
        self._open_result_pending = False
        self._pending_remove: tuple[int, int] | None = None  # (tab, row index)
        # A short note under the row buttons (e.g. why '+ Selected Device' added nothing), shown
        # until the given time.
        self._notice: tuple[str, float] | None = None

    # -- rendering ---------------------------------------------------------

    def render(self) -> None:
        open_now = self._is_open()
        if self._was_open and not open_now:
            self._reset()  # dropped project: queued db-ids are now stale
        self._was_open = open_now
        if not open_now:
            _hint(S.ML_NO_PROJECT)
            return
        if begin_tab_bar("##mass_linker_tabs"):
            if imgui.begin_tab_item(S.ML_TAB_GA_CO)[0]:
                self._render_ga_co_tab()
                imgui.end_tab_item()
            if imgui.begin_tab_item(S.ML_TAB_CO_CO)[0]:
                self._render_co_co_tab()
                imgui.end_tab_item()
            if imgui.begin_tab_item(S.ML_TAB_LOG)[0]:
                self._render_log_tab()
                imgui.end_tab_item()
            imgui.end_tab_bar()
        if self._open_obj_pending:
            imgui.open_popup(S.ML_PICK_OBJECTS_TITLE)
            self._open_obj_pending = False
        if self._open_result_pending:
            imgui.open_popup(S.ML_RESULT_TITLE)
            self._open_result_pending = False
        self._render_object_picker()
        self._render_result_popup()

    def _reset(self) -> None:
        self._rows1.clear()
        self._rows2.clear()

    # -- Object <> GA tab --------------------------------------------------

    def _render_ga_co_tab(self) -> None:
        names = _button_names()
        _guide(
            S.ML_GUIDE_GA_CO,
            [
                S.ML_GUIDE_GA_CO_1.format(**names),
                S.ML_GUIDE_GA_CO_2.format(**names),
                S.ML_GUIDE_GA_CO_3.format(**names),
            ],
        )
        self._row_toolbar(self._rows1)
        flat = self._flat_gas()
        by_id = {g.ga_id: g for g in flat}
        self._refresh_statuses(
            by_id
        )  # keep statuses fresh + drop targets deleted meanwhile

        # Exception-first summary: the table is a confirmation, so surface the problem counts.
        counts = {"ready": 0, "ambiguous": 0, "incompatible": 0, "unmatched": 0}
        for r in self._rows1:
            counts[r.status] = counts.get(r.status, 0) + 1
        if self._rows1:
            imgui.text_disabled(
                S.ML_SUMMARY.format(
                    ready=counts["ready"],
                    amb=counts["ambiguous"],
                    bad=counts["incompatible"],
                    none=counts["unmatched"],
                )
            )
            _continue_row(_checkbox_width(S.ML_SHOW_PROBLEMS))
            _, self._problems_only = checkbox(S.ML_SHOW_PROBLEMS, self._problems_only)

        # Room below for the buttons, more where they wrap onto further lines (a phone).
        below = (
            px(96.0)
            + _extra_lines(
                [
                    _button_width(S.ML_MATCH_NAME),
                    _field_width(px(120.0), S.ML_SEQ_FROM),
                    _button_width(S.ML_ASSIGN_SEQ),
                ]
            )
            * imgui.get_frame_height_with_spacing()
        )
        table_h = max(imgui.get_content_region_avail().y - below, px(120.0))
        if imgui.begin_child("##ml1_table", imgui.ImVec2(0.0, table_h)):
            self._render_ga_table(flat, by_id)
        imgui.end_child()
        self._apply_pending_remove()

        # Re-pair by name (DPT-constrained) or link to a contiguous existing block from a start.
        if imgui.button(S.ML_MATCH_NAME):
            self._match_all(flat)
        _continue_row(_field_width(px(120.0), S.ML_SEQ_FROM))
        imgui.set_next_item_width(px(120.0))
        _, self._seq_start = imgui.input_text(S.ML_SEQ_FROM, self._seq_start)
        _continue_row(_button_width(S.ML_ASSIGN_SEQ))
        if imgui.button(S.ML_ASSIGN_SEQ):
            self._assign_sequential(flat, by_id)
        # Link everything with a target that isn't blocked by a DPT conflict.
        linkable = [
            r
            for r in self._rows1
            if r.target_ga in by_id and r.status != "incompatible"
        ]
        imgui.begin_disabled(not linkable)
        if imgui.button(S.ML_LINK_ALL.format(count=len(linkable)), imgui.ImVec2(-1, 0)):
            self._do_link_ga_co(linkable)
        imgui.end_disabled()

    def _render_ga_table(self, flat: list[_FlatGa], by_id: dict[int, _FlatGa]) -> None:
        if not self._rows1:
            _hint(S.ML_TABLE_EMPTY)
            return
        flags = (
            imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.reorderable
        )
        if not begin_data_table("##ml1", 6, flags, scroll_when_narrow=True):
            return
        imgui.table_setup_column("##st", imgui.TableColumnFlags_.width_fixed, px(22.0))
        imgui.table_setup_column(
            S.ML_COL_DEVICE, imgui.TableColumnFlags_.width_stretch, 0.26
        )
        imgui.table_setup_column(
            S.ML_COL_OBJECT, imgui.TableColumnFlags_.width_stretch, 0.26
        )
        imgui.table_setup_column(
            S.ML_COL_DPT, imgui.TableColumnFlags_.width_fixed, px(52.0)
        )
        imgui.table_setup_column(
            S.ML_COL_TARGET_GA, imgui.TableColumnFlags_.width_stretch, 0.36
        )
        imgui.table_setup_column("##rm", imgui.TableColumnFlags_.width_fixed, px(26.0))
        data_table_headers()
        for i, row in enumerate(self._rows1):
            if self._problems_only and row.status == "ready":
                continue
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            color, glyph = _STATUS_STYLE.get(row.status, _STATUS_STYLE["unmatched"])
            imgui.text_colored(color, glyph)
            if imgui.is_item_hovered():
                imgui.set_tooltip(_status_tip(row.status))
            imgui.table_set_column_index(1)
            imgui.text_unformatted(row.device_name)
            imgui.table_set_column_index(2)
            imgui.text_unformatted(_co_text(row.co))
            imgui.table_set_column_index(3)
            imgui.text_disabled(_dpt_label(row.co))
            imgui.table_set_column_index(4)
            self._target_ga_combo(i, row, flat, by_id)
            imgui.table_set_column_index(5)
            if _remove_button(f"rm1{i}"):
                self._pending_remove = (1, i)
        imgui.end_table()

    def _target_ga_combo(
        self, i: int, row: _Row, flat: list[_FlatGa], by_id: dict[int, _FlatGa]
    ) -> None:
        cur = by_id.get(row.target_ga) if row.target_ga is not None else None
        label = f"{cur.text}  {cur.name}" if cur else S.ML_TARGET_NONE
        imgui.set_next_item_width(-1)
        if begin_combo(f"##tga{i}", label, imgui.ComboFlags_.height_large):
            self._tgt_filter = filter_box(
                f"##tgaflt{i}", S.ML_FILTER_HINT, self._tgt_filter
            )
            _, self._dpt_only = checkbox(S.ML_MATCHING_DPT_ONLY, self._dpt_only)
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.ML_MATCHING_DPT_TIP)
            flt = self._tgt_filter.strip().lower()
            if imgui.selectable(S.ML_TARGET_NONE, row.target_ga is None)[0]:
                self._set_target(row, None, by_id)
            obj_major = _obj_major(row.co)
            for g in flat:
                if self._dpt_only:
                    # Same main type; an address without one takes the object's when linked.
                    if None not in (obj_major, g.major) and g.major != obj_major:
                        continue
                    text = f"{g.text}  {g.name}"
                else:
                    text = f"{g.text}  {g.name}  ({g.dpt or S.ML_GA_NO_DPT})"
                if flt and flt not in text.lower():
                    continue
                if imgui.selectable(f"{text}##g{g.ga_id}", g.ga_id == row.target_ga)[0]:
                    self._set_target(row, g.ga_id, by_id)
            imgui.end_combo()

    @staticmethod
    def _set_target(row: _Row, ga_id: int | None, by_id: dict[int, _FlatGa]) -> None:
        row.target_ga = ga_id
        ga_major = by_id[ga_id].major if ga_id in by_id else None
        row.status = _status_of(ga_id, _obj_major(row.co), ga_major)

    def _refresh_statuses(self, by_id: dict[int, _FlatGa]) -> None:
        """Recompute the status of assigned rows from the current tree; a target that was deleted
        meanwhile is cleared so it can't be linked to a dead id. Unassigned rows keep their status
        (unmatched/incompatible) from the last pairing until the user re-pairs.

        No group addresses at all means the project is busy (e.g. rebuilding its devices after a
        language switch) and cannot be read right now: the targets stay until it can."""
        if not by_id:
            return
        for row in self._rows1:
            if row.target_ga is None:
                continue
            if row.target_ga not in by_id:
                row.target_ga = None
                row.status = "unmatched"
            else:
                self._set_target(row, row.target_ga, by_id)

    def _autopair_rows(self, flat: list[_FlatGa], *, only_empty: bool) -> None:
        """Fill row targets by DPT-constrained name matching. Recomputes status for every row;
        only rows without a target are (re)assigned unless ``only_empty`` is False."""
        gas = [(g.ga_id, g.name, g.major) for g in flat]
        by_id = {g.ga_id: g for g in flat}
        for row in self._rows1:
            if only_empty and row.target_ga is not None:
                self._set_target(row, row.target_ga, by_id)  # refresh status only
                continue
            ga_id, status = autopair(row.co.name, _obj_major(row.co), gas)
            row.target_ga = ga_id
            row.status = status

    def _match_all(self, flat: list[_FlatGa]) -> None:
        self._autopair_rows(flat, only_empty=False)

    def _assign_sequential(
        self, flat: list[_FlatGa], by_id: dict[int, _FlatGa]
    ) -> None:
        from xknxeditor.proj.core.addressing import parse_ga

        try:
            start = parse_ga(self._seq_start.strip(), self._group_style())
        except (ValueError, IndexError):
            start = 0
        targets = sequential_targets(
            [(g.value, g.ga_id) for g in flat], start, len(self._rows1)
        )
        for row, ga_id in zip(self._rows1, targets, strict=True):
            if ga_id is not None:
                self._set_target(row, ga_id, by_id)

    # -- Object <> Object tab ----------------------------------------------

    def _render_co_co_tab(self) -> None:
        names = _button_names()
        _guide(
            S.ML_GUIDE_CO_CO,
            [
                S.ML_GUIDE_CO_CO_1.format(**names),
                S.ML_GUIDE_CO_CO_2.format(**names),
                S.ML_GUIDE_CO_CO_3.format(**names),
            ],
        )
        self._row_toolbar(self._rows2)
        suggestions = self._co_suggestions()
        # Room below for the fields, more where they wrap onto a further line (a phone).
        below = (
            px(116.0)
            + _extra_lines(
                [
                    _field_width(px(220.0), S.ML_NAME_TEMPLATE),
                    _field_width(px(140.0), S.ML_START_ADDRESS),
                ]
            )
            * imgui.get_frame_height_with_spacing()
        )
        table_h = max(imgui.get_content_region_avail().y - below, px(120.0))
        if imgui.begin_child("##ml2_table", imgui.ImVec2(0.0, table_h)):
            self._render_co_table(suggestions)
        imgui.end_child()
        self._apply_pending_remove()

        imgui.set_next_item_width(px(220.0))
        _, self._name_template = imgui.input_text(
            S.ML_NAME_TEMPLATE, self._name_template
        )
        _continue_row(_field_width(px(140.0), S.ML_START_ADDRESS))
        imgui.set_next_item_width(px(140.0))
        _, self._co_start = imgui.input_text(S.ML_START_ADDRESS, self._co_start)
        paired = [r for r in self._rows2 if r.target_co is not None]
        imgui.begin_disabled(not paired)
        if imgui.button(S.ML_LINK_ALL.format(count=len(paired)), imgui.ImVec2(-1, 0)):
            self._do_link_co_co(suggestions)
        imgui.end_disabled()

    def _co_suggestions(self) -> dict[int, tuple[str, str]]:
        """Per-row suggested (group-address, name) for paired object<->object rows, keyed by row
        index. Address = first free value from the (optional) start; name = the template. These are
        shown as editable defaults; a non-empty per-row override wins over the suggestion."""
        import contextlib

        from xknxeditor.proj.core.addressing import format_ga, parse_ga

        style = self._group_style()
        flat = self._flat_gas()
        used = {g.value for g in flat}
        # Count existing GA names so generated names stay unique across the whole project (and the
        # batch): a repeated base name gets a "(2)", "(3)", ... suffix.
        name_counts: dict[str, int] = {}
        for g in flat:
            key = g.name.strip().lower()
            if key:
                name_counts[key] = name_counts.get(key, 0) + 1
        # A user-typed address on any row blocks that value for other rows' suggestions.
        for row in self._rows2:
            typed = row.ga_addr.strip()
            if typed:
                with contextlib.suppress(ValueError, IndexError):
                    used.add(parse_ga(typed, style))
        try:
            start = (
                parse_ga(self._co_start.strip(), style) if self._co_start.strip() else 1
            )
        except (ValueError, IndexError):
            start = 1
        paired = [i for i, r in enumerate(self._rows2) if r.target_co is not None]
        # Only rows without a typed address need a suggested one; skipping the others means a
        # typed override never "wastes" a free value and leaves a gap for the remaining rows.
        need_addr = [i for i in paired if not self._rows2[i].ga_addr.strip()]
        values = first_free_values(used, start, len(need_addr))
        addr_by_row = {
            i: format_ga(values[k], style)
            for k, i in enumerate(need_addr)
            if k < len(values)
        }
        out: dict[int, tuple[str, str]] = {}
        for slot, i in enumerate(paired):
            row = self._rows2[i]
            assert row.target_co is not None
            try:
                base = (self._name_template or "{source}").format(
                    source=row.co.name, target=row.target_co.name, n=slot + 1
                )
            except (KeyError, IndexError):
                base = row.co.name or f"GA {slot + 1}"
            # Make the name unique vs existing GAs AND earlier batch rows: suffix "(2)", "(3)",
            # ... and skip any suffix that is itself already taken (e.g. an existing "Foo (2)").
            if base.strip().lower() not in name_counts:
                name = base
            else:
                n = 2
                while f"{base} ({n})".strip().lower() in name_counts:
                    n += 1
                name = f"{base} ({n})"
            name_counts[name.strip().lower()] = (
                1  # reserve the chosen name for later rows
            )
            out[i] = (addr_by_row.get(i, ""), name)
        return out

    def _render_co_table(self, suggestions: dict[int, tuple[str, str]]) -> None:
        if not self._rows2:
            _hint(S.ML_TABLE_EMPTY)
            return
        flags = (
            imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.reorderable
        )
        if not begin_data_table("##ml2", 6, flags, scroll_when_narrow=True):
            return
        imgui.table_setup_column(
            S.ML_COL_OBJECT, imgui.TableColumnFlags_.width_stretch, 0.34
        )
        imgui.table_setup_column(
            S.ML_COL_DPT, imgui.TableColumnFlags_.width_fixed, px(52.0)
        )
        imgui.table_setup_column(
            S.ML_COL_TARGET_CO, imgui.TableColumnFlags_.width_stretch, 0.34
        )
        imgui.table_setup_column(
            S.ML_COL_GA_ADDR, imgui.TableColumnFlags_.width_fixed, px(96.0)
        )
        imgui.table_setup_column(
            S.ML_COL_GA_NAME, imgui.TableColumnFlags_.width_stretch, 0.32
        )
        imgui.table_setup_column("##rm", imgui.TableColumnFlags_.width_fixed, px(28.0))
        data_table_headers()
        candidates = self._all_objects()
        for i, row in enumerate(self._rows2):
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            imgui.text_unformatted(row.label)
            imgui.table_set_column_index(1)
            imgui.text_disabled(_dpt_label(row.co))
            imgui.table_set_column_index(2)
            self._target_co_combo(i, row, candidates)
            sugg_addr, sugg_name = suggestions.get(i, ("", ""))
            imgui.table_set_column_index(3)
            self._ga_field(f"##gaadr{i}", row, "ga_addr", sugg_addr)
            imgui.table_set_column_index(4)
            self._ga_field(f"##ganame{i}", row, "ga_name", sugg_name)
            imgui.table_set_column_index(5)
            if _remove_button(f"rm2{i}"):
                self._pending_remove = (2, i)
        imgui.end_table()

    @staticmethod
    def _ga_field(ident: str, row: _Row, attr: str, suggestion: str) -> None:
        """Editable per-pair GA field: the suggestion is shown as a hint while empty, so the user
        sees the address/name that will be created and can override it. Disabled until paired."""
        imgui.set_next_item_width(-1)
        imgui.begin_disabled(row.target_co is None)
        changed, value = imgui.input_text_with_hint(
            ident, suggestion, getattr(row, attr)
        )
        if changed:
            setattr(row, attr, value)
        imgui.end_disabled()

    def _target_co_combo(
        self, i: int, row: _Row, candidates: list[tuple[str, ComObject]]
    ) -> None:
        """The partner object: every device's objects under a header with the device's address and
        name, devices in address order. The filter matches the device or the object."""
        label = (
            f"{row.target_device}  ·  {_co_text(row.target_co)}"
            if row.target_co is not None
            else S.ML_TARGET_NONE
        )
        imgui.set_next_item_width(-1)
        opened = begin_combo(f"##tco{i}", label, imgui.ComboFlags_.height_large)
        if row.target_co is not None and imgui.is_item_hovered():
            imgui.set_tooltip(label)  # device and object, cut off in a narrow column
        if not opened:
            return
        self._tgt_filter = filter_box(
            f"##tcoflt{i}", S.ML_FILTER_HINT, self._tgt_filter
        )
        _, self._dpt_only = checkbox(S.ML_MATCHING_DPT_ONLY, self._dpt_only)
        if imgui.is_item_hovered():
            imgui.set_tooltip(S.ML_MATCHING_DPT_OBJECT_TIP)
        flt = self._tgt_filter.strip().lower()
        obj_major = _obj_major(row.co)
        # One entry per line: a device header (no object) or an object under the last header.
        entries: list[tuple[str, ComObject | None]] = []
        header = ""
        for dev_label, co in candidates:
            if co.db_id == row.co.db_id:
                continue  # can't link an object to itself
            co_major = _obj_major(co)
            if (
                self._dpt_only
                and None not in (obj_major, co_major)
                and co_major != obj_major
            ):
                continue
            text = _co_text(co)
            if flt and flt not in dev_label.lower() and flt not in text.lower():
                continue
            if dev_label != header:
                header = dev_label
                entries.append((dev_label, None))
            entries.append((text, co))
        if imgui.selectable(S.ML_TARGET_NONE, row.target_co is None)[0]:
            row.target_co = None
            row.target_device = ""
        clipper = imgui.ListClipper()
        clipper.begin(len(entries))
        while clipper.step():
            for k in range(clipper.display_start, clipper.display_end):
                text, co = entries[k]
                if co is None:
                    imgui.text_disabled(text)
                    continue
                selected = row.target_co is not None and row.target_co.db_id == co.db_id
                imgui.indent()
                if imgui.selectable(f"{text}##c{co.db_id}", selected)[0]:
                    row.target_co = co
                    row.target_device = _device_of(entries, k)
                imgui.unindent()
        imgui.end_combo()

    # -- shared rows -------------------------------------------------------

    def _row_toolbar(self, rows: list[_Row]) -> None:
        if imgui.button(S.ML_ADD_DEVICE):
            self._add_selected_device(rows)
        _continue_row(_button_width(S.ML_ADD_OBJECTS))
        if imgui.button(S.ML_ADD_OBJECTS):
            self._open_object_picker(rows)
        _continue_row(_button_width(S.ML_CLEAR_ALL))
        imgui.begin_disabled(not rows)
        if imgui.button(S.ML_CLEAR_ALL):
            rows.clear()
        imgui.end_disabled()
        count = S.ML_COUNT.format(count=len(rows))
        _continue_row(imgui.calc_text_size(count).x)
        imgui.text_disabled(count)
        if self._notice is not None:
            text, until = self._notice
            if time.monotonic() < until:
                # On its own line under the buttons, where it is seen after the click.
                imgui.text_colored(_STATUS_STYLE["ambiguous"][0], text)
            else:
                self._notice = None

    def _apply_pending_remove(self) -> None:
        if self._pending_remove is None:
            return
        tab, i = self._pending_remove
        self._pending_remove = None
        rows = self._rows1 if tab == 1 else self._rows2
        if 0 <= i < len(rows):
            rows.pop(i)

    def _add_selected_device(self, rows: list[_Row]) -> None:
        """Add the selected device's objects, or say briefly why nothing was added."""
        node_id = self._get_selected_node_id()
        dev = (
            next((d for d in self._get_devices() if d.node_id == node_id), None)
            if node_id is not None
            else None
        )
        if dev is None:
            self._note(S.ML_NOTE_NO_DEVICE)
            return
        if not any(co.db_id is not None for co in dev.get_visible_com_objects()):
            self._note(S.ML_NOTE_NO_OBJECTS.format(device=device_label(dev)))
        elif not self._append_objects(rows, dev):
            self._note(S.ML_NOTE_ALL_LISTED.format(device=device_label(dev)))

    def _note(self, text: str) -> None:
        self._notice = (text, time.monotonic() + _NOTE_SECONDS)

    def _append_objects(self, rows: list[_Row], device: Device) -> int:
        """Add the device's objects not in ``rows`` yet; returns how many were added."""
        existing = {r.co.db_id for r in rows}
        added = 0
        for co in device.get_visible_com_objects():
            if co.db_id is not None and co.db_id not in existing:
                added += 1
                row = _Row(device_label(device), co)
                # Pre-fill the target from the object's existing link so an already-linked object
                # shows its group address instead of "no target" (autopair only fills the rest).
                row.target_ga = self._existing_target_ga(co.db_id)
                rows.append(row)
                existing.add(co.db_id)
        if rows is self._rows1:
            self._autopair_rows(self._flat_gas(), only_empty=True)
        return added

    def _existing_target_ga(self, co_db_id: int) -> int | None:
        """The group address this com-object is already linked to (sending link preferred), or None."""
        if self._get_links_for_co is None:
            return None
        links = self._get_links_for_co(co_db_id)
        if not links:
            return None
        sending = next((a for a in links if a.is_sending), None)
        return (sending or links[0]).group_address_id

    def _all_objects(self) -> list[tuple[str, ComObject]]:
        """Every device's objects with the device's address and name, devices in address order
        and objects by number."""
        out: list[tuple[str, ComObject]] = []
        devices = sorted(
            self._get_devices(), key=lambda d: address_order(d.individual_address)
        )
        for device in devices:
            label = device_label(device)
            for co in sorted(device.get_visible_com_objects(), key=lambda c: c.number):
                if co.db_id is not None:
                    out.append((label, co))
        return out

    def _flat_gas(self) -> list[_FlatGa]:
        out: list[_FlatGa] = []

        def walk(node: GroupRangeInfo) -> None:
            for child in node.children:
                walk(child)
            for ga in node.group_addresses:
                out.append(
                    _FlatGa(
                        ga.id,
                        ga.address,
                        ga.text,
                        ga.name,
                        _dpt_major_from_token(ga.datapoint_type),
                        _dpt_label_from_token(ga.datapoint_type),
                    )
                )

        for node in self._get_range_tree():
            walk(node)
        out.sort(key=lambda g: g.value)
        return out

    # -- object picker -----------------------------------------------------

    def _open_object_picker(self, rows: list[_Row]) -> None:
        self._pick_into = rows
        self._pick_obj_selected.clear()
        self._pick_filter = ""
        self._open_obj_pending = True

    def _render_object_picker(self) -> None:
        if not begin_dialog(S.ML_PICK_OBJECTS_TITLE, width=520.0, height=460.0):
            return
        self._pick_filter = filter_box(
            "##ml_pick_flt", S.ML_FILTER_HINT, self._pick_filter
        )
        flt = self._pick_filter.strip().lower()
        if imgui.begin_child("##ml_pick_tree", px_vec2(0.0, 340.0)):
            for device in self._get_devices():
                cos = [
                    co
                    for co in device.get_visible_com_objects()
                    if co.db_id is not None
                    and (
                        not flt
                        or flt in co.name.lower()
                        or flt in device_label(device).lower()
                    )
                ]
                if not cos:
                    continue
                header = f"{device_label(device)}##dev{device.node_id}"
                if imgui.tree_node_ex(header, imgui.TreeNodeFlags_.default_open):
                    # One click grabs every object of the device (the "drop a device" speed-up).
                    if imgui.small_button(f"{S.ML_SELECT_ALL}##all{device.node_id}"):
                        for co in cos:
                            self._pick_obj_selected.add(co.db_id)  # type: ignore[arg-type]
                    for co in cos:
                        assert co.db_id is not None
                        checked = co.db_id in self._pick_obj_selected
                        changed, new = checkbox(
                            f"{_co_text(co)}##co{co.db_id}", checked
                        )
                        if changed:
                            self._toggle(self._pick_obj_selected, co.db_id, new)
                    imgui.tree_pop()
        imgui.end_child()
        clicked = dialog_buttons(S.ML_ADD_SELECTED, S.ML_CANCEL)
        if clicked == 0:
            self._add_selected_objects()
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _add_selected_objects(self) -> None:
        if self._pick_into is None:
            return
        existing = {r.co.db_id for r in self._pick_into}
        for device in self._get_devices():
            for co in device.get_visible_com_objects():
                if co.db_id in self._pick_obj_selected and co.db_id not in existing:
                    self._pick_into.append(_Row(device_label(device), co))
                    existing.add(co.db_id)
        into = self._pick_into
        self._pick_into = None
        if into is self._rows1:
            self._autopair_rows(self._flat_gas(), only_empty=True)

    @staticmethod
    def _toggle(target: set[int], value: int, on: bool) -> None:
        if on:
            target.add(value)
        else:
            target.discard(value)

    # -- actions -----------------------------------------------------------

    def _do_link_ga_co(self, rows: list[_Row]) -> None:
        request = [(r.co, r.target_ga) for r in rows if r.target_ga is not None]
        count, errors = self._on_link_ga_co(request)
        self._record(S.ML_LINK_GA_CO_DONE.format(count=count), errors)

    def _do_link_co_co(self, suggestions: dict[int, tuple[str, str]]) -> None:
        request: list[tuple[ComObject, ComObject, str, str]] = []
        for i, r in enumerate(self._rows2):
            if r.target_co is None:
                continue
            sugg_addr, sugg_name = suggestions.get(i, ("", r.co.name))
            addr = r.ga_addr.strip() or sugg_addr
            name = r.ga_name.strip() or sugg_name
            request.append((r.co, r.target_co, addr, name))
        count, errors = self._on_link_co_co(request)
        self._record(S.ML_LINK_CO_CO_DONE.format(count=count), errors)

    def _record(self, summary: str, errors: list[str]) -> None:
        text = summary
        if errors:
            text += "  " + S.ML_ERRORS.format(count=len(errors))
        self._log_entries.append(_LogEntry(datetime.now().strftime("%H:%M:%S"), text))
        self._result = self._log_entries[-1]
        self._result_errors = errors
        self._open_result_pending = True

    def _render_log_tab(self) -> None:
        _desc(S.ML_DESC_LOG)
        if imgui.button(S.ML_LOG_CLEAR):
            self._log_entries.clear()
        imgui.separator()
        if not self._log_entries:
            _hint(S.ML_LOG_EMPTY)
            return
        if imgui.begin_child("##ml_log"):
            for entry in reversed(self._log_entries):
                imgui.text_wrapped(f"[{entry.time}]  {entry.text}")
        imgui.end_child()

    def _render_result_popup(self) -> None:
        if not begin_dialog(S.ML_RESULT_TITLE, width=420.0):
            return
        if self._result is not None:
            imgui.text_wrapped(self._result.text)
        for err in self._result_errors:
            imgui.text_wrapped(err)
        if dialog_buttons(S.ML_OK) == 0:
            imgui.close_current_popup()
        end_dialog()


def _desc(text: str) -> None:
    """A dimmed, wrapping one-liner describing what a tab is for."""
    imgui.push_text_wrap_pos(0.0)
    imgui.text_disabled(text)
    imgui.pop_text_wrap_pos()
    imgui.spacing()


def _status_tip(status: str) -> str:
    """What a row's status mark means."""
    return {
        "ready": S.ML_STATUS_READY,
        "ambiguous": S.ML_STATUS_AMBIGUOUS,
        "incompatible": S.ML_STATUS_INCOMPATIBLE,
    }.get(status, S.ML_STATUS_UNMATCHED)


def _remove_button(str_id: str) -> bool:
    """A square "x" button as high as the row's drop-downs and fields (a small button has no
    frame padding and sits lower)."""
    side = imgui.get_frame_height()
    return x_button(str_id, imgui.ImVec2(side, side))


def _continue_row(width: float) -> None:
    """Continue the row when an item ``width`` wide still fits, else start a new line (a narrow
    window, a phone)."""
    imgui.same_line()
    if imgui.get_content_region_avail().x < width:
        imgui.new_line()


def _extra_lines(widths: list[float]) -> int:
    """How many more lines than one a row of items this wide takes in the current window, when
    each item that does not fit starts a new line (see :func:`_continue_row`)."""
    room = imgui.get_content_region_avail().x
    spacing = imgui.get_style().item_spacing.x
    lines, used = 0, -1.0
    for width in widths:
        if used < 0 or used + spacing + width > room:
            lines += 1
            used = width
        else:
            used += spacing + width
    return max(0, lines - 1)


def _button_width(label: str) -> float:
    return imgui.calc_text_size(label).x + 2 * imgui.get_style().frame_padding.x


def _checkbox_width(label: str) -> float:
    style = imgui.get_style()
    return (
        imgui.get_frame_height()
        + style.item_inner_spacing.x
        + imgui.calc_text_size(label).x
    )


def _field_width(width: float, label: str) -> float:
    """An input of ``width`` with its label beside it."""
    return (
        width + imgui.get_style().item_inner_spacing.x + imgui.calc_text_size(label).x
    )


def _guide(intro: str, steps: list[str]) -> None:
    """What a tab is for, then its steps, each numbered on a line of its own; a step that wraps
    stays indented under its text."""
    imgui.push_style_color(
        imgui.Col_.text, imgui.get_style().color_(imgui.Col_.text_disabled)
    )
    imgui.text_wrapped(intro)
    indent = imgui.calc_text_size("9.").x + imgui.get_style().item_spacing.x
    for number, step in enumerate(steps, 1):
        start = imgui.get_cursor_pos_x()
        imgui.text_unformatted(f"{number}.")
        imgui.same_line(start + indent)
        imgui.text_wrapped(step)
    imgui.pop_style_color()
    imgui.spacing()


def _button_names() -> dict[str, str]:
    """The buttons the guides refer to, by the names they show."""
    return {
        "add_device": S.ML_ADD_DEVICE,
        "add_objects": S.ML_ADD_OBJECTS,
        "match": S.ML_MATCH_NAME,
        "sequential": S.ML_ASSIGN_SEQ,
        "link_all": S.ML_LINK_ALL_NAME,
    }


def _device_of(entries: list[tuple[str, ComObject | None]], index: int) -> str:
    """The device header above the object entry at ``index``."""
    for k in range(index, -1, -1):
        text, co = entries[k]
        if co is None:
            return text
    return ""
