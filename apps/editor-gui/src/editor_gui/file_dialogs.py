"""File dialogs: the system's own on the desktop, the page's in the browser UI (``--web``).

Both are polled the same way (``ready()``, then ``result()``), so a caller does not care which one
it got."""

from __future__ import annotations

from typing import Protocol

from imgui_bundle import portable_file_dialogs as pfd

from editor_gui import web


class OpenFileDialog(Protocol):
    def ready(self, timeout: int = ...) -> bool: ...
    def kill(self) -> bool: ...
    def result(self) -> list[str]: ...


class SaveFileDialog(Protocol):
    def ready(self, timeout: int = ...) -> bool: ...
    def kill(self) -> bool: ...
    def result(self) -> str: ...


SelectFolderDialog = SaveFileDialog


def open_file(
    title: str,
    default_path: str = "",
    filters: list[str] | None = None,
    options: pfd.opt = pfd.opt.none,
) -> OpenFileDialog:
    """Ask for one file to open (several with ``pfd.opt.multiselect``)."""
    hub = web.active_hub
    if hub is not None:
        from editor_gui.web.dialogs import WebOpenFile

        return WebOpenFile(
            hub,
            title,
            default_path,
            filters,
            multiselect=bool(options & pfd.opt.multiselect),
        )
    return pfd.open_file(title, default_path, filters or [], options)


def save_file(
    title: str,
    default_path: str = "",
    filters: list[str] | None = None,
    options: pfd.opt = pfd.opt.none,
) -> SaveFileDialog:
    """Ask where to save a file."""
    hub = web.active_hub
    if hub is not None:
        from editor_gui.web.dialogs import WebSaveFile

        return WebSaveFile(hub, title, default_path, filters)
    return pfd.save_file(title, default_path, filters or [], options)


def select_folder(
    title: str, default_path: str = "", options: pfd.opt = pfd.opt.none
) -> SelectFolderDialog:
    """Ask for a folder."""
    hub = web.active_hub
    if hub is not None:
        from editor_gui.web.dialogs import WebSelectFolder

        return WebSelectFolder(hub, title, default_path)
    return pfd.select_folder(title, default_path, options)
