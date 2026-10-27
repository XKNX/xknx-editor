"""Minimal project tools in one panel (functional, UX kept simple).

Four tabs, each a thin wrapper over existing project services:

- **Extended Copy**: clone a device N times, optionally rewrite its name (find/replace) and
  auto-create a group address for every com-object of each copy.
- **Shift Addresses**: shift many addresses by an offset to open gaps or renumber. Two modes:
  device individual addresses (the device octet), or group addresses (the raw value, with
  com-object links preserved via the project's change-address command).
- **Labels**: pick fields and export the devices as CSV, printable Avery label sheets, or a
  full-page legend (HTML). Rendering lives in :mod:`label_render` (pure, unit-tested).
- **Topology Check**: read-only scan for missing / duplicate / malformed individual addresses.

The pure helpers (:func:`apply_name_swap`, :func:`shifted_ia`, :func:`shifted_ga`,
:func:`topology_findings`) hold all the logic and are unit-tested without imgui.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from imgui_bundle import imgui

from editor_gui import file_dialogs
from editor_gui.device import device_label
from editor_gui.os_open import open_path
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import label_render
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.project_diff import (
    DiffEntry,
    DiffJob,
    DiffOutcome,
    DiffResult,
)
from editor_gui.widgets.checkbox import checkbox, radio_button
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.flow import button_width, same_line_if_fits
from editor_gui.widgets.tabs import begin_tab_bar
from xknxeditor.proj.core.addressing import GroupAddressStyle, format_ga

if TYPE_CHECKING:
    from editor_gui.device import ComObject, Device
    from editor_gui.plugins.project.ui.device_compare import DeviceComparePanel
    from xknxeditor.proj.core.service import DeviceInfo

LinkResult = tuple[int, list[str]]  # (changed count, error messages)


class GroupAddressRow(Protocol):
    """The subset of a project group address the Shift tab needs (id, formatted text, name, raw)."""

    id: int
    address: str  # style-formatted, e.g. "1/2/3"
    name: str
    raw: int  # raw 16-bit value, style-independent


# Labels output modes (index into the radio group).
_MODE_CSV, _MODE_SHEET, _MODE_LEGEND = 0, 1, 2

_ERR_COLOR = imgui.ImVec4(0.90, 0.45, 0.45, 1.0)
_WARN_COLOR = imgui.ImVec4(0.90, 0.75, 0.35, 1.0)


def _help_marker(text: str) -> None:
    """A dimmed ``(?)`` that shows ``text`` as a tooltip on hover."""
    imgui.same_line()
    imgui.text_disabled("(?)")
    if imgui.is_item_hovered():
        imgui.set_tooltip(text)


def _desc(text: str) -> None:
    """A dimmed, wrapping one-liner describing what a tab is for."""
    imgui.push_text_wrap_pos(0.0)
    imgui.text_disabled(text)
    imgui.pop_text_wrap_pos()
    imgui.spacing()


def apply_name_swap(name: str, find: str, replace: str) -> str:
    """Return ``name`` with ``find`` replaced by ``replace``; unchanged when ``find`` is empty."""
    return name.replace(find, replace) if find else name


def shifted_ga(raw: int, offset: int, style: GroupAddressStyle) -> str | None:
    """Shift a raw 16-bit group-address value by ``offset``, formatted for ``style``.

    Returns the new address string, or ``None`` when the result leaves the valid 1..65535
    range (0 is reserved). The operation is on the raw value, so it is independent of how the
    address splits into main/middle/sub."""
    new = raw + offset
    if not 1 <= new <= 0xFFFF:
        return None
    return format_ga(new, style)


def shifted_ia(ia: str, offset: int) -> str | None:
    """Shift the device octet of an ``"area.line.device"`` address by ``offset``.

    Returns the new address, or ``None`` if ``ia`` is malformed or the result leaves the valid
    device-octet range (1-255; 0 is reserved for the line coupler)."""
    parts = ia.split(".")
    if len(parts) != 3:
        return None
    try:
        area, line, device = (int(p) for p in parts)
    except ValueError:
        return None
    if not (0 <= area <= 15 and 0 <= line <= 15 and 0 <= device <= 255):
        return None  # malformed source address
    new_device = device + offset
    if not 1 <= new_device <= 255:
        return None
    return f"{area}.{line}.{new_device}"


def match_by_number(
    old_cos: list[ComObject], new_cos: list[ComObject]
) -> list[tuple[ComObject, ComObject | None]]:
    """Pair each old com-object with the new one of the same ``number`` and object size.

    Returns ``(old_co, new_co | None)`` for every old object; ``None`` means no compatible match
    (Replace Device then reports it as unmapped)."""
    by_number = {co.number: co for co in new_cos}
    result: list[tuple[ComObject, ComObject | None]] = []
    for co in old_cos:
        candidate = by_number.get(co.number)
        if candidate is not None and (
            not co.object_size
            or not candidate.object_size
            or co.object_size == candidate.object_size
        ):
            result.append((co, candidate))
        else:
            result.append((co, None))
    return result


def _co_label(co: ComObject) -> str:
    """A com-object label that distinguishes channel-siblings: number + name + object function.
    Many devices name every object in a channel the same (e.g. 'G: Schlafzimmer'); the function
    text ('Heizen', 'Status', ...) is what tells them apart."""
    label = f"#{co.number}  {co.name}"
    function = getattr(co, "function_text", "")
    if function and function != co.name:
        label = f"{label}  {function}"
    return label


def topology_findings(
    devices: list[tuple[int, str, str]],
) -> list[tuple[int, str, str]]:
    """Scan ``(node_id, name, individual_address)`` triples for address problems.

    Returns ``(node_id, severity, message)`` where severity is ``"error"`` or ``"warning"``."""
    findings: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for node_id, name, ia in devices:
        label = name or f"#{node_id}"
        if not ia:
            findings.append(
                (node_id, "warning", S.TOOLS_TOPOLOGY_NO_ADDRESS.format(device=label))
            )
            continue
        parts = ia.split(".")
        valid = len(parts) == 3 and all(p.isdigit() for p in parts)
        if valid:
            area, line, dev = (int(p) for p in parts)
            valid = 0 <= area <= 15 and 0 <= line <= 15 and 0 <= dev <= 255
        if not valid:
            findings.append(
                (
                    node_id,
                    "error",
                    S.TOOLS_TOPOLOGY_MALFORMED.format(device=label, address=ia),
                )
            )
            continue
        if ia in seen:
            findings.append(
                (
                    node_id,
                    "error",
                    S.TOOLS_TOPOLOGY_DUPLICATE.format(device=label, address=ia),
                )
            )
        seen[ia] = node_id
    return findings


class ToolsPanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_device_info: Callable[[int], DeviceInfo | None],
        is_open: Callable[[], bool],
        on_extended_copy: Callable[[int, int, str, str, bool], LinkResult],
        on_shift_addresses: Callable[[list[int], int], LinkResult],
        on_navigate: Callable[[int], None],
        on_replace_device: Callable[[int, int], LinkResult],
        get_space_path: Callable[[int], str],
        get_device_gas: Callable[[Device], list[str]],
        get_group_addresses: Callable[[], Sequence[GroupAddressRow]],
        get_group_address_style: Callable[[], GroupAddressStyle],
        on_shift_group_addresses: Callable[[list[int], int], LinkResult],
        on_diff_projects: Callable[[str, str | None], DiffJob],
        compare: DeviceComparePanel | None = None,
    ) -> None:
        """``compare`` is the "Compare Devices" tab, after "Replace Device"."""
        self._compare = compare
        # Show the compare tab on the next frame (asked from a device's context menu).
        self._select_compare = False
        self._get_devices = get_devices
        self._get_device_info = get_device_info
        self._is_open = is_open
        self._on_extended_copy = on_extended_copy
        self._on_shift_addresses = on_shift_addresses
        self._on_navigate = on_navigate
        self._on_replace_device = on_replace_device
        self._get_space_path = get_space_path
        self._get_device_gas = get_device_gas
        self._get_group_addresses = get_group_addresses
        self._get_group_address_style = get_group_address_style
        self._on_shift_group_addresses = on_shift_group_addresses
        self._on_diff_projects = on_diff_projects
        # Shows the backups of the project, where Project Diff with one of them starts.
        self.show_backups: Callable[[], None] | None = None

        # Replace Device state (selection by node id, with per-list filters).
        self._repl_target_node: int | None = None
        self._repl_template_node: int | None = None
        self._repl_filters: dict[str, str] = {}
        # Extended Copy state.
        self._copy_device_idx = 0
        self._copy_count = 1
        self._copy_find = ""
        self._copy_replace = ""
        self._copy_create_gas = False
        # Shift state.
        self._shift_offset = 1
        # Shift sub-mode: individual addresses (devices) or group addresses.
        self._shift_mode = "ia"
        # Group-address multi-selection (ga ids) + its own filter.
        self._selected_gas: set[int] = set()
        self._ga_filter = ""
        # Shared device multi-selection (node ids) used by Shift + Labels.
        self._selected_nodes: set[int] = set()
        self._device_filter = ""
        # Cache of immutable device metadata (product/order/manufacturer) for the list rows.
        self._info_cache: dict[int, DeviceInfo | None] = {}
        # Labels: field selection + output mode + export dialog handle/content.
        self._label_fields: set[str] = set(label_render.DEFAULT_FIELDS)
        self._label_mode = _MODE_CSV
        self._label_use_custom = False
        self._label_grid = label_render.CustomGrid()
        self._label_dialog: file_dialogs.SaveFileDialog | None = None
        self._label_content = ""  # pending file body (CSV or HTML)
        self._label_open_after = (
            False  # open the file in the browser after saving (HTML)
        )
        # Last result line (shown under the active tab).
        self._status = ""
        # Project Diff state: file picker handle, last result, the other project's name, error.
        self._diff_dialog: file_dialogs.OpenFileDialog | None = None
        self._diff_result: DiffResult | None = None
        self._diff_other_name = ""
        self._diff_error = ""
        # The other project when it needs a password, and the password entered for it.
        self._diff_password_path: str | None = None
        self._diff_password = ""
        # The password field takes the keyboard focus when it is asked for.
        self._diff_password_focus = False
        # The comparison running on a worker thread, and the project it compares with.
        self._diff_job: DiffJob | None = None
        self._diff_job_path = ""
        # Show the Project Diff tab on the next frame (asked from a backup).
        self._select_diff = False

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.TOOLS_NO_PROJECT)
            return
        self._poll_label_dialog()
        self._poll_diff_dialog()
        if begin_tab_bar("##tools_tabs"):
            if imgui.begin_tab_item(S.TOOLS_TAB_COPY)[0]:
                self._render_copy_tab()
                imgui.end_tab_item()
            if imgui.begin_tab_item(S.TOOLS_TAB_REPLACE)[0]:
                self._render_replace_tab()
                imgui.end_tab_item()
            if self._compare is not None:
                flags = (
                    imgui.TabItemFlags_.set_selected
                    if self._select_compare
                    else imgui.TabItemFlags_.none
                )
                self._select_compare = False
                if imgui.begin_tab_item(
                    f"{S.COMPARE_TITLE}###tools_compare", None, flags
                )[0]:
                    self._compare.render()
                    imgui.end_tab_item()
            if imgui.begin_tab_item(S.TOOLS_TAB_SHIFT)[0]:
                self._render_shift_tab()
                imgui.end_tab_item()
            if imgui.begin_tab_item(S.TOOLS_TAB_LABELS)[0]:
                self._render_labels_tab()
                imgui.end_tab_item()
            flags = (
                imgui.TabItemFlags_.set_selected
                if self._select_diff
                else imgui.TabItemFlags_.none
            )
            self._select_diff = False
            if imgui.begin_tab_item(f"{S.TOOLS_TAB_DIFF}###tools_diff", None, flags)[0]:
                self._render_diff_tab()
                imgui.end_tab_item()
            if imgui.begin_tab_item(S.TOOLS_TAB_TOPOLOGY)[0]:
                self._render_topology_tab()
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def open_diff(self, path: str, name: str, password: str | None = None) -> None:
        """Show the Project Diff tab comparing with the project file ``path``, named ``name``
        there; ``password`` opens a protected one (else it is asked for). A comparison still
        running goes on."""
        self._select_diff = True
        if self._diff_job is not None:
            return
        self._diff_other_name = name
        self._diff_password = ""
        self._run_diff(path, password)

    def open_compare(self, node_id: int) -> None:
        """Show the "Compare Devices" tab with ``node_id`` as the first device."""
        if self._compare is not None:
            self._compare.set_primary(node_id)
            self._select_compare = True

    # -- Extended Copy -----------------------------------------------------

    def _render_copy_tab(self) -> None:
        devices = self._get_devices()
        if not devices:
            imgui.text_disabled(S.TOOLS_NO_DEVICES)
            return
        _desc(S.TOOLS_COPY_DESC)
        imgui.separator_text(S.TOOLS_COPY_SOURCE)
        self._copy_device_idx = self._device_combo(
            "##copy_src", self._copy_device_idx, devices
        )
        current = devices[min(self._copy_device_idx, len(devices) - 1)]
        imgui.text_disabled(S.TOOLS_COPY_COUNT)
        imgui.set_next_item_width(px(160.0))
        _, self._copy_count = imgui.input_int("##copy_count", self._copy_count)
        self._copy_count = max(1, min(self._copy_count, 500))
        imgui.text_disabled(S.TOOLS_COPY_FIND)
        imgui.set_next_item_width(-1)
        _, self._copy_find = imgui.input_text("##copy_find", self._copy_find)
        imgui.text_disabled(S.TOOLS_COPY_REPLACE)
        imgui.set_next_item_width(-1)
        _, self._copy_replace = imgui.input_text("##copy_replace", self._copy_replace)
        _, self._copy_create_gas = checkbox(
            S.TOOLS_COPY_CREATE_GAS, self._copy_create_gas
        )
        imgui.text_disabled(
            S.TOOLS_COPY_PREVIEW.format(
                name=apply_name_swap(current.name, self._copy_find, self._copy_replace)
            )
        )
        imgui.spacing()
        if imgui.button(S.TOOLS_COPY_RUN, imgui.ImVec2(-1, 0)):
            count, errors = self._on_extended_copy(
                current.node_id,
                self._copy_count,
                self._copy_find,
                self._copy_replace,
                self._copy_create_gas,
            )
            self._status = self._summary(S.TOOLS_COPY_DONE.format(count=count), errors)
        self._render_status()

    # -- Replace Device ----------------------------------------------------

    def _device_label(self, device: Device) -> str:
        """'<ia>  <name>' for combos; an unnamed device (often so on import) also shows its
        product, else its order number or manufacturer."""
        label = device_label(device)
        desc = self._unnamed_description(device)
        return f"{label}  ·  {desc}" if desc else label

    def _device_combo(self, combo_id: str, idx: int, devices: list[Device]) -> int:
        """A full-width device dropdown; returns the (possibly changed) selected index."""
        idx = min(idx, len(devices) - 1)
        imgui.set_next_item_width(-1)
        if begin_combo(combo_id, self._device_label(devices[idx])):
            for i, d in enumerate(devices):
                if imgui.selectable(
                    f"{self._device_label(d)}##{combo_id}{i}", i == idx
                )[0]:
                    idx = i
            imgui.end_combo()
        return idx

    def _device_single_select(
        self, key: str, current: int | None, devices: list[Device]
    ) -> int | None:
        """Filter box + scrollable single-select device list; returns the chosen node id."""
        self._repl_filters[key] = filter_box(
            f"##rf_{key}", S.TOOLS_FILTER_HINT, self._repl_filters.get(key, "")
        )
        needle = self._repl_filters[key].strip().lower()
        shown = [d for d in devices if self._matches(d, needle)]
        chosen = current if current is not None else devices[0].node_id
        if imgui.begin_child(f"##rl_{key}", px_vec2(0.0, 150.0), True):
            for d in shown:
                if imgui.selectable(
                    f"{self._device_label(d)}##{key}{d.node_id}", d.node_id == chosen
                )[0]:
                    chosen = d.node_id
        imgui.end_child()
        return chosen

    def _render_replace_tab(self) -> None:
        devices = self._get_devices()
        if not devices:
            imgui.text_disabled(S.TOOLS_NO_DEVICES)
            return
        _desc(S.TOOLS_REPLACE_DESC)
        by_id = {d.node_id: d for d in devices}
        imgui.separator_text(S.TOOLS_REPLACE_TARGET)
        self._repl_target_node = self._device_single_select(
            "target", self._repl_target_node, devices
        )
        imgui.separator_text(S.TOOLS_REPLACE_WITH)
        self._repl_template_node = self._device_single_select(
            "template", self._repl_template_node, devices
        )
        target = by_id.get(self._repl_target_node or -1, devices[0])
        template = by_id.get(self._repl_template_node or -1, devices[0])
        imgui.separator_text(S.TOOLS_REPLACE_PREVIEW)
        pairs = match_by_number(
            target.get_visible_com_objects(), template.get_visible_com_objects()
        )
        mapped = 0
        if imgui.begin_child("##repl_prev", px_vec2(0.0, 220.0), True):
            for old_co, new_co in pairs:
                if new_co is None:
                    imgui.text_colored(_ERR_COLOR, f"{_co_label(old_co)}  ->  —")
                else:
                    mapped += 1
                    imgui.text_wrapped(f"{_co_label(old_co)}  ->  {_co_label(new_co)}")
        imgui.end_child()
        imgui.text_disabled(S.TOOLS_REPLACE_COUNT.format(count=mapped))
        imgui.begin_disabled(mapped == 0)
        if imgui.button(S.TOOLS_REPLACE_RUN, imgui.ImVec2(-1, 0)):
            count, errors = self._on_replace_device(target.node_id, template.node_id)
            self._status = self._summary(
                S.TOOLS_REPLACE_DONE.format(count=count), errors
            )
        imgui.end_disabled()
        self._render_status()

    # -- Shift Addresses ---------------------------------------------------

    def _render_shift_tab(self) -> None:
        # Pick what to shift: device individual addresses, or group addresses.
        if radio_button(S.TOOLS_SHIFT_MODE_IA, self._shift_mode == "ia"):
            self._shift_mode = "ia"
        imgui.same_line()
        if radio_button(S.TOOLS_SHIFT_MODE_GA, self._shift_mode == "ga"):
            self._shift_mode = "ga"
        imgui.separator()
        if self._shift_mode == "ga":
            self._render_shift_ga_tab()
        else:
            self._render_shift_ia_tab()

    def _render_shift_ia_tab(self) -> None:
        devices = self._get_devices()
        if not devices:
            imgui.text_disabled(S.TOOLS_NO_DEVICES)
            return
        _desc(S.TOOLS_SHIFT_DESC)
        imgui.separator_text(S.TOOLS_TAB_SHIFT)
        imgui.text_disabled(S.TOOLS_SHIFT_OFFSET)
        _help_marker(S.TOOLS_SHIFT_HINT)
        imgui.set_next_item_width(px(160.0))
        _, self._shift_offset = imgui.input_int("##shift_offset", self._shift_offset)
        shown = self._filtered(devices, "shift")
        self._device_toolbar(devices, shown)
        # One list = multi-select + live preview: checked rows show -> new (red if invalid).
        valid = 0
        if imgui.begin_child("##shift_list", px_vec2(0.0, 260.0), True):
            for d in shown:
                selected = d.node_id in self._selected_nodes
                changed, new_sel = checkbox(f"##sh{d.node_id}", selected)
                if changed:
                    self._toggle(d.node_id, new_sel)
                    selected = new_sel
                self._device_row_label(d)
                new = shifted_ia(d.individual_address, self._shift_offset)
                if selected and new is None:
                    imgui.same_line()
                    imgui.text_colored(_ERR_COLOR, f"  ->  {S.TOOLS_SHIFT_INVALID}")
                elif selected:
                    valid += 1
                    imgui.same_line()
                    imgui.text(f"  ->  {new}")
        imgui.end_child()
        imgui.text_disabled(S.TOOLS_SHIFT_COUNT.format(count=valid))
        selected = self._selected_devices(devices)
        imgui.begin_disabled(valid == 0 or self._shift_offset == 0)
        if imgui.button(S.TOOLS_SHIFT_RUN, imgui.ImVec2(-1, 0)):
            count, errors = self._on_shift_addresses(
                [d.node_id for d in selected], self._shift_offset
            )
            self._status = self._summary(S.TOOLS_SHIFT_DONE.format(count=count), errors)
        imgui.end_disabled()
        self._render_status()

    def _render_shift_ga_tab(self) -> None:
        gas = self._get_group_addresses()
        if not gas:
            imgui.text_disabled(S.TOOLS_SHIFT_NO_GAS)
            return
        style = self._get_group_address_style()
        _desc(S.TOOLS_SHIFT_GA_DESC)
        imgui.separator_text(S.TOOLS_SHIFT_GA_TITLE)
        imgui.text_disabled(S.TOOLS_SHIFT_OFFSET)
        _help_marker(S.TOOLS_SHIFT_GA_HINT)
        imgui.set_next_item_width(px(160.0))
        _, self._shift_offset = imgui.input_int("##shift_ga_offset", self._shift_offset)

        self._ga_filter = filter_box(
            "##flt_shift_ga", S.TOOLS_FILTER_HINT, self._ga_filter
        )
        flt = self._ga_filter.strip().lower()
        shown = [g for g in gas if not flt or flt in f"{g.address} {g.name}".lower()]
        if imgui.button(S.TOOLS_SELECT_ALL):
            self._selected_gas |= {g.id for g in shown}
        imgui.same_line()
        if imgui.button(S.TOOLS_SELECT_NONE):
            self._selected_gas -= {g.id for g in shown}
        imgui.same_line()
        sel = sum(1 for g in gas if g.id in self._selected_gas)
        imgui.text_disabled(S.TOOLS_SELECTED_COUNT.format(sel=sel, total=len(gas)))

        valid = 0
        if imgui.begin_child("##shift_ga_list", px_vec2(0.0, 260.0), True):
            for g in shown:
                selected = g.id in self._selected_gas
                changed, new_sel = checkbox(f"##shga{g.id}", selected)
                if changed:
                    if new_sel:
                        self._selected_gas.add(g.id)
                    else:
                        self._selected_gas.discard(g.id)
                    selected = new_sel
                imgui.same_line()
                imgui.text(f"{g.address}  {g.name}")
                new = shifted_ga(g.raw, self._shift_offset, style)
                if selected and new is None:
                    imgui.same_line()
                    imgui.text_colored(_ERR_COLOR, f"  ->  {S.TOOLS_SHIFT_INVALID}")
                elif selected:
                    valid += 1
                    imgui.same_line()
                    imgui.text(f"  ->  {new}")
        imgui.end_child()
        imgui.text_disabled(S.TOOLS_SHIFT_COUNT.format(count=valid))
        selected_ids = [g.id for g in gas if g.id in self._selected_gas]
        imgui.begin_disabled(valid == 0 or self._shift_offset == 0)
        if imgui.button(S.TOOLS_SHIFT_GA_RUN, imgui.ImVec2(-1, 0)):
            count, errors = self._on_shift_group_addresses(
                selected_ids, self._shift_offset
            )
            self._status = self._summary(
                S.TOOLS_SHIFT_GA_DONE.format(count=count), errors
            )
        imgui.end_disabled()
        self._render_status()

    # -- shared device multi-selection (Shift + Labels) --------------------

    def _toggle(self, node_id: int, on: bool) -> None:
        if on:
            self._selected_nodes.add(node_id)
        else:
            self._selected_nodes.discard(node_id)

    def _selected_devices(self, devices: list[Device]) -> list[Device]:
        return [d for d in devices if d.node_id in self._selected_nodes]

    def _info(self, node_id: int) -> DeviceInfo | None:
        if node_id not in self._info_cache:
            self._info_cache[node_id] = self._get_device_info(node_id)
        return self._info_cache[node_id]

    def _describe(self, node_id: int) -> str:
        """Short device description for a list row: product, else order number, else manufacturer."""
        info = self._info(node_id)
        if info is None:
            return ""
        return info.product_name or info.order_number or info.manufacturer_name

    def _unnamed_description(self, device: Device) -> str:
        """The description shown after a device the user did not name (one they named shows only
        its name), unless its label already shows it."""
        if device.name:
            return ""
        desc = self._describe(device.node_id)
        return "" if desc in device_label(device) else desc

    def _matches(self, device: Device, flt: str) -> bool:
        if not flt:
            return True
        hay = f"{device_label(device)} {self._describe(device.node_id)}"
        return flt in hay.lower()

    def _filtered(self, devices: list[Device], key: str) -> list[Device]:
        """Render the filter box for ``key`` and return the devices matching it."""
        self._device_filter = filter_box(
            f"##flt_{key}", S.TOOLS_FILTER_HINT, self._device_filter
        )
        flt = self._device_filter.strip().lower()
        return [d for d in devices if self._matches(d, flt)]

    def _device_row_label(self, device: Device) -> None:
        """Render '<ia>  <name>' after a row's checkbox, an unnamed device's description after it."""
        imgui.same_line()
        imgui.text(device_label(device))
        desc = self._unnamed_description(device)
        if desc:
            imgui.same_line()
            imgui.text_disabled(f" · {desc}")

    def _device_toolbar(self, devices: list[Device], shown: list[Device]) -> None:
        """All / None (acting on the filtered ``shown`` set) + a 'N of M selected' counter."""
        if imgui.button(S.TOOLS_SELECT_ALL):
            self._selected_nodes |= {d.node_id for d in shown}
        imgui.same_line()
        if imgui.button(S.TOOLS_SELECT_NONE):
            self._selected_nodes -= {d.node_id for d in shown}
        imgui.same_line()
        sel = sum(1 for d in devices if d.node_id in self._selected_nodes)
        imgui.text_disabled(S.TOOLS_SELECTED_COUNT.format(sel=sel, total=len(devices)))

    # -- Labels ------------------------------------------------------------

    def _render_labels_tab(self) -> None:
        devices = self._get_devices()
        _desc(S.TOOLS_LABELS_DESC)
        # Devices to include.
        imgui.separator_text(S.TOOLS_TAB_LABELS)
        shown = self._filtered(devices, "labels")
        self._device_toolbar(devices, shown)
        if imgui.begin_child("##label_list", px_vec2(0.0, 200.0), True):
            for d in shown:
                selected = d.node_id in self._selected_nodes
                changed, new_sel = checkbox(f"##lb{d.node_id}", selected)
                if changed:
                    self._toggle(d.node_id, new_sel)
                self._device_row_label(d)
        imgui.end_child()
        self._render_field_picker()
        self._render_output_controls()
        chosen = self._selected_devices(devices)
        can_export = bool(chosen) and bool(self._label_fields)
        if not self._label_fields:
            imgui.text_colored(_WARN_COLOR, S.TOOLS_LABELS_NO_FIELDS)
        imgui.begin_disabled(not can_export or self._label_dialog is not None)
        button = (
            S.TOOLS_LABELS_EXPORT
            if self._label_mode == _MODE_CSV
            else S.TOOLS_LABELS_EXPORT_HTML
        )
        if imgui.button(button, imgui.ImVec2(-1, 0)):
            self._start_label_export(chosen)
        imgui.end_disabled()
        self._render_status()

    def _render_field_picker(self) -> None:
        """Checkboxes for the label fields, in canonical order, wrapped across the width."""
        imgui.separator_text(S.TOOLS_LABELS_FIELDS_TITLE)
        labels = S.TOOLS_LABEL_FIELDS
        avail = imgui.get_content_region_avail().x
        used = 0.0
        for fid in label_render.FIELD_IDS:
            selected = fid in self._label_fields
            changed, now = checkbox(f"{labels.get(fid, fid)}##fld_{fid}", selected)
            if changed:
                if now:
                    self._label_fields.add(fid)
                else:
                    self._label_fields.discard(fid)
            used += imgui.get_item_rect_size().x + px(16.0)
            if used < avail - px(120.0):
                imgui.same_line()
            else:
                used = 0.0

    def _render_output_controls(self) -> None:
        imgui.separator_text(S.TOOLS_LABELS_OUTPUT)
        for mode, label in (
            (_MODE_CSV, S.TOOLS_LABELS_MODE_CSV),
            (_MODE_SHEET, S.TOOLS_LABELS_MODE_SHEET),
            (_MODE_LEGEND, S.TOOLS_LABELS_MODE_LEGEND),
        ):
            if radio_button(label, self._label_mode == mode):
                self._label_mode = mode
            imgui.same_line()
        imgui.new_line()
        if self._label_mode == _MODE_SHEET:
            self._render_sheet_controls()

    def _render_sheet_controls(self) -> None:
        _, self._label_use_custom = checkbox(
            S.TOOLS_LABELS_SHEET_CUSTOM, self._label_use_custom
        )
        if not self._label_use_custom:
            imgui.same_line()
            imgui.text_disabled(f"({label_render.SHEET_L7651.name})")
            return
        g = self._label_grid
        imgui.set_next_item_width(px(120.0))
        _, g.cols = imgui.input_int(S.TOOLS_LABELS_GRID_COLS, g.cols)
        imgui.set_next_item_width(px(120.0))
        _, g.rows = imgui.input_int(S.TOOLS_LABELS_GRID_ROWS, g.rows)
        _, wh = imgui.input_float2(
            S.TOOLS_LABELS_GRID_LABEL_MM, (g.label_w_mm, g.label_h_mm)
        )
        g.label_w_mm, g.label_h_mm = wh
        _, tl = imgui.input_float2(
            S.TOOLS_LABELS_GRID_MARGIN_MM, (g.margin_top_mm, g.margin_left_mm)
        )
        g.margin_top_mm, g.margin_left_mm = tl
        _, xy = imgui.input_float2(S.TOOLS_LABELS_GRID_GAP_MM, (g.gap_x_mm, g.gap_y_mm))
        g.gap_x_mm, g.gap_y_mm = xy

    def _start_label_export(self, devices: list[Device]) -> None:
        """Build the file body for the chosen mode and open a save dialog."""
        fields = label_render.order_fields(self._label_fields)
        records = [self._label_record(d) for d in devices]
        header, rows = label_render.build_table(records, fields, S.TOOLS_LABEL_FIELDS)
        if self._label_mode == _MODE_CSV:
            self._label_content = label_render.labels_csv(header, rows)
            self._label_open_after = False
            self._label_dialog = file_dialogs.save_file(
                S.TOOLS_LABELS_EXPORT, "labels.csv", ["CSV", "*.csv"]
            )
            return
        if self._label_mode == _MODE_SHEET:
            sheet = (
                self._label_grid.to_sheet()
                if self._label_use_custom
                else label_render.SHEET_L7651
            )
            self._label_content = label_render.render_labels_html(rows, sheet)
        else:
            self._label_content = label_render.render_legend_html(header, rows)
        self._label_open_after = True
        self._label_dialog = file_dialogs.save_file(
            S.TOOLS_LABELS_EXPORT_HTML, "labels.html", ["HTML", "*.html"]
        )

    def _label_record(self, device: Device) -> dict[str, str]:
        """Resolve one device into a field-id -> string map for the selected label fields."""
        info = self._get_device_info(device.node_id)
        rec: dict[str, str] = {
            "ia": device.individual_address,
            "name": device.name,
            "application": device.app.name if device.app else "",
        }
        if info is not None:
            rec.update(
                description=info.description,
                order=info.order_number,
                manufacturer=info.manufacturer_name,
                product=info.product_name,
                hardware=info.hardware_name,
                serial=info.serial_number,
            )
        # Lazily resolve the two expensive fields only when the user selected them.
        if "location" in self._label_fields:
            rec["location"] = self._get_space_path(device.node_id)
        if "gas" in self._label_fields:
            rec["gas"] = "; ".join(self._get_device_gas(device))
        return rec

    def _poll_label_dialog(self) -> None:
        if self._label_dialog is None or not self._label_dialog.ready():
            return
        path = self._label_dialog.result()
        self._label_dialog = None
        if not path:
            return
        is_html = self._label_open_after
        ext = ".html" if is_html else ".csv"
        if not path.lower().endswith(ext):
            path += ext
        try:
            if is_html:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self._label_content)
            else:
                # utf-8-sig: the BOM makes Excel open the CSV with correct umlauts by default.
                with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                    fh.write(self._label_content)
            self._status = S.TOOLS_LABELS_DONE.format(path=path)
            if is_html:
                open_path(path)
        except OSError as exc:
            self._status = f"{exc}"

    # -- Project Diff ------------------------------------------------------

    def _render_diff_tab(self) -> None:
        _desc(S.TOOLS_DIFF_DESC)
        imgui.separator_text(S.TOOLS_TAB_DIFF)
        self._poll_diff_job()
        running = self._diff_job is not None
        imgui.begin_disabled(running)
        if imgui.button(S.TOOLS_DIFF_PICK):
            self._diff_dialog = file_dialogs.open_file(
                S.TOOLS_DIFF_PICK_TITLE,
                "",
                [
                    S.TOOLS_DIFF_FILTER,
                    "*.knxproj *.xknx *.zip",
                    S.TOOLS_SELECT_ALL,
                    "*",
                ],
            )
        if self.show_backups is not None:
            same_line_if_fits(button_width(S.TOOLS_DIFF_BACKUPS))
            if imgui.button(S.TOOLS_DIFF_BACKUPS):
                self.show_backups()
            if imgui.is_item_hovered(imgui.HoveredFlags_.delay_normal):
                imgui.set_tooltip(S.TOOLS_DIFF_BACKUPS_TIP)
        imgui.end_disabled()
        if self._diff_other_name:
            # What it was compared with on a line of its own, the outcome right after it.
            imgui.text(S.TOOLS_DIFF_COMPARED_WITH.format(name=self._diff_other_name))
        compared = bool(self._diff_other_name)

        def outcome(text: str) -> str:
            """``text``, beside what it was compared with where it fits there."""
            if compared:
                same_line_if_fits(imgui.calc_text_size(text).x)
            return text

        if self._diff_job is not None:
            self._render_diff_progress(self._diff_job)
            return
        if self._diff_password_path is not None:
            self._render_diff_password(self._diff_password_path)
            compared = False
        if self._diff_error:
            imgui.text_colored(_ERR_COLOR, outcome(self._diff_error))
            return
        result = self._diff_result
        if result is None:
            return
        if result.total == 0:
            imgui.text_colored(
                imgui.ImVec4(0.5, 0.8, 0.5, 1.0), outcome(S.TOOLS_DIFF_IDENTICAL)
            )
            return
        imgui.text_disabled(outcome(S.TOOLS_DIFF_COUNT.format(count=result.total)))
        if imgui.begin_child("##diff_result"):
            self._render_diff_section(S.TOOLS_DIFF_DEVICES, result.devices)
            self._render_diff_section(S.TOOLS_DIFF_GAS, result.group_addresses)
        imgui.end_child()

    def _render_diff_progress(self, job: DiffJob) -> None:
        """How far a running comparison is, with the button that stops it."""
        if job.total:
            fraction = job.done / job.total
            text = S.TOOLS_DIFF_PROGRESS.format(done=job.done, total=job.total)
        else:
            # Reading the projects: no count yet, the bar moves back and forth.
            fraction = -1.0 * imgui.get_time()
            text = S.TOOLS_DIFF_READING
        imgui.progress_bar(fraction, imgui.ImVec2(-1, 0), text)
        imgui.begin_disabled(job.cancel_requested)
        if imgui.button(S.BTN_CANCEL):
            job.cancel()
        imgui.end_disabled()

    def _poll_diff_job(self) -> None:
        """Take the outcome of a comparison over once it has ended."""
        job = self._diff_job
        if job is None or not job.future.done():
            return
        self._diff_job = None
        self._show_diff_outcome(self._diff_job_path, job.future.result())

    def _render_diff_section(self, title: str, entries: list[DiffEntry]) -> None:
        if not entries:
            return
        imgui.separator_text(f"{title} ({len(entries)})")
        for e in entries:
            color = {
                "added": imgui.ImVec4(0.5, 0.8, 0.5, 1.0),
                "removed": _ERR_COLOR,
                "changed": _WARN_COLOR,
            }[e.kind]
            tag = S.TOOLS_DIFF_KIND[e.kind]
            imgui.text_colored(color, f"[{tag}]")
            imgui.same_line()
            imgui.text_wrapped(f"{e.key}  {e.detail}".rstrip())
            if e.rows:
                self._render_diff_rows(e)

    def _render_diff_rows(self, entry: DiffEntry) -> None:
        """The parameters and group objects of a changed device, collapsed: what each is in the
        open project and in the other one."""
        label = S.TOOLS_DIFF_COUNT.format(count=len(entry.rows))
        if not imgui.tree_node(f"{label}###diff_rows_{entry.key}"):
            return
        for section, title in (
            ("parameter", S.COMPARE_SECTION_PARAMETERS),
            ("group_object", S.COMPARE_SECTION_COM_OBJECTS),
        ):
            rows = [r for r in entry.rows if r.section == section]
            if not rows:
                continue
            imgui.text_disabled(title)
            flags = (
                imgui.TableFlags_.borders_inner_v
                | imgui.TableFlags_.row_bg
                | imgui.TableFlags_.resizable
            )
            if not imgui.begin_table(f"##diff_{section}_{entry.key}", 3, flags):
                continue
            imgui.table_setup_column(S.COMPARE_COL_FIELD)
            imgui.table_setup_column(S.TOOLS_DIFF_OPEN_PROJECT)
            imgui.table_setup_column(self._diff_other_name)
            imgui.table_headers_row()
            for row in rows:
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                imgui.text_wrapped(row.label)
                imgui.table_set_column_index(1)
                imgui.text_wrapped(row.current or "-")
                imgui.table_set_column_index(2)
                imgui.text_wrapped(row.other or "-")
            imgui.end_table()
        imgui.tree_pop()

    def _poll_diff_dialog(self) -> None:
        if self._diff_dialog is None or not self._diff_dialog.ready():
            return
        files = self._diff_dialog.result()
        self._diff_dialog = None
        if not files:
            return
        path = files[0]
        self._diff_other_name = Path(path).name
        self._diff_password = ""
        self._run_diff(path, None)

    def _run_diff(self, path: str, password: str | None) -> None:
        """Start the comparison; it runs on a worker thread, the tab shows how far it is."""
        self._diff_result, self._diff_error = None, ""
        self._diff_password_path = None
        self._diff_job_path = path
        self._diff_job = self._on_diff_projects(path, password)

    def _show_diff_outcome(self, path: str, outcome: DiffOutcome) -> None:
        if outcome.canceled:
            self._diff_other_name = ""
            return
        self._diff_result, self._diff_error = outcome.result, outcome.error
        self._diff_password_path = path if outcome.needs_password else None
        self._diff_password_focus = outcome.needs_password

    def _render_diff_password(self, path: str) -> None:
        """Ask for the password of the other, password-protected project."""
        imgui.text_wrapped(S.TOOLS_DIFF_PASSWORD_PROMPT)
        imgui.set_next_item_width(px(260.0))
        if self._diff_password_focus:
            imgui.set_keyboard_focus_here()
            self._diff_password_focus = False
        submitted, self._diff_password = imgui.input_text(
            "##diff_password",
            self._diff_password,
            imgui.InputTextFlags_.password | imgui.InputTextFlags_.enter_returns_true,
        )
        imgui.same_line()
        imgui.begin_disabled(not self._diff_password)
        clicked = imgui.button(S.TOOLS_DIFF_COMPARE)
        imgui.end_disabled()
        if (clicked or submitted) and self._diff_password:
            password, self._diff_password = self._diff_password, ""
            self._run_diff(path, password)

    # -- Topology Check ----------------------------------------------------

    def _render_topology_tab(self) -> None:
        devices = self._get_devices()
        findings = topology_findings(
            [(d.node_id, d.name, d.individual_address) for d in devices]
        )
        _desc(S.TOOLS_TOPOLOGY_DESC)
        imgui.separator_text(S.TOOLS_TAB_TOPOLOGY)
        if not findings:
            imgui.text_colored(imgui.ImVec4(0.5, 0.8, 0.5, 1.0), S.TOOLS_TOPOLOGY_OK)
            return
        imgui.text_disabled(S.TOOLS_TOPOLOGY_COUNT.format(count=len(findings)))
        _help_marker(S.TOOLS_TOPOLOGY_NAV)
        if imgui.begin_child("##topo_findings"):
            for node_id, severity, message in findings:
                color = _ERR_COLOR if severity == "error" else _WARN_COLOR
                imgui.push_style_color(imgui.Col_.text, color)
                clicked = imgui.selectable(f"{message}##tf{node_id}", False)[0]
                imgui.pop_style_color()
                if clicked:
                    self._on_navigate(node_id)
        imgui.end_child()

    # -- shared ------------------------------------------------------------

    @staticmethod
    def _summary(done: str, errors: list[str]) -> str:
        return done + (
            "  " + S.TOOLS_ERRORS.format(count=len(errors)) if errors else ""
        )

    def _render_status(self) -> None:
        if self._status:
            imgui.spacing()
            imgui.text_wrapped(self._status)
