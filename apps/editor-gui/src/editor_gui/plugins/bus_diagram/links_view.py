"""The Links view of the Bus Diagram: which devices talk to which over their group addresses.

Sending devices on the left, the group addresses in the middle, receiving devices on the right (see
:mod:`editor_gui.plugins.bus_diagram.flow`). The view shows one part of the project at a time, chosen
above it: the whole project, a room, a main group or a channel of the device shown. Clicking a
device, a channel or a group address shows that one with everything linked to it, Back returns;
hovering highlights a node's links. The arrow on a device lists the group objects it links with,
under their channels; the device shown lists them from the start.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from imgui_bundle import imgui

from editor_gui.device import Device, device_label
from editor_gui.plugins.bus_diagram.canvas import (
    DragToMove,
    Palette,
    crisp_zoom,
    zoom_with_wheel,
)
from editor_gui.plugins.bus_diagram.flow import (
    ROW,
    FlowChannel,
    FlowLayout,
    FlowNode,
    GroupObject,
    Hub,
    NodeKey,
    Scope,
    build_flow,
    chosen_device,
    main_of,
)
from editor_gui.plugins.bus_diagram.strings import S
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.description import wrapped_tooltip
from editor_gui.widgets.dpi import px

# Unscaled geometry: margins, the column titles above, the three columns and the gaps for the links.
_PAD = 16.0
_TITLE_H = 40.0
_COL_W = 290.0
_GAP = 150.0
_BOX_H = 22.0
_TEXT_PAD = 8.0
_MORE_W = 96.0
# The group objects and channel headings of an opened device sit this far in from its box; the
# arrow that opens it takes this much of the box's start.
_INDENT = 18.0
_ARROW_W = 16.0
_X = (_PAD, _PAD + _COL_W + _GAP, _PAD + 2 * (_COL_W + _GAP))
_WIDTH = 2 * _PAD + 3 * _COL_W + 2 * _GAP
# "Fit" zooms out at most this far.
_FIT_MIN = 0.7
_ELLIPSIS = "…"


def _is_device(n: FlowNode) -> bool:
    return n.key[0] in ("s", "r")


def _left(n: FlowNode | FlowChannel) -> float:
    """Where a node or channel heading starts, in unscaled pixels."""
    nested = isinstance(n, FlowChannel) or n.owner is not None
    return _X[n.column] + (_INDENT if nested else 0.0)


class LinkView:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_space_tree: Callable[[], list[Any]],
        get_hubs: Callable[[], list[Hub]],
        get_group_names: Callable[[], dict[str, str]],
        on_open: Callable[[int], None],
        device_tooltip: Callable[[Device], str],
        get_group_objects: Callable[[int], Mapping[int, GroupObject]] = (lambda _n: {}),
    ) -> None:
        self._get_devices = get_devices
        self._get_space_tree = get_space_tree
        self._get_hubs = get_hubs
        self._get_group_names = get_group_names
        self._on_open = on_open
        self._device_tooltip = device_tooltip
        self._get_group_objects = get_group_objects
        # The whole project first; a room, a main group, a device, a channel or a group address on
        # choice.
        self._scope = Scope("all")
        self._history: list[Scope] = []
        # Group addresses whose devices outside the scope are shown one by one.
        self._expanded: set[int] = set()
        # Devices opened with their arrow, and the device shown when it was closed with it.
        self._opened: set[int] = set()
        self._closed: set[int] = set()
        self._zoom = 1.0
        # Fit the zoom to the canvas once (at first and on Fit); another part shown keeps the zoom
        # and starts at the top.
        self._fit_pending = True
        self._top_pending = False
        self._drag = DragToMove()
        self._rooms_key: tuple[int, int] | None = None
        self._rooms: list[tuple[str, list[int]]] = []
        self._layout_key: tuple[Any, ...] | None = None
        self._layout: FlowLayout | None = None
        self._fitted: dict[tuple[str, float], str] = {}

    # --- choosing what to show -----------------------------------------------------------------

    def render_toolbar(self) -> None:
        """The choice of what to show, Back and Fit, in one row."""
        hubs = self._get_hubs()
        scope = self._scope
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.BUS_DIAGRAM_SCOPE)
        imgui.same_line()
        # As wide as fits beside Back and Fit, at most 320 px (narrower on a phone).
        style = imgui.get_style()
        buttons = (
            imgui.calc_text_size(S.BUS_DIAGRAM_BACK).x
            + imgui.calc_text_size(S.BUS_DIAGRAM_FIT).x
            + 4 * style.frame_padding.x
            + 2 * style.item_spacing.x
        )
        room = imgui.get_content_region_avail().x - buttons
        imgui.set_next_item_width(max(px(100.0), min(px(320.0), room)))
        if begin_combo("##topo_scope", self._scope_label(scope, hubs)):
            if imgui.selectable(S.BUS_DIAGRAM_SCOPE_ALL, scope == Scope("all"))[0]:
                self.show(Scope("all"))
            rooms = [label for label, _ids in self._room_order()]
            if rooms:
                imgui.separator_text(S.BUS_DIAGRAM_SCOPE_ROOMS)
                for label in rooms:
                    if imgui.selectable(label, scope == Scope("room", label))[0]:
                        self.show(Scope("room", label))
            mains = sorted({main_of(h.address) for h in hubs if len(h.members) > 1})
            if mains:
                imgui.separator_text(S.BUS_DIAGRAM_SCOPE_MAINS)
                for main in mains:
                    candidate = Scope("main", main)
                    if imgui.selectable(
                        self._scope_label(candidate, hubs), scope == candidate
                    )[0]:
                        self.show(candidate)
            device = chosen_device(scope)
            channels = self._linked_channels(device, hubs) if device is not None else []
            if device is not None and channels:
                imgui.separator_text(S.BUS_DIAGRAM_SCOPE_CHANNELS)
                for channel, label in channels:
                    candidate = Scope("channel", device, channel)
                    if imgui.selectable(f"{label}##{channel}", scope == candidate)[0]:
                        self.show(candidate)
            imgui.end_combo()
        imgui.same_line()
        imgui.begin_disabled(not self._history)
        if imgui.button(S.BUS_DIAGRAM_BACK):
            self._scope = self._history.pop()
            self._expanded.clear()
            self._closed.clear()
            self._top_pending = True
        imgui.end_disabled()
        imgui.same_line()
        if imgui.button(S.BUS_DIAGRAM_FIT):
            self._fit_pending = True

    def show(self, scope: Scope) -> None:
        """Show ``scope``; Back returns to what was shown before."""
        if scope == self._scope:
            return
        self._history.append(self._scope)
        self._scope = scope
        self._expanded.clear()
        self._closed.clear()
        self._top_pending = True

    def _linked_channels(self, node_id: int, hubs: list[Hub]) -> list[tuple[str, str]]:
        """The channels of a device that link with a group address, as ``(id, label)`` in their
        order."""
        objects = self._get_group_objects(node_id)
        linked = {
            co
            for hub in hubs
            for m in hub.members
            if m.node_id == node_id
            for co, _s, _r in m.objects
        }
        found = {
            (o.channel_order, o.channel, o.channel_label)
            for co, o in objects.items()
            if co in linked and o.channel
        }
        return [(channel, label) for _order, channel, label in sorted(found)]

    def _channel_label(self, node_id: int, channel: str) -> str:
        objects = self._get_group_objects(node_id).values()
        return next(
            (
                o.channel_label
                for o in objects
                if o.channel == channel and o.channel_label
            ),
            channel,
        )

    def _scope_label(self, scope: Scope, hubs: list[Hub]) -> str:
        if scope.kind == "all":
            return S.BUS_DIAGRAM_SCOPE_ALL
        if scope.kind == "room":
            return str(scope.key)
        if scope.kind == "main":
            name = self._get_group_names().get(str(scope.key), "")
            return f"{scope.key}/*  {name}".rstrip()
        if scope.kind in ("device", "channel"):
            device = next(
                (d for d in self._get_devices() if d.node_id == scope.key), None
            )
            if scope.kind == "channel":
                return S.BUS_DIAGRAM_SCOPE_CHANNEL.format(
                    channel=self._channel_label(int(scope.key or 0), scope.channel),
                    device=(device.individual_address or device.display_name)
                    if device
                    else str(scope.key),
                )
            text = device_label(device) if device else str(scope.key)
            return S.BUS_DIAGRAM_SCOPE_DEVICE.format(device=text)
        hub = next((h for h in hubs if h.ga_id == scope.key), None)
        text = f"{hub.address}  {hub.name}".strip() if hub else str(scope.key)
        return S.BUS_DIAGRAM_SCOPE_GA.format(address=text)

    def _room_order(self) -> list[tuple[str, list[int]]]:
        """Every room as (label, device node ids) in building-tree order, plus a trailing
        'Unassigned' group for devices placed in no space; cached while the sources are unchanged."""
        tree, devices = self._get_space_tree(), self._get_devices()
        key = (id(tree), id(devices))
        if key == self._rooms_key:
            return self._rooms
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

        for top in tree:
            walk(top, "")
        rest = sorted(d.node_id for d in devices if d.node_id not in placed)
        if rest:
            rooms.append((S.BUS_DIAGRAM_UNASSIGNED, rest))
        self._rooms_key, self._rooms = key, rooms
        return rooms

    def _open_devices(self, scope: Scope) -> set[int]:
        """The devices that list their group objects: those opened with their arrow, and the one
        shown unless it was closed with it."""
        opened = set(self._opened)
        chosen = chosen_device(scope)
        if chosen is not None and chosen not in self._closed:
            opened.add(chosen)
        return opened

    def _toggle(self, node_id: int) -> None:
        """Open or close a device's list of group objects."""
        if node_id in self._open_devices(self._scope):
            self._opened.discard(node_id)
            if node_id == chosen_device(self._scope):
                self._closed.add(node_id)
        else:
            self._opened.add(node_id)
            self._closed.discard(node_id)

    def _flow(self, scope: Scope, hubs: list[Hub]) -> FlowLayout:
        devices = self._get_devices()
        names = self._get_group_names()
        objects = {
            nid: self._get_group_objects(nid) for nid in self._open_devices(scope)
        }
        key = (
            scope,
            id(hubs),
            id(devices),
            id(names),
            frozenset(self._expanded),
            frozenset((nid, id(o)) for nid, o in objects.items()),
        )
        if key != self._layout_key or self._layout is None:
            self._layout = build_flow(
                hubs,
                scope,
                {label: set(ids) for label, ids in self._room_order()},
                {d.node_id: device_label(d) for d in devices},
                names,
                self._expanded,
                objects,
            )
            self._layout_key = key
        return self._layout

    # --- the diagram ---------------------------------------------------------------------------

    def render_canvas(self) -> None:
        """The diagram, inside the scrolling canvas window."""
        hubs = self._get_hubs()
        layout = self._flow(self._scope, hubs)
        if not layout.nodes:
            imgui.text_disabled(S.BUS_DIAGRAM_NO_LINKS)
            return
        if self._fit_pending:
            # The width of the three columns, but never so small that the text is unreadable: a
            # narrower canvas (a phone) scrolls sideways instead.
            avail = imgui.get_content_region_avail().x
            self._zoom = max(_FIT_MIN, min(1.0, avail / (_WIDTH * px(1.0))))
            # Wider than the canvas: the group addresses in the middle come into view.
            imgui.set_scroll_x(max(0.0, (_WIDTH * self._zoom * px(1.0) - avail) / 2))
            imgui.set_scroll_y(0.0)
            self._fit_pending = self._top_pending = False
        elif self._top_pending:
            imgui.set_scroll_y(0.0)
            self._top_pending = False
        self._zoom = zoom_with_wheel(self._zoom)
        clicked = self._drag.update()
        self._draw(layout, self._scope, hubs, clicked)

    def _draw(
        self, layout: FlowLayout, scope: Scope, hubs: list[Hub], clicked: bool
    ) -> None:
        # The font at a whole pixel size, so that the text stays sharp.
        zoom = crisp_zoom(self._zoom)
        z = zoom * px(1.0)
        origin = imgui.get_cursor_screen_pos()
        dl = imgui.get_window_draw_list()
        font = imgui.get_font()
        font_size = imgui.get_font_size() * zoom
        clip_top = imgui.get_window_pos().y
        clip_bottom = clip_top + imgui.get_window_size().y
        palette = Palette()

        def to_screen(x: float, y: float) -> imgui.ImVec2:
            return imgui.ImVec2(origin.x + x * z, origin.y + (_TITLE_H + y) * z)

        def visible(top: float, bottom: float) -> bool:
            return bottom >= clip_top and top <= clip_bottom

        nodes = {n.key: n for n in layout.nodes}
        mouse = imgui.get_mouse_pos()
        hovered = imgui.is_window_hovered()

        def under_mouse(x0: float, x1: float, y: float) -> bool:
            p0 = to_screen(x0, y)
            return (
                hovered
                and p0.x <= mouse.x <= p0.x + (x1 - x0) * z
                and p0.y <= mouse.y <= p0.y + _BOX_H * z
            )

        hovered_node: FlowNode | None = None
        hovered_more: int | None = None
        hovered_channel: FlowChannel | None = None
        for n in layout.nodes:
            if under_mouse(_left(n), _X[n.column] + _COL_W, n.y):
                hovered_node = n
        for c in layout.channels:
            if under_mouse(_left(c), _X[c.column] + _COL_W, c.y):
                hovered_channel = c
        for i, more in enumerate(layout.more):
            x0 = _X[1] - _MORE_W - 12 if more.column == 0 else _X[1] + _COL_W + 12
            if under_mouse(x0, x0 + _MORE_W, more.y):
                hovered_more = i
        # The arrow at the start of a device's box opens or closes its group objects.
        on_arrow = (
            hovered_node is not None
            and _is_device(hovered_node)
            and mouse.x <= to_screen(_X[hovered_node.column] + _ARROW_W + 4, 0).x
        )

        # The hovered node with everything linked to it: a device counts on both sides with its
        # group objects, a channel with its group objects; a group object lights its device.
        own: set[NodeKey] = set()
        if hovered_node is not None:
            kind, ident = hovered_node.key
            if _is_device(hovered_node):
                own = {("s", ident), ("r", ident)}
                own |= {n.key for n in layout.nodes if n.owner == ident}
            else:
                own = {hovered_node.key}
        elif hovered_channel is not None:
            c = hovered_channel
            own = {
                n.key
                for n in layout.nodes
                if n.owner == c.node_id
                and n.channel == c.channel
                and n.column == c.column
            }
        focus = {k for k in own if k in nodes}
        for k in list(focus):
            focus |= layout.adj.get(k, set())
        for k in list(focus):
            owner = nodes[k].owner if k in nodes else None
            if owner is not None:
                focus.add((k[0][0], owner))

        for column, title in zip(
            _X,
            (
                S.BUS_DIAGRAM_COL_SENDERS,
                S.BUS_DIAGRAM_COL_GROUP_ADDRESSES,
                S.BUS_DIAGRAM_COL_RECEIVERS,
            ),
            strict=True,
        ):
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(origin.x + (column + 4) * z, origin.y + 12 * z),
                palette.muted,
                title,
            )
        for y, title in layout.headers:
            p = to_screen(_X[1] + 4, y + 6)
            if visible(p.y - ROW * z, p.y + ROW * z):
                dl.add_text(
                    font, font_size * 0.92, p, palette.muted, self._fit(title, _COL_W)
                )

        # Links first, the highlighted ones last (on top).
        lit = [e for e in layout.edges if focus and e[0] in focus and e[1] in focus]
        for source, target, main in [e for e in layout.edges if e not in lit] + lit:
            a, b = nodes[source], nodes[target]
            p0 = to_screen(_X[a.column] + _COL_W, a.y + _BOX_H / 2)
            p3 = to_screen(_left(b), b.y + _BOX_H / 2)
            if not visible(min(p0.y, p3.y), max(p0.y, p3.y)):
                continue
            on = bool(focus) and source in focus and target in focus
            alpha = 1.0 if on else 0.08 if focus else 0.45
            col = palette.hue(main, alpha)
            dx = (p3.x - p0.x) * 0.5
            dl.add_bezier_cubic(
                p0,
                imgui.ImVec2(p0.x + dx, p0.y),
                imgui.ImVec2(p3.x - dx, p3.y),
                p3,
                col,
                px(2.2) if on else px(1.3),
            )
            head = 6.0 * z
            dl.add_triangle_filled(
                p3,
                imgui.ImVec2(p3.x - head, p3.y - head / 2),
                imgui.ImVec2(p3.x - head, p3.y + head / 2),
                col,
            )

        for n in layout.nodes:
            p0 = to_screen(_left(n), n.y)
            if not visible(p0.y, p0.y + _BOX_H * z):
                continue
            dim = bool(focus) and n.key not in focus
            self._draw_node(
                dl,
                font,
                font_size,
                palette,
                n,
                p0,
                z,
                dim,
                n is hovered_node and not on_arrow,
                n is hovered_node and on_arrow,
            )
        for c in layout.channels:
            p = to_screen(_left(c) + 4, c.y + 3)
            if not visible(p.y, p.y + _BOX_H * z):
                continue
            lit_channel = c is hovered_channel
            fade = (
                0.35
                if focus
                and not lit_channel
                and not any(
                    n.owner == c.node_id and n.channel == c.channel and n.key in focus
                    for n in layout.nodes
                )
                else 1.0
            )
            color = (
                palette.text
                if lit_channel
                else palette.text_alpha((0.62 if c.inside else 0.45) * fade)
            )
            dl.add_text(font, font_size, p, color, self._fit(c.label, _COL_W - _INDENT))
        for i, more in enumerate(layout.more):
            x0 = _X[1] - _MORE_W - 12 if more.column == 0 else _X[1] + _COL_W + 12
            p0 = to_screen(x0, more.y)
            if not visible(p0.y, p0.y + _BOX_H * z):
                continue
            p1 = imgui.ImVec2(p0.x + _MORE_W * z, p0.y + _BOX_H * z)
            lit_more = i == hovered_more
            dl.add_rect_filled(p0, p1, palette.box, _BOX_H / 2 * z)
            dl.add_rect(
                p0, p1, palette.text if lit_more else palette.border, _BOX_H / 2 * z
            )
            label = S.BUS_DIAGRAM_MORE.format(count=more.count)
            width = imgui.calc_text_size(label).x * zoom
            dl.add_text(
                font,
                font_size,
                imgui.ImVec2(p0.x + (_MORE_W * z - width) / 2, p0.y + 3 * z),
                palette.text if lit_more else palette.muted,
                label,
            )

        imgui.dummy(imgui.ImVec2(_WIDTH * z, (_TITLE_H + layout.height + _PAD) * z))

        double = imgui.is_mouse_double_clicked(0)
        if hovered_more is not None:
            more = layout.more[hovered_more]
            imgui.set_tooltip(S.BUS_DIAGRAM_MORE_TIP.format(count=more.count))
            if clicked:
                self._expanded.add(more.ga_id)
        elif hovered_channel is not None:
            c = hovered_channel
            wrapped_tooltip(c.label)
            if clicked:
                self.show(Scope("channel", c.node_id, c.channel))
        elif hovered_node is not None:
            kind, ident = hovered_node.key
            if on_arrow:
                imgui.set_tooltip(
                    S.BUS_DIAGRAM_CLOSE_OBJECTS
                    if hovered_node.opened
                    else S.BUS_DIAGRAM_OPEN_OBJECTS
                )
                if clicked:
                    self._toggle(ident)
                return
            if kind == "g":
                hub = next((h for h in hubs if h.ga_id == ident), None)
                senders = sum(1 for m in hub.members if m.sends) if hub else 0
                receivers = sum(1 for m in hub.members if m.receives) if hub else 0
                wrapped_tooltip(
                    S.BUS_DIAGRAM_GA_TIP.format(
                        address=hovered_node.label, senders=senders, receivers=receivers
                    )
                )
                if clicked and Scope("ga", ident) != scope:
                    self.show(Scope("ga", ident))
                return
            device_id = hovered_node.owner if hovered_node.owner is not None else ident
            if hovered_node.owner is not None:
                wrapped_tooltip(self._object_tooltip(device_id, ident))
            else:
                device = next(
                    (d for d in self._get_devices() if d.node_id == device_id), None
                )
                if device is not None:
                    wrapped_tooltip(self._device_tooltip(device))
            if double:
                self._on_open(device_id)
            elif clicked:
                target = (
                    Scope("channel", device_id, hovered_node.channel)
                    if hovered_node.channel
                    else Scope("device", device_id)
                )
                if target != scope:
                    self.show(target)

    def _object_tooltip(self, node_id: int, group_object: int) -> str:
        """A group object's number and name, its channel and its device."""
        device = next((d for d in self._get_devices() if d.node_id == node_id), None)
        o = self._get_group_objects(node_id).get(group_object)
        lines = [device_label(device)] if device is not None else []
        if o is not None:
            lines.append(S.BUS_DIAGRAM_TIP_OBJECT.format(number=o.number, name=o.name))
            if o.channel_label:
                lines.append(S.BUS_DIAGRAM_TIP_CHANNEL.format(channel=o.channel_label))
        return "\n".join(lines)

    def _draw_node(
        self,
        dl: imgui.ImDrawList,
        font: imgui.ImFont,
        font_size: float,
        palette: Palette,
        n: FlowNode,
        p0: imgui.ImVec2,
        z: float,
        dim: bool,
        hovered: bool,
        arrow_hovered: bool = False,
    ) -> None:
        width = _X[n.column] + _COL_W - _left(n)
        p1 = imgui.ImVec2(p0.x + width * z, p0.y + _BOX_H * z)
        fade = 0.35 if dim else 1.0
        text_pos = imgui.ImVec2(p0.x + _TEXT_PAD * z, p0.y + 3 * z)
        if n.column == 1:
            rounding = _BOX_H / 2 * z
            dl.add_rect_filled(p0, p1, palette.hue(n.main, 0.16 * fade), rounding)
            dl.add_rect(
                p0,
                p1,
                palette.hue(n.main, fade),
                rounding,
                thickness=2.0 if hovered else 1.0,
            )
            dl.add_text(font, font_size, text_pos, palette.hue(n.main, fade), n.address)
            offset = self._text_width(n.address) + 8
            # A little spare room: text drawn at another zoom is not exactly as wide.
            room = _COL_W - 3 * _TEXT_PAD - offset
            if n.name and room > 20:
                dl.add_text(
                    font,
                    font_size,
                    imgui.ImVec2(text_pos.x + offset * z, text_pos.y),
                    palette.text_alpha(fade),
                    self._fit(n.name, room + 2 * _TEXT_PAD),
                )
            return
        # The theme's corner rounding (as on buttons and fields), zoomed with the diagram.
        rounding = imgui.get_style().frame_rounding * z / px(1.0)
        dl.add_rect_filled(
            p0, p1, palette.box if n.inside else palette.box_outside, rounding
        )
        # The device shown stands out on both sides.
        border = (
            palette.text
            if hovered or n.chosen
            else palette.border
            if n.inside
            else palette.border_outside
        )
        thickness = 2.0 if hovered or n.chosen else 1.0
        dl.add_rect(p0, p1, border, rounding, thickness=thickness)
        # Devices outside the chosen part: quieter, still readable.
        color = palette.text_alpha(fade if n.inside else 0.62 * fade)
        if not _is_device(n):
            dl.add_text(font, font_size, text_pos, color, self._fit(n.label, width))
            return
        # The arrow: pointing right while closed, down while the group objects are listed.
        cx, cy = p0.x + (_TEXT_PAD + 3) * z, p0.y + _BOX_H / 2 * z
        s = 3.5 * z
        arrow = palette.text if arrow_hovered else palette.text_alpha(0.55 * fade)
        if n.opened:
            dl.add_triangle_filled(
                imgui.ImVec2(cx - s, cy - s / 2),
                imgui.ImVec2(cx + s, cy - s / 2),
                imgui.ImVec2(cx, cy + s * 0.8),
                arrow,
            )
        else:
            dl.add_triangle_filled(
                imgui.ImVec2(cx - s / 2, cy - s),
                imgui.ImVec2(cx + s * 0.8, cy),
                imgui.ImVec2(cx - s / 2, cy + s),
                arrow,
            )
        dl.add_text(
            font,
            font_size,
            imgui.ImVec2(text_pos.x + _ARROW_W * z, text_pos.y),
            color,
            self._fit(n.label, _COL_W - _ARROW_W),
        )

    def _text_width(self, text: str) -> float:
        """Width of ``text`` in unscaled pixels."""
        return imgui.calc_text_size(text).x / px(1.0)

    def _fit(self, label: str, width: float) -> str:
        """``label`` cut and ending in "…" when it is wider than a box of ``width``."""
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
            if len(self._fitted) > 4000:
                self._fitted.clear()
            self._fitted[key] = fitted
        return fitted
