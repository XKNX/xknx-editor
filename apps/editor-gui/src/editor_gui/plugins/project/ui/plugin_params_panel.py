"""Generic sectioned view of a device's raw application parameters.

Shows catalog parameters, blob fields or native provider sections selected by
``plugin_params.mapping_for_device``. Edits use the project plugin's shared handler.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugin_params import (
    CollectionBinding,
    PluginMapping,
    PluginSectionView,
    collect_from_device,
    mapping_for_device,
)
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box
from xknxeditor.vendor.triton import DeviceType

if TYPE_CHECKING:
    from editor_gui.device import Device


# The triton LCD display types the setup dropdown offers, as (enum value, product label). The
# labels are product names and stay untranslated.
_TRITON_DISPLAY_TYPES: tuple[tuple[str, str], ...] = (
    (DeviceType.INTERFACE_6136UP.value, "6136UP"),
    (DeviceType.TRITON_6323.value, "triton 6323"),
    (DeviceType.TRITON_6325.value, "triton 6325"),
)
_TRITON_DEFAULT_TYPE = DeviceType.TRITON_6325.value


def _display_type_index(value: str | None) -> int:
    """Index of ``value`` in the display-type dropdown, falling back to the triton 6325."""
    values = [type_value for type_value, _label in _TRITON_DISPLAY_TYPES]
    if value in values:
        return values.index(value)
    return values.index(_TRITON_DEFAULT_TYPE)


ParamEditHandler = Callable[["Device", str, str], None]
# Generic structural handlers for list-containers: the panel calls these with the
# ``CollectionBinding`` from the active section. Vendor code (project plugin) decides what
# backend writer to invoke based on ``binding.container_tag``.
AddItemHandler = Callable[["Device", "CollectionBinding", str, str], None]
RemoveItemHandler = Callable[["Device", "CollectionBinding", str], None]
# Optional "program the plug-in state onto the device" action, enabled only when the
# project-plugin wires it for a device that has a vendor BLOB (Siemens N141/03 today).
ProgramDcaHandler = Callable[["Device"], None]
# Setup actions for a blob-only triton LCD display that has no configuration yet. The project
# plugin wires these; the panel stays pure-imgui and only calls them.
SetupDisplayHandler = Callable[["Device", str], None]
ImportPrjHandler = Callable[["Device"], None]
ExportPrjHandler = Callable[["Device"], None]
DetectDisplayType = Callable[["Device"], "str | None"]

# Fallback extractor for a row's displayable item id, in case the row label is "#N: ..." or
# a plain scalar.
_ROW_ID_RE = re.compile(r"^#(\S+?):")
_VISIBLE_PARAMETER_ROWS = 12


@dataclass
class PluginParamsPanel:
    """Stateful panel body that renders the sectioned parameter view with inline text editing."""

    _on_edit: ParamEditHandler
    _on_add_item: AddItemHandler | None = None
    _on_remove_item: RemoveItemHandler | None = None
    _on_program_dca: ProgramDcaHandler | None = None
    # Triton LCD setup area (shown for a blob-only triton display with no config yet).
    _on_setup_display: SetupDisplayHandler | None = None
    _on_import_prj: ImportPrjHandler | None = None
    _on_export_prj: ExportPrjHandler | None = None
    _detect_display_type: DetectDisplayType | None = None
    _filter: str = ""
    _closed_sections: dict[str, set[str]] = field(default_factory=dict[str, "set[str]"])
    _seen_sections: dict[str, set[str]] = field(default_factory=dict[str, "set[str]"])
    # Per-ref edit buffer; cleared when the device commits a value back to the DOM.
    _edit_buffers: dict[str, str] = field(default_factory=dict[str, str])
    # Collect-from-device is expensive on large applications (iterating 30-80k parameter refs);
    # cache the result per device so the per-frame render stays cheap.
    _sections_cache: dict[str, tuple[object, list[PluginSectionView]]] = field(
        default_factory=dict[str, "tuple[object, list[PluginSectionView]]"]
    )
    # Add form state per (device_address, section_label): item-type dropdown index and name.
    _new_type_idx: dict[tuple[str, str], int] = field(
        default_factory=dict[tuple[str, str], int]
    )
    _new_name: dict[tuple[str, str], str] = field(
        default_factory=dict[tuple[str, str], str]
    )
    # Per-device display-type dropdown index for the triton LCD setup area, keyed by the
    # device's individual address; seeded once from auto-detection on first render.
    _setup_type_idx: dict[str, int] = field(default_factory=dict[str, int])

    def render(self, device: Device) -> None:
        mapping = mapping_for_device(device)
        if mapping is None:
            imgui.text_disabled(S.PLUGIN_PARAMS_NO_MAPPING)
            return
        try:
            sections = self._cached_sections(device, mapping)
        except ValueError as exc:
            imgui.text_colored(status_color("error"), str(exc))
            return
        if not sections:
            if mapping.provider:
                return
            if (
                mapping.blob_reader == "triton_lcd"
                and self._on_setup_display is not None
            ):
                self._render_triton_setup(device)
                return
            imgui.text_disabled(S.PLUGIN_PARAMS_EMPTY)
            return
        imgui.text_colored(status_color("info"), mapping.title)
        if not mapping.provider:
            imgui.text_wrapped(S.PLUGIN_PARAMS_EDIT_NOTE)
        if mapping.blob_reader == "triton_lcd":
            self._render_triton_actions(device)
        # "Program DCA" shortcut: visible when the project plugin exposes the handler and
        # the device actually carries a vendor BLOB. One-click upload of the plug-in's
        # property-image to the gateway, bypassing the generic Parameters download.
        if self._on_program_dca is not None and _has_vendor_blob(device):
            if imgui.button(
                f"{S.PLUGIN_PARAMS_PROGRAM_DCA}##dca_{device.individual_address}"
            ):
                self._on_program_dca(device)
            imgui.same_line()
            imgui.text_disabled(S.PLUGIN_PARAMS_PROGRAM_DCA_HINT)
        self._filter = filter_box(
            "##plugin_params_filter", S.CONFIGURE_PARAM_FILTER_HINT, self._filter
        )
        needle = self._filter.lower().strip()
        total_rows = 0
        for section in sections:
            rows = _filter_rows(section, needle)
            if section.collection is not None and self._on_add_item is not None:
                # Collection sections render Add/Remove regardless of filter matches —
                # the user needs to see them to add the first item on an empty line.
                self._render_collection_section(device, section, rows)
                total_rows += max(len(rows), 1)
                continue
            if not rows:
                continue
            total_rows += len(rows)
            self._render_section(
                device,
                section.section,
                rows,
                filtering=bool(needle),
                collapsed=section.collapsed,
            )
        if total_rows == 0 and needle:
            imgui.text_disabled(S.PLUGIN_PARAMS_FILTER_EMPTY)

    def _render_triton_setup(self, device: Device) -> None:
        """Setup area for a blob-only triton LCD display with no configuration yet: a hint, a
        display-type dropdown (pre-selected from auto-detection) and the two setup actions."""
        assert self._on_setup_display is not None
        key = device.individual_address or "?"
        if key not in self._setup_type_idx:
            detected = (
                self._detect_display_type(device)
                if self._detect_display_type is not None
                else None
            )
            self._setup_type_idx[key] = _display_type_index(detected)
        imgui.text_wrapped(S.PLUGIN_PARAMS_SETUP_HINT)
        labels = [label for _value, label in _TRITON_DISPLAY_TYPES]
        idx = self._setup_type_idx[key]
        if idx >= len(labels):
            idx = 0
        imgui.set_next_item_width(px(240.0))
        changed, idx = imgui.combo(
            f"{S.PLUGIN_PARAMS_SETUP_TYPE}##triton_type_{key}", idx, labels
        )
        if changed:
            self._setup_type_idx[key] = idx
        if imgui.button(f"{S.PLUGIN_PARAMS_SETUP_BUTTON}##triton_setup_{key}"):
            self._on_setup_display(device, _TRITON_DISPLAY_TYPES[idx][0])
        if self._on_import_prj is not None:
            imgui.same_line()
            if imgui.button(f"{S.PLUGIN_PARAMS_LOAD_PRJ}##triton_prj_{key}"):
                self._on_import_prj(device)

    def _render_triton_actions(self, device: Device) -> None:
        """Import/export row shown above a configured triton LCD display, so a ".prj" can be loaded
        or saved after the display is already set up (the setup area is gone by then)."""
        key = device.individual_address or "?"
        acted = False
        if self._on_import_prj is not None:
            if imgui.button(f"{S.PLUGIN_PARAMS_LOAD_PRJ}##triton_prj_{key}"):
                self._on_import_prj(device)
            acted = True
        if self._on_export_prj is not None:
            if acted:
                imgui.same_line()
            if imgui.button(f"{S.PLUGIN_PARAMS_EXPORT_PRJ}##triton_prj_export_{key}"):
                self._on_export_prj(device)

    def invalidate(self, device: Device) -> None:
        """Drop the cached section list for ``device`` so the next render reads fresh values."""
        self._sections_cache.pop(device.individual_address, None)

    def _cached_sections(
        self, device: Device, mapping: PluginMapping
    ) -> list[PluginSectionView]:
        # Fast cache key: the parameter_instance_refs identity (replaced on project reload) plus
        # its length (changes on set_param). Avoids iterating the ref list here just to key the
        # cache; a stale cache only shows outdated values until the user triggers an edit (which
        # calls invalidate()).
        pirs = device.parameter_instance_refs
        key = (id(pirs), len(pirs), id(mapping))
        cached = self._sections_cache.get(device.individual_address)
        if cached is not None and cached[0] == key:
            return cached[1]
        sections = collect_from_device(device, mapping)
        self._sections_cache[device.individual_address] = (key, sections)
        return sections

    def _render_section(
        self,
        device: Device,
        section_label: str,
        rows: list[_SectionRow],
        *,
        filtering: bool = False,
        collapsed: bool = False,
    ) -> None:
        device_key = device.individual_address
        closed = self._closed_sections.setdefault(device_key, set())
        seen = self._seen_sections.setdefault(device_key, set())
        if section_label not in seen:
            seen.add(section_label)
            if collapsed:
                closed.add(section_label)
        if filtering:
            # While a filter is active, force every matching section open so the matches are
            # visible, without touching the user's remembered collapse state (it is restored the
            # moment the filter is cleared).
            imgui.set_next_item_open(True)
            if imgui.collapsing_header(f"{section_label}  ({len(rows)})"):
                self._render_standard_table(device, section_label, rows, filtering=True)
            return
        imgui.set_next_item_open(section_label not in closed)
        shown = imgui.collapsing_header(f"{section_label}  ({len(rows)})")
        if not shown:
            closed.add(section_label)
            return
        closed.discard(section_label)
        self._render_standard_table(device, section_label, rows)

    def _render_collection_section(
        self,
        device: Device,
        section: PluginSectionView,
        rows: list[_SectionRow],
    ) -> None:
        """Collection-backed section: Add form on top, table with Remove column below.

        Driven entirely by ``section.collection`` — the panel is vendor-agnostic. For a
        container that offers a type dropdown (``item_types`` non-empty) the Add form shows
        a combo + optional name field; for a container without a type picker only a name
        field is shown. Remove sends the row's item id back through ``on_remove_item``.
        """
        binding = section.collection
        assert binding is not None
        device_key = device.individual_address
        closed = self._closed_sections.setdefault(device_key, set())
        label = f"{section.section}  ({len(rows)})"
        is_open = section.section not in closed
        if is_open:
            imgui.set_next_item_open(True, imgui.Cond_.appearing)
        shown = imgui.collapsing_header(label)
        if not shown:
            closed.add(section.section)
            return
        closed.discard(section.section)

        key = (device.individual_address or "?", section.section)
        imgui.text_disabled(
            S.PLUGIN_PARAMS_ADD_TO.format(section=binding.display_label)
        )
        type_id = ""
        if binding.item_types and self._on_add_item is not None:
            labels = [lbl for _tid, lbl in binding.item_types]
            type_idx = self._new_type_idx.get(key, 0)
            if type_idx >= len(labels):
                type_idx = 0
            imgui.set_next_item_width(px(360.0))
            type_changed, type_idx = imgui.combo(
                f"{S.PLUGIN_PARAMS_TYPE}##add_{section.section}", type_idx, labels
            )
            if type_changed:
                self._new_type_idx[key] = type_idx
            type_id = binding.item_types[type_idx][0]
        name = ""
        if binding.needs_name:
            name = self._new_name.get(key, "")
            imgui.set_next_item_width(px(240.0))
            name_changed, name = imgui.input_text(
                f"{S.PLUGIN_PARAMS_NAME}##add_{section.section}", name
            )
            if name_changed:
                self._new_name[key] = name
        if self._on_add_item is not None and imgui.button(
            f"{S.PLUGIN_PARAMS_ADD}##add_{section.section}"
        ):
            final_name = (name or "").strip() or f"{binding.display_label} new"
            self._on_add_item(device, binding, type_id, final_name)
            self._new_name[key] = ""
            self._sections_cache.pop(device.individual_address, None)
        imgui.separator()

        if not rows:
            imgui.text_disabled(S.PLUGIN_PARAMS_NO_ENTRIES)
            return
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
        )
        if not imgui.begin_table(f"##coll_{section.section}", 3, flags):
            return
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_ENTRY, imgui.TableColumnFlags_.width_stretch, 0.5
        )
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_DETAILS, imgui.TableColumnFlags_.width_stretch, 0.4
        )
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed, px(72.0))
        imgui.table_headers_row()
        for row in rows:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(row.label)
            imgui.table_next_column()
            imgui.text(row.value)
            imgui.table_next_column()
            item_id = _row_item_id(row)
            if (
                item_id
                and self._on_remove_item is not None
                and imgui.button(
                    f"{S.PLUGIN_PARAMS_REMOVE}##{section.section}_{row.label}"
                )
            ):
                self._on_remove_item(device, binding, item_id)
                self._sections_cache.pop(device.individual_address, None)
        imgui.end_table()

    def _render_standard_table(
        self,
        device: Device,
        section_label: str,
        rows: list[_SectionRow],
        *,
        filtering: bool = False,
    ) -> None:
        if any(r.group for r in rows):
            self._render_grouped_table(device, section_label, rows, filtering=filtering)
            return
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
        )
        scrolling = len(rows) > _VISIBLE_PARAMETER_ROWS
        row_height = imgui.get_frame_height() + 2 * imgui.get_style().cell_padding.y
        height = 0.0
        if scrolling:
            flags |= imgui.TableFlags_.scroll_y
            header_height = (
                imgui.get_text_line_height() + 2 * imgui.get_style().cell_padding.y
            )
            height = header_height + _VISIBLE_PARAMETER_ROWS * row_height
        if not imgui.begin_table(
            f"##plugin_params_{section_label}", 3, flags, imgui.ImVec2(0.0, height)
        ):
            return
        self._setup_param_columns()
        if scrolling:
            imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_headers_row()
        if scrolling:
            clipper = imgui.ListClipper()
            clipper.begin(len(rows), row_height)
            while clipper.step():
                for idx in range(clipper.display_start, clipper.display_end):
                    self._render_row(device, rows[idx], min_height=row_height)
        else:
            for row in rows:
                self._render_row(device, row)
        imgui.end_table()

    def _render_grouped_table(
        self,
        device: Device,
        section_label: str,
        rows: list[_SectionRow],
        *,
        filtering: bool = False,
    ) -> None:
        """Keep parameter pages collapsible and bound scrolling to long pages."""
        idx = 0
        while idx < len(rows):
            first = rows[idx]
            key = first.group_key or first.group
            end = idx + 1
            while end < len(rows) and (rows[end].group_key or rows[end].group) == key:
                end += 1
            page = [replace(row, group="", group_key="") for row in rows[idx:end]]
            if not first.group:
                self._render_standard_table(device, section_label + ":ungrouped", page)
            else:
                if filtering:
                    imgui.set_next_item_open(True)
                if imgui.tree_node_ex(
                    f"{first.group}##{section_label}:{key}",
                    imgui.TreeNodeFlags_.span_avail_width,
                ):
                    self._render_standard_table(device, section_label + ":" + key, page)
                    imgui.tree_pop()
            idx = end

    def _setup_param_columns(self) -> None:
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_NAME, imgui.TableColumnFlags_.width_stretch, 0.6
        )
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_VALUE, imgui.TableColumnFlags_.width_stretch, 0.25
        )
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_DEFAULT, imgui.TableColumnFlags_.width_stretch, 0.15
        )

    def _render_row(
        self, device: Device, row: _SectionRow, *, min_height: float = 0.0
    ) -> None:
        imgui.table_next_row(min_row_height=min_height)
        imgui.table_next_column()
        imgui.text(row.label)
        if row.raw_value is not None and imgui.is_item_hovered():
            imgui.set_tooltip(f"{row.ref_id}\n{row.raw_value}")
        imgui.table_next_column()
        if not row.writable:
            # Read-only rows (BLOB-sourced sections). When the schema provides a choice list
            # the value is already a humanized label; show it directly. For plain scalars we
            # just print the value.
            imgui.text(row.value)
            imgui.table_next_column()
            imgui.text_disabled(_default_label(row))
            return
        # The edit buffer wins over the DOM value while the user is typing; the commit
        # happens on Enter / focus-lost. For BLOB-backed rows we drop ``enter_returns_true``
        # so each keystroke writes straight through — ``apply_edit`` is cheap and the user
        # gets immediate feedback. ParameterRef rows keep the Enter-to-commit flow because
        # ``set_param`` runs full validation + visibility recalculation on every call.
        current = self._edit_buffers.get(row.ref_id, row.value)
        imgui.set_next_item_width(-1.0)
        if row.choices:
            # Enum field: a combo with the humanized labels. The DOM holds the raw token the
            # value already matched one of; find its index to seed the combo. On a change the
            # raw token (not the label) is dispatched so the backend path stays unchanged.
            labels = [label for _tok, label in row.choices]
            raw_values = [tok for tok, _label in row.choices]
            # Resolve current -> index; if the stored value is not in the choice set (vendor
            # pre-release or stale enum), append it so the user still sees it.
            try:
                current_idx = raw_values.index(current)
            except ValueError:
                try:
                    current_idx = labels.index(current)
                except ValueError:
                    current_idx = len(labels)
                    labels = [*labels, current]
                    raw_values = [*raw_values, current]
            changed, new_idx = imgui.combo(f"##val_{row.ref_id}", current_idx, labels)
            if changed:
                new_token = raw_values[new_idx]
                self._on_edit(device, row.ref_id, new_token)
                self._edit_buffers.pop(row.ref_id, None)
                self._sections_cache.pop(device.individual_address, None)
            imgui.table_next_column()
            imgui.text_disabled(_default_label(row))
            return
        # BLOB-rows go through apply_edit which is cheap, so commit on every keystroke
        # (``input_text`` without ``enter_returns_true`` returns ``changed=True`` whenever
        # the buffer differs from the previous frame). Standard ParameterRef rows keep the
        # ``enter_returns_true`` flow so a 20-char type does not fire 20 validations.
        is_blob = row.ref_id.startswith("siemens_blob:")
        flags = (
            imgui.InputTextFlags_.none
            if is_blob
            else imgui.InputTextFlags_.enter_returns_true
        )
        changed, new_value = imgui.input_text(
            f"##val_{row.ref_id}",
            current,
            flags,
        )
        if changed:
            self._on_edit(device, row.ref_id, new_value)
            self._edit_buffers.pop(row.ref_id, None)
            # Edit commits mutate parameter_instance_refs; drop the cached section list so the
            # next frame reflects any newly activated/dropped parameters (parser visibility can
            # add or remove rows after a function-selector change).
            self._sections_cache.pop(device.individual_address, None)
        elif new_value != current:
            self._edit_buffers[row.ref_id] = new_value
        imgui.table_next_column()
        imgui.text_disabled(_default_label(row))


@dataclass(frozen=True, slots=True)
class _SectionRow:
    ref_id: str
    label: str
    value: str
    default: str
    writable: bool
    choices: tuple[tuple[str, str], ...] = ()
    group: str = ""
    group_key: str = ""
    raw_value: str | None = None


def _default_label(row: _SectionRow) -> str:
    """The "Vorgabe" (default) cell text: an enum default resolved to its humanized label.

    For a dropdown row the stored default is a raw token (e.g. ``3``); showing that number is as
    cryptic as the old raw value column was, so map it through the row's choices to the label the
    dropdown itself shows (``RGBW Colour``). Non-enum rows, or a default not in the choice set,
    fall back to the raw default (or ``-`` when empty).
    """
    if row.choices and row.default:
        for token, label in row.choices:
            if token == row.default:
                return label
    return row.default or "-"


def _filter_rows(section: PluginSectionView, needle: str) -> list[_SectionRow]:
    out: list[_SectionRow] = []
    # A filter match against the section name keeps every row of that section — searching
    # for "sensor" should surface the whole "Siemens Line 0 - Sensors" block even though the
    # row labels only hold the sensor instance names (e.g. "#1: Pushbutton 1").
    section_matches = bool(needle) and needle in section.section.lower()
    for p in section.parameters:
        if (
            needle
            and not section_matches
            and needle not in p.name.lower()
            and needle not in p.label.lower()
            and needle not in p.value.lower()
            and needle not in p.group.lower()
        ):
            continue
        out.append(
            _SectionRow(
                ref_id=p.ref_id,
                label=p.label,
                value=p.value,
                default=p.default_value,
                writable=p.writable,
                choices=p.choices,
                group=p.group,
                group_key=p.group_key,
                raw_value=p.raw_value,
            )
        )
    return out


def _row_item_id(row: _SectionRow) -> str:
    return collection_item_id(row.label)


def collection_item_id(label: str) -> str:
    """Best-effort id for a Collection row's Remove action — the id_field token encoded
    into the row label by the reader. Formats supported: ``#N: name`` → ``N``, bare
    label → the label as-is. Rows without an identifier end up with an empty id and the
    Remove button is hidden by the panel."""
    m = _ROW_ID_RE.match(label)
    if m:
        return m.group(1)
    return label.strip()


def _has_vendor_blob(device: Device) -> bool:
    """Does the device carry a non-empty BinaryData entry? Gates the Program-DCA button."""
    for entry in getattr(device, "binary_data", []) or []:
        if entry.data and "BLOB_PARA" in (entry.name or ""):
            return True
    return False
