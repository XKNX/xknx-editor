from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import cast

from imgui_bundle import imgui

from editor_gui.device import Device, UnloadedDevice, address_order, device_label
from editor_gui.plugins.project.medium_text import (
    medium_error_text,
    segment_display_name,
)
from editor_gui.plugins.project.paste import PasteTarget
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui._filter import filter_box
from editor_gui.plugins.project.ui.add_dialog import (
    AddChoice,
    AddDialog,
    AddRequest,
    AddRow,
    UndoStep,
    add_menu_item,
    add_rows,
    no_room,
    no_undo_step,
    start_add,
)
from editor_gui.plugins.project.ui.config_dialog import DeviceConfigDialog
from editor_gui.plugins.project.ui.move_menu import render_move_menu
from editor_gui.plugins.project.ui.paste_special import (
    MenuGroups,
    PasteSpecialDialog,
    ShortcutRow,
    handle_row_keys,
    render_copy_device_address_item,
    render_copy_item,
    render_delete_item,
)
from editor_gui.plugins.project.ui.tree_drag import Dragged, drag_source, drop_target
from editor_gui.widgets.description import render_description, render_device_marks
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.tree import (
    begin_tree_area,
    context_menu,
    leaf_rows,
    tree_node,
)
from xknxeditor.proj.core.medium import (
    MAX_LINES,
    MAX_SEGMENTS,
    MEDIUM_IP,
    MEDIUM_TP,
    MediumError,
    check_new_sub_segment,
    medium_name,
    sub_segment_media,
)
from xknxeditor.proj.core.paste import AddressPool

TreeDevice = Device | UnloadedDevice

# The most areas a project has (numbers 0 to 15).
_MAX_AREAS = 16

_UNLOADED_COLOR = imgui.ImVec4(0.95, 0.6, 0.25, 1.0)


def _address_order(device: TreeDevice) -> tuple[tuple[int, int, int], int]:
    """Numeric individual-address order; devices without a valid address keep their creation
    order after the addressed ones."""
    return (address_order(device.individual_address), device.node_id)


def _line_of_address(address: str | None) -> tuple[int, int] | None:
    """The area and line numbers an ``a.l.d`` individual address names, None for none."""
    parts = (address or "").split(".")
    if len(parts) < 3:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


@dataclass
class Area:
    id: int
    number: int
    name: str
    description: str = ""


@dataclass
class Segment:
    """A sub-segment of a line (its main segment is the line itself)."""

    id: int
    name: str
    description: str = ""
    medium_type: str = MEDIUM_TP


@dataclass
class Line:
    id: int
    area_id: int
    number: int
    name: str
    segments: list[Segment] = field(default_factory=list[Segment])
    medium_type: str = MEDIUM_TP
    # The line's own segment (its devices not on a sub-segment); None when unknown.
    main_segment_id: int | None = None
    description: str = ""


def _split_line(
    line: Line, devices: list[TreeDevice], segment_of: dict[int, int]
) -> tuple[list[tuple[Segment, list[TreeDevice]]], list[TreeDevice]]:
    """A line's devices by where they show: each sub-segment with its own, and the main
    segment's, which show directly under the line."""
    by_segment: dict[int, list[TreeDevice]] = {s.id: [] for s in line.segments}
    main: list[TreeDevice] = []
    for device in devices:
        own = by_segment.get(segment_of.get(device.node_id, -1))
        (main if own is None else own).append(device)
    return [(s, by_segment[s.id]) for s in line.segments], main


def _segment_room(line: Line) -> int:
    """How many more sub-segments ``line`` takes (its main segment counts)."""
    return MAX_SEGMENTS - 1 - len(line.segments)


def _no_segment(line: Line) -> str:
    """Why ``line`` takes no more sub-segment: its medium takes none, or it has the most ("" when
    it takes one)."""
    if not sub_segment_media(line.medium_type):
        try:
            check_new_sub_segment(line.medium_type, MEDIUM_TP)
        except MediumError as error:
            return medium_error_text(error)
    return no_room(_segment_room(line))


class TopologyPanel:
    def __init__(
        self,
        get_devices: Callable[[], list[Device]],
        get_areas: Callable[[], list[Area]],
        get_lines: Callable[[int], list[Line]],
        on_select_device: Callable[[Device], None],
        on_move_device: Callable[[Device, int, int], None],
        on_create_area: Callable[[int, str, str], str | None],
        on_remove_area: Callable[[Area], None],
        on_create_line: Callable[[int, int, str, str], str | None],
        on_remove_line: Callable[[Line], None],
        get_selected_node_id: Callable[[], int | None] = lambda: None,
        on_select_devices: Callable[[Device, list[int]], None] | None = None,
        get_selected_node_ids: Callable[[], set[int]] = lambda: set(),
        on_delete_device: Callable[[TreeDevice], None] = lambda _d: None,
        on_copy_config: Callable[[int], None] = lambda _n: None,
        on_paste_config: Callable[[int, bool, bool], None] = lambda _n, _p, _l: None,
        on_duplicate_config: Callable[[int, bool, bool], None] = (
            lambda _n, _p, _l: None
        ),
        can_paste_config: Callable[[int], bool] = lambda _n: False,
        count_paste_targets: Callable[[int], int] = lambda _n: 1,
        get_unloaded_devices: Callable[[], list[UnloadedDevice]] = lambda: [],
        on_device_info: Callable[[int], None] | None = None,
        on_compare: Callable[[int], None] | None = None,
        on_select_element: Callable[[str, int], None] = lambda _k, _i: None,
        get_selected_topology: Callable[[], tuple[str, int] | None] = lambda: None,
        get_device_segments: Callable[[], dict[int, int]] = dict,
        on_add_segment: Callable[[Line, str, str], str | None] = (
            lambda _l, _n, _m: None
        ),
        on_remove_segment: Callable[[Segment], None] = lambda _s: None,
        on_move_device_to_segment: Callable[[Device, Segment], None] = (
            lambda _d, _s: None
        ),
        on_move_line: Callable[[int, int], None] = lambda _l, _a: None,
        on_move_segment: Callable[[int, int], None] = lambda _s, _l: None,
        one_undo_step: UndoStep = no_undo_step,
        paste_dialog: PasteSpecialDialog | None = None,
        on_unlink_device: Callable[[int], None] | None = None,
        get_new_line_media: Callable[[int, int], list[str]] = lambda _a, _l: [
            MEDIUM_TP
        ],
        notify: Callable[[str], None] = lambda _t: None,
        on_park_device: Callable[[int], None] | None = None,
        on_add_devices: Callable[[int | None], None] | None = None,
    ) -> None:
        """``on_park_device`` clears a device's (by id) device number, keeping it on its line
        ("Park"). ``on_add_devices`` opens the catalog to add devices to a segment (by id; None:
        where the catalog puts them), "Add → Devices…". ``on_create_area`` adds an area (number, name, its main line's medium),
        ``on_create_line`` a line (area id, number, name, medium) and ``on_add_segment`` a
        sub-segment (line, name, medium), each giving the reason it cannot, or None;
        ``get_new_line_media`` gives the media a new line (area and line number) can have.
        ``on_move_line`` puts a sub line (id) into an area (id), ``on_move_segment`` a
        sub-segment (id) onto a line (id); ``one_undo_step`` makes the edits of one drop one
        undo step; ``paste_dialog`` asks how to copy what is dropped with Ctrl held (no copies
        without it); ``on_unlink_device`` takes a device (by id) off its line ("Unlink").
        ``notify`` tells why something was not added."""
        self._get_devices = get_devices
        self._get_new_line_media = get_new_line_media
        self._notify = notify
        self._adder = AddDialog("topology")
        self._one_undo_step = one_undo_step
        self._paste_dialog = paste_dialog
        self._on_unlink_device = on_unlink_device
        self._on_park_device = on_park_device
        self._on_add_devices = on_add_devices
        self._on_move_line = on_move_line
        self._on_move_segment = on_move_segment
        # A line's sub-segments show as nodes before its main segment's devices, each with its
        # own devices (``get_device_segments``: node id -> segment id).
        self._get_device_segments = get_device_segments
        self._on_add_segment = on_add_segment
        self._on_remove_segment = on_remove_segment
        self._on_move_device_to_segment = on_move_device_to_segment
        # Selecting an area or a line (its details and comment show in place of the device's),
        # and the one selected: highlighted instead of the device until a device is selected.
        self._on_select_element = on_select_element
        self._get_selected_topology = get_selected_topology
        self._on_device_info = on_device_info
        self._on_compare = on_compare
        self._get_unloaded_devices = get_unloaded_devices
        self._get_areas = get_areas
        self._get_lines = get_lines
        self._on_select_device = on_select_device
        self._get_selected_node_id = get_selected_node_id
        # Multi-select (Ctrl/Shift). When wired, drives a device selection *set*; the plain
        # on_select_device path stays as the single-select fallback.
        self._on_select_devices = on_select_devices
        self._get_selected_node_ids = get_selected_node_ids
        self._last_selected: int | None = None
        self._on_delete_device = on_delete_device
        self._on_copy_config = on_copy_config
        self._can_paste_config_cb = can_paste_config
        self._config_dialog = DeviceConfigDialog(
            "topology", on_paste_config, on_duplicate_config, count_paste_targets
        )
        self._on_move_device = on_move_device
        self._on_create_area = on_create_area
        self._on_remove_area = on_remove_area
        self._on_create_line = on_create_line
        self._on_remove_line = on_remove_line
        self._filter_text: str = ""
        # The selected row as Ctrl+C/X/V take it, and the devices selected, found each frame.
        self._shortcut_row: ShortcutRow | None = None
        self._selected_devices: set[int] = set()
        # Per frame: the line of each device placed on one ("1.5"), and the devices with an
        # individual address.
        self._line_text: dict[int, str] = {}
        self._addressed: set[int] = set()

        # The devices "Delete" (or the Delete key) asks about: one, or the selected ones.
        self._delete_targets: list[TreeDevice] = []
        self._open_delete_device_popup: bool = False
        # The selected device pressed while several are selected (see _handle_device_click).
        self._press_in_selection: Device | None = None

    def render(self) -> None:
        self._settle_press_in_selection()
        devices: list[TreeDevice] = sorted(
            [*self._get_devices(), *self._get_unloaded_devices()],
            key=_address_order,
        )
        areas = self._get_areas()
        device_tree, unassigned_devices = self._device_lines(devices, areas)

        self._filter_text = filter_box(
            "##device_filter", S.DEVICE_FILTER_HINT, self._filter_text
        )
        flt = self._filter_text.strip().lower()
        begin_tree_area("##topology_tree")
        self._shortcut_row = None
        self._render_tree(devices, areas, device_tree, unassigned_devices, flt)
        if self._paste_dialog is not None:
            self._paste_dialog.handle_shortcuts(self._shortcut_row)
        else:
            handle_row_keys(self._shortcut_row)
        imgui.end_child()

    def _render_tree(
        self,
        devices: list[TreeDevice],
        areas: list[Area],
        device_tree: dict[int, dict[int, list[TreeDevice]]],
        unassigned_devices: list[TreeDevice],
        flt: str,
    ) -> None:
        """The topology below the filter box, with its menus and dialogs; the devices on no
        line (``unassigned_devices``) at the end."""
        if not areas and not devices:
            imgui.text_disabled(S.DEVICE_EMPTY_HINT)

        # Below the elements only areas are added (lines, segments and devices in their
        # elements' menus).
        if imgui.begin_popup_context_window("##topology_context"):
            if add_menu_item(
                S.TOPOLOGY_ADD_AREAS_ITEM, no_room(_MAX_AREAS - len(self._get_areas()))
            ):
                self._start_add_areas()
            imgui.end_popup()

        if self._open_delete_device_popup:
            imgui.open_popup(self._delete_dialog_title())
            self._open_delete_device_popup = False

        self._adder.render()
        self._render_delete_device_popup()
        self._config_dialog.render()
        if self._paste_dialog is not None:
            self._paste_dialog.render()

        leaf_flags = (
            imgui.TreeNodeFlags_.leaf
            | imgui.TreeNodeFlags_.no_tree_push_on_open
            | imgui.TreeNodeFlags_.span_avail_width
        )
        # Highlight every selected device (multi-select); include the single primary for safety.
        # An area or line selected after them takes the highlight.
        selected_ids = set(self._get_selected_node_ids())
        single = self._get_selected_node_id()
        if single is not None:
            selected_ids.add(single)
        target = self._get_selected_topology()
        if target is not None:
            selected_ids = set[int]()
        self._selected_devices = selected_ids

        def _leaf(device: TreeDevice) -> int:
            flags = leaf_flags
            if device.node_id in selected_ids:
                flags = flags | imgui.TreeNodeFlags_.selected
            return flags

        # Each area shows as its main line ("1.0 …", the backbone "0.0 …" first): its sub-lines,
        # then the main line's sub-segments and devices. Flat device order as rendered (then the
        # unassigned ones) for Shift-range.
        segment_of = self._get_device_segments()

        def line_devices(area: Area, line: Line) -> list[TreeDevice]:
            found = device_tree.get(area.number, {}).get(line.number, [])
            return [d for d in found if self._device_matches(d, flt)] if flt else found

        def shown(area: Area) -> list[tuple[Line, list[TreeDevice]]]:
            """The area's lines as they show: the sub-lines, then the main line."""
            lines = self._get_lines(area.id)
            ordered = [ln for ln in lines if ln.number != 0] + [
                ln for ln in lines if ln.number == 0
            ]
            return [(line, line_devices(area, line)) for line in ordered]

        ordered_ids: list[int] = []
        for _area in areas:
            for _line, _devices in shown(_area):
                _segments, _main = _split_line(_line, _devices, segment_of)
                for _d in [*(d for _s, ds in _segments for d in ds), *_main]:
                    if isinstance(_d, Device):
                        ordered_ids.append(_d.node_id)
        for _d in unassigned_devices:
            if isinstance(_d, Device) and (not flt or self._device_matches(_d, flt)):
                ordered_ids.append(_d.node_id)

        for area in areas:
            lines = shown(area)
            if flt and not any(found for _line, found in lines):
                continue  # while filtering, hide areas with no matching device
            main = next((line for line, _found in lines if line.number == 0), None)
            kind = "backbone" if area.number == 0 else "area"
            # A click selects the area, the arrow and a double click open it.
            area_flags = (
                imgui.TreeNodeFlags_.default_open
                | imgui.TreeNodeFlags_.span_avail_width
                | imgui.TreeNodeFlags_.open_on_arrow
                | imgui.TreeNodeFlags_.open_on_double_click
            )
            if not any(
                line.number != 0 or found or line.segments for line, found in lines
            ):
                area_flags |= imgui.TreeNodeFlags_.leaf
            if target == (kind, area.id):
                area_flags |= imgui.TreeNodeFlags_.selected
                if main is not None:
                    self._shortcut_row = ShortcutRow(
                        None,
                        lambda c, a=area, m=main: self._line_copy_target(
                            a, m, c, area_node=True
                        ),
                        on_delete=lambda a=area: self._on_remove_area(a),
                    )
            if flt:
                imgui.set_next_item_open(True, imgui.Cond_.always)
            # The context menu belongs to the node whether it is open or closed.
            label = self._format_area_label(area, main)
            area_open = tree_node(f"{label}##area_{area.id}", area_flags)
            if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
                self._select_element(kind, area.id)
            self._render_area_context_menu(area, [line for line, _f in lines], main)
            if main is not None:
                self._render_line_drop_target(area, main, area_node=True)
            render_description(area.description)
            if area_open:
                for line, found in lines:
                    if line.number == 0:
                        self._render_line_content(
                            area, line, found, segment_of, _leaf, ordered_ids
                        )
                    elif found or not flt:
                        self._render_line(
                            area, line, found, segment_of, _leaf, ordered_ids
                        )
                imgui.tree_pop()

        unassigned = unassigned_devices
        if flt:
            unassigned = [d for d in unassigned if self._device_matches(d, flt)]
        if unassigned:
            unassigned_flags = (
                imgui.TreeNodeFlags_.default_open
                | imgui.TreeNodeFlags_.span_avail_width
            )
            if flt:
                imgui.set_next_item_open(True, imgui.Cond_.always)
            if tree_node(
                S.DEVICE_UNASSIGNED.format(count=len(unassigned)), unassigned_flags
            ):
                with leaf_rows(nodes=True):
                    for device in unassigned:
                        self._render_device_node(device, _leaf, ordered_ids)
                imgui.tree_pop()

    def _render_line(
        self,
        area: Area,
        line: Line,
        devices: list[TreeDevice],
        segment_of: dict[int, int],
        leaf: Callable[[TreeDevice], int],
        ordered_ids: list[int],
    ) -> None:
        """A sub-line's node with its sub-segments and devices below it."""
        flags = (
            imgui.TreeNodeFlags_.default_open
            | imgui.TreeNodeFlags_.span_avail_width
            | imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.open_on_double_click
        )
        if not devices and not line.segments:
            flags |= imgui.TreeNodeFlags_.leaf
        if self._get_selected_topology() == ("line", line.id):
            flags |= imgui.TreeNodeFlags_.selected
            self._shortcut_row = ShortcutRow(
                Dragged("line", (line.id,)),
                lambda c: self._line_copy_target(area, line, c),
                on_delete=lambda: self._on_remove_line(line),
            )
        if self._filter_text.strip():
            imgui.set_next_item_open(True, imgui.Cond_.always)
        label = self._format_line_label(area, line)
        opened = tree_node(f"{label}##line_{line.id}", flags)
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._select_element("line", line.id)
        self._render_line_context_menu(area, line)
        drag_source(Dragged("line", (line.id,)), label)
        self._render_line_drop_target(area, line)
        render_description(line.description)
        if opened:
            self._render_line_content(
                area, line, devices, segment_of, leaf, ordered_ids
            )
            imgui.tree_pop()

    def _render_line_content(
        self,
        area: Area,
        line: Line,
        devices: list[TreeDevice],
        segment_of: dict[int, int],
        leaf: Callable[[TreeDevice], int],
        ordered_ids: list[int],
    ) -> None:
        """A line's sub-segments, then the devices of its main segment."""
        segments, main_devices = _split_line(line, devices, segment_of)
        for segment, segment_devices in segments:
            if self._filter_text.strip() and not segment_devices:
                continue  # hide segments with no match while filtering
            self._render_segment(
                area, line, segment, segment_devices, leaf, ordered_ids
            )
        with leaf_rows(nodes=True):
            for device in main_devices:
                self._render_device_node(device, leaf, ordered_ids)

    def _render_segment(
        self,
        area: Area,
        line: Line,
        segment: Segment,
        devices: list[TreeDevice],
        leaf: Callable[[TreeDevice], int],
        ordered_ids: list[int],
    ) -> None:
        """A sub-segment's node, its devices below it; a click selects it, a device dropped on
        it moves there."""
        flags = (
            imgui.TreeNodeFlags_.default_open
            | imgui.TreeNodeFlags_.span_avail_width
            | imgui.TreeNodeFlags_.open_on_arrow
            | imgui.TreeNodeFlags_.open_on_double_click
        )
        if not devices:
            flags |= imgui.TreeNodeFlags_.leaf
        if self._get_selected_topology() == ("segment", segment.id):
            flags |= imgui.TreeNodeFlags_.selected
            self._shortcut_row = ShortcutRow(
                Dragged("segment", (segment.id,)),
                lambda c: (
                    PasteTarget("segment", segment.id) if c.kind == "device" else None
                ),
                on_delete=lambda: self._on_remove_segment(segment),
            )
        if self._filter_text.strip():
            imgui.set_next_item_open(True, imgui.Cond_.always)
        label = self._format_segment_label(area, line, segment)
        opened = tree_node(f"{label}##segment_{segment.id}", flags)
        if imgui.is_item_clicked() and not imgui.is_item_toggled_open():
            self._select_element("segment", segment.id)
        if context_menu(f"##segment_ctx_{segment.id}"):
            if self._on_add_devices is not None and imgui.begin_menu(S.SPACES_ADD_MENU):
                self._render_add_devices_item(segment.id)
                imgui.end_menu()
            self._render_move_segment_menu(segment, line)
            self._render_clipboard_items(
                Dragged("segment", (segment.id,)),
                lambda c: (
                    PasteTarget("segment", segment.id) if c.kind == "device" else None
                ),
            )
            imgui.separator()
            if render_delete_item():
                self._on_remove_segment(segment)
            imgui.end_popup()
        drag_source(Dragged("segment", (segment.id,)), label)
        self._drop_devices(
            segment.id, lambda device: self._on_move_device_to_segment(device, segment)
        )
        render_description(segment.description)
        if opened:
            with leaf_rows(nodes=True):
                for device in devices:
                    self._render_device_node(device, leaf, ordered_ids)
            imgui.tree_pop()

    def _render_device_node(
        self,
        device: TreeDevice,
        leaf: Callable[[TreeDevice], int],
        ordered_ids: list[int],
    ) -> None:
        if isinstance(device, UnloadedDevice):
            # Shown like any device, but without an application there is nothing to configure.
            imgui.push_style_color(imgui.Col_.text, _UNLOADED_COLOR)
            tree_node(self._device_label(device), leaf(device))
            imgui.pop_style_color()
            hovered = imgui.is_item_hovered()
            self._render_unloaded_device_context_menu(device)
            if self._render_description(device) or not hovered:
                return
            imgui.set_tooltip(S.DEVICE_PRODUCT_DATA_MISSING)
            return
        tree_node(self._device_label(device), leaf(device))
        if device.node_id in self._selected_devices and self._shortcut_row is None:
            segment = self._get_device_segments().get(device.node_id)
            dragged = self._dragged_devices(device)
            self._shortcut_row = ShortcutRow(
                dragged,
                lambda c: (
                    PasteTarget("segment", segment)
                    if c.kind == "device" and segment is not None
                    else None
                ),
                # The Delete key deletes all selected devices.
                on_delete=lambda: self._ask_delete_devices(self._devices_of(dragged)),
            )
        if imgui.is_item_clicked():
            self._handle_device_click(device, ordered_ids)
        self._render_device_context_menu(device)
        self._render_device_drag_source(device)
        self._render_description(device)

    @staticmethod
    def _render_description(device: TreeDevice) -> bool:
        """After the device's row: the KNX Data Secure shield, then its description (a short
        one inline, a longer one behind a hover icon). Returns whether an icon is hovered."""
        return render_device_marks(device.node_id, device.description)

    def _render_unloaded_device_context_menu(self, device: UnloadedDevice) -> None:
        if context_menu(f"##dev_ctx_{device.node_id}"):
            groups = MenuGroups()
            self._render_move_device_menu(device, groups.show)
            render_copy_device_address_item(device.individual_address, groups.show)
            groups.next()
            self._render_unlink_item((device.node_id,), groups.show)
            groups.next()
            groups.show()
            if render_delete_item():
                self._ask_delete_device(device)
            imgui.end_popup()

    def _ask_delete_device(self, device: TreeDevice) -> None:
        """Ask before ``device`` is deleted from the project."""
        self._ask_delete_devices([device])

    def _ask_delete_devices(self, devices: Sequence[TreeDevice]) -> None:
        """Ask before ``devices`` are deleted from the project (one undo step)."""
        if not devices:
            return
        self._delete_targets = list(devices)
        self._open_delete_device_popup = True

    def _devices_of(self, dragged: Dragged) -> list[Device]:
        """The devices ``dragged`` names, in its order."""
        by_id = {d.node_id: d for d in self._get_devices()}
        return [by_id[i] for i in dragged.ids if i in by_id]

    def _render_unlink_item(
        self, node_ids: tuple[int, ...], before: Callable[[], bool]
    ) -> None:
        """ "Park" of devices with a device number: they keep their line ("1.5.-"); "Unlink" of
        devices on a line: they are on no line then. One undo step each. ``before`` runs right
        before an item shows."""
        segment_of = self._get_device_segments()
        on_line = [n for n in node_ids if n in segment_of]
        numbered = [n for n in on_line if n in self._addressed]
        if (
            self._on_park_device is not None
            and numbered
            and before()
            and imgui.menu_item(S.CONTEXT_PARK, "", False)[0]
        ):
            with self._one_undo_step():
                for node_id in numbered:
                    self._on_park_device(node_id)
        if self._on_unlink_device is None:
            return
        if (
            on_line
            and before()
            and imgui.menu_item(S.CONTEXT_UNLINK_LINE, "", False)[0]
        ):
            with self._one_undo_step():
                for node_id in on_line:
                    self._on_unlink_device(node_id)

    def _select_element(self, kind: str, element_id: int) -> None:
        self._on_select_element(kind, element_id)

    def _handle_device_click(self, device: Device, ordered_ids: list[int]) -> None:
        """Selection: plain click = single, Ctrl = toggle, Shift = range (additive with
        Ctrl). Falls back to single-select when the multi-select callback is not wired."""
        if self._on_select_devices is None:
            self._on_select_device(device)
            return
        io = imgui.get_io()
        ctrl = io.key_ctrl or io.key_super
        shift = io.key_shift
        current = set(self._get_selected_node_ids())
        nid = device.node_id
        if not ctrl and not shift and nid in current and len(current) > 1:
            # A press on one of several selected devices may start dragging them all: it
            # selects only this one when the button comes up without a drag.
            self._press_in_selection = device
            return
        if shift and self._last_selected in ordered_ids and nid in ordered_ids:
            i0 = ordered_ids.index(self._last_selected)
            i1 = ordered_ids.index(nid)
            lo, hi = sorted((i0, i1))
            rng = set(ordered_ids[lo : hi + 1])
            current = (current | rng) if ctrl else rng
        elif ctrl:
            current ^= {nid}
        else:
            current = {nid}
        self._last_selected = nid
        self._on_select_devices(device, sorted(current))

    def _settle_press_in_selection(self) -> None:
        """A press on one of several selected devices: a drag takes them all along; released
        without one, only that device stays selected."""
        device = self._press_in_selection
        if device is None:
            return
        if imgui.is_mouse_dragging(imgui.MouseButton_.left):
            self._press_in_selection = None
        elif not imgui.is_mouse_down(imgui.MouseButton_.left):
            self._press_in_selection = None
            self._last_selected = device.node_id
            if self._on_select_devices is not None:
                self._on_select_devices(device, [device.node_id])

    def _render_device_context_menu(self, device: Device) -> None:
        if context_menu(f"##dev_ctx_{device.node_id}"):
            # Its configuration; where it is and its address; reading, comparing and
            # unlinking; then Copy / Cut / Paste and "Delete". On one of several selected
            # devices only what acts on all of them: pasting a configuration into them, moving,
            # parking and unlinking them, Copy / Cut / Paste and deleting them.
            dragged = self._dragged_devices(device)
            several = len(dragged.ids) > 1
            groups = MenuGroups()
            label = self._device_display_name(device)
            groups.show()
            if not several and imgui.menu_item(S.CONTEXT_DUPLICATE, "", False)[0]:
                self._config_dialog.open(device.node_id, label, "duplicate")
            if not several and imgui.menu_item(S.CONTEXT_COPY_CONFIG, "", False)[0]:
                self._on_copy_config(device.node_id)
            if imgui.menu_item(
                S.CONTEXT_PASTE_CONFIG,
                "",
                False,
                self._can_paste_config_cb(device.node_id),
            )[0]:
                self._config_dialog.open(device.node_id, label, "paste")
            groups.next()
            self._render_move_device_menu(device, groups.show, dragged.ids)
            if not several:
                render_copy_device_address_item(device.individual_address, groups.show)
            groups.next()
            if not several and self._on_device_info is not None and groups.show():
                render_read_device_item(
                    device.node_id,
                    bool(device.individual_address),
                    self._on_device_info,
                )
            if (
                not several
                and self._on_compare is not None
                and groups.show()
                and imgui.menu_item(S.CONTEXT_COMPARE_DEVICE, "", False)[0]
            ):
                self._on_compare(device.node_id)
            self._render_unlink_item(dragged.ids, groups.show)
            # Devices pasted on a device go to its segment.
            segment = self._get_device_segments().get(device.node_id)
            self._render_clipboard_items(
                dragged,
                lambda c: (
                    PasteTarget("segment", segment)
                    if c.kind == "device" and segment is not None
                    else None
                ),
            )
            imgui.separator()
            if render_delete_item():
                self._ask_delete_devices(self._devices_of(dragged))
            imgui.end_popup()

    def _render_move_device_menu(
        self,
        device: TreeDevice,
        before: Callable[[], object],
        node_ids: tuple[int, ...] = (),
    ) -> None:
        """ "Move to" of a device, or of the devices ``node_ids`` (several selected, also on
        different lines; one undo step): every line (as its main segment) and sub-segment, the
        one they are on grayed out. A device keeps its address within its line and gets the
        lowest free one of another line. ``before`` runs right before it shows."""
        segment_of = self._get_device_segments()
        moved = node_ids or (device.node_id,)
        on = {segment_of.get(node_id) for node_id in moved}
        own = next(iter(on)) if len(on) == 1 else None
        targets: list[tuple[str, int]] = []
        for area in self._get_areas():
            for line in sorted(self._get_lines(area.id), key=lambda ln: ln.number):
                if line.main_segment_id is None:
                    continue
                label = (
                    self._format_area_label(area, line)
                    if line.number == 0
                    else self._format_line_label(area, line)
                )
                targets.append(
                    (f"{label}##mvdev{line.main_segment_id}", line.main_segment_id)
                )
                targets.extend(
                    (
                        f"  {self._format_segment_label(area, line, s)}##mvdev{s.id}",
                        s.id,
                    )
                    for s in line.segments
                )
        chosen = render_move_menu(
            targets, own if own is not None else -1, before=before
        )
        if chosen is None:
            return
        if len(moved) == 1:
            self._on_move_device_to_segment(
                cast(Device, device), Segment(chosen[0], "")
            )
            return
        with self._one_undo_step():
            for each in self._devices_of(Dragged("device", moved)):
                if segment_of.get(each.node_id) != chosen[0]:
                    self._on_move_device_to_segment(each, Segment(chosen[0], ""))

    def _delete_dialog_title(self) -> str:
        """ "Delete Device", or "Delete Devices" for several (one dialog id for both)."""
        several = len(self._delete_targets) > 1
        title = S.DEVICE_DELETE_TITLE_SEVERAL if several else S.DEVICE_DELETE_TITLE
        return f"{title}###topology_delete_devices"

    def _render_delete_device_popup(self) -> None:
        if begin_dialog(self._delete_dialog_title(), width=420.0):
            devices = self._delete_targets
            if len(devices) == 1:
                text = S.DEVICE_DELETE_CONFIRM.format(
                    name=self._device_display_name(devices[0])
                )
            else:
                text = S.DEVICE_DELETE_CONFIRM_SEVERAL.format(count=len(devices))
            imgui.text_wrapped(text)
            clicked = dialog_buttons(S.CONTEXT_DELETE, S.BTN_CANCEL)
            if clicked == 0:
                # Several in one undo step.
                label = (
                    S.HISTORY_EV_REMOVE_DEVICES.format(count=len(devices))
                    if len(devices) > 1
                    else None
                )
                with self._one_undo_step(label):
                    for device in devices:
                        self._on_delete_device(device)
                self._delete_targets = []
                imgui.close_current_popup()
            elif clicked == 1:
                self._delete_targets = []
                imgui.close_current_popup()
            end_dialog()

    def _device_display_name(self, device: TreeDevice) -> str:
        """Human label for dialogs: name, else product/app name, prefixed with the address."""
        return device_label(device)

    def _device_matches(self, device: TreeDevice, flt: str) -> bool:
        """Case-insensitive match of a device against the filter (name, product, address, app)."""
        if flt in (device.name or "").lower():
            return True
        if flt in (device.product_name or "").lower():
            return True
        if flt in (device.individual_address or "").lower():
            return True
        if isinstance(device, UnloadedDevice):
            return False
        app_name = getattr(device.app, "name", "") or ""
        return flt in app_name.lower()

    def _device_label(self, device: TreeDevice) -> str:
        # Imported devices are often unnamed; fall back to the product/application name. One on
        # a line without a device number shows its line's address ("1.5.-").
        line = self._line_text.get(device.node_id)
        if not device.individual_address and line is not None:
            return f"{line}.-  {device.display_name or '?'}##dev{device.node_id}"
        return f"{device_label(device)}##dev{device.node_id}"

    @staticmethod
    def _format_area_label(area: Area, main: Line | None) -> str:
        """An area as its main line: "1.0 Name"; one without a name is called after its medium
        ("0.0 IP backbone line", "1.0 TP main line")."""
        name = area.name
        if not name:
            medium = medium_name(main.medium_type) if main is not None else ""
            text = S.BACKBONE_LINE_NAME if area.number == 0 else S.MAIN_LINE_NAME
            name = text.format(medium=medium).strip()
        return f"{area.number}.0 {name}"

    @staticmethod
    def _format_line_label(area: Area, line: Line) -> str:
        """A line as "1.1 Name"; one without a name is called after its medium ("1.1 TP
        line")."""
        name = line.name or S.LINE_NAME.format(medium=medium_name(line.medium_type))
        return f"{area.number}.{line.number} {name}"

    @staticmethod
    def _format_segment_label(area: Area, line: Line, segment: Segment) -> str:
        name = segment_display_name(segment.name, segment.medium_type)
        return f"{area.number}.{line.number} {name}"

    def _render_area_context_menu(
        self, area: Area, lines: list[Line], main: Line | None
    ) -> None:
        if context_menu(f"##area_ctx_{area.id}"):
            if imgui.begin_menu(S.SPACES_ADD_MENU):
                self._render_add_items(area, lines, main)
                if main is not None:
                    self._render_add_devices_item(main.main_segment_id)
                imgui.end_menu()
            if main is not None:
                self._render_clipboard_items(
                    None,
                    lambda c: self._line_copy_target(area, main, c, area_node=True),
                )
            imgui.separator()
            if render_delete_item():
                self._on_remove_area(area)
            imgui.end_popup()

    def _render_line_context_menu(self, area: Area, line: Line) -> None:
        if context_menu(f"##line_ctx_{line.id}"):
            if imgui.begin_menu(S.SPACES_ADD_MENU):
                if add_menu_item(S.TOPOLOGY_ADD_SEGMENTS, _no_segment(line)):
                    self._start_add_segments(area, line)
                self._render_add_devices_item(line.main_segment_id)
                imgui.end_menu()
            self._render_move_line_menu(area, line)
            # The backbone or main line of an area cannot be copied.
            self._render_clipboard_items(
                Dragged("line", (line.id,)) if line.number != 0 else None,
                lambda c: self._line_copy_target(area, line, c),
            )
            imgui.separator()
            if render_delete_item():
                self._on_remove_line(line)
            imgui.end_popup()

    def _render_add_devices_item(self, segment_id: int | None) -> None:
        """ "Devices…" of an "Add" submenu: the catalog, adding devices to the segment
        ``segment_id`` (None: where the catalog puts them)."""
        if (
            self._on_add_devices is not None
            and imgui.menu_item(S.SPACES_ADD_DEVICES, "", False)[0]
        ):
            self._on_add_devices(segment_id)

    def _render_move_line_menu(self, area: Area, line: Line) -> None:
        """ "Move to" of a sub line: every area (by its main line), its own grayed out. It
        keeps its number when free there, else gets the lowest free one."""
        targets: list[tuple[str, int]] = []
        for target in self._get_areas():
            main = next(
                (ln for ln in self._get_lines(target.id) if ln.number == 0), None
            )
            label = self._format_area_label(target, main)
            targets.append((f"{label}##mvline{line.id}_{target.id}", target.id))
        chosen = render_move_menu(targets, area.id)
        if chosen is not None:
            self._on_move_line(line.id, chosen[0])

    def _render_move_segment_menu(self, segment: Segment, line: Line) -> None:
        """ "Move to" of a sub-segment: every line (main lines by their area), its own grayed
        out. It gets the line's lowest free segment number."""
        targets: list[tuple[str, int]] = []
        for area in self._get_areas():
            for target in sorted(self._get_lines(area.id), key=lambda ln: ln.number):
                label = (
                    self._format_area_label(area, target)
                    if target.number == 0
                    else self._format_line_label(area, target)
                )
                targets.append((f"{label}##mvseg{segment.id}_{target.id}", target.id))
        chosen = render_move_menu(targets, line.id)
        if chosen is not None:
            self._on_move_segment(segment.id, chosen[0])

    def _render_add_items(
        self, area: Area, lines: list[Line], main: Line | None
    ) -> None:
        """The items of an area's "Add" submenu: areas (of the backbone), lines of the area and
        segments of its main line."""
        if area.number == 0 and add_menu_item(
            S.TOPOLOGY_ADD_AREAS, no_room(_MAX_AREAS - len(self._get_areas()))
        ):
            self._start_add_areas()
        if add_menu_item(S.TOPOLOGY_ADD_LINES, no_room(MAX_LINES - len(lines))):
            self._start_add_lines(area, lines, main)
        if main is not None and add_menu_item(
            S.TOPOLOGY_ADD_SEGMENTS, _no_segment(main)
        ):
            self._start_add_segments(area, main)

    def _start(
        self,
        on_add: Callable[[AddRequest], None],
        choice: AddChoice,
        open_dialog: Callable[[], bool],
    ) -> None:
        start_add(open_dialog, on_add, self._notify, choice)

    @staticmethod
    def _medium_choice(media: list[str], default: str) -> AddChoice:
        """The Medium column of the media offered, ``default`` chosen (else the first)."""
        return AddChoice(
            S.MEDIUM,
            [(medium, medium_name(medium)) for medium in media],
            default if default in media else media[0],
        )

    def _start_add_areas(self) -> None:
        areas = self._get_areas()
        used = {a.number for a in areas}
        free = next((n for n in range(1, 16) if n not in used), 1)
        media = self._get_new_line_media(free, 0) or [MEDIUM_TP]
        choice = self._medium_choice(media, MEDIUM_TP)
        target = S.PANEL_TOPOLOGY
        self._start(
            lambda request: self._add_areas(request, target),
            choice,
            lambda: self._adder.open(
                S.TOPOLOGY_TITLE_AREAS,
                target,
                lambda request: self._add_areas(request, target),
                # An area without a name shows as its main line ("TP main line").
                default_name=lambda row: S.MAIN_LINE_NAME.format(
                    medium=medium_name(row.choice)
                ),
                choice=choice,
                numbered=True,
                start_maximum=15,
                limit=_MAX_AREAS - len(areas),
            ),
        )

    def _add_areas(self, request: AddRequest, target: str) -> None:
        pool = AddressPool(
            0,
            15,
            (a.number for a in self._get_areas()),
            request.allocation,
            start_with=request.start_with,
        )

        def add(row: AddRow) -> str | None:
            number = pool.take()
            if number is None:
                return S.ADD_LIMIT_REACHED
            return self._on_create_area(number, row.name.strip(), row.choice)

        self._add_rows(request, S.HISTORY_EV_ADDED_AREAS, target, add)

    def _start_add_lines(
        self, area: Area, lines: list[Line], main: Line | None
    ) -> None:
        used = {ln.number for ln in lines}
        free = next((n for n in range(1, 16) if n not in used), 1)
        media = self._get_new_line_media(area.number, free)
        target = self._format_area_label(area, main)
        if not media:
            self._notify(S.ADD_NO_LINE_MEDIUM.format(area=target))
            return
        default = main.medium_type if main is not None else MEDIUM_TP
        if default == MEDIUM_IP or area.number == 0:
            default = MEDIUM_TP
        choice = self._medium_choice(media, default)
        self._start(
            lambda request: self._add_lines(request, area, target),
            choice,
            lambda: self._adder.open(
                S.TOPOLOGY_TITLE_LINES,
                target,
                lambda request: self._add_lines(request, area, target),
                # One without a name shows after its medium ("TP line").
                default_name=lambda row: S.LINE_NAME.format(
                    medium=medium_name(row.choice)
                ),
                choice=choice,
                numbered=True,
                start_maximum=15,
                limit=MAX_LINES - len(lines),
            ),
        )

    def _add_lines(self, request: AddRequest, area: Area, target: str) -> None:
        pool = AddressPool(
            0,
            15,
            (ln.number for ln in self._get_lines(area.id)),
            request.allocation,
            start_with=request.start_with,
        )

        def add(row: AddRow) -> str | None:
            number = pool.take()
            if number is None:
                return S.ADD_LIMIT_REACHED
            return self._on_create_line(area.id, number, row.name.strip(), row.choice)

        self._add_rows(request, S.HISTORY_EV_ADDED_LINES, target, add)

    def _start_add_segments(self, area: Area, line: Line) -> None:
        media = list(sub_segment_media(line.medium_type))
        if not media:
            return
        default = line.medium_type
        if default == MEDIUM_IP or area.number == 0:
            default = MEDIUM_TP
        choice = self._medium_choice(media, default)
        target = (
            self._format_area_label(area, line)
            if line.number == 0
            else self._format_line_label(area, line)
        )
        self._start(
            lambda request: self._add_segments(request, line, target),
            choice,
            lambda: self._adder.open(
                S.TOPOLOGY_TITLE_SEGMENTS,
                target,
                lambda request: self._add_segments(request, line, target),
                # One without a name shows after its medium ("TP segment").
                default_name=lambda row: segment_display_name("", row.choice),
                choice=choice,
                limit=_segment_room(line),
            ),
        )

    def _add_segments(self, request: AddRequest, line: Line, target: str) -> None:
        self._add_rows(
            request,
            S.HISTORY_EV_ADDED_SEGMENTS,
            target,
            lambda row: self._on_add_segment(line, row.name.strip(), row.choice),
        )

    def _add_rows(
        self,
        request: AddRequest,
        several: str,
        target: str,
        add: Callable[[AddRow], str | None],
    ) -> None:
        add_rows(request, several, target, add, self._one_undo_step, self._notify)

    def _device_lines(
        self, devices: list[TreeDevice], areas: list[Area]
    ) -> tuple[dict[int, dict[int, list[TreeDevice]]], list[TreeDevice]]:
        """The devices by area and line number, and those on no line ("Unassigned"). A device
        is on the line of its segment, else (its segment not known) on the line its individual
        address names; one on neither is on no line."""
        lines_of: dict[int, set[int]] = {}
        place: dict[int, tuple[int, int]] = {}
        for area in areas:
            for line in self._get_lines(area.id):
                lines_of.setdefault(area.number, set()).add(line.number)
                for segment_id in (
                    line.main_segment_id,
                    *(s.id for s in line.segments),
                ):
                    if segment_id is not None:
                        place[segment_id] = (area.number, line.number)
        segment_of = self._get_device_segments()
        tree: dict[int, dict[int, list[TreeDevice]]] = {}
        unassigned: list[TreeDevice] = []
        self._line_text = {}
        self._addressed = {d.node_id for d in devices if d.individual_address}
        for device in devices:
            where = place.get(segment_of.get(device.node_id, -1))
            if where is not None:
                self._line_text[device.node_id] = f"{where[0]}.{where[1]}"
            if where is None:
                where = _line_of_address(device.individual_address)
                if where is not None and where[1] not in lines_of.get(where[0], set()):
                    where = None
            if where is None:
                unassigned.append(device)
                continue
            tree.setdefault(where[0], {}).setdefault(where[1], []).append(device)
        return tree, unassigned

    def _render_device_drag_source(self, device: Device) -> None:
        """A device is dragged, together with the other selected ones when it is selected."""
        dragged = self._dragged_devices(device)
        text = device.display_name
        if len(dragged.ids) > 1:
            text = S.DRAG_DEVICES.format(count=len(dragged.ids))
        drag_source(dragged, text)

    def _dragged_devices(self, device: Device) -> Dragged:
        """The device, or all selected devices when it is one of several selected."""
        selected = set(self._get_selected_node_ids())
        ids = (
            tuple(sorted(selected))
            if device.node_id in selected and len(selected) > 1
            else (device.node_id,)
        )
        return Dragged("device", ids)

    def _line_copy_target(
        self, area: Area, line: Line, copied: Dragged, *, area_node: bool = False
    ) -> PasteTarget | None:
        """Where copies of ``copied`` go when pasted on the line: devices onto its main
        segment, segments onto the line, sub lines into the area when it is the area's node
        (a main line is not copied); None for anything else."""
        if self._paste_dialog is None:
            return None
        if copied.kind == "device" and line.main_segment_id is not None:
            return PasteTarget("segment", line.main_segment_id)
        if copied.kind == "segment":
            return PasteTarget("line", line.id)
        if (
            copied.kind == "line"
            and area_node
            and self._main_lines().isdisjoint(copied.ids)
        ):
            return PasteTarget("area", area.id)
        return None

    def _render_clipboard_items(
        self,
        copied: Dragged | None,
        target_for: Callable[[Dragged], PasteTarget | None],
    ) -> None:
        """ "Copy" and "Cut" of ``copied`` (None: none), "Paste" and "Paste Special…" in a
        context menu, with a separator above."""
        if self._paste_dialog is None:
            return
        imgui.separator()
        if copied is not None:
            render_copy_item(copied)
        self._paste_dialog.render_paste_items(target_for)

    def _render_line_drop_target(
        self, area: Area, line: Line, *, area_node: bool = False
    ) -> None:
        """A line as a place to drop on: devices go onto its main segment, sub-segments onto the
        line; an area's node (its main line) also takes the lines of other areas."""

        def accepts(dragged: Dragged) -> bool:
            if dragged.kind == "segment":
                line_of = self._line_of_segments()
                return any(line_of.get(ident) != line.id for ident in dragged.ids)
            if dragged.kind == "line":
                area_of = self._area_of_lines()
                return area_node and any(
                    area_of.get(ident) != area.id for ident in dragged.ids
                )
            return False

        def copy_target(dragged: Dragged) -> PasteTarget | None:
            return self._line_copy_target(area, line, dragged, area_node=area_node)

        self._drop_devices(
            line.main_segment_id,
            lambda device: self._on_move_device(device, area.number, line.number),
            accepts,
            lambda dragged, ident: (
                self._on_move_segment(ident, line.id)
                if dragged.kind == "segment"
                else self._on_move_line(ident, area.id)
            ),
            copy_target,
        )

    def _main_lines(self) -> set[int]:
        """The ids of the areas' main lines."""
        return {
            line.id
            for area in self._get_areas()
            for line in self._get_lines(area.id)
            if line.number == 0
        }

    def _line_of_segments(self) -> dict[int, int]:
        """The line of each sub-segment, by segment id."""
        return {
            segment.id: line.id
            for area in self._get_areas()
            for line in self._get_lines(area.id)
            for segment in line.segments
        }

    def _area_of_lines(self) -> dict[int, int]:
        """The area of each line, by line id."""
        return {
            line.id: area.id
            for area in self._get_areas()
            for line in self._get_lines(area.id)
        }

    def _drop_devices(
        self,
        segment_id: int | None,
        move: Callable[[Device], None],
        accepts_other: Callable[[Dragged], bool] = lambda _d: False,
        move_other: Callable[[Dragged, int], None] = lambda _d, _i: None,
        copy_other: Callable[[Dragged], PasteTarget | None] = lambda _d: None,
    ) -> None:
        """The line or segment just drawn (``segment_id``) as a place to drop devices on, from
        this tree or the Buildings tree; they move there as with "Move to", or are copied there
        with Ctrl held. ``accepts_other`` and ``move_other`` take other dragged elements (lines,
        segments) there, ``copy_other`` names where their copies go (None: not here)."""
        segment_of = self._get_device_segments()

        def accepts(dragged: Dragged) -> bool:
            if dragged.kind != "device":
                return accepts_other(dragged)
            return any(segment_of.get(ident) != segment_id for ident in dragged.ids)

        def copy_target(dragged: Dragged) -> PasteTarget | None:
            if self._paste_dialog is None:
                return None
            if dragged.kind == "device":
                return (
                    PasteTarget("segment", segment_id)
                    if segment_id is not None
                    else None
                )
            return copy_other(dragged)

        drop = drop_target(accepts, lambda dragged: copy_target(dragged) is not None)
        if drop is None:
            return
        if drop.copy:
            target = copy_target(drop.dragged)
            assert self._paste_dialog is not None and target is not None
            self._paste_dialog.open(drop.dragged.kind, list(drop.dragged.ids), target)
            return
        with self._one_undo_step():
            if drop.dragged.kind != "device":
                for ident in drop.dragged.ids:
                    move_other(drop.dragged, ident)
                return
            devices = {d.node_id: d for d in self._get_devices()}
            for ident in drop.dragged.ids:
                device = devices.get(ident)
                if device is not None and segment_of.get(ident) != segment_id:
                    move(device)


def render_read_device_item(
    node_id: int, enabled: bool, on_read: Callable[[int], None]
) -> None:
    """The "Read from device" entry of a device's context menu (the menu draws the separators
    around its group)."""
    if imgui.menu_item(S.BTN_READ_DEVICE_INFO, "", False, enabled)[0]:
        on_read(node_id)
