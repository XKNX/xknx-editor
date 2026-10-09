"""Topology map: a layered canvas drawn with the ImGui draw list, with zoom and two modes.

Read-only. *Physical* mode shows Area -> Line -> Device (from individual addresses); *Building*
mode shows Building/Room -> Device (from the location tree). Ctrl+mouse-wheel zooms; a box shows
the device's address, name and application, and clicking it selects the device."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from imgui_bundle import imgui

from editor_gui.device import Device, address_order
from editor_gui.plugins.topology.strings import S
from editor_gui.widgets.dpi import px

# Unscaled layout metrics (multiplied by the zoom factor at draw time).
_ROW_H = 32.0
_BOX_H = 24.0
_COL0_X = 10.0
_COL1_X = 150.0
_COL2_X = 320.0
_COL0_W = 120.0
_COL1_W = 150.0
_COL2_W = 260.0


@dataclass
class _DevBox:
    y: float
    node_id: int
    label: str
    tooltip: str


@dataclass
class _Layout:
    groups: list[tuple[str, float]] = field(default_factory=list[tuple[str, float]])
    subs: list[tuple[str, float]] = field(default_factory=list[tuple[str, float]])
    devices: list[_DevBox] = field(default_factory=list[_DevBox])
    links: list[tuple[float, float, float, float]] = field(
        default_factory=list[tuple[float, float, float, float]]
    )
    rows: int = 0


class TopologyPanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_space_tree: Callable[[], list[Any]],
        on_select: Callable[[int], None],
        is_open: Callable[[], bool],
    ) -> None:
        self._get_devices = get_devices
        self._get_space_tree = get_space_tree
        self._on_select = on_select
        self._is_open = is_open
        self._zoom = 1.0
        self._building_mode = False

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.TOPOLOGY_EMPTY)
            return
        _, self._building_mode = imgui.checkbox(
            S.TOPOLOGY_BUILDING_MODE, self._building_mode
        )
        imgui.same_line()
        imgui.text_disabled(S.TOPOLOGY_HINT)
        layout = (
            self._build_building_layout(self._get_space_tree())
            if self._building_mode
            else self._build_physical_layout(self._get_devices())
        )
        if imgui.begin_child("##topo_canvas", imgui.ImVec2(0.0, 0.0), True):
            self._handle_zoom()
            self._draw(layout)
        imgui.end_child()

    def _handle_zoom(self) -> None:
        io = imgui.get_io()
        # Ctrl (Windows/Linux) or Cmd (macOS, where Ctrl+scroll is the OS screen zoom).
        if (
            imgui.is_window_hovered()
            and (io.key_ctrl or io.key_super)
            and io.mouse_wheel != 0.0
        ):
            self._zoom = max(
                0.4, min(2.5, self._zoom * (1.1 if io.mouse_wheel > 0 else 0.9))
            )

    def _build_physical_layout(self, devices: list[Device]) -> _Layout:
        grouped: dict[str, dict[str, list[Device]]] = {}
        for d in devices:
            parts = (d.individual_address or "").split(".")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                area, line = parts[0], parts[1]
            else:
                area = line = S.TOPOLOGY_UNASSIGNED
            grouped.setdefault(area, {}).setdefault(line, []).append(d)

        def _key(a: str) -> tuple[int, int, str]:
            return (0, int(a), "") if a.isdigit() else (1, 0, a)

        layout = _Layout()
        row = 0
        for area in sorted(grouped, key=_key):
            area_rows: list[float] = []
            for line in sorted(grouped[area], key=_key):
                devs = sorted(
                    grouped[area][line],
                    key=lambda d: address_order(d.individual_address),
                )
                dev_rows: list[float] = []
                for d in devs:
                    y = row * _ROW_H
                    dev_rows.append(y)
                    layout.devices.append(
                        _DevBox(y, d.node_id, _device_label(d), _device_tooltip(d))
                    )
                    row += 1
                sub_y = sum(dev_rows) / len(dev_rows)
                sub_label = (
                    S.TOPOLOGY_UNASSIGNED
                    if line == S.TOPOLOGY_UNASSIGNED
                    else f"L {line}"
                )
                layout.subs.append((sub_label, sub_y))
                area_rows.append(sub_y)
                for dy in dev_rows:
                    layout.links.append(
                        (
                            _COL1_X + _COL1_W,
                            sub_y + _BOX_H / 2,
                            _COL2_X,
                            dy + _BOX_H / 2,
                        )
                    )
            group_y = sum(area_rows) / len(area_rows) if area_rows else 0.0
            group_label = area if area == S.TOPOLOGY_UNASSIGNED else f"Area {area}"
            layout.groups.append((group_label, group_y))
            for sy in area_rows:
                layout.links.append(
                    (_COL0_X + _COL0_W, group_y + _BOX_H / 2, _COL1_X, sy + _BOX_H / 2)
                )
        layout.rows = row
        return layout

    def _build_building_layout(self, spaces: list[Any]) -> _Layout:
        # Cross-reference the resolved devices so building boxes show name/application, not just the
        # address (SpaceDeviceInfo only carries id/name/address).
        dev_by_id = {d.node_id: d for d in self._get_devices()}
        # Flatten the space tree to (root building label, room label, devices) groups.
        flat: list[tuple[str, str, list[Any]]] = []

        def walk(node: Any, root_label: str) -> None:
            label = root_label or node.name or node.space_type
            if node.devices:
                flat.append((label, node.name or node.space_type, list(node.devices)))
            for child in node.children:
                walk(child, root_label or node.name or node.space_type)

        for top in spaces:
            walk(top, "")

        layout = _Layout()
        row = 0
        by_root: dict[str, list[tuple[str, list[Any]]]] = {}
        for root_label, room_label, devs in flat:
            by_root.setdefault(root_label, []).append((room_label, devs))
        for root_label, rooms in by_root.items():
            root_rows: list[float] = []
            for room_label, devs in rooms:
                dev_rows: list[float] = []
                for sd in devs:
                    y = row * _ROW_H
                    dev_rows.append(y)
                    dev = dev_by_id.get(sd.id)
                    if dev is not None:
                        label, tooltip = _device_label(dev), _device_tooltip(dev)
                    else:
                        ia = sd.individual_address or "-"
                        label, tooltip = f"{ia}  {sd.name}", sd.name or ia
                    layout.devices.append(_DevBox(y, sd.id, label, tooltip))
                    row += 1
                sub_y = sum(dev_rows) / len(dev_rows)
                layout.subs.append((room_label, sub_y))
                root_rows.append(sub_y)
                for dy in dev_rows:
                    layout.links.append(
                        (
                            _COL1_X + _COL1_W,
                            sub_y + _BOX_H / 2,
                            _COL2_X,
                            dy + _BOX_H / 2,
                        )
                    )
            group_y = sum(root_rows) / len(root_rows) if root_rows else 0.0
            layout.groups.append((root_label, group_y))
            for sy in root_rows:
                layout.links.append(
                    (_COL0_X + _COL0_W, group_y + _BOX_H / 2, _COL1_X, sy + _BOX_H / 2)
                )
        layout.rows = row
        return layout

    def _draw(self, layout: _Layout) -> None:
        z = self._zoom * px(1.0)
        origin = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()
        font = imgui.get_font()
        font_size = imgui.get_font_size() * self._zoom
        line_col = imgui.get_color_u32(imgui.ImVec4(0.5, 0.5, 0.5, 0.6))
        group_col = imgui.get_color_u32(imgui.ImVec4(0.22, 0.34, 0.5, 1.0))
        sub_col = imgui.get_color_u32(imgui.ImVec4(0.28, 0.38, 0.3, 1.0))
        dev_col = imgui.get_color_u32(imgui.ImVec4(0.18, 0.18, 0.2, 1.0))
        border_col = imgui.get_color_u32(imgui.ImVec4(0.4, 0.4, 0.45, 1.0))
        text_col = imgui.get_color_u32(imgui.ImVec4(0.9, 0.9, 0.9, 1.0))

        def box(x: float, y: float, w: float, fill: int, label: str) -> None:
            p0 = imgui.ImVec2(origin.x + x * z, origin.y + y * z)
            p1 = imgui.ImVec2(origin.x + (x + w) * z, origin.y + (y + _BOX_H) * z)
            dl.add_rect_filled(p0, p1, fill, px(4.0))
            dl.add_rect(p0, p1, border_col, px(4.0))
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(p0.x + 5 * z, p0.y + 4 * z),
                text_col,
                label,
            )

        for x0, y0, x1, y1 in layout.links:
            dl.add_line(
                imgui.ImVec2(origin.x + x0 * z, origin.y + y0 * z),
                imgui.ImVec2(origin.x + x1 * z, origin.y + y1 * z),
                line_col,
                px(1.5),
            )
        for label, y in layout.groups:
            box(_COL0_X, y, _COL0_W, group_col, label)
        for label, y in layout.subs:
            box(_COL1_X, y, _COL1_W, sub_col, label)
        hovered = imgui.is_window_hovered()
        mouse = imgui.get_mouse_pos()
        clicked = imgui.is_mouse_clicked(0)
        for d in layout.devices:
            box(_COL2_X, d.y, _COL2_W, dev_col, d.label)
            bx = origin.x + _COL2_X * z
            by = origin.y + d.y * z
            if (
                hovered
                and bx <= mouse.x <= bx + _COL2_W * z
                and by <= mouse.y <= by + _BOX_H * z
            ):
                imgui.set_tooltip(d.tooltip)
                if clicked:
                    self._on_select(d.node_id)
        total_w = (_COL2_X + _COL2_W + 20.0) * z
        total_h = (layout.rows * _ROW_H + _ROW_H) * z
        imgui.dummy(imgui.ImVec2(total_w, total_h))


def _device_label(d: Device) -> str:
    ia = d.individual_address or "-"
    name = d.name or getattr(d.app, "name", "") or "?"
    return f"{ia}  {name}"


def _device_tooltip(d: Device) -> str:
    ia = d.individual_address or "-"
    app = getattr(d.app, "name", "") or ""
    lines = [d.name or "(unnamed)", f"Address: {ia}"]
    if app:
        lines.append(f"Application: {app}")
    return "\n".join(lines)
