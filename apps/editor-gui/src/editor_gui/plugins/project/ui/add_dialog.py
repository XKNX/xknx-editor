"""The "Add …" dialog of the trees: several elements of one kind at once. Each row adds ``Count``
elements with a name (empty: the kind's default name) and, for some kinds, a choice (a line's or
a segment's medium, a room's use); rows are added with "Add Row" and removed with "-". Kinds with
numbers (areas, lines, groups, group addresses) also choose where their numbers start
("Generate Addresses"). It starts with one empty row every time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

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


@dataclass
class AddRow:
    """One row: how many, their name ("" for the default one) and the chosen option's value."""

    count: int = 1
    name: str = ""
    choice: str = ""


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


class AddDialog:
    """The dialog (see the module) of one tree panel; ``dialog_id`` keeps it apart from the
    others. ``on_add`` adds what was asked for and gives the reason when it cannot, which the
    dialog shows (it stays open then)."""

    def __init__(self, dialog_id: str) -> None:
        self._title = f"###add_{dialog_id}"
        self._heading = ""
        self._target = ""
        self._default_name = ""
        self._choice: AddChoice | None = None
        self._numbered = False
        self._start_minimum = 1
        self._on_add: Callable[[AddRequest], str | None] = lambda _r: None
        self._request = AddRequest()
        self._error = ""
        self._open = False

    def open(
        self,
        heading: str,
        target: str,
        on_add: Callable[[AddRequest], str | None],
        *,
        default_name: str = "",
        choice: AddChoice | None = None,
        numbered: bool = False,
        start_minimum: int = 1,
    ) -> None:
        """Ask what to add (``heading``, e.g. "Add Lines") to ``target`` (its name); the dialog
        opens in the next frame it is rendered in. ``numbered`` shows "Generate Addresses",
        whose "Start with" takes ``start_minimum`` at least."""
        self._heading = heading
        self._target = target
        self._on_add = on_add
        self._default_name = default_name
        self._choice = choice
        self._numbered = numbered
        self._start_minimum = start_minimum
        self._request = AddRequest(
            rows=[AddRow(choice=choice.default if choice is not None else "")],
            start_with=start_minimum,
        )
        self._error = ""
        self._open = True

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
            self._error = self._on_add(self._request) or ""
            if not self._error:
                imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

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
        imgui.same_line(left + count_width + style.item_spacing.x)
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
            _, count = imgui.input_int("##count", row.count)
            row.count = max(1, min(count, MAX_COUNT))
            imgui.same_line()
            spare = choice_width + style.item_spacing.x if self._choice else 0.0
            imgui.set_next_item_width(
                imgui.get_content_region_avail().x
                - spare
                - remove_width
                - style.item_spacing.x
            )
            _, row.name = imgui.input_text_with_hint(
                "##name", self._default_name, row.name
            )
            if self._choice is not None:
                imgui.same_line()
                imgui.set_next_item_width(choice_width)
                values = [value for value, _text in self._choice.options]
                current = values.index(row.choice) if row.choice in values else 0
                _, chosen = combo(
                    f"##choice{index}",
                    current,
                    [text for _value, text in self._choice.options],
                )
                row.choice = values[chosen] if values else ""
            imgui.same_line()
            imgui.begin_disabled(len(rows) == 1)
            if imgui.button("-##remove", imgui.ImVec2(remove_width, 0.0)):
                removed = index
            imgui.end_disabled()
            if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled):
                imgui.set_tooltip(S.ADD_REMOVE_ROW)
            imgui.pop_id()
        if removed is not None:
            del rows[removed]
        imgui.begin_disabled(len(rows) >= MAX_ROWS)
        if imgui.button(S.ADD_ROW):
            rows.append(AddRow(choice=rows[-1].choice))
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
        request.start_with = max(self._start_minimum, start)
        imgui.end_disabled()
