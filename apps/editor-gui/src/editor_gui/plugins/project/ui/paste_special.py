"""The Paste Special dialog a drop with Ctrl held opens: how many copies, what they link (the same
group addresses, new ones in a group, or none) and where new group addresses go. It starts with
its defaults every time.

The trees' context menus copy and paste too: "Copy" puts an element on the clipboard (one for all
trees), "Paste" makes a copy of it where the menu was opened, "Paste Special…" asks how first.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.paste import PasteKind, PasteTarget
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.tree_drag import Dragged
from editor_gui.widgets.checkbox import radio_button
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.paste import (
    Allocation,
    GroupAddressCopy,
    PasteOptions,
    parse_target_range,
)

if TYPE_CHECKING:
    from xknxeditor.proj.core.service import GroupRangeInfo

# Copies of these need no choice about group addresses: they are group addresses or hold them.
_GROUP_KINDS = ("group_address", "group_range")
_ERROR_COLOR = imgui.ImVec4(1.0, 0.42, 0.42, 1.0)
# The most copies one paste makes.
_MAX_COUNT = 65535
# The text typed for the group of new group addresses, by style, at first.
_DEFAULT_RANGE = {
    GroupAddressStyle.THREE_LEVEL: "0/0",
    GroupAddressStyle.TWO_LEVEL: "0",
}


class _Clipboard:
    """What "Copy" in a tree's context menu copied last."""

    copied: Dragged | None = None


def render_copy_item(copied: Dragged) -> None:
    """The "Copy" item of a tree row's context menu; ``copied`` is what it copies."""
    if imgui.menu_item(S.CONTEXT_COPY, "", False)[0]:
        _Clipboard.copied = copied


def copied() -> Dragged | None:
    """What "Copy" copied last, None before."""
    return _Clipboard.copied


def _flat(ranges: list[GroupRangeInfo]) -> list[GroupRangeInfo]:
    """Every group range, nested ones too, by where they start."""
    out: list[GroupRangeInfo] = []

    def walk(nodes: list[GroupRangeInfo]) -> None:
        for node in nodes:
            out.append(node)
            walk(node.children)

    walk(ranges)
    return sorted(out, key=lambda r: (r.range_start, r.id))


class PasteSpecialDialog:
    """The dialog (see the module) of one tree panel; ``dialog_id`` keeps it apart from the
    other panels'. ``on_paste`` makes the copies and gives the reason when it cannot (shown with
    ``notify``)."""

    def __init__(
        self,
        dialog_id: str,
        *,
        group_style: Callable[[], GroupAddressStyle],
        group_ranges: Callable[[], list[GroupRangeInfo]],
        names: Callable[[PasteKind, list[int]], list[str]],
        on_paste: Callable[
            [PasteKind, list[int], PasteTarget, PasteOptions], str | None
        ],
        notify: Callable[[str], None] = lambda _text: None,
    ) -> None:
        self._title = f"{S.PASTE_TITLE}###paste_{dialog_id}"
        self._group_style = group_style
        self._group_ranges = group_ranges
        self._names = names
        self._on_paste = on_paste
        self._notify = notify
        self._pending: tuple[PasteKind, list[int], PasteTarget] | None = None
        self._open = False
        self._description = ""
        self._count = 1
        self._behaviour = GroupAddressCopy.DONT_COPY
        self._range_text = ""
        self._range_index = 0
        self._allocation = Allocation.FILL_UP
        self._start_with = 1
        self._offset = 0

    def open(self, kind: PasteKind, ids: list[int], target: PasteTarget) -> None:
        """Ask how to paste copies of ``ids`` to ``target``; the dialog opens in the next frame
        it is rendered in, with its defaults."""
        self._pending = (kind, ids, target)
        self._open = True
        self._description = ", ".join(self._names(kind, ids))
        self._count = 1
        self._behaviour = GroupAddressCopy.DONT_COPY
        self._range_text = _DEFAULT_RANGE.get(self._group_style(), "")
        self._range_index = 0
        self._allocation = Allocation.FILL_UP
        self._start_with = 1
        self._offset = 0

    def render_paste_items(
        self, target_for: Callable[[Dragged], PasteTarget | None]
    ) -> None:
        """The "Paste" and "Paste Special…" items of a tree row's context menu: what "Copy"
        copied goes where ``target_for`` says (None: not here, the items are disabled)."""
        clip = _Clipboard.copied
        target = target_for(clip) if clip is not None else None
        enabled = clip is not None and target is not None
        paste = imgui.menu_item(S.CONTEXT_PASTE, "", False, enabled)[0]
        special = imgui.menu_item(S.CONTEXT_PASTE_SPECIAL, "", False, enabled)[0]
        if clip is None or target is None:
            return
        if paste:
            error = self._on_paste(clip.kind, list(clip.ids), target, PasteOptions())
            if error:
                self._notify(error)
        if special:
            self.open(clip.kind, list(clip.ids), target)

    def options(self) -> PasteOptions:
        """The options chosen."""
        ranges = _flat(self._group_ranges())
        free = self._group_style() is GroupAddressStyle.FREE
        range_id = (
            ranges[self._range_index].id
            if free and 0 <= self._range_index < len(ranges)
            else None
        )
        return PasteOptions(
            count=self._count,
            group_addresses=self._behaviour,
            target_range="" if free else self._range_text,
            target_range_id=range_id,
            allocation=self._allocation,
            start_with=self._start_with,
            offset=self._offset,
        )

    def _range_valid(self) -> bool:
        style = self._group_style()
        if style is GroupAddressStyle.FREE:
            return bool(self._group_ranges())
        return parse_target_range(self._range_text, style) is not None

    def render(self) -> None:
        if self._open:
            imgui.open_popup(self._title)
            self._open = False
        if not begin_dialog(self._title, width=480.0):
            return
        pending = self._pending
        if pending is None:
            imgui.close_current_popup()
            end_dialog()
            return
        kind = pending[0]
        groups = kind in _GROUP_KINDS
        self._render_description()
        imgui.set_next_item_width(px(140.0))
        _, count = imgui.input_int(S.PASTE_COUNT, self._count)
        self._count = max(1, min(count, _MAX_COUNT))
        if not groups:
            self._render_group_addresses()
        imgui.begin_disabled(
            not groups and self._behaviour is not GroupAddressCopy.CREATE_NEW
        )
        self._render_generate()
        imgui.end_disabled()
        valid = groups or self._range_valid()
        clicked = dialog_buttons(S.BTN_OK, S.ML_CANCEL, enabled=(valid, True))
        if clicked == 0:
            self._pending = None
            imgui.close_current_popup()
            error = self._on_paste(kind, pending[1], pending[2], self.options())
            if error:
                self._notify(error)
        elif clicked == 1:
            self._pending = None
            imgui.close_current_popup()
        end_dialog()

    def _render_description(self) -> None:
        """The names of what is pasted, on one line, cut off with "…" when too long."""
        text = self._description
        room = imgui.get_content_region_avail().x
        if imgui.calc_text_size(text).x > room:
            while text and imgui.calc_text_size(f"{text}…").x > room:
                text = text[:-1]
            text = f"{text}…"
        imgui.text_disabled(text)
        if text != self._description and imgui.is_item_hovered():
            imgui.set_tooltip(self._description)

    def _render_group_addresses(self) -> None:
        imgui.separator_text(S.PANEL_GROUP_ADDRESSES)
        for behaviour, label in (
            (GroupAddressCopy.DONT_COPY, S.PASTE_GA_DONT_COPY),
            (GroupAddressCopy.KEEP, S.PASTE_GA_KEEP),
        ):
            if radio_button(label, self._behaviour is behaviour):
                self._behaviour = behaviour
        style = self._group_style()
        label = {
            GroupAddressStyle.THREE_LEVEL: S.PASTE_GA_NEW_MIDDLE,
            GroupAddressStyle.TWO_LEVEL: S.PASTE_GA_NEW_MAIN,
        }.get(style, S.PASTE_GA_NEW_RANGE)
        creating = self._behaviour is GroupAddressCopy.CREATE_NEW
        if radio_button(f"{label}##new", creating, centred=True):
            self._behaviour = GroupAddressCopy.CREATE_NEW
        imgui.same_line()
        imgui.begin_disabled(not creating)
        if style is GroupAddressStyle.FREE:
            ranges = _flat(self._group_ranges())
            imgui.set_next_item_width(px(150.0))
            _, self._range_index = combo(
                "##paste_range",
                self._range_index,
                [f"{r.range_start}-{r.range_end} {r.name}".strip() for r in ranges],
            )
        else:
            imgui.set_next_item_width(px(75.0))
            _, self._range_text = imgui.input_text("##paste_range", self._range_text)
        imgui.end_disabled()
        if style is not GroupAddressStyle.FREE and not self._range_valid():
            imgui.text_colored(
                _ERROR_COLOR,
                S.PASTE_NOT_MIDDLE
                if style is GroupAddressStyle.THREE_LEVEL
                else S.PASTE_NOT_MAIN,
            )

    def _render_generate(self) -> None:
        imgui.separator_text(S.PASTE_GENERATE)
        for allocation, label in (
            (Allocation.FILL_UP, S.PASTE_FILL_UP),
            (Allocation.APPEND, S.PASTE_APPEND),
        ):
            if radio_button(label, self._allocation is allocation):
                self._allocation = allocation
        for allocation, label in (
            (Allocation.START_WITH, S.PASTE_START_WITH),
            (Allocation.OFFSET, S.PASTE_OFFSET),
        ):
            chosen = self._allocation is allocation
            if radio_button(f"{label}##alloc", chosen, centred=True):
                self._allocation = allocation
            imgui.same_line()
            imgui.begin_disabled(not chosen)
            imgui.set_next_item_width(px(120.0))
            if allocation is Allocation.START_WITH:
                _, value = imgui.input_int("##start_with", self._start_with)
                self._start_with = max(1, min(value, _MAX_COUNT))
            else:
                _, value = imgui.input_int("##offset", self._offset)
                self._offset = max(0, min(value, _MAX_COUNT))
            imgui.end_disabled()
