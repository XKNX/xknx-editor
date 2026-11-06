"""The Settings window: the program's settings on pages, found by a search over their names in the
interface language and in English. The same settings are read and changed through MCP
(``settings_get`` / ``settings_set``), with the same functions the window calls."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from imgui_bundle import imgui

from editor_gui.strings import S, get_locale, in_english
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.dialog import (
    begin_dialog,
    dialog_buttons,
    end_dialog,
)
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box

Kind = Literal["bool", "choice", "text", "password", "int", "info", "custom"]


def _always() -> bool:
    return True


def _never() -> bool:
    return False


@dataclass(frozen=True, slots=True)
class Setting:
    """One setting: its name, how it is read and changed, and how the window shows it.

    ``kind`` picks the control: a checkbox (``bool``), a drop-down of ``choices`` (``choice``),
    a text, password or number field, a value to read only (``info``), or ``render`` drawing it
    (``custom``, e.g. buttons). ``get``/``set`` read and change the value; MCP uses them too, so
    a setting without ``set`` is read-only there. ``hint`` is shown below it and searched, like
    ``keywords``."""

    key: str
    label: Callable[[], str]
    kind: Kind
    get: Callable[[], Any] | None = None
    set: Callable[[Any], None] | None = None
    # (value, label) per choice of a drop-down.
    choices: Callable[[], Sequence[tuple[str, str]]] | None = None
    bounds: tuple[int, int] | None = None
    hint: Callable[[], str] | None = None
    keywords: Callable[[], str] | None = None
    render: Callable[[], None] | None = None
    shown: Callable[[], bool] = _always
    read_only: Callable[[], bool] = _never
    # Drawn across the whole page, its name above it, instead of beside it.
    wide: bool = False
    # A secret: MCP tells whether it is set, never the value.
    secret: bool = False


@dataclass(frozen=True, slots=True)
class SettingsPage:
    key: str
    title: Callable[[], str]
    settings: Sequence[Setting] = ()
    # A text above the page's settings, saying what they are for (searched like a hint).
    intro: Callable[[], str] | None = None


def _words(text: str) -> str:
    return " ".join(text.casefold().split())


def matches(query: str, texts: Sequence[str]) -> bool:
    """Whether every word of ``query`` is in one of ``texts`` (ignoring case)."""
    words = _words(query).split()
    haystack = [_words(text) for text in texts]
    return all(any(word in text for text in haystack) for word in words)


# Below this width (unscaled pixels) the pages are a drop-down above the settings, and a name is
# above its control.
_NARROW = 560.0
# Width of the list of pages (unscaled pixels).
_PAGE_LIST = 170.0
# Share of a page's width the names take beside their controls.
_LABEL_SHARE = 0.42


class SettingsWindow:
    """The window (a dialog). Opened with :meth:`open`, drawn by :meth:`render` every frame."""

    TITLE_ID = "###settings_window"

    def __init__(
        self,
        pages: Callable[[], Sequence[SettingsPage]],
        *,
        nested: Callable[[], None] | None = None,
    ) -> None:
        """``pages`` gives the pages; ``nested`` draws the dialogs a setting opens (e.g. a
        confirmation), on top of the window."""
        self._pages = pages
        self._nested = nested
        self._page = ""
        self._query = ""
        self._open_requested = False
        # The texts each setting is found by, per language: its name, hint and keywords, plus
        # its page's title, in that language and in English.
        self._search_texts: dict[tuple[str, str], tuple[str, ...]] = {}

    # --- the settings ---------------------------------------------------------------

    def pages(self) -> list[SettingsPage]:
        """The pages with the settings shown in this mode (desktop, browser, Home Assistant)."""
        out: list[SettingsPage] = []
        for page in self._pages():
            shown = [s for s in page.settings if s.shown()]
            if shown:
                out.append(SettingsPage(page.key, page.title, shown, page.intro))
        return out

    def setting(self, key: str) -> tuple[SettingsPage, Setting] | None:
        for page in self.pages():
            for setting in page.settings:
                if setting.key == key:
                    return page, setting
        return None

    def search(self, query: str) -> list[tuple[SettingsPage, list[Setting]]]:
        """The settings ``query`` finds, by page; all of them for an empty query."""
        found: list[tuple[SettingsPage, list[Setting]]] = []
        for page in self.pages():
            hits = [s for s in page.settings if matches(query, self._texts(page, s))]
            if hits:
                found.append((page, hits))
        return found

    def _texts(self, page: SettingsPage, setting: Setting) -> tuple[str, ...]:
        cache_key = (get_locale(), setting.key)
        texts = self._search_texts.get(cache_key)
        if texts is None:
            texts = (*_own_texts(page, setting), *_english(page, setting))
            self._search_texts[cache_key] = texts
        return texts

    # --- the window -------------------------------------------------------------------

    def open(self, page: str = "") -> None:
        """Open the window, on the page ``page`` (the one shown last when empty)."""
        if page:
            self._page = page
        self._query = ""
        self._open_requested = True

    @property
    def title(self) -> str:
        return f"{S.SETTINGS_TITLE}{self.TITLE_ID}"

    def is_open(self) -> bool:
        return imgui.is_popup_open(self.title)

    def render(self) -> None:
        if self._open_requested:
            self._open_requested = False
            imgui.open_popup(self.title)
        if not begin_dialog(self.title, width=780.0, height=560.0):
            return
        pages = self.pages()
        if pages and self._page not in {p.key for p in pages}:
            self._page = pages[0].key
        self._query = filter_box("##settings_search", S.SETTINGS_SEARCH, self._query)
        narrow = imgui.get_content_region_avail().x < px(_NARROW)
        if narrow and not self._query:
            self._render_page_combo(pages)
        # The rest of the dialog, but the row of buttons below.
        style = imgui.get_style()
        buttons = (
            4 * style.item_spacing.y + style.separator_size + imgui.get_frame_height()
        )
        height = max(imgui.get_content_region_avail().y - buttons, px(160.0))
        if not narrow:
            imgui.begin_child(
                "##settings_pages",
                imgui.ImVec2(px(_PAGE_LIST), height),
                imgui.ChildFlags_.borders,
            )
            self._render_page_list(pages)
            imgui.end_child()
            imgui.same_line()
        imgui.begin_child(
            "##settings_content", imgui.ImVec2(0.0, height), imgui.ChildFlags_.borders
        )
        self._render_content(pages, narrow)
        imgui.end_child()
        if self._nested is not None:
            self._nested()
        if dialog_buttons(S.BTN_CLOSE) is not None:
            imgui.close_current_popup()
        end_dialog()

    def _render_page_list(self, pages: Sequence[SettingsPage]) -> None:
        found = {page.key for page, _ in self.search(self._query)}
        for page in pages:
            # While searching, the pages without a match are dimmed.
            imgui.begin_disabled(bool(self._query) and page.key not in found)
            if imgui.selectable(
                f"{page.title()}##page_{page.key}",
                not self._query and page.key == self._page,
            )[0]:
                self._page = page.key
                self._query = ""
            imgui.end_disabled()

    def _render_page_combo(self, pages: Sequence[SettingsPage]) -> None:
        current = next((p for p in pages if p.key == self._page), None)
        imgui.set_next_item_width(-1)
        if begin_combo("##settings_page", current.title() if current else ""):
            for page in pages:
                if imgui.selectable(page.title(), page.key == self._page)[0]:
                    self._page = page.key
            imgui.end_combo()

    def _render_content(self, pages: Sequence[SettingsPage], narrow: bool) -> None:
        if self._query:
            found = self.search(self._query)
            if not found:
                imgui.text_disabled(S.SETTINGS_NOTHING_FOUND)
            for index, (page, settings) in enumerate(found):
                if index:
                    imgui.spacing()
                imgui.separator_text(page.title())
                self._render_settings(settings, narrow)
            return
        for page in pages:
            if page.key == self._page:
                if page.intro is not None:
                    imgui.push_text_wrap_pos(0.0)
                    imgui.text_disabled(page.intro())
                    imgui.pop_text_wrap_pos()
                    imgui.spacing()
                self._render_settings(page.settings, narrow)

    def _render_settings(self, settings: Sequence[Setting], narrow: bool) -> None:
        for setting in settings:
            imgui.push_id(setting.key)
            _render_setting(setting, narrow)
            imgui.pop_id()


def _own_texts(page: SettingsPage, setting: Setting) -> tuple[str, ...]:
    texts = [page.title(), setting.label()]
    if page.intro is not None:
        texts.append(page.intro())
    if setting.hint is not None:
        texts.append(setting.hint())
    if setting.keywords is not None:
        texts.append(setting.keywords())
    return tuple(texts)


def _english(page: SettingsPage, setting: Setting) -> tuple[str, ...]:
    with in_english():
        return _own_texts(page, setting)


def _render_setting(setting: Setting, narrow: bool) -> None:
    """A setting's name, its control beside it (below it on a narrow page or when ``wide``), and
    its hint below."""
    width = imgui.get_content_region_avail().x
    label_width = round(width * _LABEL_SHARE)
    beside = not narrow and not setting.wide
    if beside:
        imgui.align_text_to_frame_padding()
        imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + label_width - px(8.0))
        imgui.text(setting.label())
        imgui.pop_text_wrap_pos()
        imgui.same_line(label_width)
    elif setting.kind != "bool":
        imgui.text(setting.label())
    imgui.begin_group()
    imgui.begin_disabled(setting.read_only())
    _render_control(setting, label="" if beside else setting.label())
    imgui.end_disabled()
    if setting.hint is not None:
        hint = setting.hint()
        if hint:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_disabled(hint)
            imgui.pop_text_wrap_pos()
    imgui.end_group()
    imgui.spacing()


def _render_control(setting: Setting, label: str) -> None:
    if setting.kind == "custom":
        if setting.render is not None:
            setting.render()
        return
    value = setting.get() if setting.get is not None else None
    change = setting.set
    imgui.set_next_item_width(-1)
    if setting.kind == "bool":
        # Beside its name the box alone; on its own its name is its text.
        changed, checked = checkbox(f"{label}##value", bool(value), centred=not label)
        if changed and change is not None:
            change(checked)
    elif setting.kind == "choice":
        choices = list(setting.choices() if setting.choices is not None else [])
        preview = next(
            (text for key, text in choices if key == value), str(value or "")
        )
        if begin_combo("##value", preview):
            for key, text in choices:
                chosen = imgui.selectable(f"{text}##{key}", key == value)[0]
                if chosen and key != value and change is not None:
                    change(key)
            imgui.end_combo()
    elif setting.kind in ("text", "password"):
        flags = imgui.InputTextFlags_.password if setting.kind == "password" else 0
        changed, text = imgui.input_text("##value", str(value or ""), flags)
        if changed and change is not None:
            change(text)
    elif setting.kind == "int":
        changed, number = imgui.input_int("##value", int(value or 0))
        if changed and change is not None:
            if setting.bounds is not None:
                number = min(max(number, setting.bounds[0]), setting.bounds[1])
            change(number)
    elif setting.kind == "info":
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(str(value or ""))
