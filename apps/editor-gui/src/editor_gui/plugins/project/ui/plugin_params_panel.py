"""Generic sectioned view of a device's raw application parameters.

Reads the device's static parameter list and groups it with a vendor mapping file loaded by
``plugin_params.mapping_for``. The alternative to porting the vendor's full WPF plug-in: a flat
hierarchical browser that at least surfaces every parameter the application program defines, so
the user knows what the plug-in would otherwise set.

Edits route through the standard parameter change handler the project plugin injects, so a
normal download transfers the edited values to the device.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugin_params import (
    CollectionBinding,
    PluginMapping,
    PluginSectionView,
    collect_from_device,
    mapping_for,
)
from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px

if TYPE_CHECKING:
    from editor_gui.device import Device

_HEADING_COLOR = imgui.ImVec4(0.75, 0.85, 0.95, 1.0)

ParamEditHandler = Callable[["Device", str, str], None]
# Generic structural handlers for list-containers: the panel calls these with the
# ``CollectionBinding`` from the active section. Vendor code (project plugin) decides what
# backend writer to invoke based on ``binding.container_tag``.
AddItemHandler = Callable[["Device", "CollectionBinding", str, str], None]
RemoveItemHandler = Callable[["Device", "CollectionBinding", str], None]
# Optional "program the plug-in state onto the device" action, enabled only when the
# project-plugin wires it for a device that has a vendor BLOB (Siemens N141/03 today).
ProgramDcaHandler = Callable[["Device"], None]

# Fallback extractor for a row's displayable item id, in case the row label is "#N: ..." or
# a plain scalar.
_ROW_ID_RE = re.compile(r"^#(\S+?):")


@dataclass
class PluginParamsPanel:
    """Stateful panel body that renders the sectioned parameter view with inline text editing."""

    _on_edit: ParamEditHandler
    _on_add_item: AddItemHandler | None = None
    _on_remove_item: RemoveItemHandler | None = None
    _on_program_dca: ProgramDcaHandler | None = None
    _filter: str = ""
    _closed_sections: dict[str, set[str]] = field(default_factory=dict[str, "set[str]"])
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

    def render(self, device: Device) -> None:
        app_id = device.app.id if device.app is not None else None
        mapping = mapping_for(app_id)
        if mapping is None:
            imgui.text_disabled(S.PLUGIN_PARAMS_NO_MAPPING)
            return
        sections = self._cached_sections(device, mapping)
        if not sections:
            imgui.text_disabled(S.PLUGIN_PARAMS_EMPTY)
            return
        imgui.text_colored(_HEADING_COLOR, mapping.title)
        imgui.text_wrapped(S.PLUGIN_PARAMS_EDIT_NOTE)
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
        imgui.set_next_item_width(px(320.0))
        _, self._filter = imgui.input_text(
            S.PLUGIN_PARAMS_FILTER, self._filter, imgui.InputTextFlags_.none
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
            self._render_section(device, section.section, rows)
        if total_rows == 0:
            imgui.text_disabled(S.PLUGIN_PARAMS_FILTER_EMPTY)

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
        self, device: Device, section_label: str, rows: list[_SectionRow]
    ) -> None:
        device_key = device.individual_address
        closed = self._closed_sections.setdefault(device_key, set())
        is_open = section_label not in closed
        if is_open:
            imgui.set_next_item_open(True, imgui.Cond_.appearing)
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
        self, device: Device, section_label: str, rows: list[_SectionRow]
    ) -> None:
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        # Fixed table height so a very long section (hundreds of rows) does not blow up the
        # outer scroll area; combined with the clipper below each frame renders only the
        # visible rows + a small margin, so scrolling stays O(viewport) rather than O(section).
        row_height = max(imgui.get_text_line_height_with_spacing(), 1.0)
        max_table_h = min(
            float(len(rows)) * row_height + px(32.0),
            px(360.0),
        )
        if not imgui.begin_table(
            f"##plugin_params_{section_label}",
            3,
            flags,
            imgui.ImVec2(0.0, max_table_h),
        ):
            return
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_NAME, imgui.TableColumnFlags_.width_stretch, 0.6
        )
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_VALUE, imgui.TableColumnFlags_.width_stretch, 0.25
        )
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_DEFAULT, imgui.TableColumnFlags_.width_stretch, 0.15
        )
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_headers_row()
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        while clipper.step():
            for idx in range(clipper.display_start, clipper.display_end):
                self._render_row(device, rows[idx])
        imgui.end_table()

    def _render_row(self, device: Device, row: _SectionRow) -> None:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.text(row.label)
        imgui.table_next_column()
        if not row.writable:
            # Read-only rows (BLOB-sourced sections). When the schema provides a choice list
            # the value is already a humanized label; show it directly. For plain scalars we
            # just print the value.
            imgui.text(row.value)
            imgui.table_next_column()
            imgui.text_disabled(row.default or "-")
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
            imgui.text_disabled(row.default or "-")
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
        imgui.text_disabled(row.default or "-")


@dataclass(frozen=True, slots=True)
class _SectionRow:
    ref_id: str
    label: str
    value: str
    default: str
    writable: bool
    choices: tuple[tuple[str, str], ...] = ()


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
