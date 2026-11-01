"""The "Group Objects" table: each com-object with its assigned group addresses and flags,
including the group-address assignment column with linking/unlinking.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.color import sending_green, themed
from editor_gui.device import (
    FLAG_LABELS,
    ComObject,
    Device,
    com_object_display_name,
    flag_letter,
    flag_name,
)
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dialog import (
    begin_dialog,
    begin_tool_window,
    dialog_buttons,
    end_dialog,
)
from editor_gui.widgets.dpi import px
from editor_gui.widgets.flow import button_width, checkbox_width, same_line_if_fits
from editor_gui.widgets.strings import S
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from xknxeditor.proj.core.addressing import GroupAddressStyle

# One com-object → group-address link: (assignment_id, group_address_id, text, is_sending).
GroupLink = tuple[int, int, str, bool]
# Resolver: com-object db id -> its links.
GroupLinkResolver = Callable[[int], list[GroupLink]]
# All assignable group addresses: (group_address_id, text).
GroupAddressCatalog = Callable[[], list[tuple[int, str]]]
# Paste a com-object's copied links onto a target: (target_db_id, [(ga_id, is_sending)], replace).
PasteLinks = Callable[[int, list[tuple[int, bool]], bool], None]


@dataclass
class _CopiedObject:
    """One source com-object's configuration on the clipboard: its links and flag values."""

    links: list[tuple[int, bool]]  # (ga_id, is_sending), in the source's display order
    # attr -> value (C/R/W/T/U)
    flags: dict[str, bool] = field(default_factory=dict[str, bool])


# Colour of the last value a group address was seen carrying, by what the object does with it
# (its flags): amber = the object sends, blue = it receives, teal = both; grey-blue when the flags
# say neither. An object's older value (a different address updated more recently) is dimmed.
# Each as (on dark themes, on light themes).
_SENT_COLOR = (imgui.ImVec4(0.92, 0.74, 0.42, 1.0), imgui.ImVec4(0.62, 0.36, 0.0, 1.0))
_RECEIVED_COLOR = (
    imgui.ImVec4(0.45, 0.7, 0.92, 1.0),
    imgui.ImVec4(0.1, 0.38, 0.7, 1.0),
)
_BOTH_COLOR = (imgui.ImVec4(0.45, 0.82, 0.72, 1.0), imgui.ImVec4(0.0, 0.5, 0.42, 1.0))
_LIVE_COLOR = (imgui.ImVec4(0.6, 0.6, 0.66, 1.0), imgui.ImVec4(0.4, 0.4, 0.46, 1.0))


# The line above a table row takes the row's first pixel.
_ROW_LINE = 1


def _sign_size() -> tuple[int, int]:
    """The side of the "x" and the "+" of the link buttons and the width of their strokes, in
    whole pixels: a stroke as wide as the sign's middle, so it has a middle pixel row."""
    stroke = max(1, round(px(1.0)))
    side = round(px(5.0))
    return side + (side - stroke) % 2, stroke


def _row_room() -> int:
    """The height of a row of fields below its line."""
    style = imgui.get_style()
    return round(imgui.get_frame_height() + 2 * style.cell_padding.y) - _ROW_LINE


def _link_button_size() -> imgui.ImVec2:
    """The "x" of a linked group address and the "+" below them: one size for both, with the
    sign in its middle on whole pixels; a text line high (one more pixel where that leaves as
    much room above as below it in a row of fields)."""
    side, _ = _sign_size()
    width = round(imgui.calc_text_size("+").x + 2 * imgui.get_style().frame_padding.x)
    height = round(imgui.get_text_line_height())
    if (_row_room() - height) % 2:
        height += 1
    return imgui.ImVec2(width + (width - side) % 2, height + (height - side) % 2)


def _link_gap() -> int:
    """The room above, beside, between and below the link buttons of a cell."""
    return (_row_room() - round(_link_button_size().y)) // 2


def _link_button(str_id: str, sign: str) -> bool:
    """A link button with an "x" or a "+" of whole pixels in its middle; whether it was
    pressed."""
    size = _link_button_size()
    pressed = imgui.button(f"##{str_id}", size)
    low = imgui.get_item_rect_min()
    side, stroke = _sign_size()
    x = round(low.x + (size.x - side) / 2)
    y = round(low.y + (size.y - side) / 2)
    draw = imgui.get_window_draw_list()
    color = imgui.get_color_u32(imgui.Col_.text)

    def dot(left: float, top: float, width: float, height: float) -> None:
        draw.add_rect_filled(
            imgui.ImVec2(x + left, y + top),
            imgui.ImVec2(x + left + width, y + top + height),
            color,
        )

    if sign == "+":
        middle = (side - stroke) // 2
        dot(0, middle, side, stroke)
        dot(middle, 0, stroke, side)
    else:
        for step in range(side - stroke + 1):
            dot(step, step, stroke, stroke)
            dot(side - stroke - step, step, stroke, stroke)
    return pressed


def _begin_link_buttons() -> None:
    """Start the group addresses cell: its buttons are as far from each other as the first one
    from the cell's top and left edge and the last one from its bottom, a lone "+" in the
    middle of a row of fields."""
    style = imgui.get_style()
    gap = _link_gap()
    imgui.set_cursor_pos_y(
        imgui.get_cursor_pos_y() + gap + _ROW_LINE - style.cell_padding.y
    )
    imgui.indent(gap - style.cell_padding.x)
    imgui.push_style_var(
        imgui.StyleVar_.item_spacing, imgui.ImVec2(style.item_spacing.x, gap)
    )


def _end_link_buttons() -> None:
    """End the cell ``_begin_link_buttons`` started: the row reaches as far below the last
    button."""
    style = imgui.get_style()
    imgui.pop_style_var()
    gap = _link_gap()
    imgui.unindent(gap - style.cell_padding.x)
    below = _row_room() - gap - round(_link_button_size().y)
    window = internal.get_current_window()
    bottom = imgui.get_item_rect_max().y + below - style.cell_padding.y
    window.dc.cursor_max_pos.y = max(window.dc.cursor_max_pos.y, bottom)


def _text_beside() -> None:
    """Texts after a link button on its line start at its top, so that they sit in its middle
    (a button would put them a frame padding lower)."""
    internal.get_current_window().dc.prev_line_text_base_offset = 0.0


def _dim(color: imgui.ImVec4) -> imgui.ImVec4:
    """A faded variant, for a value older than the object's most recent one."""
    return imgui.ImVec4(color.x, color.y, color.z, 0.5)


# Width of the group-address link picker: wide enough for "address + full name" without clipping.
_PICKER_WIDTH = 460.0
# Valid per-instance ComObjectInstanceRef @Priority values (ComObjectPriority_t).
_PRIORITIES = ("Low", "High", "Alert")


def priority_text(priority: str) -> str:
    """The shown name of a group object priority (stored as Low, High or Alert)."""
    names = {
        "Low": S.GROUP_OBJECTS_PRIORITY_LOW,
        "High": S.GROUP_OBJECTS_PRIORITY_HIGH,
        "Alert": S.GROUP_OBJECTS_PRIORITY_ALERT,
    }
    return names.get(priority, priority)


def _clamp_int(text: str, lo: int, hi: int) -> int:
    """Parse a group-address field to an int in [lo, hi]; empty/invalid text is treated as ``lo``."""
    try:
        return max(lo, min(hi, int(text.strip() or lo)))
    except ValueError:
        return lo


def _value_role(com_object: ComObject) -> tuple[str, imgui.ImVec4]:
    """How the object uses the bus, from its flags: it sends (Transmit/Read), receives
    (Write/Update), or both; a label and colour for its value. This is the object's actual
    direction, distinct from which group address is marked sending."""
    flags = com_object.flags
    sends = flags.transmit or flags.read
    receives = flags.write or flags.update
    if sends and receives:
        return S.GROUP_OBJECTS_VALUE_SENT_RECEIVED, themed(*_BOTH_COLOR)
    if sends:
        return S.GROUP_OBJECTS_VALUE_SENT, themed(*_SENT_COLOR)
    if receives:
        return S.GROUP_OBJECTS_VALUE_RECEIVED, themed(*_RECEIVED_COLOR)
    return "", themed(*_LIVE_COLOR)


class GroupObjectsTable:
    def __init__(
        self,
        set_flag: Callable[[Device, str, str, bool], None],
        on_link: Callable[[int, int], None],
        on_unlink: Callable[[int], None],
        on_auto_create_gas: Callable[[Device, list[ComObject], str, str], None]
        | None = None,
        suggest_ga: Callable[[Device, ComObject], tuple[str, str]] | None = None,
        get_name_template: Callable[[], str] | None = None,
        set_name_template: Callable[[str], None] | None = None,
        on_create_and_link: Callable[[Device, ComObject, str, str], None] | None = None,
        group_style: Callable[[], object] | None = None,
        next_free_sub: Callable[[int, int], int] | None = None,
        get_ga_range_tree: Callable[[], list[Any]] | None = None,
        on_navigate_ga: Callable[[int], None] | None = None,
        on_paste_links: PasteLinks | None = None,
        on_set_dpt: Callable[[Device, str, str | None], None] | None = None,
        on_set_priority: Callable[[Device, str, str | None], None] | None = None,
        on_set_description: Callable[[Device, str, str | None], None] | None = None,
        ga_menu: Callable[[int, ComObject], None] | None = None,
        get_latest_value: Callable[[str, str | None], tuple[str, str, float] | None]
        | None = None,
    ) -> None:
        self._set_flag = set_flag
        # Renders the context menu items of an assigned group address: (group_address_id, object).
        self._ga_menu = ga_menu
        # The last value seen on a group address (decoded, with its arrival time), or None.
        self._get_latest_value = get_latest_value
        self._on_link = on_link
        self._on_unlink = on_unlink
        self._on_auto_create_gas = on_auto_create_gas
        self._suggest_ga = suggest_ga
        # Read/write the program-wide group-address name template, edited inline in the "+" dialog.
        self._get_name_template = get_name_template
        self._set_name_template = set_name_template
        self._on_create_and_link = on_create_and_link
        self._group_style = group_style
        self._next_free_sub = next_free_sub
        self._get_ga_range_tree = get_ga_range_tree
        self._on_navigate_ga = on_navigate_ga
        self._on_paste_links = on_paste_links
        # Edit the per-instance DatapointType ("Data Type") / Priority via a dropdown of the valid
        # values; ``None`` clears the override (inherit the application default).
        self._on_set_dpt = on_set_dpt
        self._on_set_priority = on_set_priority
        # Edit the per-instance Description (free-text note) via a small popup opened from the row
        # context menu. State: the object being edited, its buffer, and a one-shot open flag.
        self._on_set_description = on_set_description
        self._desc_popup_device: Device | None = None
        self._desc_popup_co_id: str | None = None
        self._desc_buffer = ""
        self._open_desc_popup = False
        # Copied com-object configs (one per source object, in table order). Paste maps them onto
        # the checked target objects positionally.
        self._link_clipboard: list[_CopiedObject] = []
        # Paste option: merge keeps the target's existing links (add-only) instead of replacing.
        self._paste_merge = False
        self._picker_filter = ""
        self._selected: set[str] = set()
        self._batch_template = "{object}"
        self._batch_start = ""
        self._batch_open = False
        # Buffers for the per-row "create new GA" fields, prefilled when the "+" popup opens.
        self._create_addr = ""
        self._create_name = ""
        # True once the user hand-edited the name field, so changing the template no longer
        # overwrites their name. Reset each time the "+" dialog is (re)opened for an object.
        self._create_name_edited = False
        # 3-level entry: main/middle/sub; changing main or middle auto-fills the next free sub.
        # Kept as text (not input_int) so edits register per keystroke — input_int only reports the
        # change when the field loses focus, which broke the "recompute after 1s idle" debounce.
        self._create_main = "0"
        self._create_middle = "0"
        self._create_sub = "0"
        # Debounce timestamp for recomputing the next free sub after typing stops (monotonic secs).
        self._sub_recompute_at: float | None = None
        # The link/create dialog is a persistent, dockable window (not a popup that closes on a
        # click elsewhere). "+" sets its target; it stays open across tab clicks until closed.
        self._add_open = False
        self._add_device: Device | None = None
        self._add_co: ComObject | None = None

    def render(
        self,
        device: Device,
        com_objects: list[ComObject],
        get_links: GroupLinkResolver,
        get_all_group_addresses: GroupAddressCatalog,
    ) -> None:
        # Bulk action: create + link a group address for every selected com-object.
        if self._on_auto_create_gas is not None:
            selected = [c for c in com_objects if c.id in self._selected]
            # All three regular buttons so they share the same height/baseline (mixing small_button
            # with button left the taller "Create" button looking lower than All/None).
            # The row goes on in the next line where the panel is narrow.
            if imgui.button(S.GROUP_OBJECTS_SELECT_ALL):
                self._selected = {c.id for c in com_objects}
            same_line_if_fits(button_width(S.GROUP_OBJECTS_SELECT_NONE))
            imgui.begin_disabled(not selected)
            if imgui.button(S.GROUP_OBJECTS_SELECT_NONE):
                self._selected.clear()
            imgui.end_disabled()
            create = S.GROUP_OBJECTS_AUTO_CREATE.format(count=len(selected))
            same_line_if_fits(button_width(create))
            imgui.begin_disabled(not selected)
            if imgui.button(create):
                self._batch_open = True
            imgui.end_disabled()
            if self._on_paste_links is not None:
                targets = self._checked_targets(com_objects)
                self._handle_copy_paste_shortcuts(device, com_objects, get_links)
                copy = S.GROUP_OBJECTS_COPY_LINKS.format(count=len(targets))
                same_line_if_fits(button_width(copy))
                imgui.begin_disabled(not targets)
                if imgui.button(copy):
                    self._copy_checked(com_objects, get_links)
                imgui.end_disabled()
                paste_n = min(len(self._link_clipboard), len(targets))
                paste = S.GROUP_OBJECTS_PASTE_LINKS.format(count=paste_n)
                same_line_if_fits(button_width(paste))
                imgui.begin_disabled(paste_n == 0)
                if imgui.button(paste):
                    self._paste_to_checked(device, com_objects)
                imgui.end_disabled()
                # A disabled button tells nothing.
                if imgui.is_item_hovered():
                    imgui.set_tooltip(S.GROUP_OBJECTS_PASTE_LINKS_HINT)
                same_line_if_fits(checkbox_width(S.GROUP_OBJECTS_PASTE_MERGE))
                _, self._paste_merge = checkbox(
                    S.GROUP_OBJECTS_PASTE_MERGE, self._paste_merge, centred=True
                )
                if imgui.is_item_hovered():
                    imgui.set_tooltip(S.GROUP_OBJECTS_PASTE_MERGE_HINT)
            self._render_batch_popup(device, selected)

        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.row_bg  # zebra striping for readability across many objects
            | imgui.TableFlags_.reorderable
        )
        n_cols = 6 + len(FLAG_LABELS)
        if not begin_data_table(
            f"##group_objects_{device.node_id}", n_cols, flags, scroll_when_narrow=True
        ):
            return

        imgui.table_setup_column("#", imgui.TableColumnFlags_.width_fixed, px(64.0))
        stretch = imgui.TableColumnFlags_.width_stretch
        imgui.table_setup_column(S.GROUP_OBJECTS_COL_NAME, stretch, 0.278)
        imgui.table_setup_column(S.GROUP_OBJECTS_COL_DPT, stretch, 0.131)
        imgui.table_setup_column(S.GROUP_OBJECTS_COL_LENGTH, stretch, 0.067)
        imgui.table_setup_column(S.GROUP_OBJECTS_COL_PRIO, stretch, 0.096)
        imgui.table_setup_column(S.GROUP_OBJECTS_COL_GROUP_ADDRESSES, stretch, 0.428)
        # One letter per flag in most languages; some use a short abbreviation, which the column
        # widens for.
        padding = 2 * imgui.get_style().cell_padding.x
        for attr, _letter, _name in FLAG_LABELS:
            letter = flag_letter(attr)
            width = max(px(22.0), imgui.calc_text_size(letter).x + padding)
            imgui.table_setup_column(letter, imgui.TableColumnFlags_.width_fixed, width)
        data_table_headers()

        for com_obj in com_objects:
            self._render_row(device, com_obj, get_links, get_all_group_addresses)

        imgui.end_table()
        # The link/create dialog is drawn here (not as a per-row popup) so it survives clicks on
        # other left-hand tabs and can be docked.
        self._render_add_window(get_links, get_all_group_addresses)
        self._render_description_popup()

    def _checked_targets(self, com_objects: list[ComObject]) -> list[ComObject]:
        """Checked com-objects that have a DB row (the copy/paste source and target set)."""
        return [
            c for c in com_objects if c.id in self._selected and c.db_id is not None
        ]

    def _capture(
        self, com_object: ComObject, get_links: GroupLinkResolver
    ) -> _CopiedObject:
        """Snapshot one com-object's links and flag values onto the clipboard shape."""
        db_id = com_object.db_id
        links = get_links(db_id) if db_id is not None else []
        return _CopiedObject(
            links=[(ga_id, sending) for _aid, ga_id, _text, sending in links],
            flags={
                attr: bool(getattr(com_object.flags, attr))
                for attr, _letter, _name in FLAG_LABELS
            },
        )

    def _copy_checked(
        self, com_objects: list[ComObject], get_links: GroupLinkResolver
    ) -> None:
        self._link_clipboard = [
            self._capture(c, get_links) for c in self._checked_targets(com_objects)
        ]

    def _apply(self, device: Device, target: ComObject, copied: _CopiedObject) -> None:
        """Paste one clipboard entry onto a target object: its links (replace or merge) and its
        unlocked flag values, so flags are carried along with a copied object."""
        db_id = target.db_id
        if db_id is None or self._on_paste_links is None:
            return
        self._on_paste_links(db_id, copied.links, not self._paste_merge)
        for attr, _letter, _name in FLAG_LABELS:
            if attr not in copied.flags:
                continue
            is_locked = (
                getattr(target.flags, f"{attr}_locked", False)
                if attr != "communication"
                else False
            )
            value = copied.flags[attr]
            if not is_locked and bool(getattr(target.flags, attr)) != value:
                self._set_flag(device, target.id, attr, value)

    def _paste_to_checked(self, device: Device, com_objects: list[ComObject]) -> None:
        """Map the clipboard onto the checked targets positionally (1st to 1st, 2nd to 2nd, …)."""
        targets = self._checked_targets(com_objects)
        for target, copied in zip(targets, self._link_clipboard, strict=False):
            self._apply(device, target, copied)

    def _handle_copy_paste_shortcuts(
        self, device: Device, com_objects: list[ComObject], get_links: GroupLinkResolver
    ) -> None:
        """Ctrl+C / Ctrl+V for the checked rows, mirroring the toolbar buttons."""
        io = imgui.get_io()
        if io.want_text_input:
            return
        if not imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows):
            return
        if not (io.key_ctrl or io.key_super):
            return
        if imgui.is_key_pressed(imgui.Key.c) and self._checked_targets(com_objects):
            self._copy_checked(com_objects, get_links)
        elif (
            imgui.is_key_pressed(imgui.Key.v)
            and self._link_clipboard
            and self._checked_targets(com_objects)
        ):
            self._paste_to_checked(device, com_objects)

    def _render_batch_popup(self, device: Device, selected: list[ComObject]) -> None:
        if self._batch_open:
            imgui.open_popup(S.GROUP_OBJECTS_BATCH_TITLE)
            self._batch_open = False
        if not begin_dialog(S.GROUP_OBJECTS_BATCH_TITLE, width=440.0):
            return
        imgui.text_disabled(S.GROUP_OBJECTS_BATCH_NAME)
        imgui.set_next_item_width(-1)
        # Share the program-wide name template with the single-link dialog (uniform input); fall
        # back to the local buffer when no template callbacks are wired.
        template = (
            self._get_name_template()
            if self._get_name_template is not None
            else self._batch_template
        )
        changed, template = imgui.input_text("##batch_name", template)
        if changed:
            if self._set_name_template is not None:
                self._set_name_template(template)
            else:
                self._batch_template = template
        self._render_placeholder_reference(S.GROUP_OBJECTS_BATCH_HINT)
        imgui.text_disabled(S.GROUP_OBJECTS_BATCH_START)
        imgui.set_next_item_width(-1)
        _, self._batch_start = imgui.input_text_with_hint(
            "##batch_start", S.GROUP_OBJECTS_BATCH_START_HINT, self._batch_start
        )
        clicked = dialog_buttons(
            S.GROUP_OBJECTS_AUTO_CREATE.format(count=len(selected)),
            S.GROUP_OBJECTS_BATCH_CANCEL,
        )
        if clicked == 0:
            if self._on_auto_create_gas is not None:
                self._on_auto_create_gas(
                    device, selected, self._batch_start.strip(), template
                )
            self._selected.clear()
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _latest_values(
        self, links: list[GroupLink], com_object: ComObject
    ) -> dict[int, tuple[str, str, float]]:
        """Last bus value of each linked group address, by assignment id: decoded text, arrival
        time and a sortable epoch. ``link`` text is "address  name"; look up by the leading
        address."""
        if self._get_latest_value is None:
            return {}
        values: dict[int, tuple[str, str, float]] = {}
        for assignment_id, _ga_id, text, _is_sending in links:
            address = text.split(maxsplit=1)[0] if text else ""
            live = self._get_latest_value(address, com_object.dpt.token)
            if live is not None and live[0]:
                values[assignment_id] = live
        return values

    def _render_row(
        self,
        device: Device,
        com_object: ComObject,
        get_links: GroupLinkResolver,
        get_all_group_addresses: GroupAddressCatalog,
    ) -> None:
        row_id = f"{device.node_id}_{com_object.id}"
        imgui.table_next_row()

        imgui.table_set_column_index(0)
        if self._on_auto_create_gas is not None:
            sel = com_object.id in self._selected
            changed, new_sel = checkbox(f"##sel_{row_id}", sel)
            if changed:
                if new_sel:
                    self._selected.add(com_object.id)
                else:
                    self._selected.discard(com_object.id)
            imgui.same_line()
        imgui.text_disabled(str(com_object.number))

        imgui.table_set_column_index(1)
        imgui.text(com_object_display_name(com_object))
        # The per-instance @Description (free-text note) has no column of its own; show it on hover.
        if com_object.description and imgui.is_item_hovered():
            imgui.set_tooltip(com_object.description)
        if self._on_paste_links is not None or self._on_set_description is not None:
            self._render_row_context_menu(device, com_object, get_links)

        imgui.table_set_column_index(2)
        if self._on_set_dpt is not None and com_object.supported_dpts:
            self._render_dpt_combo(device, com_object)
        else:
            imgui.text_disabled(getattr(com_object.dpt, "name", "") or "")

        imgui.table_set_column_index(3)
        imgui.text_disabled(com_object.object_size)

        imgui.table_set_column_index(4)
        if self._on_set_priority is not None:
            self._render_priority_combo(device, com_object)
        else:
            imgui.text_disabled(priority_text(com_object.priority))

        imgui.table_set_column_index(5)
        db_id = com_object.db_id
        links = get_links(db_id) if db_id is not None else []
        # Sending group address first (accent colour); the rest are receive-only.
        ordered = sorted(links, key=lambda link: not link[3])
        # The last value seen on each linked address (decoded with the object's DPT). The sending
        # address carries what the object last sent, a receive-only one what it last got; the most
        # recently updated of them is the object's current value, so show it bright and dim the
        # others. ``text`` is "address  name", so look up by the leading address token.
        values = self._latest_values(ordered, com_object)
        newest = max((at for _text, _time, at in values.values()), default=None)
        direction, value_color = _value_role(com_object)
        older = S.GROUP_OBJECTS_VALUE_OLDER
        # Only a saved object has links, and its "+".
        if db_id is not None:
            _begin_link_buttons()
        for assignment_id, ga_id, text, is_sending in ordered:
            if _link_button(f"unlink{assignment_id}", "x"):
                self._on_unlink(assignment_id)
            _text_beside()
            imgui.same_line()
            if is_sending:
                # The sending group address is emphasised.
                imgui.text_colored(sending_green(), text)
            else:
                imgui.text(text)
            if imgui.is_item_hovered():
                if self._on_navigate_ga is not None:
                    imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
                imgui.set_tooltip(S.GA_SENDING if is_sending else S.GA_RECEIVING)
            if self._on_navigate_ga is not None and imgui.is_item_clicked():
                self._on_navigate_ga(ga_id)
            if self._ga_menu is not None and imgui.begin_popup_context_item(
                f"##ga_ctx{assignment_id}"
            ):
                self._ga_menu(ga_id, com_object)
                imgui.end_popup()
            live = values.get(assignment_id)
            if live is not None:
                value_text, value_time, at = live
                current = newest is None or at >= newest
                imgui.same_line()
                imgui.text_colored(
                    value_color if current else _dim(value_color), value_text
                )
                if imgui.is_item_hovered():
                    # Direction from the object's flags (what it does), not the sending-GA role;
                    # mark an address whose value is older than the object's newest.
                    notes = [n for n in (direction, None if current else older) if n]
                    suffix = f"  ({', '.join(notes)})" if notes else ""
                    imgui.set_tooltip(
                        f"{value_text}{suffix}\n{value_time}"
                        if value_time
                        else f"{value_text}{suffix}"
                    )
        if db_id is not None:
            self._render_add(device, com_object, db_id, links, get_all_group_addresses)
            _end_link_buttons()

        for col, (attr, _letter, _name) in enumerate(FLAG_LABELS, start=6):
            full_name = flag_name(attr)
            imgui.table_set_column_index(col)
            current = getattr(com_object.flags, attr)
            is_locked = (
                getattr(com_object.flags, f"{attr}_locked", False)
                if attr != "communication"
                else False
            )
            if is_locked:
                imgui.begin_disabled()
            changed, new_value = checkbox(f"##{row_id}_{attr}", current)
            if changed and not is_locked:
                self._set_flag(device, com_object.id, attr, new_value)
            if is_locked:
                imgui.end_disabled()
            if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
                imgui.set_tooltip(
                    S.TOOLTIP_LOCKED.format(name=full_name) if is_locked else full_name
                )

    def _render_dpt_combo(self, device: Device, com_object: ComObject) -> None:
        """Dropdown to pin the group object's per-instance DatapointType: the object's supported
        types, plus a "Default" entry that clears the override (inherit the application default)."""
        assert self._on_set_dpt is not None
        current = com_object.dpt
        preview = (
            f"{current.code} {current.name}"
            if current.major
            else S.GROUP_OBJECTS_DPT_DEFAULT
        )
        imgui.set_next_item_width(-1.0)
        if not begin_combo(f"##dpt_{com_object.id}", preview):
            return
        if imgui.selectable(
            S.GROUP_OBJECTS_DPT_DEFAULT, com_object.datapoint_type_override is None
        )[0]:
            self._on_set_dpt(device, com_object.id, None)
        for dpt in com_object.supported_dpts:
            selected = dpt.code == current.code
            if imgui.selectable(f"{dpt.code}  {dpt.name}", selected)[0]:
                self._on_set_dpt(device, com_object.id, dpt.code)
        imgui.end_combo()

    def _render_priority_combo(self, device: Device, com_object: ComObject) -> None:
        """Dropdown for the group object's per-instance Priority (Low/High/Alert), plus a "Default"
        entry that clears the override (inherit the application default)."""
        assert self._on_set_priority is not None
        current = com_object.priority
        imgui.set_next_item_width(-1.0)
        if not begin_combo(
            f"##prio_{com_object.id}",
            priority_text(current) if current else S.GROUP_OBJECTS_DPT_DEFAULT,
        ):
            return
        if imgui.selectable(S.GROUP_OBJECTS_DPT_DEFAULT, not current)[0]:
            self._on_set_priority(device, com_object.id, None)
        for priority in _PRIORITIES:
            if imgui.selectable(
                f"{priority_text(priority)}##{priority}", priority == current
            )[0]:
                self._on_set_priority(device, com_object.id, priority)
        imgui.end_combo()

    def _render_description_popup(self) -> None:
        """A small modal to edit the per-instance Description, opened from the row context menu."""
        if self._on_set_description is None:
            return
        if self._open_desc_popup:
            imgui.open_popup(S.GROUP_OBJECTS_EDIT_DESCRIPTION)
            self._open_desc_popup = False
        if not begin_dialog(S.GROUP_OBJECTS_EDIT_DESCRIPTION):
            return
        device, co_id = self._desc_popup_device, self._desc_popup_co_id
        if device is None or co_id is None:
            imgui.close_current_popup()
            end_dialog()
            return
        imgui.set_next_item_width(px(_PICKER_WIDTH))
        entered, self._desc_buffer = imgui.input_text(
            "##desc_edit",
            self._desc_buffer,
            imgui.InputTextFlags_.enter_returns_true,
        )
        clicked = dialog_buttons(S.OK, S.CANCEL)
        if clicked == 0 or entered:
            self._on_set_description(device, co_id, self._desc_buffer or None)
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_row_context_menu(
        self, device: Device, com_object: ComObject, get_links: GroupLinkResolver
    ) -> None:
        """Right-click menu on a single row: copy/paste this object's links, or copy its assigned
        addresses as plain text to the system clipboard."""
        if not imgui.begin_popup_context_item(
            f"##ctx_{device.node_id}_{com_object.id}"
        ):
            return
        if self._on_set_description is not None:
            if imgui.menu_item(S.GROUP_OBJECTS_CTX_EDIT_DESCRIPTION, "", False)[0]:
                self._desc_popup_device = device
                self._desc_popup_co_id = com_object.id
                self._desc_buffer = com_object.description
                self._open_desc_popup = True
            imgui.separator()
        if imgui.menu_item(S.GROUP_OBJECTS_CTX_COPY, "", False)[0]:
            self._link_clipboard = [self._capture(com_object, get_links)]
        if imgui.menu_item(
            S.GROUP_OBJECTS_CTX_PASTE, "", False, bool(self._link_clipboard)
        )[0]:
            self._apply(device, com_object, self._link_clipboard[0])
        db_id = com_object.db_id
        addresses = [text for _aid, _ga, text, _s in get_links(db_id)] if db_id else []
        if imgui.menu_item(S.GROUP_OBJECTS_CTX_COPY_TEXT, "", False, bool(addresses))[
            0
        ]:
            imgui.set_clipboard_text("\n".join(addresses))
        imgui.end_popup()

    def _render_create_new(self, device: Device, com_object: ComObject) -> None:
        """Quick-create section at the top of the '+' popup: a recommended new group address (next
        free address + name from room and object function), both editable, created and linked in
        one click. Reuses the project's next-free/create-and-link logic via injected callbacks."""
        if self._on_create_and_link is None:
            return
        imgui.text_disabled(S.GA_CREATE_NEW)
        address = self._render_address_inputs()
        imgui.set_next_item_width(-1.0)
        name_changed, self._create_name = imgui.input_text_with_hint(
            "##newname", S.GA_CREATE_NAME_HINT, self._create_name
        )
        if name_changed:
            self._create_name_edited = True
        self._render_name_template(device, com_object)
        # Share one row: Create fills the remaining width, Cancel keeps a fixed width so it stays
        # inside the window (a full-width Create button pushed Cancel off the right edge).
        cancel_w = px(90.0)
        spacing = imgui.get_style().item_spacing.x
        create_w = max(
            px(120.0), imgui.get_content_region_avail().x - cancel_w - spacing
        )
        if imgui.button(S.GA_CREATE_BUTTON, imgui.ImVec2(create_w, 0)):
            self._on_create_and_link(device, com_object, address, self._create_name)
            self._add_open = False  # created + linked -> close the dialog
        imgui.same_line()
        if imgui.button(
            f"{S.GROUP_OBJECTS_BATCH_CANCEL}##newga_cancel", imgui.ImVec2(cancel_w, 0)
        ):
            self._add_open = False
        imgui.separator()

    def _render_name_template(self, device: Device, com_object: ComObject) -> None:
        """The always-visible 'Name template' editor for the program-wide auto-name template, with
        the placeholder reference behind a collapsed section below it. Editing the template
        regenerates the suggested name live (unless the user hand-edited the name field)."""
        if self._get_name_template is None or self._set_name_template is None:
            return
        imgui.text_disabled(S.GA_NAME_TEMPLATE_SECTION)
        imgui.set_next_item_width(-1.0)
        changed, template = imgui.input_text_with_hint(
            "##ganametemplate", S.GA_CREATE_NAME_HINT, self._get_name_template()
        )
        if changed:
            self._set_name_template(template)
            if not self._create_name_edited and self._suggest_ga is not None:
                _, self._create_name = self._suggest_ga(device, com_object)
        self._render_placeholder_reference()

    def _render_placeholder_reference(self, extra: str | None = None) -> None:
        """A collapsed 'Placeholders' section listing each placeholder with a short description;
        ``extra`` appends a flow-specific line (e.g. the bulk flow's ``{n}`` index)."""
        if imgui.tree_node_ex(S.GA_NAME_TEMPLATE_PLACEHOLDERS):
            imgui.text_disabled(S.GA_NAME_TEMPLATE_HINT)
            if extra:
                imgui.text_disabled(extra)
            imgui.tree_pop()

    def _render_address_inputs(self) -> str:
        """Address entry for the new GA. In 3-level style three int fields (main/middle/sub) where
        changing main or middle auto-fills the next free sub; otherwise a single text field.
        Returns the assembled address string."""
        three_level = (
            self._group_style is not None
            and self._next_free_sub is not None
            and self._group_style() == GroupAddressStyle.THREE_LEVEL
        )
        if not three_level:
            imgui.set_next_item_width(px(_PICKER_WIDTH))
            _, self._create_addr = imgui.input_text_with_hint(
                "##newaddr", S.GA_CREATE_ADDR_HINT, self._create_addr
            )
            return self._create_addr

        w = px(_PICKER_WIDTH * 0.2)
        digits = imgui.InputTextFlags_.chars_decimal
        imgui.set_next_item_width(w)
        ch_main, self._create_main = imgui.input_text(
            "##ga_main", self._create_main, digits
        )
        imgui.same_line()
        imgui.text("/")
        imgui.same_line()
        imgui.set_next_item_width(w)
        ch_mid, self._create_middle = imgui.input_text(
            "##ga_mid", self._create_middle, digits
        )
        imgui.same_line()
        imgui.text("/")
        imgui.same_line()
        imgui.set_next_item_width(w)
        ch_sub, self._create_sub = imgui.input_text(
            "##ga_sub", self._create_sub, digits
        )
        main = _clamp_int(self._create_main, 0, 31)
        middle = _clamp_int(self._create_middle, 0, 7)
        sub = _clamp_int(self._create_sub, 0, 255)
        # Debounce: recompute the next free sub ~1 s after the last keystroke in main/middle (so
        # typing a multi-digit value doesn't recompute mid-entry). Editing the sub directly cancels
        # the pending recompute (respect the manual value). input_text reports ch_* per keystroke,
        # so this fires while still in the field — no need to click out.
        if ch_main or ch_mid:
            self._sub_recompute_at = time.monotonic() + 1.0
        if ch_sub:
            self._sub_recompute_at = None
        if (
            self._sub_recompute_at is not None
            and time.monotonic() >= self._sub_recompute_at
            and self._next_free_sub is not None
        ):
            sub = self._next_free_sub(main, middle)
            self._create_sub = str(sub)
            self._sub_recompute_at = None
        return f"{main}/{middle}/{sub}"

    def _render_add(
        self,
        device: Device,
        com_object: ComObject,
        db_id: int,
        links: list[GroupLink],
        get_all_group_addresses: GroupAddressCatalog,
    ) -> None:
        # The "+" only targets this object and opens the persistent dialog; the dialog itself is
        # drawn once per frame in _render_add_window (so it does not vanish on a click elsewhere).
        if _link_button(f"add{db_id}", "+"):
            self._picker_filter = ""
            self._add_device = device
            self._add_co = com_object
            self._add_open = True
            self._sub_recompute_at = None
            self._create_name_edited = False
            if self._suggest_ga is not None:
                self._create_addr, self._create_name = self._suggest_ga(
                    device, com_object
                )
                parts = self._create_addr.split("/")
                if len(parts) == 3 and all(
                    p.strip().lstrip("-").isdigit() for p in parts
                ):
                    self._create_main, self._create_middle, self._create_sub = (
                        p.strip() for p in parts
                    )

    def _render_add_window(
        self,
        get_links: GroupLinkResolver,
        get_all_group_addresses: GroupAddressCatalog,
    ) -> None:
        """Persistent, dockable 'link/create group address' window for the object last targeted by
        a '+'. Stays open across clicks on other tabs until closed with its window close button."""
        if not self._add_open or self._add_co is None or self._add_device is None:
            return
        device, com_object = self._add_device, self._add_co
        db_id = com_object.db_id
        if db_id is None:
            self._add_open = False
            return
        title = (
            f"{S.GA_LINK_TITLE}: {com_object_display_name(com_object)}##addga_window"
        )
        expanded, add_open = begin_tool_window(
            title, self._add_open, width=_PICKER_WIDTH + 24.0, height=560.0
        )
        self._add_open = bool(add_open)
        if expanded:
            self._render_create_new(device, com_object)
            already = {ga_id for _aid, ga_id, _text, _sending in get_links(db_id)}
            imgui.set_next_item_width(-1.0)
            _, self._picker_filter = imgui.input_text_with_hint(
                "##ga_filter", S.SEARCH_HINT, self._picker_filter
            )
            needle = self._picker_filter.lower()
            if imgui.begin_child("##ga_list", imgui.ImVec2(0.0, 0.0)):
                if self._get_ga_range_tree is not None:
                    # Show the group-address folder tree (ranges), collapsed by default; a filter
                    # force-opens matching folders.
                    for node in self._get_ga_range_tree():
                        self._render_ga_range(node, db_id, already, needle)
                else:
                    for ga_id, text in get_all_group_addresses():
                        if ga_id in already or (needle and needle not in text.lower()):
                            continue
                        if imgui.selectable(f"{text}##pick{db_id}_{ga_id}", False)[0]:
                            self._on_link(db_id, ga_id)
            imgui.end_child()
        imgui.end()

    def _render_ga_range(
        self, node: Any, db_id: int, already: set[int], needle: str
    ) -> None:
        """One group-range folder in the link picker; recurses into children and lists selectable
        group addresses. While filtering, only matching folders/addresses show (folder auto-open)."""
        if needle and not self._range_matches(node, needle):
            return
        label = f"{node.name or f'[{node.id}]'}##grpick{node.id}"
        if needle:
            imgui.set_next_item_open(True, imgui.Cond_.always)
        if not imgui.tree_node_ex(label):
            return
        for child in node.children:
            self._render_ga_range(child, db_id, already, needle)
        for ga in node.group_addresses:
            if ga.id in already:
                continue
            text = f"{ga.text}  {ga.name}"
            if needle and needle not in text.lower():
                continue
            if imgui.selectable(f"{text}##pick{db_id}_{ga.id}", False)[0]:
                self._on_link(db_id, ga.id)
        imgui.tree_pop()

    def _range_matches(self, node: Any, needle: str) -> bool:
        """True if this range or any descendant has a group address matching ``needle``."""
        if any(
            needle in f"{ga.text}  {ga.name}".lower() for ga in node.group_addresses
        ):
            return True
        return any(self._range_matches(child, needle) for child in node.children)
