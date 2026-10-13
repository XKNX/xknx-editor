from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from editor_gui.concurrency import MainThreadExecutor
    from editor_gui.plugins.catalog.service import CatalogService
    from editor_gui.plugins.connection.service import ConnectionService
    from editor_gui.plugins.logger.service import LogService
    from editor_gui.plugins.project.service import ProjectService

API_VERSION = 1


@dataclass
class PluginAPI:
    api_version: int
    project: "ProjectService"
    catalog: "CatalogService"
    connection: "ConnectionService"
    log: "LogService"
    # Transient user feedback (e.g. "Copied"), shown as a short toast by the app. Optional so
    # tests/other callers can build the API without a UI.
    notify: Callable[[str], None] | None = None
    # Marshals a callable onto the imgui/UI thread (drained each frame). Used to apply project edits
    # from background threads (e.g. async programming callbacks). Optional so tests can omit a UI.
    main_thread: "MainThreadExecutor | None" = None
    # Runs a callable on a worker thread behind the app's progress modal, labelled with the text.
    run_background: Callable[[str, Callable[[], None]], None] | None = None
