"""File dialogs of the browser UI, polled like ``portable_file_dialogs``.

The browser shows the dialog. *Open* uploads a file from the user's computer into the file folder
or picks one already there; *Save* and *Select folder* name a file or folder in the file folder and,
when the user asks for it, download it to their computer once the app has written it."""

from __future__ import annotations

import itertools
import threading
from typing import TYPE_CHECKING, Any, cast

from editor_gui.web.files import safe_name

if TYPE_CHECKING:
    from editor_gui.web.server import WebHub

_ids = itertools.count(1)


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


class WebDialog:
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
        self.default_name = safe_name(default_path) if default_path else ""
        self.filters = filter_pairs(filters)
        self.multiselect = multiselect
        hub.open_dialog(self)

    def ready(self, timeout: int = 20) -> bool:
        return self._done.is_set()

    def kill(self) -> bool:
        self._done.set()
        self._hub.close_dialog(self.id)
        return True

    def event(self) -> dict[str, Any]:
        """The request for the browser."""
        folder = self._hub.files
        return {
            "t": "dialog",
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "name": self.default_name,
            "filters": [[label, patterns] for label, patterns in self.filters],
            "multi": self.multiselect,
            "files": [e.as_json() for e in folder.files()],
            "folders": [e.as_json() for e in folder.folders()],
            "where": str(folder.root),
        }

    def answer(self, event: dict[str, Any]) -> None:
        """The user's answer from the browser (on the GUI thread)."""
        if self._done.is_set():
            return
        if event.get("ok"):
            self._accept(event)
        self._done.set()
        self._hub.close_dialog(self.id)

    def _accept(self, event: dict[str, Any]) -> None:
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
