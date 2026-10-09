"""Generic module/channel table: pivot repeating module instances into one editable table.

Manufacturer-agnostic. A module-based device (an MDT/Lunatone DALI gateway's 64 ECGs + 16 groups, a
Gira/Jung/ABB/Theben multi-channel actuator, …) instantiates the same module N times. The standard
Parameters tab renders those as N stacked blocks; this view pivots them so each instance is a row and
each module parameter a column, which is far easier to scan and bulk-edit.

It reads nothing manufacturer-specific: instances are discovered purely from the instance-qualified
``ParameterRef`` ids in the device's evaluated UI tree (``…_MD-x_M-y_MI-n_P-p_R-r``). Edits go through
the same ``on_change(device, ref_id, value)`` path as the parameter tree.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.device import Device
from editor_gui.widgets.dpi import px
from editor_gui.widgets.parameter_widgets import render_param_widget
from xknxeditor.prod.parser_v2.ui import (
    UiNode,
    UiParameter,
    UiParameterBlock,
    UiTab,
)

# A ParameterRef id qualified by a module instance: everything before the last "_MI-<n>_" is the
# module identity (shared by every instance), the number is the instance index, the tail is the
# parameter's slot within the module (stable across instances).
_MI_RE = re.compile(r"^(?P<mod>.+)_MI-(?P<idx>\d+)_(?P<slot>.+)$")


@dataclass(slots=True)
class ModuleColumn:
    slot: str  # parameter slot within the module (e.g. "P-1_R-1"), stable across instances
    label: str  # column header (instance-varying bits stripped)


@dataclass(slots=True)
class ModuleRow:
    index: int  # instance index (the MI-<n>)
    params: dict[str, UiParameter]  # slot -> instance-qualified UiParameter


@dataclass(slots=True)
class ModuleTable:
    key: str  # module identity (the shared ref prefix)
    title: str  # display title (nearest enclosing block/tab text)
    columns: list[ModuleColumn]
    rows: list[ModuleRow]


class _Group:
    def __init__(self, title_path: tuple[str, ...]) -> None:
        self.title_path = title_path  # non-template ancestor titles, outermost first
        self.slots: list[str] = []  # column order (first appearance)
        self.labels: dict[str, list[str]] = {}  # slot -> labels seen across instances
        self.rows: dict[int, dict[str, UiParameter]] = {}

    @property
    def title(self) -> str:
        # Skip the outermost tab (usually a generic "General") and take the section below it
        # (e.g. "D1-Groups"); fall back to the outermost, then to nothing.
        path = self.title_path
        raw = path[1] if len(path) >= 2 else (path[0] if path else "")
        # Clean the app's odd section text ("ECG ," / "MD {{MD_NO}},") down to a plain name.
        raw = re.sub(r"\{\{[^}]*\}\}", "", raw)
        return re.sub(r"\s+", " ", raw).strip(" ,-:")


def build_module_tables(nodes: list[UiNode] | tuple[UiNode, ...]) -> list[ModuleTable]:
    """Group a device's UI parameters into one table per repeating module (>= 2 instances)."""
    groups: dict[str, _Group] = {}
    order: list[str] = []

    def walk(
        children: tuple[UiNode, ...] | list[UiNode], titles: tuple[str, ...]
    ) -> None:
        for node in children:
            if isinstance(node, UiTab | UiParameterBlock):
                # Accumulate the ancestor section titles, ignoring templated per-instance ones
                # ("ECG {{ECG_NO}}") so only stable human section names ("D1-Groups") remain.
                text = node.text or node.name or ""
                child_titles = (*titles, text) if text and "{{" not in text else titles
                walk(node.children, child_titles)
            elif isinstance(node, UiParameter):
                match = _MI_RE.match(node.ref_id)
                if match is None:
                    continue
                key = match.group("mod")
                idx = int(match.group("idx"))
                slot = match.group("slot")
                group = groups.get(key)
                if group is None:
                    group = groups[key] = _Group(title_path=titles)
                    order.append(key)
                if slot not in group.labels:
                    group.slots.append(slot)
                    group.labels[slot] = []
                group.labels[slot].append(node.label)
                group.rows.setdefault(idx, {})[slot] = node

    walk(nodes, ())

    tables: list[ModuleTable] = []
    for key in order:
        group = groups[key]
        if len(group.rows) <= 1:
            continue  # a single instance is not worth a table; the tree shows it fine
        columns = [
            ModuleColumn(slot=slot, label=_column_header(group.labels[slot]))
            for slot in group.slots
        ]
        rows = [ModuleRow(index=i, params=group.rows[i]) for i in sorted(group.rows)]
        tables.append(
            ModuleTable(key=key, title=group.title or key, columns=columns, rows=rows)
        )
    return tables


def _column_header(labels: list[str]) -> str:
    """A clean column header from an instance's labels.

    The per-instance entity prefix belongs in the row (the "#" column), not repeated in every column
    header: "ECG 1, Group" / "ECG {{ECG_NO}}, Group" -> "Group". Handles both a substituted index
    (varies per instance) and an unsubstituted "{{…}}" template (identical across instances).
    """
    seen = [label for label in labels if label]
    if not seen:
        return ""
    per_instance = any("{{" in label for label in seen) or len(set(seen)) > 1
    # Drop "{{…}}" placeholders, then reduce to the part common to all instances.
    cleaned = [re.sub(r"\{\{[^}]*\}\}", "", label) for label in seen]
    if len(set(cleaned)) == 1:
        header = cleaned[0]
    else:
        prefix = os.path.commonprefix(cleaned)
        suffix = os.path.commonprefix([label[::-1] for label in cleaned])[::-1]
        shortest = min(len(label) for label in cleaned)
        if (
            len(prefix) + len(suffix) > shortest
        ):  # prefix and suffix overlap; trim the suffix
            suffix = suffix[len(prefix) + len(suffix) - shortest :]
        header = prefix + suffix
    header = re.sub(r"\s+", " ", header)
    header = re.sub(
        r"\s+([,;:.])", r"\1", header
    ).strip()  # close the gap left by the index
    if per_instance:
        # Strip a leading entity prefix ("ECG,", "Group,", "MD,") so only the field name remains.
        header = re.sub(r"^[^,]{1,24},\s*", "", header)
    return header.strip(" ,-:") or seen[0]


def render_module_tables(
    device: Device,
    tables: list[ModuleTable],
    on_change: Callable[[Device, str, str], None],
    filter_text: str = "",
) -> None:
    """Render each module as an editable imgui table (one row per instance)."""
    needle = filter_text.lower().strip()
    for table in tables:
        header = f"{table.title} ({len(table.rows)})"
        if not imgui.collapsing_header(f"{header}##mod_{table.key}"):
            continue
        rows = [
            row
            for row in table.rows
            if not needle or _row_matches(row, table.columns, needle)
        ]
        if not rows:
            imgui.text_disabled("—")
            continue
        # No vertical scroll inside the table: it auto-sizes to all rows and the panel provides the
        # single vertical scrollbar (a table with its own ScrollY nested in the scrolling panel gives
        # a confusing double-scroll and can trap the wheel). Horizontal ScrollX stays for wide tables;
        # freezing the "#" column keeps the row index visible while scrolling sideways.
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_x
        )
        n_cols = len(table.columns) + 1  # +1 for the leading instance-index column
        row_h = imgui.get_frame_height_with_spacing()
        outer = imgui.ImVec2(
            0.0, (len(rows) + 1) * row_h + 8.0
        )  # header + all rows, no clipping
        if not imgui.begin_table(f"##modtbl_{table.key}", n_cols, flags, outer):
            continue
        imgui.table_setup_scroll_freeze(
            1, 0
        )  # keep the index column visible during x-scroll
        imgui.table_setup_column("#", imgui.TableColumnFlags_.width_fixed, px(44.0))
        for col in table.columns:
            imgui.table_setup_column(col.label or col.slot)
        imgui.table_headers_row()
        for row in rows:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(str(row.index))
            for col in table.columns:
                imgui.table_next_column()
                param = row.params.get(col.slot)
                if param is None:
                    continue
                imgui.set_next_item_width(-1)
                widget_id = f"mt_{table.key}_{row.index}_{col.slot}"
                render_param_widget(
                    param,
                    widget_id,
                    lambda value, ref=param.ref_id: on_change(device, ref, value),
                )
        imgui.end_table()


def _row_matches(row: ModuleRow, columns: list[ModuleColumn], needle: str) -> bool:
    if str(row.index) == needle or needle in str(row.index):
        return True
    for param in row.params.values():
        if needle in param.label.lower() or needle in param.value.lower():
            return True
    return False
