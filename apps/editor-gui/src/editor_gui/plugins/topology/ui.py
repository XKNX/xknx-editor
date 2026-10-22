"""Topology map: a layered canvas drawn with the ImGui draw list, with zoom and two modes.

Read-only. *Physical* mode shows Area -> Line -> Device (from individual addresses); *Building*
mode shows Building/Room -> Device (from the location tree). Ctrl+mouse-wheel zooms; a box shows
the device's address, name and application (a shield when it is secured; the tooltip adds its
description), and clicking it selects the device."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from imgui_bundle import imgui

from editor_gui import web
from editor_gui.device import Device, address_order, device_label
from editor_gui.plugins.topology.strings import S
from editor_gui.widgets.backgrounds import child_background, content_background
from editor_gui.widgets.checkbox import radio_button
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.description import (
    ICON_SECURE,
    is_secure_device,
    secure_tooltip,
    wrapped_tooltip,
)
from editor_gui.widgets.dpi import px

# Unscaled layout metrics (multiplied by the zoom factor at draw time).
_ROW_H = 32.0
_BOX_H = 24.0
_LEFT = 10.0
_COL_GAP = 20.0
_TEXT_PAD = 5.0
# Each column is as wide as its longest label, within these bounds; longer labels end in "…".
_COL_MIN_W = (120.0, 150.0, 260.0)
_COL_MAX_W = (240.0, 260.0, 480.0)
_ELLIPSIS = "…"
# Space between a device's label and its shield.
_ICON_GAP = 6.0
# Link-graph node box (unscaled): the width a device label is fitted into, and the clear space
# kept around each box so the force layout, once spread, leaves no two boxes overlapping.
_GRAPH_NODE_W = 210.0
# Device sub-grid gaps inside a room block.
_GRAPH_GAP_X = 34.0
_GRAPH_GAP_Y = 24.0
# Room block: inner padding, header band height, and the gap between blocks when shelf-packed.
_BLOCK_PAD = 18.0
_BLOCK_HEADER = 28.0
_BLOCK_GAP = 72.0
# Group-address hub node (a pill placed at the centroid of its participating devices).
_GA_NODE_W = 180.0


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


@dataclass
class _GraphNode:
    key: int  # unified: a device's node id (>= 0), or a group address drawn as -(ga_id + 1)
    kind: str  # "device" or "ga"
    label: str
    tooltip: str
    cx: float  # box centre in unscaled canvas pixels
    cy: float
    device_id: int | None = None  # the device to select on click (device nodes only)


# One directed edge between node keys (sender -> receiver; device -> GA hub -> device).
_GraphEdge = tuple[int, int]

# A group-address hub from the plugin: ga id, address, name, and its participants
# (device node id, sends, receives).
_GaHub = tuple[int, str, str, tuple[tuple[int, bool, bool], ...]]


@dataclass
class _GraphBlock:
    """A room's box in the link graph: a titled panel enclosing that room's device sub-grid."""

    label: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class _Graph:
    nodes: list[_GraphNode] = field(default_factory=list[_GraphNode])
    edges: list[_GraphEdge] = field(default_factory=list[_GraphEdge])
    # Undirected neighbours per node, for highlighting a hovered device's ego-network.
    adj: dict[int, set[int]] = field(default_factory=dict[int, set[int]])
    blocks: list[_GraphBlock] = field(default_factory=list[_GraphBlock])
    width: float = 0.0  # unscaled canvas size for the scroll region
    height: float = 0.0


class TopologyPanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_space_tree: Callable[[], list[Any]],
        on_select: Callable[[int], None],
        is_open: Callable[[], bool],
        get_link_graph: Callable[[], list[_GaHub]] | None = None,
        get_main_names: Callable[[], dict[int, str]] | None = None,
    ) -> None:
        self._get_devices = get_devices
        self._get_space_tree = get_space_tree
        self._on_select = on_select
        self._is_open = is_open
        # Returns the group-address hubs: each a group address with its participating devices and
        # their direction. The link view draws a node per hub with devices linked to it.
        self._get_link_graph = get_link_graph
        # Main group number -> name, to title the hub blocks.
        self._get_main_names = get_main_names
        self._zoom = 1.0
        # One of the mutually exclusive views: "physical", "building" or "links".
        self._mode = "physical"
        # Link view filters: a room label (or None = all) and a main group number (or None = all).
        self._filter_room: str | None = None
        self._filter_main: int | None = None
        # A node pinned by a plain click: its focus (highlight + spokes) stays while nothing is
        # hovered; hovering another node previews it, then the pin returns.
        self._pinned_key: int | None = None
        # Fit the whole link graph into the canvas once, the next time it is drawn.
        self._fit_view = False
        # (label, width in unscaled pixels) -> the label cut to fit that width
        self._fitted: dict[tuple[str, float], str] = {}
        # Built layout cached by (mode, source-list identity): both source reads are revision-cached
        # (a fresh object on the next edit), and the layout geometry is zoom-independent (zoom is
        # applied in _draw), so it need not be regrouped/re-sorted every frame.
        self._layout_key: tuple[bool, int] | None = None
        self._layout_cache: _Layout | None = None
        # Link-graph layout cached by (devices identity, links identity, filters).
        self._graph_key: tuple[int, int, str | None, int | None] | None = None
        self._graph_cache: _Graph | None = None

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.TOPOLOGY_EMPTY)
            return
        if radio_button(S.TOPOLOGY_MODE_PHYSICAL, self._mode == "physical"):
            self._mode = "physical"
        imgui.same_line()
        if radio_button(S.TOPOLOGY_MODE_BUILDING, self._mode == "building"):
            self._mode = "building"
        if self._get_link_graph is not None:
            imgui.same_line()
            if radio_button(S.TOPOLOGY_MODE_LINKS, self._mode == "links"):
                self._mode = "links"
                self._fit_view = True  # show the whole graph, not a zoomed-in centre
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.TOPOLOGY_SHOW_LINKS_HINT)
        imgui.same_line()
        imgui.text_disabled(
            S.TOPOLOGY_HINT_TOUCH if web.touch_used() else S.TOPOLOGY_HINT
        )
        if self._mode == "links":
            self._render_link_filters()
        is_tree = self._mode in ("physical", "building")
        layout = self._tree_layout() if is_tree else None
        with child_background(content_background()):
            canvas_open = imgui.begin_child(
                "##topo_canvas", imgui.ImVec2(0.0, 0.0), True
            )
        if canvas_open:
            # In the browser, two fingers on the diagram zoom it.
            low = imgui.get_window_pos()
            size = imgui.get_window_size()
            web.pinch_area(low, imgui.ImVec2(low.x + size.x, low.y + size.y))
            self._handle_zoom()
            if layout is not None:
                self._draw(layout)
            else:
                self._draw_graph()
        imgui.end_child()

    def _render_link_filters(self) -> None:
        """Room and main-group drop-downs that scope the link view."""
        imgui.same_line()
        imgui.text_disabled("|")
        rooms = list(self._room_members())
        if self._filter_room not in rooms:
            self._filter_room = None
        imgui.same_line()
        imgui.text_disabled(S.TOPOLOGY_FILTER_ROOM)
        imgui.same_line()
        imgui.set_next_item_width(px(170.0))
        if begin_combo("##topo_room", self._filter_room or S.TOPOLOGY_FILTER_ALL):
            if imgui.selectable(S.TOPOLOGY_FILTER_ALL, self._filter_room is None)[0]:
                self._set_room(None)
            for room in rooms:
                if imgui.selectable(room, room == self._filter_room)[0]:
                    self._set_room(room)
            imgui.end_combo()

        mains = self._main_options()
        if self._filter_main not in {m for m, _ in mains}:
            self._filter_main = None
        current = next((lbl for m, lbl in mains if m == self._filter_main), None)
        imgui.same_line()
        imgui.text_disabled(S.TOPOLOGY_FILTER_GROUP)
        imgui.same_line()
        imgui.set_next_item_width(px(200.0))
        if begin_combo("##topo_main", current or S.TOPOLOGY_FILTER_ALL):
            if imgui.selectable(S.TOPOLOGY_FILTER_ALL, self._filter_main is None)[0]:
                self._set_main(None)
            for main, label in mains:
                if imgui.selectable(label, main == self._filter_main)[0]:
                    self._set_main(main)
            imgui.end_combo()

    def _set_room(self, room: str | None) -> None:
        self._filter_room = room
        self._fit_view = True

    def _set_main(self, main: int | None) -> None:
        self._filter_main = main
        self._fit_view = True

    def _main_options(self) -> list[tuple[int, str]]:
        """Sorted ``(main group number, label)`` present in the link graph, with the group name."""
        hubs = self._get_link_graph() if self._get_link_graph else []
        names = self._get_main_names() if self._get_main_names else {}
        mains: set[int] = set()
        for _gid, address, _name, _parts in hubs:
            try:
                mains.add(int(address.split("/", 1)[0]))
            except ValueError:
                mains.add(0)
        return [
            (m, f"{m}/*  {names[m]}" if m in names else f"{m}/*") for m in sorted(mains)
        ]

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

    def _handle_zoom(self) -> None:
        io = imgui.get_io()
        # Ctrl (Windows/Linux) or Cmd (macOS, where Ctrl+scroll is the OS screen zoom).
        if (
            imgui.is_window_hovered()
            and (io.key_ctrl or io.key_super)
            and io.mouse_wheel != 0.0
        ):
            old = self._zoom
            new = max(0.1, min(2.5, old * (1.1 if io.mouse_wheel > 0 else 0.9)))
            if new != old:
                # Keep the point under the cursor fixed: the content scales by new/old, so the
                # scroll offset to that point scales the same way.
                win = imgui.get_window_pos()
                content = (
                    imgui.get_scroll_x() + io.mouse_pos.x - win.x,
                    imgui.get_scroll_y() + io.mouse_pos.y - win.y,
                )
                ratio = new / old
                self._zoom = new
                imgui.set_scroll_x(imgui.get_scroll_x() + content[0] * (ratio - 1))
                imgui.set_scroll_y(imgui.get_scroll_y() + content[1] * (ratio - 1))

    def _graph(self) -> _Graph:
        """Build (and cache) the link graph. Each group address is one hub NODE; devices link to the
        hub (sender -> hub -> receiver) instead of every co-user of an address pointing at every
        other (which turns a shared central address into an N-squared hairball).

        Two tidy regions: the group-address hubs on top, grouped into one titled block per main
        group; the devices below, one titled block per room. Both are compact sub-grids shelf-packed
        with generous gaps. Spokes connect hubs to devices; hovering focuses one node's links."""
        devices = self._get_devices()
        hubs = self._get_link_graph() if self._get_link_graph else []
        key = (id(devices), id(hubs), self._filter_room, self._filter_main)
        if self._graph_key == key and self._graph_cache is not None:
            return self._graph_cache
        dev_by_id = {d.node_id: d for d in devices}
        # Scope: a chosen room limits the devices; a chosen main group limits the hubs. Unfiltered,
        # a hub needs two devices to be a link; scoped, a single in-scope device is enough (and a
        # shared hub stays visible even with only one of its devices inside the room).
        room_devs = (
            self._room_members().get(self._filter_room)
            if self._filter_room is not None
            else None
        )
        filtered = room_devs is not None or self._filter_main is not None
        kept: list[tuple[int, str, str, list[tuple[int, bool, bool]]]] = []
        for ga_id, address, name, parts in hubs:
            if self._filter_main is not None and _main_of(address) != self._filter_main:
                continue
            placed = [
                (nid, s, r)
                for nid, s, r in parts
                if nid in dev_by_id and (room_devs is None or nid in room_devs)
            ]
            if len(placed) >= (1 if filtered else 2):
                kept.append((ga_id, address, name, placed))
        connected = {nid for _gid, _a, _n, placed in kept for nid, _s, _r in placed}

        ga_pending = [
            self._ga_grid(label, items) for label, items in self._ga_groups(kept)
        ]
        ga_blocks, ga_nodes, ga_bottom = _shelf_pack(ga_pending, 0.0)
        dev_pending = [
            self._room_grid(label, ids, dev_by_id)
            for label, ids in self._rooms(connected)
        ]
        dev_top = ga_bottom + _BLOCK_GAP if ga_pending else 0.0
        dev_blocks, dev_nodes, _bottom = _shelf_pack(dev_pending, dev_top)
        nodes = ga_nodes + dev_nodes
        blocks = ga_blocks + dev_blocks

        edges: list[_GraphEdge] = []
        adj: dict[int, set[int]] = {n.key: set() for n in nodes}
        for ga_id, _address, _name, placed in kept:
            gak = -(ga_id + 1)
            if gak not in adj:
                continue
            for nid, sends, receives in placed:
                if nid not in adj:
                    continue
                if sends:
                    edges.append((nid, gak))
                if receives:
                    edges.append((gak, nid))
                adj[nid].add(gak)
                adj[gak].add(nid)

        width = max([b.x + b.w for b in blocks], default=0.0) + _BLOCK_GAP
        height = max([b.y + b.h for b in blocks], default=0.0) + _BLOCK_GAP
        self._graph_cache = _Graph(nodes, edges, adj, blocks, width, height)
        self._graph_key = key
        return self._graph_cache

    def _ga_groups(
        self, kept: list[tuple[int, str, str, list[tuple[int, bool, bool]]]]
    ) -> list[tuple[str, list[tuple[int, str, str]]]]:
        """Group the kept hubs by main group, each a titled block ("<main>/*  <name>"), sorted."""
        names = self._get_main_names() if self._get_main_names else {}
        groups: dict[int, list[tuple[int, str, str]]] = {}
        for ga_id, address, name, _placed in kept:
            groups.setdefault(_main_of(address), []).append((ga_id, address, name))
        out: list[tuple[str, list[tuple[int, str, str]]]] = []
        for main in sorted(groups):
            items = sorted(groups[main], key=lambda t: _addr_key(t[1]))
            label = f"{main}/*  {names[main]}" if main in names else f"{main}/*"
            out.append((label, items))
        return out

    def _ga_grid(
        self, label: str, items: list[tuple[int, str, str]]
    ) -> tuple[str, list[_GraphNode], float, float]:
        """A main group's hubs as a roughly-square sub-grid of group-address pills (block-relative
        node positions)."""
        n = len(items)
        cols = _grid_cols(n, _GA_NODE_W)
        rows = (n + cols - 1) // cols if n else 1
        cells: list[_GraphNode] = []
        for i, (ga_id, address, name) in enumerate(items):
            dx = _BLOCK_PAD + (i % cols) * (_GA_NODE_W + _GRAPH_GAP_X)
            dy = _BLOCK_PAD + _BLOCK_HEADER + (i // cols) * (_BOX_H + _GRAPH_GAP_Y)
            text = f"{address}  {name}".strip() if name else address
            cells.append(
                _GraphNode(
                    -(ga_id + 1),
                    "ga",
                    text,
                    text,
                    dx + _GA_NODE_W / 2,
                    dy + _BOX_H / 2,
                )
            )
        bw = 2 * _BLOCK_PAD + cols * _GA_NODE_W + (cols - 1) * _GRAPH_GAP_X
        bh = 2 * _BLOCK_PAD + _BLOCK_HEADER + rows * _BOX_H + (rows - 1) * _GRAPH_GAP_Y
        return label, cells, bw, bh

    def _room_order(self) -> list[tuple[str, list[int]]]:
        """Every room as (label, device node ids) in building-tree order, plus a trailing
        'Unassigned' group for devices placed in no space."""
        placed: set[int] = set()
        rooms: list[tuple[str, list[int]]] = []

        def walk(node: Any, prefix: str) -> None:
            name = node.name or node.space_type
            here = [sd.id for sd in node.devices]
            if here:
                rooms.append((f"{prefix}{name}" if prefix else name, here))
                placed.update(here)
            for child in node.children:
                walk(child, f"{prefix}{name} / " if name else prefix)

        for top in self._get_space_tree():
            walk(top, "")
        rest = sorted(d.node_id for d in self._get_devices() if d.node_id not in placed)
        if rest:
            rooms.append((S.TOPOLOGY_UNASSIGNED, rest))
        return rooms

    def _room_members(self) -> dict[str, set[int]]:
        """Room label -> its device node ids (for the room filter)."""
        return {label: set(ids) for label, ids in self._room_order()}

    def _rooms(self, connected: set[int]) -> list[tuple[str, list[int]]]:
        """Rooms restricted to the connected devices; empty rooms dropped."""
        result: list[tuple[str, list[int]]] = []
        for label, ids in self._room_order():
            here = [nid for nid in ids if nid in connected]
            if here:
                result.append((label, here))
        return result

    def _room_grid(
        self, label: str, ids: list[int], dev_by_id: dict[int, Device]
    ) -> tuple[str, list[_GraphNode], float, float]:
        """Lay a room's devices (sorted by address) into a roughly-square sub-grid of device nodes
        (block-relative positions). Returns the room label, the cells, and the block size."""
        ordered = sorted(
            (nid for nid in ids if nid in dev_by_id),
            key=lambda nid: address_order(dev_by_id[nid].individual_address),
        )
        n = len(ordered)
        cols = _grid_cols(n, _GRAPH_NODE_W)
        rows = (n + cols - 1) // cols if n else 1
        cells: list[_GraphNode] = []
        for i, nid in enumerate(ordered):
            dx = _BLOCK_PAD + (i % cols) * (_GRAPH_NODE_W + _GRAPH_GAP_X)
            dy = _BLOCK_PAD + _BLOCK_HEADER + (i // cols) * (_BOX_H + _GRAPH_GAP_Y)
            cells.append(
                _GraphNode(
                    nid,
                    "device",
                    device_label(dev_by_id[nid]),
                    _device_tooltip(dev_by_id[nid]),
                    dx + _GRAPH_NODE_W / 2,
                    dy + _BOX_H / 2,
                    device_id=nid,
                )
            )
        bw = 2 * _BLOCK_PAD + cols * _GRAPH_NODE_W + (cols - 1) * _GRAPH_GAP_X
        bh = 2 * _BLOCK_PAD + _BLOCK_HEADER + rows * _BOX_H + (rows - 1) * _GRAPH_GAP_Y
        return label, cells, bw, bh

    def _fit_to(self, width: float, height: float) -> None:
        """Zoom so a content of ``width`` x ``height`` unscaled pixels fits the canvas, once."""
        avail = imgui.get_content_region_avail()
        base = px(1.0)
        if width > 0 and height > 0 and avail.x > 0 and avail.y > 0:
            fit = min(avail.x / (width * base), avail.y / (height * base))
            self._zoom = max(0.1, min(1.0, fit * 0.96))
        imgui.set_scroll_x(0.0)
        imgui.set_scroll_y(0.0)
        self._fit_view = False

    def _draw_graph(self) -> None:
        graph = self._graph()
        if not graph.nodes:
            imgui.text_disabled(S.TOPOLOGY_NO_LINKS)
            return
        if self._fit_view:
            self._fit_to(graph.width, graph.height)
        z = self._zoom * px(1.0)
        origin = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()
        font = imgui.get_font()
        font_size = imgui.get_font_size() * self._zoom
        node_h = _BOX_H
        centers = {n.key: (n.cx, n.cy) for n in graph.nodes}
        widths = {
            n.key: (_GA_NODE_W if n.kind == "ga" else _GRAPH_NODE_W)
            for n in graph.nodes
        }

        # Room blocks (behind everything): a quiet panel with a header, so the graph reads as rooms.
        block_fill = imgui.get_color_u32(imgui.ImVec4(1.0, 1.0, 1.0, 0.03))
        block_border = imgui.get_color_u32(imgui.ImVec4(0.55, 0.6, 0.68, 0.25))
        header_col = imgui.get_color_u32(imgui.ImVec4(0.82, 0.86, 0.92, 0.9))
        for b in graph.blocks:
            p0 = imgui.ImVec2(origin.x + b.x * z, origin.y + b.y * z)
            p1 = imgui.ImVec2(origin.x + (b.x + b.w) * z, origin.y + (b.y + b.h) * z)
            dl.add_rect_filled(p0, p1, block_fill, px(6.0))
            dl.add_rect(p0, p1, block_border, px(6.0))
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(p0.x + _BLOCK_PAD * z, p0.y + 6 * z),
                header_col,
                self._fit(b.label, b.w - 2 * _BLOCK_PAD),
            )

        mouse = imgui.get_mouse_pos()
        win_hovered = imgui.is_window_hovered()
        hovered_id: int | None = None
        for n in graph.nodes:
            w = widths[n.key]
            x0 = origin.x + (n.cx - w / 2) * z
            y0 = origin.y + (n.cy - node_h / 2) * z
            if (
                win_hovered
                and x0 <= mouse.x <= x0 + w * z
                and y0 <= mouse.y <= y0 + node_h * z
            ):
                hovered_id = n.key
        # A click pins the node under the cursor (or clears the pin on empty space), and on a
        # device also opens it, as in the other views. Hover previews, else the pin stays focused.
        select = imgui.is_mouse_clicked(0)
        if select:
            self._pinned_key = hovered_id
        focus_key = hovered_id if hovered_id is not None else self._pinned_key
        if focus_key is not None and focus_key not in graph.adj:
            focus_key = None  # pinned node is gone (e.g. filter changed)
        focus = focus_key is not None
        incident: set[int] = (
            graph.adj.get(focus_key, set()) if focus_key is not None else set()
        )

        # Without a focused node the spokes are a faint haze; focusing a device lights up the group
        # addresses it is on, focusing a group-address hub lights up every device on it.
        edge_dim = imgui.get_color_u32(imgui.ImVec4(0.5, 0.68, 0.9, 0.12))
        edge_hot = imgui.get_color_u32(imgui.ImVec4(0.6, 0.85, 1.0, 0.95))
        edge_faint = imgui.get_color_u32(imgui.ImVec4(0.5, 0.5, 0.55, 0.03))
        head = px(6.0)
        for s, t in sorted(graph.edges, key=lambda e: _on_focus(e, focus_key)):
            on = (not focus) or s == focus_key or t == focus_key
            col = edge_hot if (focus and on) else edge_faint if focus else edge_dim
            sx, sy = centers[s]
            tx, ty = centers[t]
            dx, dy = tx - sx, ty - sy
            dist = math.hypot(dx, dy) or 1.0
            ux, uy = dx / dist, dy / dist
            # Land the tip just outside the target box: distance from its centre to the box edge
            # along the spoke (so wide hub pills are not pierced).
            half_w, half_h = widths[t] / 2 + 2.0, node_h / 2 + 2.0
            retract = min(
                half_w / abs(ux) if abs(ux) > 1e-6 else 1e9,
                half_h / abs(uy) if abs(uy) > 1e-6 else 1e9,
            )
            tip_x = origin.x + (tx - ux * retract) * z
            tip_y = origin.y + (ty - uy * retract) * z
            tip = imgui.ImVec2(tip_x, tip_y)
            dl.add_line(
                imgui.ImVec2(origin.x + sx * z, origin.y + sy * z),
                tip,
                col,
                px(2.0) if (focus and on) else px(1.3),
            )
            back_x, back_y = tip_x - ux * 2 * head, tip_y - uy * 2 * head
            dl.add_triangle_filled(
                tip,
                imgui.ImVec2(back_x - uy * head, back_y + ux * head),
                imgui.ImVec2(back_x + uy * head, back_y - ux * head),
                col,
            )

        border_col = imgui.get_color_u32(imgui.ImVec4(0.4, 0.4, 0.45, 1.0))
        tooltip_for: _GraphNode | None = None
        for n in graph.nodes:
            active = (not focus) or n.key == focus_key or n.key in incident
            w = widths[n.key]
            p0 = imgui.ImVec2(
                origin.x + (n.cx - w / 2) * z, origin.y + (n.cy - node_h / 2) * z
            )
            p1 = imgui.ImVec2(
                origin.x + (n.cx + w / 2) * z, origin.y + (n.cy + node_h / 2) * z
            )
            is_ga = n.kind == "ga"
            if n.key == focus_key:
                fill = (
                    imgui.ImVec4(0.3, 0.46, 0.4, 1.0)
                    if is_ga
                    else imgui.ImVec4(0.28, 0.36, 0.5, 1.0)
                )
            elif is_ga:
                fill = imgui.ImVec4(0.2, 0.33, 0.29, 1.0)
            elif active:
                fill = imgui.ImVec4(0.2, 0.26, 0.34, 1.0)
            else:
                fill = imgui.ImVec4(0.15, 0.15, 0.17, 1.0)
            rounding = px(node_h / 2) if is_ga else px(4.0)  # hubs are pills
            dl.add_rect_filled(p0, p1, imgui.get_color_u32(fill), rounding)
            dl.add_rect(p0, p1, border_col, rounding)
            # Hub labels stay readable even unfocused; device labels dim when not in the focus.
            text_alpha = 0.95 if (active or is_ga) else 0.4
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(p0.x + (10 if is_ga else 5) * z, p0.y + 4 * z),
                imgui.get_color_u32(imgui.ImVec4(0.92, 0.95, 0.9, text_alpha)),
                self._fit(n.label, w - (14 if is_ga else 6)),
            )
            if n.key == hovered_id:
                tooltip_for = n
                if select and n.device_id is not None:
                    self._on_select(n.device_id)
        if tooltip_for is not None:
            wrapped_tooltip(tooltip_for.tooltip)
        imgui.dummy(imgui.ImVec2(graph.width * z, graph.height * z))

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
                sub_y = sum(dev_rows) / len(dev_rows)
                sub_label = (
                    S.TOPOLOGY_UNASSIGNED
                    if line == S.TOPOLOGY_UNASSIGNED
                    else S.TOPOLOGY_LINE.format(line=line)
                )
                layout.subs.append((sub_label, sub_y))
                area_rows.append(sub_y)
                for dy in dev_rows:
                    layout.links.append((1, sub_y, dy))
            group_y = sum(area_rows) / len(area_rows) if area_rows else 0.0
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
                sub_y = sum(dev_rows) / len(dev_rows)
                layout.subs.append((room_label, sub_y))
                root_rows.append(sub_y)
                for dy in dev_rows:
                    layout.links.append((1, sub_y, dy))
            group_y = sum(root_rows) / len(root_rows) if root_rows else 0.0
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

    def _draw(self, layout: _Layout) -> None:
        widths = self._column_widths(layout)
        xs = [_LEFT]
        for w in widths[:-1]:
            xs.append(xs[-1] + w + _COL_GAP)
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
        # The boxes are dark in every theme: the shield is dimmed, and lighter while hovered.
        icon_col = imgui.get_color_u32(imgui.ImVec4(0.6, 0.6, 0.62, 1.0))
        icon_hover_col = imgui.get_color_u32(imgui.ImVec4(1.0, 1.0, 1.0, 1.0))

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

        for column, y0, y1 in layout.links:
            x0 = xs[column] + widths[column]
            x1 = xs[column + 1]
            dl.add_line(
                imgui.ImVec2(origin.x + x0 * z, origin.y + (y0 + _BOX_H / 2) * z),
                imgui.ImVec2(origin.x + x1 * z, origin.y + (y1 + _BOX_H / 2) * z),
                line_col,
                px(1.5),
            )
        for label, y in layout.groups:
            box(xs[0], y, widths[0], group_col, self._fit(label, widths[0]))
        for label, y in layout.subs:
            box(xs[1], y, widths[1], sub_col, self._fit(label, widths[1]))
        hovered = imgui.is_window_hovered()
        mouse = imgui.get_mouse_pos()
        clicked = imgui.is_mouse_clicked(0)
        secure_w = self._text_width(ICON_SECURE)
        for d in layout.devices:
            label_w = widths[2] - self._icons_room(d)
            box(xs[2], d.y, widths[2], dev_col, self._fit(d.label, label_w))
            bx = origin.x + xs[2] * z
            by = origin.y + d.y * z
            box_hovered = (
                hovered
                and bx <= mouse.x <= bx + widths[2] * z
                and by <= mouse.y <= by + _BOX_H * z
            )
            secure_hovered = False
            if d.secure:
                # At the end of the box.
                sx = bx + (widths[2] - _TEXT_PAD - secure_w) * z
                secure_hovered = box_hovered and mouse.x >= sx - _ICON_GAP / 2 * z
                dl.add_text(
                    font,
                    font_size,
                    imgui.ImVec2(sx, by + 4 * z),
                    icon_hover_col if secure_hovered else icon_col,
                    ICON_SECURE,
                )
            if box_hovered:
                if secure_hovered:
                    imgui.set_tooltip(secure_tooltip())
                else:
                    wrapped_tooltip(d.tooltip)
                if clicked:
                    self._on_select(d.node_id)
        total_w = (xs[2] + widths[2] + _COL_GAP) * z
        total_h = (layout.rows * _ROW_H + _ROW_H) * z
        imgui.dummy(imgui.ImVec2(total_w, total_h))


def _on_focus(edge: _GraphEdge, hovered_id: int | None) -> int:
    """Sort key so a hovered device's edges draw last (on top of the faded rest)."""
    if hovered_id is None:
        return 0
    return 1 if hovered_id in (edge[0], edge[1]) else 0


def _main_of(address: str) -> int:
    """The main group number of a group address ("1/2/3" -> 1); 0 when unparseable."""
    try:
        return int(address.split("/", 1)[0])
    except ValueError:
        return 0


def _addr_key(address: str) -> tuple[int, ...]:
    """Numeric sort key for a group address so "1/2/10" sorts after "1/2/9"."""
    try:
        return tuple(int(part) for part in address.split("/"))
    except ValueError:
        return (1 << 30,)


def _shelf_pack(
    pending: list[tuple[str, list[_GraphNode], float, float]], y_start: float
) -> tuple[list[_GraphBlock], list[_GraphNode], float]:
    """Place titled sub-grid blocks left-to-right into shelves of a gently landscape area, starting
    at ``y_start``. Returns the blocks, their nodes (shifted to absolute positions) and the bottom
    y of the packed region."""
    blocks: list[_GraphBlock] = []
    nodes: list[_GraphNode] = []
    if not pending:
        return blocks, nodes, y_start
    area = sum((bw + _BLOCK_GAP) * (bh + _BLOCK_GAP) for _l, _c, bw, bh in pending)
    widest = max(bw for _l, _c, bw, _bh in pending)
    target = max(widest, math.sqrt(area * 1.6))
    x, y, shelf_h = 0.0, y_start, 0.0
    for label, cells, bw, bh in pending:
        if x > 0 and x + bw > target:
            x = 0.0
            y += shelf_h + _BLOCK_GAP
            shelf_h = 0.0
        blocks.append(_GraphBlock(label, x, y, bw, bh))
        for c in cells:
            nodes.append(
                _GraphNode(
                    c.key, c.kind, c.label, c.tooltip, x + c.cx, y + c.cy, c.device_id
                )
            )
        x += bw + _BLOCK_GAP
        shelf_h = max(shelf_h, bh)
    return blocks, nodes, y + shelf_h


def _grid_cols(n: int, cell_w: float) -> int:
    """Columns for an ``n``-item block of ``cell_w``-wide cells: the count whose block is closest to
    a gently landscape 1.4 aspect ratio, breaking ties toward fewer empty cells."""
    if n <= 1:
        return 1
    best_cols, best_key = 1, None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        w = 2 * _BLOCK_PAD + cols * cell_w + (cols - 1) * _GRAPH_GAP_X
        h = 2 * _BLOCK_PAD + _BLOCK_HEADER + rows * _BOX_H + (rows - 1) * _GRAPH_GAP_Y
        key = (round(abs(math.log((w / h) / 1.4)), 4), cols * rows - n)
        if best_key is None or key < best_key:
            best_key, best_cols = key, cols
    return best_cols


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
