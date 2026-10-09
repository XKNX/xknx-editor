"""The "Group Objects" table: each com-object with its assigned group addresses and flags,
including the group-address assignment column with linking/unlinking.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from imgui_bundle import imgui

from editor_gui.device import (
    FLAG_LABELS,
    ComObject,
    Device,
    com_object_display_name,
)
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.strings import S
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
    flags: dict[str, bool] = field(default_factory=dict)  # attr -> value (C/R/W/T/U)


# Accent colour for the sending group address (shown emphasised).
_SENDING_COLOR = imgui.ImVec4(0.45, 0.8, 0.5, 1.0)
# Width of the group-address link picker: wide enough for "address + full name" without clipping.
_PICKER_WIDTH = 460.0


def _clamp_int(text: str, lo: int, hi: int) -> int:
    """Parse a group-address field to an int in [lo, hi]; empty/invalid text is treated as ``lo``."""
    try:
        return max(lo, min(hi, int(text.strip() or lo)))
    except ValueError:
        return lo


class GroupObjectsTable:
    def __init__(
        self,
        set_flag: Callable[[Device, str, str, bool], None],
        on_link: Callable[[int, int], None],
        on_unlink: Callable[[int], None],
        on_auto_create_gas: Callable[[Device, list[ComObject], str, str], None]
        | None = None,
        suggest_ga: Callable[[Device, ComObject], tuple[str, str]] | None = None,
        on_create_and_link: Callable[[Device, ComObject, str, str], None] | None = None,
        group_style: Callable[[], object] | None = None,
        next_free_sub: Callable[[int, int], int] | None = None,
        get_ga_range_tree: Callable[[], list[Any]] | None = None,
        on_navigate_ga: Callable[[int], None] | None = None,
        on_paste_links: PasteLinks | None = None,
    ) -> None:
        self._set_flag = set_flag
        self._on_link = on_link
        self._on_unlink = on_unlink
        self._on_auto_create_gas = on_auto_create_gas
        self._suggest_ga = suggest_ga
        self._on_create_and_link = on_create_and_link
        self._group_style = group_style
        self._next_free_sub = next_free_sub
        self._get_ga_range_tree = get_ga_range_tree
        self._on_navigate_ga = on_navigate_ga
        self._on_paste_links = on_paste_links
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
            if imgui.button(S.GROUP_OBJECTS_SELECT_ALL):
                self._selected = {c.id for c in com_objects}
            imgui.same_line()
            imgui.begin_disabled(not selected)
            if imgui.button(S.GROUP_OBJECTS_SELECT_NONE):
                self._selected.clear()
            imgui.end_disabled()
            imgui.same_line()
            imgui.begin_disabled(not selected)
            if imgui.button(S.GROUP_OBJECTS_AUTO_CREATE.format(count=len(selected))):
                self._batch_open = True
            imgui.end_disabled()
            if self._on_paste_links is not None:
                targets = self._checked_targets(com_objects)
                self._handle_copy_paste_shortcuts(device, com_objects, get_links)
                imgui.same_line()
                imgui.begin_disabled(not targets)
                if imgui.button(S.GROUP_OBJECTS_COPY_LINKS.format(count=len(targets))):
                    self._copy_checked(com_objects, get_links)
                imgui.end_disabled()
                paste_n = min(len(self._link_clipboard), len(targets))
                imgui.same_line()
                imgui.begin_disabled(paste_n == 0)
                if imgui.button(S.GROUP_OBJECTS_PASTE_LINKS.format(count=paste_n)):
                    self._paste_to_checked(device, com_objects)
                imgui.end_disabled()
                if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
                    imgui.set_tooltip(S.GROUP_OBJECTS_PASTE_LINKS_HINT)
                imgui.same_line()
                _, self._paste_merge = imgui.checkbox(
                    S.GROUP_OBJECTS_PASTE_MERGE, self._paste_merge
                )
                if imgui.is_item_hovered():
                    imgui.set_tooltip(S.GROUP_OBJECTS_PASTE_MERGE_HINT)
            self._render_batch_popup(device, selected)

        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.row_bg  # zebra striping for readability across many objects
        )
        n_cols = 6 + len(FLAG_LABELS)
        if not imgui.begin_table(f"##group_objects_{device.node_id}", n_cols, flags):
            return

        imgui.table_setup_column("#", imgui.TableColumnFlags_.width_fixed, px(64.0))
        imgui.table_setup_column("Name", imgui.TableColumnFlags_.width_stretch, 0.28)
        imgui.table_setup_column("DPT", imgui.TableColumnFlags_.width_stretch, 0.1)
        imgui.table_setup_column("Length", imgui.TableColumnFlags_.width_stretch, 0.1)
        imgui.table_setup_column("Prio", imgui.TableColumnFlags_.width_stretch, 0.08)
        imgui.table_setup_column(
            "Group Addresses", imgui.TableColumnFlags_.width_stretch, 0.44
        )
        for _attr, letter, _name in FLAG_LABELS:
            imgui.table_setup_column(
                letter, imgui.TableColumnFlags_.width_fixed, px(22.0)
            )
        imgui.table_headers_row()

        for com_obj in com_objects:
            self._render_row(device, com_obj, get_links, get_all_group_addresses)

        imgui.end_table()
        # The link/create dialog is drawn here (not as a per-row popup) so it survives clicks on
        # other left-hand tabs and can be docked.
        self._render_add_window(get_links, get_all_group_addresses)

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
        unlocked flag values, mirroring how ETS carries flags along with a copied object."""
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
        imgui.set_next_window_size(px_vec2(440.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.GROUP_OBJECTS_BATCH_TITLE, None)[0]:
            return
        imgui.text_wrapped(S.GROUP_OBJECTS_BATCH_HINT)
        imgui.spacing()
        imgui.text_disabled(S.GROUP_OBJECTS_BATCH_NAME)
        imgui.set_next_item_width(-1)
        _, self._batch_template = imgui.input_text("##batch_name", self._batch_template)
        imgui.text_disabled(S.GROUP_OBJECTS_BATCH_START)
        imgui.set_next_item_width(-1)
        _, self._batch_start = imgui.input_text_with_hint(
            "##batch_start", "auto", self._batch_start
        )
        imgui.spacing()
        if imgui.button(
            S.GROUP_OBJECTS_AUTO_CREATE.format(count=len(selected)),
            px_vec2(200, 0),
        ):
            if self._on_auto_create_gas is not None:
                self._on_auto_create_gas(
                    device, selected, self._batch_start.strip(), self._batch_template
                )
            self._selected.clear()
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.GROUP_OBJECTS_BATCH_CANCEL, px_vec2(120, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

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
            changed, new_sel = imgui.checkbox(f"##sel_{row_id}", sel)
            if changed:
                if new_sel:
                    self._selected.add(com_object.id)
                else:
                    self._selected.discard(com_object.id)
            imgui.same_line()
        imgui.text_disabled(str(com_object.number))

        imgui.table_set_column_index(1)
        imgui.text(com_object_display_name(com_object))
        # The per-instance ETS @Description (free-text note) has no column of its own; show it on hover.
        if com_object.description and imgui.is_item_hovered():
            imgui.set_tooltip(com_object.description)
        if self._on_paste_links is not None:
            self._render_row_context_menu(device, com_object, get_links)

        imgui.table_set_column_index(2)
        imgui.text_disabled(getattr(com_object.dpt, "name", "") or "")

        imgui.table_set_column_index(3)
        imgui.text_disabled(com_object.object_size)

        imgui.table_set_column_index(4)
        imgui.text_disabled(com_object.priority)

        imgui.table_set_column_index(5)
        db_id = com_object.db_id
        links = get_links(db_id) if db_id is not None else []
        # Sending group address first (accent colour); the rest are receive-only.
        for assignment_id, ga_id, text, is_sending in sorted(
            links, key=lambda link: not link[3]
        ):
            if imgui.small_button(f"x##unlink{assignment_id}"):
                self._on_unlink(assignment_id)
            imgui.same_line()
            if is_sending:
                imgui.text_colored(_SENDING_COLOR, text)
            else:
                imgui.text(text)
            if imgui.is_item_hovered():
                if self._on_navigate_ga is not None:
                    imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
                imgui.set_tooltip(S.GA_SENDING if is_sending else S.GA_RECEIVING)
            if self._on_navigate_ga is not None and imgui.is_item_clicked():
                self._on_navigate_ga(ga_id)
        if db_id is not None:
            self._render_add(device, com_object, db_id, links, get_all_group_addresses)

        for col, (attr, _letter, full_name) in enumerate(FLAG_LABELS, start=6):
            imgui.table_set_column_index(col)
            current = getattr(com_object.flags, attr)
            is_locked = (
                getattr(com_object.flags, f"{attr}_locked", False)
                if attr != "communication"
                else False
            )
            if is_locked:
                imgui.begin_disabled()
            changed, new_value = imgui.checkbox(f"##{row_id}_{attr}", current)
            if changed and not is_locked:
                self._set_flag(device, com_object.id, attr, new_value)
            if is_locked:
                imgui.end_disabled()
            if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
                imgui.set_tooltip(
                    S.TOOLTIP_LOCKED.format(name=full_name) if is_locked else full_name
                )

    def _render_row_context_menu(
        self, device: Device, com_object: ComObject, get_links: GroupLinkResolver
    ) -> None:
        """Right-click menu on a single row: copy/paste this object's links, or copy its assigned
        addresses as plain text to the system clipboard."""
        if not imgui.begin_popup_context_item(
            f"##ctx_{device.node_id}_{com_object.id}"
        ):
            return
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
        imgui.set_next_item_width(px(_PICKER_WIDTH))
        _, self._create_name = imgui.input_text_with_hint(
            "##newname", S.GA_CREATE_NAME_HINT, self._create_name
        )
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
        if imgui.small_button(f"+##add{db_id}"):
            self._picker_filter = ""
            self._add_device = device
            self._add_co = com_object
            self._add_open = True
            self._sub_recompute_at = None
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
        win_w = px(_PICKER_WIDTH + 24.0)
        vp = imgui.get_main_viewport()
        # Open near the right edge by default (Cond_.appearing -> only first time; user can move it).
        imgui.set_next_window_pos(
            imgui.ImVec2(
                vp.work_pos.x + vp.work_size.x - win_w - px(40.0),
                vp.work_pos.y + px(120.0),
            ),
            imgui.Cond_.appearing,
        )
        imgui.set_next_window_size(
            imgui.ImVec2(win_w, px(420.0)), imgui.Cond_.appearing
        )
        imgui.set_next_window_bg_alpha(1.0)  # opaque, not see-through
        expanded, self._add_open = imgui.begin(title, self._add_open)
        if expanded:
            self._render_create_new(device, com_object)
            already = {ga_id for _aid, ga_id, _text, _sending in get_links(db_id)}
            imgui.set_next_item_width(px(_PICKER_WIDTH))
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
