"""File dialogs and notices of the browser UI, drawn by the app like its other dialogs.

They are polled like ``portable_file_dialogs`` (``ready()``, then ``result()``). *Open* uploads
files from the user's computer into the file folder (the browser's own file picker, or files dropped
on the page) or picks one already there; *Save* and *Select folder* name a file or folder in the
file folder and, when the user wants, download it once the app has written it.

:class:`DialogHost` shows them one at a time: nested in the topmost open dialog (from
``end_dialog``), so a dialog that asked for a file stays open below, or on their own when no dialog
is open."""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from imgui_bundle import imgui

from editor_gui.regional import regional_format
from editor_gui.settings import load_settings, save_settings
from editor_gui.strings import S
from editor_gui.web.files import Entry, matches, safe_name
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import combo
from editor_gui.widgets.dialog import (
    begin_dialog,
    dialog_buttons,
    end_dialog,
    focus_when_appearing,
)
from editor_gui.widgets.dpi import px
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from editor_gui.widgets.text_util import fit_text, human_size
from editor_gui.widgets.value_pickers import format_date

if TYPE_CHECKING:
    from editor_gui.web.server import WebHub

_ids = itertools.count(1)
_WIDTH = 620.0
_LIST_HEIGHT = 220.0
# How often an open dialog reads the file folder again (seconds).
_REFRESH_SECONDS = 2.0


def filter_pairs(filters: list[str] | None) -> list[tuple[str, list[str]]]:
    """``portable_file_dialogs`` filters (label, patterns, label, patterns, …) as pairs."""
    items = list(filters or [])
    pairs: list[tuple[str, list[str]]] = []
    for i in range(0, len(items) - 1, 2):
        patterns = [p for p in items[i + 1].split() if p]
        pairs.append((items[i], ["*"] if patterns == ["*"] else patterns))
    return pairs


def _suffix(patterns: list[str]) -> str:
    """The extension a save adds when the name has none: ``.knxproj`` for ``*.knxproj``."""
    for pattern in patterns:
        if pattern.startswith("*.") and not any(c in pattern[2:] for c in "*?["):
            return pattern[1:]
    return ""


def _modified(timestamp: float) -> str:
    """A file's time in the regional date format."""
    moment = datetime.fromtimestamp(timestamp)
    day = format_date(moment.date().isoformat(), True, regional_format())
    return f"{day} {moment:%H:%M}"


def _download_default(filters: list[tuple[str, list[str]]]) -> bool:
    """Whether "Download to This Computer" starts ticked: not when a project is saved, which
    stays on the server, but for exports."""
    return not any("*.xknx" in patterns for _, patterns in filters[:1])


def _download_wanted(title: str, default: bool) -> bool:
    """Whether "Download to This Computer" starts ticked in a dialog of this title: as the user
    left it there, when that differs from ``default`` (saving a project on the server and
    exporting one are asked separately)."""
    choices = load_settings("web").get("download_choices")
    if isinstance(choices, dict):
        value = cast("dict[str, object]", choices).get(title)
        if isinstance(value, bool):
            return value
    return default


def _remember_download(title: str, value: bool, default: bool) -> None:
    """Keep the choice of a dialog of this title when it differs from ``default``."""
    settings = load_settings("web")
    stored = settings.get("download_choices")
    choices = (
        dict(cast("dict[str, object]", stored)) if isinstance(stored, dict) else {}
    )
    changed = choices.pop(title, None) is not None
    if value != default:
        choices[title] = value
        changed = True
    if changed:
        save_settings("web", {**settings, "download_choices": choices})


class _Item:
    """Something :class:`DialogHost` shows: a file dialog or a notice."""

    popup_id = ""
    closing = False
    # The page actions of this item's buttons are keyed with this prefix.
    area_prefix = ""

    def draw(self) -> None:
        raise NotImplementedError


def _item_rect() -> tuple[float, float, float, float] | None:
    """The last item's screen rectangle, None when it is scrolled out of view."""
    if not imgui.is_item_visible():
        return None
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    return (low.x, low.y, high.x - low.x, high.y - low.y)


def _entries_table(
    table_id: str,
    entries: list[Entry],
    *,
    selected: set[str] | None = None,
    sizes: bool = True,
    download: bool = False,
    on_download: Callable[[Entry], None] | None = None,
) -> tuple[Entry | None, bool, Entry | None]:
    """The files (or folders) of the file folder as a table: the clicked entry, whether it was a
    double click, and the entry whose Download button was clicked."""
    clicked: Entry | None = None
    double = False
    fetch: Entry | None = None
    if not entries:
        imgui.text_unformatted(S.WEB_NO_FILES)
        return None, False, None
    columns = 2 + int(sizes) + int(download)
    # Fixed columns as wide as their widest content (always computed, never a width saved from an
    # earlier list), the name gets the rest and long names end in "…".
    flags = (
        imgui.TableFlags_.borders_inner
        | imgui.TableFlags_.row_bg
        | imgui.TableFlags_.scroll_y
        | imgui.TableFlags_.sizing_stretch_prop
        | imgui.TableFlags_.resizable
        | imgui.TableFlags_.no_saved_settings
    )
    if not begin_data_table(
        table_id, columns, flags, imgui.ImVec2(0, px(_LIST_HEIGHT))
    ):
        return None, False, None
    style = imgui.get_style()
    imgui.table_setup_scroll_freeze(0, 1)
    imgui.table_setup_column(S.WEB_COL_NAME, imgui.TableColumnFlags_.width_stretch)
    if sizes:
        imgui.table_setup_column(
            S.WEB_COL_SIZE,
            imgui.TableColumnFlags_.width_fixed,
            max(imgui.calc_text_size(t).x for t in ("999.9 MB", S.WEB_COL_SIZE)),
        )
    imgui.table_setup_column(
        S.WEB_COL_MODIFIED,
        imgui.TableColumnFlags_.width_fixed,
        max(
            imgui.calc_text_size(t).x
            for t in (_modified(datetime.now().timestamp()), S.WEB_COL_MODIFIED)
        ),
    )
    if download:
        imgui.table_setup_column(
            "",
            imgui.TableColumnFlags_.width_fixed,
            imgui.calc_text_size(S.WEB_DOWNLOAD).x + 2 * style.frame_padding.x,
        )
    data_table_headers()
    for entry in entries:
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        chosen = selected is not None and entry.name in selected
        row_flags = imgui.SelectableFlags_.allow_double_click
        if not download:
            row_flags |= imgui.SelectableFlags_.span_all_columns
        full = f"{entry.name}/" if entry.folder and entry.name != ".." else entry.name
        shown = fit_text(full, imgui.get_content_region_avail().x)
        if imgui.selectable(f"{shown}##{entry.name}", chosen, row_flags)[0]:
            clicked = entry
            double = imgui.is_mouse_double_clicked(imgui.MouseButton_.left)
        if shown != full and imgui.is_item_hovered():
            imgui.set_tooltip(full)
        if sizes:
            imgui.table_next_column()
            if not entry.folder:
                imgui.text_disabled(human_size(entry.size))
        imgui.table_next_column()
        if entry.name != "..":
            imgui.text_disabled(_modified(entry.modified))
        if download:
            imgui.table_next_column()
            if not entry.folder:
                if imgui.small_button(f"{S.WEB_DOWNLOAD}##{entry.name}"):
                    fetch = entry
                if on_download is not None:
                    on_download(entry)
    imgui.end_table()
    return clicked, double, fetch


class WebDialog(_Item):
    kind = ""

    def __init__(
        self,
        hub: WebHub,
        title: str,
        default_path: str = "",
        filters: list[str] | None = None,
        *,
        multiselect: bool = False,
    ) -> None:
        self.id = next(_ids)
        self._hub = hub
        self._done = threading.Event()
        self.title = title
        self.popup_id = f"{title}###web-file-dialog-{self.id}"
        self.area_prefix = f"dialog-{self.id}:"
        self.default_name = safe_name(default_path) if default_path else ""
        self.filters = filter_pairs(filters) or [(S.FILE_DIALOG_ALL_FILES, ["*"])]
        self.multiselect = multiselect
        self._filter = 0
        self._name = self.default_name
        self._selected: set[str] = set()
        self._status = ""
        self._entries: list[Entry] = []
        self._read_at = 0.0
        self._download_default = _download_default(self.filters)
        self._download = _download_wanted(title, self._download_default)
        hub.open_dialog(self)

    def ready(self, timeout: int = 20) -> bool:
        return self._done.is_set()

    def kill(self) -> bool:
        self._finish()
        return True

    def event(self) -> dict[str, Any]:
        """What the browser page needs to know: whether files can be dropped on it."""
        return {
            "t": "dialog",
            "id": self.id,
            "upload": self.kind == "open",
            "multi": self.multiselect,
            "accept": self._accept_attribute(),
        }

    def _accept_attribute(self) -> str:
        patterns = self.filters[self._filter][1]
        suffixes = [p[1:] for p in patterns if p.startswith("*.") and "*" not in p[2:]]
        return ",".join(suffixes)

    def answer(self, event: dict[str, Any]) -> None:
        """The user's answer (from this dialog, or an upload from the page)."""
        if self._done.is_set():
            return
        if event.get("ok"):
            self._accept(event)
        self._finish()

    def _finish(self) -> None:
        self._done.set()
        self.closing = True
        self._hub.close_dialog(self.id)

    def _accept(self, event: dict[str, Any]) -> None:
        raise NotImplementedError

    def upload_event(self, event: dict[str, Any]) -> None:
        """Progress of an upload into this dialog, reported by the page."""
        state = event.get("state")
        if state == "start":
            self._status = S.WEB_UPLOADING.format(name=str(event.get("name", "")))
        elif state == "error":
            self._status = S.WEB_UPLOAD_FAILED.format(error=str(event.get("error", "")))
        elif state == "done":
            names = event.get("names")
            if isinstance(names, list) and names:
                self.answer({"ok": True, "names": cast("list[object]", names)})

    def _listed(self, folders: bool = False) -> list[Entry]:
        now = imgui.get_time()
        if not self._read_at or now - self._read_at > _REFRESH_SECONDS:
            folder = self._hub.files
            self._entries = folder.folders() if folders else folder.files()
            self._read_at = now or 1e-9
        if folders:
            return self._entries
        patterns = self.filters[self._filter][1]
        return [e for e in self._entries if matches(e.name, patterns)]

    def _file_type(self) -> None:
        if len(self.filters) < 2:
            return
        imgui.text_disabled(S.WEB_FILE_TYPE)
        imgui.set_next_item_width(-1)
        changed, self._filter = combo(
            "##file_type", self._filter, [label for label, _ in self.filters]
        )
        if changed and self.kind == "open":
            self._hub.open_dialog(self)  # the page's file picker follows the type

    def _name_field(self, label: str) -> bool:
        """The name input; whether Enter was pressed in it."""
        imgui.text_disabled(label)
        imgui.set_next_item_width(-1)
        focus_when_appearing()
        entered, self._name = imgui.input_text(
            "##name", self._name, imgui.InputTextFlags_.enter_returns_true
        )
        return bool(entered)

    def _download_box(self) -> None:
        imgui.spacing()
        _, self._download = checkbox(S.WEB_DOWNLOAD_AFTER, self._download)

    def draw(self) -> None:
        if not begin_dialog(self.popup_id, width=_WIDTH):
            return
        if self.closing:
            imgui.close_current_popup()
        else:
            self._draw_content()
        end_dialog()

    def _draw_content(self) -> None:
        raise NotImplementedError


class WebOpenFile(WebDialog):
    """Pick or upload files; ``result()`` is the list of their server paths."""

    kind = "open"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._paths: list[str] = []
        super().__init__(*args, **kwargs)

    def _accept(self, event: dict[str, Any]) -> None:
        names = event.get("names")
        if not isinstance(names, list):
            return
        for name in cast("list[object]", names):
            path = self._hub.files.path(str(name))
            if path.is_file():
                self._paths.append(str(path))
        if not self.multiselect:
            self._paths = self._paths[:1]

    def result(self) -> list[str]:
        return list(self._paths)

    def _draw_content(self) -> None:
        if imgui.button(S.WEB_UPLOAD):
            self._status = ""
            self._hub.request_pick(self)
        # A click on the button opens the browser's file picker in the page itself (Firefox and
        # Safari open it only from the click's own handler).
        self._hub.page_action(
            f"{self.area_prefix}pick", _item_rect(), {**self.event(), "kind": "pick"}
        )
        imgui.same_line()
        imgui.text_disabled(S.WEB_DROP_HINT)
        if self._status:
            imgui.text_wrapped(self._status)
        imgui.spacing()
        self._file_type()
        imgui.text_disabled(S.WEB_SERVER_FILES)
        clicked, double, _ = _entries_table(
            "##web_open", self._listed(), selected=self._selected
        )
        if clicked is not None:
            if not (self.multiselect and imgui.get_io().key_ctrl):
                self._selected = set()
            self._selected ^= {clicked.name}
            if double:
                self._selected = {clicked.name}
        chosen = dialog_buttons(
            S.WEB_OPEN, S.BTN_CANCEL, enabled=[bool(self._selected), True]
        )
        if chosen == 0 or (double and self._selected):
            self.answer({"ok": True, "names": sorted(self._selected)})
        elif chosen == 1:
            self.answer({"ok": False})


class WebSaveFile(WebDialog):
    """Name a file in the file folder; ``result()`` is its server path ("" when cancelled)."""

    kind = "save"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._path = ""
        super().__init__(*args, **kwargs)

    def _accept(self, event: dict[str, Any]) -> None:
        name = safe_name(str(event.get("name", "")))
        chosen = int(event.get("filter", 0) or 0)
        patterns = self.filters[chosen][1] if 0 <= chosen < len(self.filters) else []
        suffix = _suffix(patterns)
        if suffix and not name.lower().endswith(suffix.lower()):
            name += suffix
        path = self._hub.files.path(name)
        self._path = str(path)
        if event.get("download"):
            self._hub.download_when_written(path)

    def result(self) -> str:
        return self._path

    def _draw_content(self) -> None:
        entered = self._name_field(S.WEB_FILE_NAME)
        self._file_type()
        imgui.text_disabled(S.WEB_SERVER_FILES)
        clicked, double, _ = _entries_table("##web_save", self._listed())
        if clicked is not None:
            self._name = clicked.name
        self._download_box()
        named = bool(self._name.strip())
        chosen = dialog_buttons(S.WEB_SAVE, S.BTN_CANCEL, enabled=[named, True])
        if named and (chosen == 0 or entered or double):
            _remember_download(self.title, self._download, self._download_default)
            self.answer(
                {
                    "ok": True,
                    "name": self._name.strip(),
                    "filter": self._filter,
                    "download": self._download,
                }
            )
        elif chosen == 1:
            self.answer({"ok": False})


class WebSelectFolder(WebDialog):
    """Name a folder in the file folder; ``result()`` is its server path ("" when cancelled)."""

    kind = "folder"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._path = ""
        super().__init__(*args, **kwargs)

    def _accept(self, event: dict[str, Any]) -> None:
        path = self._hub.files.path(str(event.get("name", "")))
        path.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        if event.get("download"):
            self._hub.download_when_written(path)

    def result(self) -> str:
        return self._path

    def _draw_content(self) -> None:
        entered = self._name_field(S.WEB_FOLDER_NAME)
        imgui.text_disabled(S.WEB_SERVER_FILES)
        clicked, double, _ = _entries_table(
            "##web_folder", self._listed(folders=True), sizes=False
        )
        if clicked is not None:
            self._name = clicked.name
        self._download_box()
        named = bool(self._name.strip())
        chosen = dialog_buttons(S.WEB_SELECT, S.BTN_CANCEL, enabled=[named, True])
        if named and (chosen == 0 or entered or double):
            _remember_download(self.title, self._download, self._download_default)
            self.answer(
                {"ok": True, "name": self._name.strip(), "download": self._download}
            )
        elif chosen == 1:
            self.answer({"ok": False})


class FolderNotice(_Item):
    """A folder of the server ("Open … Folder"), its files downloadable one by one. Its subfolders
    open with a click, ".." goes back up, but not above the folder shown first."""

    def __init__(self, hub: WebHub, folder: Path) -> None:
        self._hub = hub
        self.root = folder
        self.folder = folder
        number = next(_ids)
        self.popup_id = f"{S.WEB_FOLDER_TITLE}###web-folder-{number}"
        self.area_prefix = f"folder-{number}:"
        self._urls: dict[Path, str] = {}
        self.entries = self._list()

    def _list(self) -> list[Entry]:
        """The subfolders by name, then the newest files (at most 200)."""
        try:
            children = list(self.folder.iterdir())
        except OSError:
            children = []
        folders: list[Entry] = []
        files: list[Entry] = []
        for child in children:
            try:
                stat = child.stat()
                is_dir = child.is_dir()
            except OSError:
                continue
            if is_dir:
                folders.append(Entry(child.name, 0, stat.st_mtime, folder=True))
            else:
                files.append(Entry(child.name, stat.st_size, stat.st_mtime))
        folders.sort(key=lambda e: e.name.lower())
        files.sort(key=lambda e: e.modified, reverse=True)
        up = [Entry("..", 0, 0.0, folder=True)] if self.folder != self.root else []
        return up + folders + files[:200]

    def open(self, entry: Entry) -> None:
        """Go into the folder ``entry``, or up for ".."."""
        # The page's download areas of the files shown so far go with them.
        self._hub.clear_actions(self.area_prefix)
        if entry.name == "..":
            self.folder = self.folder.parent
        else:
            self.folder = self.folder / entry.name
        self.entries = self._list()

    def _url(self, name: str) -> str:
        path = self.folder / name
        if path not in self._urls:
            self._urls[path] = self._hub.file_url(path)
        return self._urls[path]

    def _download_area(self, entry: Entry) -> None:
        url = self._url(entry.name)
        self._hub.page_action(
            f"{self.area_prefix}{entry.name}",
            _item_rect(),
            {"kind": "download", "url": url, "name": entry.name},
        )

    def draw(self) -> None:
        if not begin_dialog(self.popup_id, width=_WIDTH):
            return
        if not self.closing:
            imgui.text_disabled(str(self.folder))
            clicked, _, fetch = _entries_table(
                "##web_folder_files",
                self.entries,
                download=True,
                on_download=self._download_area,
            )
            if clicked is not None and clicked.folder:
                self.open(clicked)
            elif fetch is not None:
                self._hub.download(self._url(fetch.name), fetch.name)
            if dialog_buttons(S.BTN_CLOSE) == 0:
                self.closing = True
        if self.closing:
            imgui.close_current_popup()
        end_dialog()


class LinkNotice(_Item):
    """A link or report the browser did not open by itself: the user opens it with a click."""

    def __init__(self, hub: WebHub, url: str, *, show: bool, name: str = "") -> None:
        self._hub = hub
        self.url = url
        self.show = show
        self.name = name or url
        number = next(_ids)
        self.popup_id = f"{S.WEB_OPEN_LINK}###web-link-{number}"
        self.area_prefix = f"link-{number}:"

    def draw(self) -> None:
        if not begin_dialog(self.popup_id, width=520.0):
            return
        if not self.closing:
            imgui.text_wrapped(S.WEB_LINK_BLOCKED)
            imgui.text_disabled(self.name)
            rects: list[tuple[float, float, float, float]] = []
            chosen = dialog_buttons(
                S.WEB_SHOW if self.show else S.WEB_OPEN, S.BTN_CLOSE, rects=rects
            )
            self._hub.page_action(
                f"{self.area_prefix}open",
                rects[0],
                {"kind": "open", "url": self.url, "name": self.name},
            )
            if chosen == 0:
                self._hub.open_url(self.url, name=self.name, show=self.show)
            if chosen is not None:
                self.closing = True
        if self.closing:
            imgui.close_current_popup()
        end_dialog()


class DownloadNotice(_Item):
    """A file ready for download, for a browser that starts downloads only from a click (Safari)."""

    def __init__(self, hub: WebHub, url: str, name: str) -> None:
        self._hub = hub
        self.url = url
        self.name = name
        number = next(_ids)
        self.popup_id = f"{S.WEB_DOWNLOAD}###web-download-{number}"
        self.area_prefix = f"download-{number}:"

    def draw(self) -> None:
        if not begin_dialog(self.popup_id, width=460.0):
            return
        if not self.closing:
            imgui.text_wrapped(S.WEB_FILE_READY.format(name=self.name))
            rects: list[tuple[float, float, float, float]] = []
            chosen = dialog_buttons(S.WEB_DOWNLOAD, S.BTN_CLOSE, rects=rects)
            self._hub.page_action(
                f"{self.area_prefix}download",
                rects[0],
                {"kind": "download", "url": self.url, "name": self.name},
            )
            if chosen == 0:
                self._hub.download(self.url, self.name)
            if chosen is not None:
                self.closing = True
        if self.closing:
            imgui.close_current_popup()
        end_dialog()


class DialogHost:
    """Shows the web dialogs and notices one at a time, on the GUI thread."""

    def __init__(self, clear_actions: Callable[[str], None] | None = None) -> None:
        self._items: list[_Item] = []
        self._frame = -1
        self._clear_actions = clear_actions
        # The frame each item was added in (see draw).
        self._added: dict[int, int] = {}

    def add(self, item: _Item) -> None:
        if item not in self._items:
            self._items.append(item)
            # (Without an imgui context, in tests, there is no frame to wait for.)
            if cast("object | None", imgui.get_current_context()) is not None:
                self._added[id(item)] = imgui.get_frame_count()

    @property
    def items(self) -> list[_Item]:
        return list(self._items)

    def draw(self) -> None:
        """Called inside every ending dialog and once after the app's windows: shows the first
        item where it belongs, at most once a frame."""
        frame = imgui.get_frame_count()
        if not self._items or self._frame == frame:
            return
        item = self._items[0]
        # Not in the frame it was asked for: a dialog closing with that click (a confirmation)
        # would still be the one to nest it in, and a popup opened in a closed dialog never shows
        # but keeps every later one waiting.
        if self._added.get(id(item)) == frame:
            return
        mine = imgui.is_popup_open(item.popup_id)
        if not mine and item.closing:
            self._remove(item)
            return
        # Another popup is open at this level (a deeper dialog): that one ends first and shows it.
        if not mine and imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id):
            return
        self._frame = frame
        if not mine:
            imgui.open_popup(item.popup_id)
        item.draw()
        if item.closing and not imgui.is_popup_open(item.popup_id):
            self._remove(item)

    def _remove(self, item: _Item) -> None:
        self._items.remove(item)
        self._added.pop(id(item), None)
        if self._clear_actions is not None and item.area_prefix:
            self._clear_actions(item.area_prefix)
