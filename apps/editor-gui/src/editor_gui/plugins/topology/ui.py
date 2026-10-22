"""Topology map: a layered canvas drawn with the ImGui draw list, with zoom and three modes.

Read-only. *Physical* mode shows Area -> Line -> Device (from individual addresses); *Building*
mode shows Building/Room -> Device (from the location tree); each parent joins its children with a
bracket. *Links* shows which devices talk to which (see :mod:`.links_view`). Ctrl+mouse-wheel
zooms, dragging moves; a box shows the device's address, name and application (a shield when it is
secured; the tooltip adds its description), and clicking it selects the device."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from imgui_bundle import imgui

from editor_gui import web
from editor_gui.device import Device, address_order, device_label
from editor_gui.plugins.topology.canvas import (
    DragToMove,
    Palette,
    crisp_zoom,
    zoom_with_wheel,
)
from editor_gui.plugins.topology.links_view import LinkView
from editor_gui.plugins.topology.strings import S
from editor_gui.widgets.backgrounds import child_background, content_background
from editor_gui.widgets.checkbox import radio_button
from editor_gui.widgets.description import (
    ICON_SECURE,
    is_secure_device,
    secure_tooltip,
    wrapped_tooltip,
)
from editor_gui.widgets.dpi import px

# Unscaled layout metrics (multiplied by the zoom factor at draw time), as in the Links view.
_ROW_H = 30.0
_BOX_H = 22.0
_LEFT = 16.0
# The column titles above the boxes.
_TITLE_H = 40.0
# Room between the columns for the brackets joining a parent to its children.
_COL_GAP = 56.0
_TEXT_PAD = 8.0
# Each column is as wide as its longest label, within these bounds; longer labels end in "…".
_COL_MIN_W = (120.0, 150.0, 260.0)
_COL_MAX_W = (240.0, 260.0, 480.0)
_ELLIPSIS = "…"
# Space between a device's label and its shield.
_ICON_GAP = 6.0


@dataclass
class _DevBox:
    y: float
    node_id: int
    label: str
    tooltip: str
    # Commissioned with KNX Data Secure: a shield at the end of the box.
    secure: bool = False


@dataclass
class _Layout:
    groups: list[tuple[str, float]] = field(default_factory=list[tuple[str, float]])
    subs: list[tuple[str, float]] = field(default_factory=list[tuple[str, float]])
    devices: list[_DevBox] = field(default_factory=list[_DevBox])
    # (left column 0 or 1, y in the left column, y in the column right of it)
    links: list[tuple[int, float, float]] = field(
        default_factory=list[tuple[int, float, float]]
    )
    rows: int = 0


class TopologyPanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_space_tree: Callable[[], list[Any]],
        on_select: Callable[[int], None],
        is_open: Callable[[], bool],
        get_link_graph: Callable[
            [], list[tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]]
        ]
        | None = None,
        get_group_names: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._get_devices = get_devices
        self._get_space_tree = get_space_tree
        self._on_select = on_select
        self._is_open = is_open
        self._zoom = 1.0
        # One of the mutually exclusive views: "physical", "building" or "links".
        self._mode = "physical"
        # The tree view drawn last, to place a newly shown one.
        self._shown_mode: str | None = None
        self._drag = DragToMove()
        # (label, width in unscaled pixels) -> the label cut to fit that width
        self._fitted: dict[tuple[str, float], str] = {}
        # Built layout cached by (mode, source-list identity): both source reads are revision-cached
        # (a fresh object on the next edit), and the layout geometry is zoom-independent (zoom is
        # applied in _draw), so it need not be regrouped/re-sorted every frame.
        self._layout_key: tuple[bool, int] | None = None
        self._layout_cache: _Layout | None = None
        self._links = (
            LinkView(
                get_devices,
                get_space_tree,
                get_link_graph,
                get_group_names or dict,
                on_select,
                _device_tooltip,
            )
            if get_link_graph is not None
            else None
        )

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.TOPOLOGY_EMPTY)
            return
        if radio_button(S.TOPOLOGY_MODE_PHYSICAL, self._mode == "physical"):
            self._mode = "physical"
        imgui.same_line()
        if radio_button(S.TOPOLOGY_MODE_BUILDING, self._mode == "building"):
            self._mode = "building"
        if self._links is not None:
            imgui.same_line()
            if radio_button(S.TOPOLOGY_MODE_LINKS, self._mode == "links"):
                self._mode = "links"
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.TOPOLOGY_SHOW_LINKS_HINT)
        links = self._links if self._mode == "links" else None
        self._render_hint()
        if links is not None:
            links.render_toolbar()
        layout = self._tree_layout() if links is None else None
        with child_background(content_background()):
            canvas_open = imgui.begin_child(
                "##topo_canvas",
                imgui.ImVec2(0.0, 0.0),
                imgui.ChildFlags_.borders,
                imgui.WindowFlags_.horizontal_scrollbar,
            )
        if canvas_open:
            # In the browser, two fingers on the diagram zoom it.
            low = imgui.get_window_pos()
            size = imgui.get_window_size()
            web.pinch_area(low, imgui.ImVec2(low.x + size.x, low.y + size.y))
            if layout is not None:
                self._zoom = zoom_with_wheel(self._zoom)
                self._draw(layout, self._drag.update())
            elif links is not None:
                links.render_canvas()
                # The canvas scrolled for the links: a tree view shown next is placed anew.
                self._shown_mode = "links"
        imgui.end_child()

    def _render_hint(self) -> None:
        """How to use the view: after the mode buttons, or on a line of its own where the row is
        too narrow (a phone)."""
        touch = web.touch_used()
        if self._mode == "links":
            text = S.TOPOLOGY_LINKS_HINT_TOUCH if touch else S.TOPOLOGY_LINKS_HINT
        else:
            text = S.TOPOLOGY_HINT_TOUCH if touch else S.TOPOLOGY_HINT
        imgui.same_line()
        if imgui.calc_text_size(text).x <= imgui.get_content_region_avail().x:
            imgui.text_disabled(text)
            return
        imgui.new_line()
        imgui.push_style_color(
            imgui.Col_.text, imgui.get_style().color_(imgui.Col_.text_disabled)
        )
        imgui.text_wrapped(text)
        imgui.pop_style_color()

    def _tree_layout(self) -> _Layout:
        building = self._mode == "building"
        if building:
            source = self._get_space_tree()
            key = (True, id(source))
        else:
            source = self._get_devices()
            key = (False, id(source))
        if self._layout_key != key or self._layout_cache is None:
            self._layout_cache = (
                self._build_building_layout(source)
                if building
                else self._build_physical_layout(source)
            )
            self._layout_key = key
        return self._layout_cache

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
                        _DevBox(
                            y,
                            d.node_id,
                            _device_label(d),
                            _device_tooltip(d),
                            is_secure_device(d.node_id),
                        )
                    )
                    row += 1
                # Level with its first child, so the label shows at the top of its bracket.
                sub_y = dev_rows[0]
                sub_label = (
                    S.TOPOLOGY_UNASSIGNED
                    if line == S.TOPOLOGY_UNASSIGNED
                    else S.TOPOLOGY_LINE.format(line=line)
                )
                layout.subs.append((sub_label, sub_y))
                area_rows.append(sub_y)
                for dy in dev_rows:
                    layout.links.append((1, sub_y, dy))
            group_y = area_rows[0] if area_rows else 0.0
            group_label = (
                area
                if area == S.TOPOLOGY_UNASSIGNED
                else S.TOPOLOGY_AREA.format(area=area)
            )
            layout.groups.append((group_label, group_y))
            for sy in area_rows:
                layout.links.append((0, group_y, sy))
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
                        label = f"{ia}  {sd.name}"
                        tooltip = _with_description(sd.name or ia, sd.description or "")
                    layout.devices.append(
                        _DevBox(
                            y,
                            sd.id,
                            label,
                            tooltip,
                            is_secure_device(sd.id),
                        )
                    )
                    row += 1
                # Level with its first child, so the label shows at the top of its bracket.
                sub_y = dev_rows[0]
                layout.subs.append((room_label, sub_y))
                root_rows.append(sub_y)
                for dy in dev_rows:
                    layout.links.append((1, sub_y, dy))
            group_y = root_rows[0] if root_rows else 0.0
            layout.groups.append((root_label, group_y))
            for sy in root_rows:
                layout.links.append((0, group_y, sy))
        layout.rows = row
        return layout

    def _text_width(self, text: str) -> float:
        """Width of ``text`` in unscaled pixels."""
        return imgui.calc_text_size(text).x / px(1.0)

    def _icons_room(self, d: _DevBox) -> float:
        """Width a device box keeps for its shield, in unscaled pixels."""
        return _ICON_GAP + self._text_width(ICON_SECURE) if d.secure else 0.0

    def _column_widths(self, layout: _Layout) -> list[float]:
        columns = (
            [self._text_width(label) for label, _ in layout.groups],
            [self._text_width(label) for label, _ in layout.subs],
            [self._text_width(d.label) + self._icons_room(d) for d in layout.devices],
        )
        return [
            max(low, min(high, max(widths, default=0.0) + 2 * _TEXT_PAD))
            for widths, low, high in zip(columns, _COL_MIN_W, _COL_MAX_W, strict=True)
        ]

    def _fit(self, label: str, width: float) -> str:
        """``label`` cut and ending in "..." when it is wider than a box of ``width``."""
        key = (label, width)
        fitted = self._fitted.get(key)
        if fitted is None:
            room = width - 2 * _TEXT_PAD
            fitted = label
            if self._text_width(label) > room:
                end = len(label)
                while end > 0 and self._text_width(label[:end] + _ELLIPSIS) > room:
                    end -= 1
                fitted = label[:end].rstrip() + _ELLIPSIS
            if len(self._fitted) > 2000:
                self._fitted.clear()
            self._fitted[key] = fitted
        return fitted

    def _draw(self, layout: _Layout, clicked: bool) -> None:
        widths = self._column_widths(layout)
        xs = [_LEFT]
        for w in widths[:-1]:
            xs.append(xs[-1] + w + _COL_GAP)
        # The font at a whole pixel size, so that the text stays sharp.
        zoom = crisp_zoom(self._zoom)
        z = zoom * px(1.0)
        origin = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()
        font = imgui.get_font()
        font_size = imgui.get_font_size() * zoom
        if self._shown_mode != self._mode:
            # Newly shown on a canvas narrower than the diagram (a phone): start at the second
            # column, so the devices are in view beside their lines or rooms. The canvas scrolls
            # only once it knows the diagram's width (from the frame before).
            total = (xs[2] + widths[2] + _LEFT) * z
            if total <= imgui.get_content_region_avail().x:
                imgui.set_scroll_x(0.0)
                self._shown_mode = self._mode
            elif imgui.get_scroll_max_x() > 0:
                imgui.set_scroll_x((xs[1] - _LEFT) * z)
                self._shown_mode = self._mode
        palette = Palette()
        clip_top = imgui.get_window_pos().y
        clip_bottom = clip_top + imgui.get_window_size().y

        def at(x: float, y: float) -> imgui.ImVec2:
            return imgui.ImVec2(origin.x + x * z, origin.y + (_TITLE_H + y) * z)

        def mid(x: float, y: float) -> imgui.ImVec2:
            return at(x, y + _BOX_H / 2)

        def visible(top: imgui.ImVec2, bottom: imgui.ImVec2) -> bool:
            return bottom.y >= clip_top and top.y <= clip_bottom

        titles = (
            (S.TOPOLOGY_COL_BUILDINGS, S.TOPOLOGY_COL_ROOMS, S.TOPOLOGY_COL_DEVICES)
            if self._mode == "building"
            else (S.TOPOLOGY_COL_AREAS, S.TOPOLOGY_COL_LINES, S.TOPOLOGY_COL_DEVICES)
        )
        for x, title in zip(xs, titles, strict=True):
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(origin.x + (x + 4) * z, origin.y + 12 * z),
                palette.muted,
                title,
            )

        # A hue per area (building), shared by its lines (rooms) and the brackets below them.
        group_hue = {y: i for i, (_label, y) in enumerate(layout.groups)}
        sub_hue: dict[float, int] = {}
        for column, y0, y1 in layout.links:
            if column == 0:
                sub_hue[y1] = group_hue.get(y0, 0)

        # One bracket per parent: a stroke from its box to a spine running along its children, and
        # a short stroke from the spine into each child.
        brackets: dict[tuple[int, float], list[float]] = {}
        for column, y0, y1 in layout.links:
            brackets.setdefault((column, y0), []).append(y1)
        for (column, y0), children in brackets.items():
            top, bottom = min([*children, y0]), max([*children, y0])
            if not visible(mid(0, top), mid(0, bottom)):
                continue
            hue = group_hue.get(y0, 0) if column == 0 else sub_hue.get(y0, 0)
            col = palette.hue(hue, 0.55)
            x0 = xs[column] + widths[column]
            x1 = xs[column + 1]
            spine = (x0 + x1) / 2
            dl.add_line(mid(x0, y0), mid(spine, y0), col, px(1.5))
            if bottom > top:
                dl.add_line(mid(spine, top), mid(spine, bottom), col, px(1.5))
            for y1 in children:
                dl.add_line(mid(spine, y1), mid(x1, y1), col, px(1.5))

        def pill(x: float, y: float, w: float, hue: int, label: str) -> None:
            p0 = at(x, y)
            p1 = imgui.ImVec2(p0.x + w * z, p0.y + _BOX_H * z)
            if not visible(p0, p1):
                return
            rounding = _BOX_H / 2 * z
            dl.add_rect_filled(p0, p1, palette.hue(hue, 0.16), rounding)
            dl.add_rect(p0, p1, palette.hue(hue, 1.0), rounding)
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(p0.x + _TEXT_PAD * z, p0.y + 3 * z),
                palette.text,
                self._fit(label, w),
            )

        for label, y in layout.groups:
            pill(xs[0], y, widths[0], group_hue[y], label)
        for label, y in layout.subs:
            pill(xs[1], y, widths[1], sub_hue.get(y, 0), label)
        hovered = imgui.is_window_hovered()
        mouse = imgui.get_mouse_pos()
        secure_w = self._text_width(ICON_SECURE)
        for d in layout.devices:
            p0 = at(xs[2], d.y)
            p1 = imgui.ImVec2(p0.x + widths[2] * z, p0.y + _BOX_H * z)
            if not visible(p0, p1):
                continue
            box_hovered = (
                hovered and p0.x <= mouse.x <= p1.x and p0.y <= mouse.y <= p1.y
            )
            rounding = 4.0 * z
            dl.add_rect_filled(p0, p1, palette.box, rounding)
            dl.add_rect(
                p0,
                p1,
                palette.text if box_hovered else palette.border,
                rounding,
                thickness=2.0 if box_hovered else 1.0,
            )
            label_w = widths[2] - self._icons_room(d)
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(p0.x + _TEXT_PAD * z, p0.y + 3 * z),
                palette.text,
                self._fit(d.label, label_w),
            )
            secure_hovered = False
            if d.secure:
                # At the end of the box.
                sx = p0.x + (widths[2] - _TEXT_PAD - secure_w) * z
                secure_hovered = box_hovered and mouse.x >= sx - _ICON_GAP / 2 * z
                dl.add_text(
                    font,
                    font_size,
                    imgui.ImVec2(sx, p0.y + 3 * z),
                    palette.text if secure_hovered else palette.muted,
                    ICON_SECURE,
                )
            if box_hovered:
                if secure_hovered:
                    imgui.set_tooltip(secure_tooltip())
                else:
                    wrapped_tooltip(d.tooltip)
                if clicked:
                    self._on_select(d.node_id)
        total_w = (xs[2] + widths[2] + _LEFT) * z
        total_h = (_TITLE_H + layout.rows * _ROW_H + _LEFT) * z
        imgui.dummy(imgui.ImVec2(total_w, total_h))


def _device_label(d: Device) -> str:
    return device_label(d)


def _device_tooltip(d: Device) -> str:
    ia = d.individual_address or "-"
    app = getattr(d.app, "name", "") or ""
    lines = [
        d.display_name or S.TOPOLOGY_TIP_UNNAMED,
        S.TOPOLOGY_TIP_ADDRESS.format(address=ia),
    ]
    if app:
        lines.append(S.TOPOLOGY_TIP_APPLICATION.format(application=app))
    return _with_description("\n".join(lines), d.description)


def _with_description(tooltip: str, description: str) -> str:
    """``tooltip`` followed by the device's description, when it has one."""
    description = description.strip()
    return f"{tooltip}\n\n{description}" if description else tooltip
