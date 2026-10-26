"""The "Add …" dialog of the trees: several elements of one kind at once. Each row adds ``Count``
elements with a name (empty: the kind's default name) and, for some kinds, a choice (a line's or
a segment's medium, a room's use); rows are added with "Add Row" and removed with "-". Kinds with
numbers (areas, lines, groups, group addresses) also choose where their numbers start
("Generate Addresses"). It starts with one empty row every time.

A kind with room for only so many more (16 areas, 16 lines in an area, …) is given that
``limit``: the counts of all rows together stay within it ("The limit to add was reached."), and
with no room left its "Add …" item is grayed out, telling why. OK always closes the dialog; the rows are added top
to bottom, one undo step each, and a row that cannot be added does not keep the later ones from
being added."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Protocol

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.checkbox import radio_beside_field, radio_button
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog
from editor_gui.widgets.dpi import px
from xknxeditor.proj.core.paste import Allocation

# The most rows, and the most elements one row adds.
MAX_ROWS = 20
MAX_COUNT = 100
# The most addresses a free-style group range spans.
MAX_CAPACITY = 65535


class UndoStep(Protocol):
    """Makes the edits made inside one undo step, ``label`` naming it in the history."""

    def __call__(self, label: str | None = None, /) -> AbstractContextManager[None]: ...


def no_undo_step(_label: str | None = None, /) -> AbstractContextManager[None]:
    """An :class:`UndoStep` that leaves the edits as they are."""
    return nullcontext()


@dataclass
class AddRow:
    """One row: how many, their name ("" for the default one), the chosen option's value and,
    for free-style group ranges, how many addresses each spans."""

    count: int = 1
    name: str = ""
    choice: str = ""
    capacity: int = 0


@dataclass(frozen=True)
class AddChoice:
    """The choice a kind's rows offer: its label and the options as (value, text)."""

    label: str
    options: list[tuple[str, str]]
    default: str = ""


@dataclass
class AddRequest:
    """What the dialog asks to add: its rows, in order, and where the numbers start."""

    rows: list[AddRow] = field(default_factory=list[AddRow])
    allocation: Allocation = Allocation.FILL_UP
    start_with: int = 1


def one_row(
    choice: AddChoice | None = None, start_with: int = 1, capacity: int = 0
) -> AddRequest:
    """What the dialog asks for when nothing in it is changed: one element with the defaults
    (a Shift+click on an "Add" item adds that without the dialog)."""
    return AddRequest(
        rows=[
            AddRow(
                choice=choice.default if choice is not None else "", capacity=capacity
            )
        ],
        start_with=start_with,
    )


def start_add(
    open_dialog: Callable[[], bool],
    on_add: Callable[[AddRequest], None],
    notify: Callable[[str], None],
    choice: AddChoice | None = None,
    *,
    start_with: int = 1,
    capacity: int = 0,
) -> None:
    """What an "Add" item does: open the dialog (``open_dialog``, False when there is no
    room left, which is told), or on a Shift+click add one with the defaults right away."""
    if imgui.get_io().key_shift:
        on_add(one_row(choice, start_with, capacity))
    elif not open_dialog():
        notify(S.ADD_LIMIT_REACHED)


def no_room(room: int) -> str:
    """Why nothing more can be added with ``room`` for so many more ("" while there is)."""
    return S.ADD_LIMIT_REACHED if room <= 0 else ""


def add_menu_item(label: str, why_not: str = "") -> bool:
    """An "Add …" menu item, clicked; with ``why_not`` (e.g. :func:`no_room`) it is grayed
    out and tells that when hovered."""
    clicked = imgui.menu_item(label, "", False, not why_not)[0]
    if why_not and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip(why_not)
    return clicked and not why_not


def add_button(label: str, why_not: str = "") -> bool:
    """An "Add …" toolbar button, clicked; grayed out like :func:`add_menu_item`."""
    imgui.begin_disabled(bool(why_not))
    clicked = imgui.small_button(label)
    imgui.end_disabled()
    if why_not and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
        imgui.set_tooltip(why_not)
    return clicked and not why_not


def add_rows(
    request: AddRequest,
    several: str,
    target: str,
    add: Callable[[AddRow], str | None],
    undo_step: UndoStep,
    notify: Callable[[str], None],
) -> None:
    """Add the rows of ``request`` top to bottom (``add`` adds one element of a row and gives
    the reason it cannot), one undo step each, ``several`` (with ``{count}`` and
    ``{target}``) naming one of several elements. A row stops at its first element that cannot
    be added, telling why, and the next row goes on."""
    for row in request.rows:
        label = (
            several.format(count=row.count, target=target) if row.count > 1 else None
        )
        with undo_step(label):
            for _ in range(row.count):
                reason = add(row)
                if reason:
                    notify(reason)
                    break


class AddDialog:
    """The dialog (see the module) of one tree panel; ``dialog_id`` keeps it apart from the
    others. ``on_add`` adds what was asked for."""

    def __init__(self, dialog_id: str) -> None:
        self._title = f"###add_{dialog_id}"
        self._heading = ""
        self._target = ""
        # The name a row's elements get when it has none, shown in its empty name field.
        self._default_name: Callable[[AddRow], str] = lambda _row: ""
        self._choice: AddChoice | None = None
        self._numbered = False
        self._start_minimum = 1
        self._start_maximum: int | None = None
        self._limit: int | None = None
        # The span a new free-style group range starts with; None: no Capacity column.
        self._capacity: int | None = None
        self._on_add: Callable[[AddRequest], None] = lambda _r: None
        self._request = AddRequest()
        self._error = ""
        self._open = False

    def open(
        self,
        heading: str,
        target: str,
        on_add: Callable[[AddRequest], None],
        *,
        default_name: str | Callable[[AddRow], str] = "",
        choice: AddChoice | None = None,
        numbered: bool = False,
        start_minimum: int = 1,
        start_maximum: int | None = None,
        limit: int | None = None,
        capacity: int | None = None,
    ) -> bool:
        """Ask what to add (``heading``, e.g. "Add Lines") to ``target`` (its name); the dialog
        opens in the next frame it is rendered in. ``default_name``: the name the elements of a
        row without one get (or show with), shown dimmed in its empty name field; a function of
        the row when it follows the row's choice. ``numbered`` shows "Generate Addresses",
        whose "Start with" takes ``start_minimum`` to ``start_maximum``. ``limit``: how many
        more there is room for. ``capacity`` shows a "Capacity" column (1 to 65535), starting
        at that. False (and no dialog) when there is room for none."""
        if limit is not None and limit <= 0:
            return False
        self._capacity = capacity
        self._heading = heading
        self._target = target
        self._on_add = on_add
        self._default_name = (
            default_name
            if callable(default_name)
            else (lambda _row, text=default_name: text)
        )
        self._choice = choice
        self._numbered = numbered
        self._start_minimum = start_minimum
        self._start_maximum = start_maximum
        self._limit = limit
        self._request = one_row(choice, start_minimum, capacity or 0)
        self._error = ""
        self._open = True
        return True

    @property
    def request(self) -> AddRequest:
        """What the dialog asks to add as filled in so far."""
        return self._request

    def render(self) -> None:
        title = f"{self._heading}{self._title}"
        if self._open:
            imgui.open_popup(title)
            self._open = False
        if not begin_dialog(title, width=520.0):
            return
        imgui.text_disabled(S.SPACES_ADD_TO.format(name=self._target))
        self._render_rows()
        if self._numbered:
            self._render_generate()
        if self._error:
            imgui.text_colored(imgui.ImVec4(1.0, 0.42, 0.42, 1.0), self._error)
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        if clicked == 0:
            imgui.close_current_popup()
            self._on_add(self._request)
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _clip(self, index: int) -> None:
        """Keep the counts of all rows within the limit, taking from the row ``index``."""
        rows = self._request.rows
        if self._limit is None:
            return
        others = sum(row.count for i, row in enumerate(rows) if i != index)
        if others + rows[index].count > self._limit:
            rows[index].count = max(1, self._limit - others)
            self._error = S.ADD_LIMIT_REACHED

    def _render_rows(self) -> None:
        rows = self._request.rows
        style = imgui.get_style()
        remove_width = imgui.get_frame_height()
        count_width = px(90.0)
        choice_width = px(130.0) if self._choice is not None else 0.0
        if self._choice is not None:
            choice_width = min(choice_width, imgui.get_content_region_avail().x / 3)
        # The column headings above the fields.
        left = imgui.get_cursor_pos_x()
        avail = imgui.get_content_region_avail().x
        imgui.text_disabled(S.SPACES_COUNT)
        name_left = left + count_width + style.item_spacing.x
        if self._capacity is not None:
            imgui.same_line(name_left)
            imgui.text_disabled(S.ADD_CAPACITY)
            name_left += count_width + style.item_spacing.x
        imgui.same_line(name_left)
        imgui.text_disabled(S.SPACES_SPACE_NAME)
        if self._choice is not None:
            imgui.same_line(
                left + avail - remove_width - style.item_spacing.x - choice_width
            )
            imgui.text_disabled(self._choice.label)
        removed: int | None = None
        for index, row in enumerate(rows):
            imgui.push_id(index)
            imgui.set_next_item_width(count_width)
            changed, count = imgui.input_int("##count", row.count)
            row.count = max(1, min(count, MAX_COUNT))
            if changed:
                self._clip(index)
            if self._capacity is not None:
                imgui.same_line()
                imgui.set_next_item_width(count_width)
                _, capacity = imgui.input_int("##capacity", row.capacity)
                row.capacity = max(1, min(capacity, MAX_CAPACITY))
            imgui.same_line()
            spare = choice_width + style.item_spacing.x if self._choice else 0.0
            imgui.set_next_item_width(
                imgui.get_content_region_avail().x
                - spare
                - remove_width
                - style.item_spacing.x
            )
            _, row.name = imgui.input_text_with_hint(
                "##name", self._default_name(row), row.name
            )
            if self._choice is not None:
                imgui.same_line()
                imgui.set_next_item_width(choice_width)
                values = [value for value, _text in self._choice.options]
                current = values.index(row.choice) if row.choice in values else 0
                # A single option is no choice.
                imgui.begin_disabled(len(values) <= 1)
                _, chosen = combo(
                    f"##choice{index}",
                    current,
                    [text for _value, text in self._choice.options],
                )
                imgui.end_disabled()
                row.choice = values[chosen] if values else ""
            imgui.same_line()
            imgui.begin_disabled(len(rows) == 1)
            if imgui.button("-##remove", imgui.ImVec2(remove_width, 0.0)):
                removed = index
            imgui.end_disabled()
            # A disabled button tells nothing.
            if imgui.is_item_hovered():
                imgui.set_tooltip(S.ADD_REMOVE_ROW)
            imgui.pop_id()
        if removed is not None:
            del rows[removed]
        full = self._limit is not None and sum(r.count for r in rows) >= self._limit
        imgui.begin_disabled(len(rows) >= MAX_ROWS or full)
        if imgui.button(S.ADD_ROW):
            rows.append(AddRow(choice=rows[-1].choice, capacity=self._capacity or 0))
        imgui.end_disabled()

    def _render_generate(self) -> None:
        imgui.separator_text(S.ADD_GENERATE)
        request = self._request
        for allocation, label in (
            (Allocation.FILL_UP, S.PASTE_FILL_UP),
            (Allocation.APPEND, S.PASTE_APPEND),
        ):
            if radio_button(label, request.allocation is allocation):
                request.allocation = allocation
        chosen = request.allocation is Allocation.START_WITH
        if radio_beside_field(f"{S.PASTE_START_WITH}##add", chosen):
            request.allocation = Allocation.START_WITH
        imgui.begin_disabled(not chosen)
        imgui.set_next_item_width(px(120.0))
        _, start = imgui.input_int("##add_start", request.start_with)
        if self._start_maximum is not None:
            start = min(start, self._start_maximum)
        request.start_with = max(self._start_minimum, start)
        imgui.end_disabled()
