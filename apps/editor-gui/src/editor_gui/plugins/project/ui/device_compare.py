"""The "Compare devices" window: pick two devices and see where their configuration differs
(header, parameters, group objects). Pure diff logic lives in :mod:`device_diff`; this module only
builds snapshots on demand (cached) and renders the result."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.device import Device, device_label
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.device_diff import (
    AddressResolver,
    DeviceDiffResult,
    DiffRow,
    LinksResolver,
    diff_devices,
    snapshot_device,
)
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.description import (
    description_icon_width,
    render_description,
    shows_description_icon,
)
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from editor_gui.widgets.text_util import text_clipped_tooltip
from editor_gui.widgets.tree import collapsing_header

# Row colours: amber when both devices have the item but it differs; red when it is present on only
# one side (missing on the other — the empty cell shows which device lacks it).
_CHANGED = imgui.ImVec4(0.95, 0.75, 0.35, 1.0)
_MISSING = imgui.ImVec4(0.9, 0.5, 0.5, 1.0)
_KIND_COLOR = {"changed": _CHANGED, "added": _MISSING, "removed": _MISSING}


class DeviceComparePanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_links: LinksResolver,
        get_address: AddressResolver,
        get_revision: Callable[[], int],
    ) -> None:
        self._get_devices = get_devices
        self._get_links = get_links
        self._get_address = get_address
        self._get_revision = get_revision
        self._a_id: int | None = None
        self._b_id: int | None = None
        self._only_diff = True
        self._filter = ""
        # Cache the computed diff; device.get_ui() rebuilds the heavy DynamicUI, so recompute only
        # when the pair or the project revision changes (never per frame).
        self._cache_key: tuple[int, int, int] | None = None
        self._result: DeviceDiffResult | None = None

    def set_primary(self, node_id: int) -> None:
        """Seed device A (from the Devices-tree context menu); keep B unless it is unset/equal."""
        self._a_id = node_id
        if self._b_id is None or self._b_id == node_id:
            self._b_id = None
        self._cache_key = None

    def render(self) -> None:
        devices = self._get_devices()
        if len(devices) < 2:
            imgui.text_disabled(S.COMPARE_NEED_TWO)
            return
        by_id = {d.node_id: d for d in devices}
        if self._a_id not in by_id:
            self._a_id = devices[0].node_id
        if self._b_id not in by_id or self._b_id == self._a_id:
            self._b_id = next(
                (d.node_id for d in devices if d.node_id != self._a_id),
                devices[0].node_id,
            )

        self._filter = filter_box("##cmp_filter", S.COMPARE_FILTER_HINT, self._filter)
        needle = self._filter.lower()
        visible = [d for d in devices if self._matches(d, needle)]
        self._a_id = self._device_combo("##cmp_a", self._a_id, visible, by_id)
        self._b_id = self._device_combo("##cmp_b", self._b_id, visible, by_id)
        _, self._only_diff = checkbox(S.COMPARE_ONLY_DIFF, self._only_diff)

        result = self._diff(by_id[self._a_id], by_id[self._b_id])
        imgui.same_line()
        imgui.text_disabled(S.COMPARE_SUMMARY.format(count=result.difference_count))
        imgui.same_line()
        if imgui.button(S.COMPARE_COPY):
            imgui.set_clipboard_text(
                self._clipboard_text(result, by_id[self._a_id], by_id[self._b_id])
            )
        imgui.separator()

        for title, rows in self._result_sections(result):
            self._render_section(title, rows)

    def _result_sections(
        self, result: DeviceDiffResult
    ) -> list[tuple[str, list[DiffRow]]]:
        return [
            (S.COMPARE_SECTION_HEADER, result.header),
            (S.COMPARE_SECTION_PARAMETERS, result.parameters),
            (S.COMPARE_SECTION_COM_OBJECTS, result.com_objects),
        ]

    def _clipboard_text(self, result: DeviceDiffResult, a: Device, b: Device) -> str:
        """Tab-separated ``Field<TAB>A<TAB>B`` of the shown rows (honouring 'only differences'),
        grouped by section — paste into a spreadsheet or text."""
        lines = [f"Field\t{device_label(a)}\t{device_label(b)}"]
        for title, rows in self._result_sections(result):
            shown = [r for r in rows if not self._only_diff or r.kind != "same"]
            if not shown:
                continue
            lines.append(f"# {title}")
            lines.extend(f"{r.label}\t{r.a}\t{r.b}" for r in shown)
        return "\n".join(lines)

    def _diff(self, a: Device, b: Device) -> DeviceDiffResult:
        key = (a.node_id, b.node_id, self._get_revision())
        if self._cache_key != key or self._result is None:
            self._result = diff_devices(
                snapshot_device(a, self._get_links, self._get_address),
                snapshot_device(b, self._get_links, self._get_address),
            )
            self._cache_key = key
        return self._result

    def _device_combo(
        self,
        combo_id: str,
        current: int,
        devices: list[Device],
        by_id: dict[int, Device],
    ) -> int:
        cur = by_id.get(current)
        description = cur.description if cur is not None else ""
        # Room after the dropdown for the description: the icon (long) or the inline text (short).
        spacing = imgui.get_style().item_spacing.x
        if shows_description_icon(description):
            room = description_icon_width() + spacing
        elif description.strip():
            room = imgui.calc_text_size(description).x + spacing
        else:
            room = 0.0
        imgui.set_next_item_width(-room if room else -1)
        label = device_label(cur) if cur is not None else "?"
        if begin_combo(combo_id, label):
            for d in devices:
                if imgui.selectable(
                    f"{device_label(d)}##{combo_id}{d.node_id}",
                    d.node_id == current,
                )[0]:
                    current = d.node_id
                render_description(d.description)
            imgui.end_combo()
        render_description(description)
        return current

    @staticmethod
    def _matches(d: Device, needle: str) -> bool:
        if not needle:
            return True
        return needle in device_label(d).lower() or needle in d.description.lower()

    def _render_section(self, title: str, rows: list[DiffRow]) -> None:
        shown = [r for r in rows if not self._only_diff or r.kind != "same"]
        changed = sum(1 for r in rows if r.kind != "same")
        header = f"{title} ({changed})" if changed else title
        if not collapsing_header(
            f"{header}##cmp_{title}", imgui.TreeNodeFlags_.default_open
        ):
            return
        if not shown:
            imgui.text_disabled(S.COMPARE_NO_DIFFERENCES)
            return
        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.reorderable
        )
        if not begin_data_table(f"##cmp_tbl_{title}", 3, flags):
            return
        imgui.table_setup_column(
            S.COMPARE_COL_FIELD, imgui.TableColumnFlags_.width_stretch, 0.34
        )
        imgui.table_setup_column(
            S.COMPARE_COL_A, imgui.TableColumnFlags_.width_stretch, 0.33
        )
        imgui.table_setup_column(
            S.COMPARE_COL_B, imgui.TableColumnFlags_.width_stretch, 0.33
        )
        data_table_headers()
        clipper = imgui.ListClipper()
        clipper.begin(len(shown))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                self._render_row(shown[i])
        imgui.end_table()

    def _render_row(self, row: DiffRow) -> None:
        color = _KIND_COLOR.get(row.kind)
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        if color is not None:
            imgui.text_colored(color, row.label)
        else:
            text_clipped_tooltip(row.label)
        imgui.table_set_column_index(1)
        text_clipped_tooltip(row.a)
        imgui.table_set_column_index(2)
        text_clipped_tooltip(row.b)
