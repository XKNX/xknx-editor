"""Mass Linker: link many communication objects at once, without ordering two parallel lists.

Instead of two positional lists, each object is a table row and its target is set explicitly
per row, so the order of rows never matters:

- **GA <> Object**: rows are com-objects; each row picks an existing group address (inline combo).
  Helpers fill the targets fast: "assign sequential from <address>" and "match by name".
- **Object <> Object**: rows are source com-objects; each row picks a target com-object. Every
  pair gets a freshly created group address (name template + optional start address).
- **Log**: chronological record of every mass operation.

The panel is imgui-only glue: the assignment logic is the pure :func:`sequential_targets` /
:func:`match_by_name`, and the create/link work is injected callbacks (implemented on the project
plugin over ``ProjectService``), so the logic is testable without a UI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.widgets.dpi import px, px_vec2

if TYPE_CHECKING:
    from editor_gui.device import ComObject, Device
    from editor_gui.plugins.project.service import _Assignment as Assignment
    from xknxeditor.proj.core.service import GroupRangeInfo

LinkResult = tuple[int, list[str]]  # (linked count, error messages)


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
        group_style: Callable[[], object],
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

        self._rows1: list[_Row] = []  # GA <> Object rows
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
        self._problems_only = False  # table filter: hide rows that are already ready
        # Defer open_popup() to render()'s root scope (must match begin_popup_modal's id scope).
        self._open_obj_pending = False
        self._open_result_pending = False
        self._pending_remove: tuple[int, int] | None = None  # (tab, row index)

    # -- rendering ---------------------------------------------------------

    def render(self) -> None:
        open_now = self._is_open()
        if self._was_open and not open_now:
            self._reset()  # dropped project: queued db-ids are now stale
        self._was_open = open_now
        if not open_now:
            imgui.text_disabled(S.ML_NO_PROJECT)
            return
        if imgui.begin_tab_bar("##mass_linker_tabs"):
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

    # -- GA <> Object tab --------------------------------------------------

    def _render_ga_co_tab(self) -> None:
        _desc(S.ML_DESC_GA_CO)
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
            imgui.same_line()
            _, self._problems_only = imgui.checkbox(
                S.ML_SHOW_PROBLEMS, self._problems_only
            )

        table_h = max(imgui.get_content_region_avail().y - px(96.0), px(120.0))
        if imgui.begin_child("##ml1_table", imgui.ImVec2(0.0, table_h)):
            self._render_ga_table(flat, by_id)
        imgui.end_child()
        self._apply_pending_remove()

        # Re-pair by name (DPT-constrained) or link to a contiguous existing block from a start.
        if imgui.button(S.ML_MATCH_NAME):
            self._match_all(flat)
        imgui.same_line()
        imgui.set_next_item_width(px(120.0))
        _, self._seq_start = imgui.input_text(S.ML_SEQ_FROM, self._seq_start)
        imgui.same_line()
        if imgui.button(S.ML_ASSIGN_SEQ):
            self._assign_sequential(flat, by_id)
        # Link everything with a target that isn't blocked by a DPT conflict.
        linkable = [
            r
            for r in self._rows1
            if r.target_ga is not None and r.status != "incompatible"
        ]
        imgui.begin_disabled(not linkable)
        if imgui.button(S.ML_LINK_ALL.format(count=len(linkable)), imgui.ImVec2(-1, 0)):
            self._do_link_ga_co(linkable)
        imgui.end_disabled()

    def _render_ga_table(self, flat: list[_FlatGa], by_id: dict[int, _FlatGa]) -> None:
        if not self._rows1:
            imgui.text_disabled(S.ML_TABLE_EMPTY)
            return
        flags = (
            imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
        )
        if not imgui.begin_table("##ml1", 6, flags):
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
        imgui.table_headers_row()
        for i, row in enumerate(self._rows1):
            if self._problems_only and row.status == "ready":
                continue
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            color, glyph = _STATUS_STYLE.get(row.status, _STATUS_STYLE["unmatched"])
            imgui.text_colored(color, glyph)
            if imgui.is_item_hovered():
                imgui.set_tooltip(row.status)
            imgui.table_set_column_index(1)
            imgui.text_unformatted(row.device_name)
            imgui.table_set_column_index(2)
            imgui.text_unformatted(_co_text(row.co))
            imgui.table_set_column_index(3)
            imgui.text_disabled(_dpt_label(row.co))
            imgui.table_set_column_index(4)
            self._target_ga_combo(i, row, flat, by_id)
            imgui.table_set_column_index(5)
            if imgui.small_button(f"x##rm1{i}"):
                self._pending_remove = (1, i)
        imgui.end_table()

    def _target_ga_combo(
        self, i: int, row: _Row, flat: list[_FlatGa], by_id: dict[int, _FlatGa]
    ) -> None:
        cur = by_id.get(row.target_ga) if row.target_ga is not None else None
        label = f"{cur.text}  {cur.name}" if cur else S.ML_TARGET_NONE
        imgui.set_next_item_width(-1)
        if imgui.begin_combo(f"##tga{i}", label):
            self._tgt_filter = filter_box(
                f"##tgaflt{i}", S.ML_FILTER_HINT, self._tgt_filter
            )
            flt = self._tgt_filter.strip().lower()
            if imgui.selectable(S.ML_TARGET_NONE, row.target_ga is None)[0]:
                self._set_target(row, None, by_id)
            for g in flat:
                text = f"{g.text}  {g.name}"
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
        (unmatched/incompatible) from the last pairing until the user re-pairs."""
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
            start = parse_ga(self._seq_start.strip(), self._group_style())  # type: ignore[arg-type]
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
        _desc(S.ML_DESC_CO_CO)
        self._row_toolbar(self._rows2)
        suggestions = self._co_suggestions()
        table_h = max(imgui.get_content_region_avail().y - px(116.0), px(120.0))
        if imgui.begin_child("##ml2_table", imgui.ImVec2(0.0, table_h)):
            self._render_co_table(suggestions)
        imgui.end_child()
        self._apply_pending_remove()

        imgui.set_next_item_width(px(220.0))
        _, self._name_template = imgui.input_text(
            S.ML_NAME_TEMPLATE, self._name_template
        )
        imgui.same_line()
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
                    used.add(parse_ga(typed, style))  # type: ignore[arg-type]
        try:
            start = (
                parse_ga(self._co_start.strip(), style) if self._co_start.strip() else 1
            )  # type: ignore[arg-type]
        except (ValueError, IndexError):
            start = 1
        paired = [i for i, r in enumerate(self._rows2) if r.target_co is not None]
        # Only rows without a typed address need a suggested one; skipping the others means a
        # typed override never "wastes" a free value and leaves a gap for the remaining rows.
        need_addr = [i for i in paired if not self._rows2[i].ga_addr.strip()]
        values = first_free_values(used, start, len(need_addr))
        addr_by_row = {
            i: format_ga(values[k], style)  # type: ignore[arg-type]
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
            imgui.text_disabled(S.ML_TABLE_EMPTY)
            return
        flags = (
            imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
        )
        if not imgui.begin_table("##ml2", 6, flags):
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
        imgui.table_headers_row()
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
            if imgui.small_button(f"x##rm2{i}"):
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
        label = (
            _co_text(row.target_co) if row.target_co is not None else S.ML_TARGET_NONE
        )
        imgui.set_next_item_width(-1)
        if imgui.begin_combo(f"##tco{i}", label):
            self._tgt_filter = filter_box(
                f"##tcoflt{i}", S.ML_FILTER_HINT, self._tgt_filter
            )
            flt = self._tgt_filter.strip().lower()
            for dev_name, co in candidates:
                if co.db_id == row.co.db_id:
                    continue  # can't link an object to itself
                text = f"{dev_name}  {_co_text(co)}"
                if flt and flt not in text.lower():
                    continue
                sel = row.target_co is not None and row.target_co.db_id == co.db_id
                if imgui.selectable(f"{text}##c{co.db_id}", sel)[0]:
                    row.target_co = co
            imgui.end_combo()

    # -- shared rows -------------------------------------------------------

    def _row_toolbar(self, rows: list[_Row]) -> None:
        if imgui.button(S.ML_ADD_DEVICE):
            self._add_selected_device(rows)
        imgui.same_line()
        if imgui.button(S.ML_ADD_OBJECTS):
            self._open_object_picker(rows)
        imgui.same_line()
        imgui.begin_disabled(not rows)
        if imgui.button(S.ML_CLEAR_ALL):
            rows.clear()
        imgui.end_disabled()
        imgui.same_line()
        imgui.text_disabled(S.ML_COUNT.format(count=len(rows)))

    def _apply_pending_remove(self) -> None:
        if self._pending_remove is None:
            return
        tab, i = self._pending_remove
        self._pending_remove = None
        rows = self._rows1 if tab == 1 else self._rows2
        if 0 <= i < len(rows):
            rows.pop(i)

    def _add_selected_device(self, rows: list[_Row]) -> None:
        node_id = self._get_selected_node_id()
        if node_id is None:
            return
        dev = next((d for d in self._get_devices() if d.node_id == node_id), None)
        if dev is not None:
            self._append_objects(rows, dev)

    def _append_objects(self, rows: list[_Row], device: Device) -> None:
        existing = {r.co.db_id for r in rows}
        for co in device.get_visible_com_objects():
            if co.db_id is not None and co.db_id not in existing:
                row = _Row(device.name, co)
                # Pre-fill the target from the object's existing link so an already-linked object
                # shows its group address instead of "no target" (autopair only fills the rest).
                row.target_ga = self._existing_target_ga(co.db_id)
                rows.append(row)
                existing.add(co.db_id)
        if rows is self._rows1:
            self._autopair_rows(self._flat_gas(), only_empty=True)

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
        out: list[tuple[str, ComObject]] = []
        for device in self._get_devices():
            for co in device.get_visible_com_objects():
                if co.db_id is not None:
                    out.append((device.name, co))
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
        imgui.set_next_window_size(px_vec2(520.0, 460.0), imgui.Cond_.appearing)
        opened, _ = imgui.begin_popup_modal(S.ML_PICK_OBJECTS_TITLE, None)
        if not opened:
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
                        not flt or flt in co.name.lower() or flt in device.name.lower()
                    )
                ]
                if not cos:
                    continue
                header = (
                    f"{device.individual_address}  {device.name}##dev{device.node_id}"
                )
                if imgui.tree_node_ex(header, imgui.TreeNodeFlags_.default_open):
                    # One click grabs every object of the device (the "drop a device" speed-up).
                    if imgui.small_button(f"{S.ML_SELECT_ALL}##all{device.node_id}"):
                        for co in cos:
                            self._pick_obj_selected.add(co.db_id)  # type: ignore[arg-type]
                    for co in cos:
                        assert co.db_id is not None
                        checked = co.db_id in self._pick_obj_selected
                        changed, new = imgui.checkbox(
                            f"{_co_text(co)}##co{co.db_id}", checked
                        )
                        if changed:
                            self._toggle(self._pick_obj_selected, co.db_id, new)
                    imgui.tree_pop()
        imgui.end_child()
        imgui.separator()
        if imgui.button(S.ML_ADD_SELECTED):
            self._add_selected_objects()
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.ML_CANCEL):
            imgui.close_current_popup()
        imgui.end_popup()

    def _add_selected_objects(self) -> None:
        if self._pick_into is None:
            return
        existing = {r.co.db_id for r in self._pick_into}
        for device in self._get_devices():
            for co in device.get_visible_com_objects():
                if co.db_id in self._pick_obj_selected and co.db_id not in existing:
                    self._pick_into.append(_Row(device.name, co))
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
            imgui.text_disabled(S.ML_LOG_EMPTY)
            return
        if imgui.begin_child("##ml_log"):
            for entry in reversed(self._log_entries):
                imgui.text_wrapped(f"[{entry.time}]  {entry.text}")
        imgui.end_child()

    def _render_result_popup(self) -> None:
        imgui.set_next_window_size(px_vec2(420.0, 0.0), imgui.Cond_.appearing)
        opened, _ = imgui.begin_popup_modal(S.ML_RESULT_TITLE, None)
        if not opened:
            return
        if self._result is not None:
            imgui.text_wrapped(self._result.text)
        for err in self._result_errors:
            imgui.text_wrapped(err)
        imgui.separator()
        if imgui.button(S.ML_OK, imgui.ImVec2(-1, 0)):
            imgui.close_current_popup()
        imgui.end_popup()


def _desc(text: str) -> None:
    """A dimmed, wrapping one-liner describing what a tab is for."""
    imgui.push_text_wrap_pos(0.0)
    imgui.text_disabled(text)
    imgui.pop_text_wrap_pos()
    imgui.spacing()
