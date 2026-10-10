import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import imgui_bundle._patch_runners_add_save_screenshot_param as _screenshot_patch
import platformdirs

_screenshot_patch._get_caller_filename = lambda depth: ""  # type: ignore[assignment]

from imgui_bundle import hello_imgui, imgui
from imgui_bundle import portable_file_dialogs as pfd
from sqlalchemy.exc import SQLAlchemyError
from xknxproject.exceptions import InvalidPasswordException, XknxProjectException

from editor_gui import __version__
from editor_gui import signing_key as signing_key_store
from editor_gui import trace_key as trace_key_store
from editor_gui.certs import ensure_ca_bundle
from editor_gui.concurrency import MainThreadExecutor
from editor_gui.master_data import MasterDataInfo, load_master, master_xml_bytes
from editor_gui.plugins.base import API_VERSION, Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.catalog import CatalogPlugin, CatalogService
from editor_gui.plugins.cockpit import CockpitPlugin
from editor_gui.plugins.connection import ConnectionPlugin
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.health import HealthPlugin
from editor_gui.plugins.keyring import KeyringPlugin
from editor_gui.plugins.logger import LoggerPlugin, LogService
from editor_gui.plugins.mcp import McpServerPlugin
from editor_gui.plugins.monitor import MonitorPlugin
from editor_gui.plugins.network import NetworkPlugin
from editor_gui.plugins.project import ProjectPlugin, ProjectService
from editor_gui.plugins.project.knxproj_manufacturer import collect_manufacturer_bundle
from editor_gui.plugins.recover import RecoverPlugin
from editor_gui.plugins.signing import SigningPlugin
from editor_gui.plugins.timeline import TimelinePlugin
from editor_gui.plugins.topology import TopologyPlugin
from editor_gui.settings import config_dir, load_settings, save_settings
from editor_gui.strings import S, get_locale, set_locale
from editor_gui.update_check import UpdateInfo, check_for_update
from editor_gui.widgets.dpi import px, px_vec2
from xknxeditor.prod.errors import ArchiveError
from xknxeditor.proj import (
    ExportResult,
    MyKnxError,
    ProjectStorageError,
    export_knxproj,
    fetch_myknx_products,
    myknx_certificate_signer,
    signing_key_is_placeholder,
)
from xknxeditor.proj.core import import_notes
from xknxeditor.proj.core.import_notes import ImportLoss
from xknxeditor.proj.db import frame_db_profiler

_REPO_URL = "https://github.com/knx-ai/xknx-editor"
# Direct download of the Windows "compat" build (bundled Mesa software OpenGL, pure CPU) — the one that
# runs in VMs (UTM/QEMU) and on hosts without a working OpenGL driver. Pick the architecture-matching
# asset (native ARM64 vs x64). /releases/latest/download always points at the newest release's asset.
_COMPAT_ASSET = (
    "XKNX-Editor-windows-arm64.zip"  # the arm64 build already bundles software OpenGL
    if platform.machine() == "ARM64"
    else "XKNX-Editor-windows-softwaregl.zip"
)
_COMPAT_BUILD_URL = f"{_REPO_URL}/releases/latest/download/{_COMPAT_ASSET}"
_LINUX_FILE_DIALOG_HELPERS = ("zenity", "kdialog", "matedialog", "qarma")


def _find_linux_file_dialog_helper(path: str | None = None) -> str | None:
    """Return the first portable-file-dialog helper available on ``path``."""
    return next(
        (
            helper
            for helper in _LINUX_FILE_DIALOG_HELPERS
            if shutil.which(helper, path=path)
        ),
        None,
    )


def _restore_subprocess_library_path() -> None:
    """PyInstaller's Linux bootloader prepends the bundle directory to ``LD_LIBRARY_PATH`` and saves
    the caller's original value as ``LD_LIBRARY_PATH_ORIG``. External binaries the app spawns (the
    zenity/kdialog file-dialog helpers, the MCP subprocess) otherwise inherit the bundle's libraries
    and fail to start, which looks like a file dialog that never opens. Restore the original path so
    child processes see the system libraries; the GUI's own native libraries are already loaded."""
    if not getattr(sys, "frozen", False) or sys.platform != "linux":
        return
    original = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


def _linux_display_env() -> dict[str, str]:
    """Snapshot the display/sandbox variables that decide whether a file-dialog helper can show a
    window. A helper present on PATH but unable to reach a display server returns no selection, which
    looks like 'nothing happens'; logging this on an empty result pins the cause without a repro."""
    return {
        "helper": _find_linux_file_dialog_helper() or "none",
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
        "display": os.environ.get("DISPLAY", ""),
        "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
        "flatpak": os.environ.get("FLATPAK_ID", ""),
        "snap": os.environ.get("SNAP", ""),
        "appimage": os.environ.get("APPIMAGE", ""),
    }


def _github_release_api_url(url: str) -> str | None:
    """Map a GitHub *release page* URL to the REST API endpoint for that release, or ``None`` if the
    URL is not a GitHub release page (a direct asset URL / non-GitHub URL is returned unchanged by
    the caller). Handles ``/releases/tag/<tag>``, ``/releases/latest``, ``/releases`` and a bare
    ``owner/repo`` URL (both resolve to the latest release)."""
    import re

    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/releases/tag/([^/?#]+)", url)
    if m:
        owner, repo, tag = m.groups()
        return f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    m = re.match(
        r"^https?://github\.com/([^/]+)/([^/]+)(?:/releases(?:/latest)?)?/?(?:[?#].*)?$",
        url,
    )
    if m and "/releases/download/" not in url:
        owner, repo = m.group(1), m.group(2)
        return f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    return None


def _pick_release_asset(assets: list[dict[str, str]]) -> str:
    """Choose the importable asset's download URL from a GitHub release's assets. Prefers a
    ``.knxprod``, else a ``.zip``. Raises when there is none, or several to choose between (so the
    user pastes the specific asset link)."""
    candidates = [a for a in assets if a["name"].lower().endswith(".knxprod")] or [
        a for a in assets if a["name"].lower().endswith(".zip")
    ]
    if not candidates:
        raise ValueError("release has no .knxprod or .zip asset")
    if len(candidates) > 1:
        names = ", ".join(a["name"] for a in candidates)
        raise ValueError(
            f"release has several assets ({names}); paste the direct link to the one you want"
        )
    return candidates[0]["browser_download_url"]


def _resolve_download_url(url: str, *, timeout: float = 30.0) -> str:
    """Resolve a GitHub release *page* URL to its asset download URL; pass any other URL through."""
    api_url = _github_release_api_url(url)
    if api_url is None:
        return url
    import json
    import urllib.request

    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "xknx-editor", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        release = json.loads(resp.read())
    return _pick_release_asset(release.get("assets", []))


def _download_bytes(
    url: str, *, timeout: float = 60.0, max_bytes: int = 200 * 1024 * 1024
) -> bytes:
    """Download ``url`` to bytes. Only http(s) is allowed; the response is capped at ``max_bytes``
    (OpenKNX release ZIPs are a few MB). Follows redirects (GitHub release assets redirect to a CDN)."""
    import urllib.request
    from urllib.parse import urlparse

    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs are supported")
    req = urllib.request.Request(url, headers={"User-Agent": "xknx-editor"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("download exceeds the size limit")
    return data


def _myknx_product_name(product: dict[str, object], pid: str) -> str:
    """Best-effort human-readable name for a MyKnx product/license, else the id.

    The exact field name depends on the account's products, so try the known keys first and then
    any string-valued field whose key looks like a name/description (case-insensitive) and differs
    from the id. Returns ``pid`` when nothing usable is found (the caller renders the id alone)."""
    for key in ("name", "productName", "productType", "product_type", "description"):
        value = product.get(key)
        if isinstance(value, str) and value.strip() and value != pid:
            return value
    for key, value in product.items():
        if (
            ("name" in key.lower() or "description" in key.lower())
            and isinstance(value, str)
            and value.strip()
            and value != pid
        ):
            return value
    return pid


def _log_gl_renderer(log: Logger) -> None:
    """Query glGetString once and log the active OpenGL vendor/renderer/version.

    glGetString is a core GL 1.x entry point exported directly by the platform GL library, so no
    GL loader or extension lookup is needed. Called from post_init, where the context is current.
    Diagnostics only: any failure is swallowed so a missing/odd GL stack never blocks startup."""
    import ctypes

    gl_vendor, gl_renderer, gl_version = 0x1F00, 0x1F01, 0x1F02
    try:
        if sys.platform == "win32":
            lib = ctypes.WinDLL("opengl32")
        elif sys.platform == "darwin":
            lib = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
        else:
            lib = ctypes.CDLL("libGL.so.1")
        lib.glGetString.restype = ctypes.c_char_p
        lib.glGetString.argtypes = [ctypes.c_uint]
        vendor = lib.glGetString(gl_vendor)
        renderer = lib.glGetString(gl_renderer)
        version = lib.glGetString(gl_version)
    except Exception as exc:  # diagnostics only; never fatal
        log.debug("opengl query failed", error=str(exc))
        return

    def decode(value: bytes | None) -> str:
        return value.decode("ascii", "replace") if value else "?"

    log.info(
        "opengl",
        vendor=decode(vendor),
        renderer=decode(renderer),
        version=decode(version),
        gallium_driver=os.environ.get("GALLIUM_DRIVER", ""),
    )


class KnxGuiApp:
    def __init__(self, catalog_path: Path) -> None:
        self._catalog_service_path = catalog_path
        self._catalog_service = CatalogService(catalog_path)

        self._open_file_dialog: pfd.open_file | None = None
        self._save_file_dialog: pfd.save_file | None = None
        self._open_project_dialog: pfd.open_file | None = None
        self._save_project_dialog: pfd.save_file | None = None
        self._save_as_dialog: pfd.save_file | None = None
        self._import_knxproj_save_dialog: pfd.save_file | None = None
        self._import_ga_export_save_dialog: pfd.save_file | None = None
        self._import_ga_export_source: str | None = None
        self._export_knxproj_dialog: pfd.save_file | None = None
        self._last_export_path: str | None = None
        self._file_dialogs_available: bool | None = None
        # One-shot guard for the startup OpenGL renderer log (see _log_gl_renderer). Done from the
        # frame path, not post_init, so the GL context is guaranteed current (a context-less
        # glGetString segfaults at the C level and would be uncatchable).
        self._gl_logged = False
        # Overwrite guard: the portable file dialog does not confirm replacing an existing file on
        # macOS (it wraps AppleScript's "choose file name"), so we ask here. Requests queue as
        # (target, action) pairs so a second dialog resolving mid-confirmation cannot drop the first.
        self._overwrite_queue: list[tuple[str, Callable[[], None]]] = []
        self._overwrite_popup_open = False
        self._myknx_thread: threading.Thread | None = None
        # MyKnx sign-on-export prompt: after the save dialog, ask whether to also request a
        # project certificate. Credentials are prefilled from the environment for convenience.
        self._export_pending_dest: str | None = None
        self._myknx_prompt_requested = False
        self._myknx_username = os.environ.get("MYKNX_USERNAME", "")
        self._myknx_password = os.environ.get("MYKNX_PASSWORD", "")
        # After login we fetch the account's licenses so the user picks one instead of typing an
        # opaque product id. None = not logged in yet; [] = logged in but no licenses.
        self._myknx_products: list[tuple[str, str, bool]] | None = None
        self._myknx_selected_pid = os.environ.get("MYKNX_PRODUCT_ID", "")
        self._myknx_login_thread: threading.Thread | None = None
        self._myknx_login_error = ""
        self._import_knxproj_source: str | None = None
        self._import_knxproj_dest: str | None = None
        self._password_prompt_requested = False
        self._import_password = ""
        self._import_password_error: str | None = None
        # Network-drive consent: when opening/importing to a location SQLite can't use (SMB/NFS), we
        # ask before working on a local copy. `_action` runs on "Use local copy"; `_home` is shown.
        self._network_consent_requested = False
        self._network_consent_home: str | None = None
        self._network_consent_action: Callable[[], None] | None = None
        # "Load product from URL" prompt (imports a .knxprod / OpenKNX release / product XML).
        self._url_prompt_requested = False
        self._url_input = ""
        # Background import (keeps the UI responsive during the slow parse/catalog/device build).
        self._import_thread: threading.Thread | None = None
        self._import_pw: str | None = None
        self._import_needs_password = False
        # Generic background worker (catalog load, project open) sharing the progress modal.
        self._bg_thread: threading.Thread | None = None
        # Shared progress-modal state, driven by both the importer and generic background ops.
        self._progress_running = False
        self._progress_requested = False
        self._progress_started_at = 0.0
        self._progress_text = ""
        # Optional determinate progress: fraction in [0,1] (None = indeterminate animated bar) and a
        # sub-step label. Assigned from worker threads (float/str assignment is atomic under the GIL).
        self._progress_fraction: float | None = None
        self._progress_stage = ""
        # MCP tab state (host/port for the embedded MCP server), loaded from the local config.
        _mcp_cfg = load_settings("mcp")
        self._mcp_host = str(_mcp_cfg.get("host") or "127.0.0.1")
        self._mcp_port = str(_mcp_cfg.get("port") or "8765")
        # Optional bearer token; when set, the server requires it on every request. Empty = no auth.
        self._mcp_token = str(_mcp_cfg.get("token") or "")
        # Runs MCP tool bodies on this (imgui) thread; drained each frame in gui_menu.
        self._ui_executor = MainThreadExecutor()
        # Transient toasts, auto-raised from warning/error log records (visible failure feedback).
        self._toasts: list[tuple[str, str, float]] = []  # (text, level, expires_at)
        self._toast_seen_ts = time.time()
        # Set by the export worker on success; the render loop opens a summary window with the
        # archive's details (size, counts, signing). Worker->render handoff via the request flag.
        self._export_summary: ExportResult | None = None
        self._export_summary_path = ""
        self._export_summary_schema = ""
        self._export_summary_at = ""
        self._export_summary_requested = False
        # Set by the .knxprod load worker; the render loop opens a modal message box with it so a
        # catalog import gives explicit, click-to-dismiss feedback (not just a log line). _msg holds
        # the text while the modal is open (worker->render handoff via _result, latched into _msg).
        self._knxprod_result: str | None = None
        self._knxprod_msg: str | None = None
        # Lossy-import notes to show in a readable modal (set after a lossy import or before an
        # export that echoes them); rendered by _render_import_notes_modal.
        self._import_notes: list[ImportLoss] = []
        self._import_notes_intro: str = ""
        self._import_notes_requested = False
        # Set by the open/import worker when a network project is mirrored locally; the toast
        # renderer turns it into an info toast on the UI thread.
        self._mirror_notice: str | None = None
        self._welcome_dismissed = False  # user closed the welcome card this session
        self._about_requested = False
        # GitHub update check (best-effort, off the UI thread; see update_check.py).
        self._update_info: UpdateInfo | None = None
        self._update_prompt_requested = False
        self._update_thread: threading.Thread | None = None
        self._update_manual = False  # a user-triggered check also reports "up to date"
        # Target schema for .knxproj export. Default project/20 (schema 20 shape) imports into
        # schema-20 and schema-23 importers (the latter converts up). A native project/23 export is
        # only possible when every used device's product data is project/23-era; see _do_export_knxproj.
        self._export_schema = (
            "20"  # "23"=native project/23, "20"=project/20 (also imports in project/23)
        )
        # Command palette (Ctrl+P): fuzzy jump to devices / run actions.
        self._palette_open = False
        self._palette_query = ""
        self._palette_index = 0
        self._palette_focus = False

        self._project_service = ProjectService(self._catalog_service)
        self._connection_service = ConnectionService()
        self._log_service = LogService()

        # Global KNX master data (mask-version default procedures): needed to resolve
        # an UNLOAD scope and default/merged load procedures during a download.
        # Failure leaves master unset: the footer omits the version and an UNLOAD
        # download raises a clear error rather than crashing startup.
        self._master_info: MasterDataInfo | None = None
        try:
            master, self._master_info = load_master()
            self._connection_service.master = master
        except (OSError, ValueError):
            self._master_info = None

        self._plugin_api = PluginAPI(
            api_version=API_VERSION,
            project=self._project_service,
            catalog=self._catalog_service,
            connection=self._connection_service,
            log=self._log_service,
            notify=self._flash_toast,
            main_thread=self._ui_executor,
        )

        self._catalog_plugin = CatalogPlugin(self._plugin_api)
        self._connection_plugin = ConnectionPlugin(self._plugin_api)
        self._network_plugin = NetworkPlugin(self._plugin_api)
        self._monitor_plugin = MonitorPlugin(self._plugin_api)
        self._keyring_plugin = KeyringPlugin(self._plugin_api)
        # Data Secure: let programming/testing look up a device's tool key from the loaded keyring.
        self._connection_service.keyring = self._keyring_plugin.service
        self._signing_plugin = SigningPlugin(self._plugin_api)
        self._recover_plugin = RecoverPlugin(self._plugin_api)
        self._health_plugin = HealthPlugin(self._plugin_api)
        self._cockpit_plugin = CockpitPlugin(self._plugin_api)
        self._topology_plugin = TopologyPlugin(self._plugin_api)
        self._timeline_plugin = TimelinePlugin(
            self._plugin_api,
            get_telegrams=lambda: self._network_plugin.service.telegrams,
        )
        self._project_plugin = ProjectPlugin(
            self._plugin_api,
            get_selected_node_ids=lambda: [],
        )

        self._log = Logger(self._log_service, "app")
        self._logger_plugin = LoggerPlugin(self._log_service)

        # Embedded MCP server: drives these same live services (no panels, so not in _plugins).
        self._mcp_plugin = McpServerPlugin(
            self._plugin_api,
            self._ui_executor.run,
            self._connection_plugin,
            self._monitor_plugin.service,
            self._network_plugin.service,
            self._keyring_plugin.service,
        )

        self._plugins: list[Any] = [
            self._catalog_plugin,
            self._connection_plugin,
            self._network_plugin,
            self._monitor_plugin,
            self._keyring_plugin,
            self._signing_plugin,
            self._recover_plugin,
            self._project_plugin,
            self._cockpit_plugin,
            self._topology_plugin,
            self._timeline_plugin,
            self._health_plugin,
            self._logger_plugin,
        ]

    def setup(self) -> None:
        self._log.info("editor started")
        self._can_launch_file_dialog()
        # Apply a previously extracted/saved signing key so exports are signed with it.
        if signing_key_store.apply_cached_key():
            self._log.info("signing key loaded")
        # Apply a previously extracted project-log (trace) key so comments decrypt on open.
        if trace_key_store.apply_cached_key():
            self._log.info("trace key loaded")
        # Discover KNX gateways at startup and auto-connect to the last-used (or first) one.
        self._connection_plugin.autostart()
        # Best-effort check for a newer release on GitHub (unless the user disabled it).
        if self._update_check_enabled():
            self._start_update_check(manual=False)

    # --- update check (GitHub releases) -----------------------------------

    def _update_check_enabled(self) -> bool:
        return bool(load_settings("app").get("update_check_enabled", True))

    def _set_update_check_enabled(self, enabled: bool) -> None:
        data = load_settings("app")
        data["update_check_enabled"] = enabled
        save_settings("app", data)

    def _skipped_update_version(self) -> str:
        return str(load_settings("app").get("skipped_update_version", ""))

    def _skip_update_version(self, version: str) -> None:
        data = load_settings("app")
        data["skipped_update_version"] = version
        save_settings("app", data)

    def _start_update_check(self, *, manual: bool) -> None:
        if self._update_thread is not None and self._update_thread.is_alive():
            return
        self._update_manual = manual

        def worker() -> None:
            info = check_for_update(__version__)
            # Marshal the result back onto the UI thread (touches settings + toasts + popups).
            self._ui_executor.submit(lambda: self._on_update_result(info))

        self._update_thread = threading.Thread(
            target=worker, daemon=True, name="update-check"
        )
        self._update_thread.start()

    def _on_update_result(self, info: UpdateInfo | None) -> None:
        self._update_thread = None
        # A user-triggered check ignores a previously skipped version and reassures when up to date.
        if info is not None and (
            self._update_manual or info.version != self._skipped_update_version()
        ):
            self._update_info = info
            self._update_prompt_requested = True
        elif self._update_manual:
            self._flash_toast(S.UPDATE_UP_TO_DATE.format(version=__version__))

    def shutdown(self) -> None:
        # Stop the MCP server first so in-flight tool calls stop before the bus loop and project they
        # depend on are torn down.
        self._mcp_plugin.stop()
        self._connection_plugin.shutdown()
        if self._project_service.is_open:
            self._project_service.close()

    # --- recent files (persisted in the app settings) ---------------------

    def _recent_files(self) -> list[str]:
        raw = load_settings("app").get("recent_files")
        return [p for p in raw if isinstance(p, str)] if isinstance(raw, list) else []

    def _add_recent(self, path: str) -> None:
        recent = [p for p in self._recent_files() if p != path]
        recent.insert(0, path)
        data = load_settings("app")
        data["recent_files"] = recent[:10]  # keep the 10 most recent
        save_settings("app", data)

    def _remove_recent(self, path: str) -> None:
        """Drop a path from the recent list (e.g. a file that no longer exists)."""
        recent = [p for p in self._recent_files() if p != path]
        data = load_settings("app")
        data["recent_files"] = recent
        save_settings("app", data)

    def _new_project(self) -> None:
        if not self._can_launch_file_dialog():
            return
        self._save_project_dialog = pfd.save_file(
            S.FILE_DIALOG_PROJECT_SAVE_TITLE,
            "",
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
        )

    def _save_as(self) -> None:
        if not self._project_service.is_open or not self._can_launch_file_dialog():
            return
        self._save_as_dialog = pfd.save_file(
            S.FILE_DIALOG_SAVE_AS_TITLE,
            "",
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
        )

    def _open_project(self) -> None:
        if not self._can_launch_file_dialog():
            return
        # Accept .knxproj and .xml here too: _do_open_project routes .knxproj archives and ETS
        # group-address exports (ga-export/01 .xml) through their importers.
        self._log.debug("opening project file dialog")
        self._open_project_dialog = pfd.open_file(
            S.FILE_DIALOG_PROJECT_TITLE,
            "",
            [
                S.FILE_DIALOG_OPEN_FILTER,
                "*.xknx *.knxproj *.xml",
                S.FILE_DIALOG_PROJECT_FILTER,
                "*.xknx",
                S.FILE_DIALOG_KNXPROJ_FILTER,
                "*.knxproj",
                S.FILE_DIALOG_GA_EXPORT_FILTER,
                "*.xml",
                S.FILE_DIALOG_ALL_FILES,
                "*",
            ],
        )
        self._log.debug("project file dialog opened")

    def _can_launch_file_dialog(self) -> bool:
        if sys.platform != "linux":
            return True
        if self._file_dialogs_available is None:
            helper = _find_linux_file_dialog_helper()
            self._file_dialogs_available = helper is not None
            if helper is None:
                self._log.error(
                    "file dialog unavailable",
                    error="Install a file-dialog helper such as zenity or kdialog, then restart the app.",
                )
            else:
                self._log.debug("file dialog helper found", helper=helper)
        return self._file_dialogs_available

    def _export_knxproj(self) -> None:
        if not self._project_service.is_open or not self._can_launch_file_dialog():
            return
        default = "project.knxproj"
        if self._project_service.path is not None:
            default = self._project_service.path.with_suffix(".knxproj").name
        self._export_knxproj_dialog = pfd.save_file(
            S.FILE_DIALOG_KNXPROJ_SAVE_TITLE,
            default,
            [S.FILE_DIALOG_KNXPROJ_FILTER, "*.knxproj", S.FILE_DIALOG_ALL_FILES, "*"],
        )

    def _begin_export_sign_prompt(self, dest: str) -> None:
        """Ask about MyKnx certificate signing before running the export to ``dest``."""
        self._export_pending_dest = dest
        self._myknx_prompt_requested = True

    def _do_export_knxproj(
        self, dest: str, signer: Callable[[str, bytes, str], bytes | None] | None = None
    ) -> None:
        """Export the open project to ``dest`` on a worker thread.

        When ``signer`` is given (the MyKnx certificate signer), the export also requests a project
        certificate and embeds it. Runs off the UI thread because signing does blocking network I/O.
        """
        # Export READS the SQLite file, so use the working copy — for a network project the home
        # file is only current after a write-back, and reading it directly could be stale or fail.
        source = self._project_service.working_path
        if source is None or (self._myknx_thread and self._myknx_thread.is_alive()):
            return
        # Snapshot the program refs now (UI thread) together with `source`, so switching projects
        # mid-export can't pair project A's file with project B's manufacturer bundle.
        program_refs = self._project_service.program_refs()

        def _run() -> None:
            self._log.debug(
                "export knxproj",
                dest=dest,
                schema=self._export_schema,
                signed=signer is not None,
            )
            extra_files: dict[str, bytes] = {}
            master_xml: bytes | None = None
            try:
                bundle = collect_manufacturer_bundle(
                    program_refs, self._catalog_service
                )
                extra_files, master_xml = bundle.extra_files, bundle.master_xml
                self._log.info(
                    "manufacturer bundle collected",
                    manufacturers=len(bundle.resolved_manufacturers),
                    files=len(extra_files),
                    skipped=len(bundle.skipped_refs),
                )
            except (
                Exception
            ) as e:  # best effort: export the structure even if bundling fails
                self._log.warning(
                    "manufacturer bundle failed", error=f"{type(e).__name__}: {e}"
                )
            try:
                result = export_knxproj(
                    source,
                    Path(dest),
                    schema=self._export_schema,
                    extra_files=extra_files,
                    master_xml=master_xml,
                    # Substitute the canonical signed master when the bundled one is unusable (e.g.
                    # a merged, unsigned master from a legacy multi-source project).
                    fetch_master=True,
                    certificate_signer=signer,
                    # Name the exported project after the chosen file (the in-app project has no
                    # editable name yet), so the importer shows a meaningful name instead of "New project".
                    project_name=Path(dest).stem or None,
                )
                used_schema = result.schema
                self._last_export_path = dest
                # Report the schema actually written (it follows the device product data, so it may
                # differ from the selection -- e.g. project/20 when the catalog is schema-20-era).
                labels = {
                    "14": "ETS4",
                    "20": "ETS5/6 (project/20)",
                    "22": "ETS6",
                    "23": "ETS6 (project/23)",
                }
                # An export that references manufacturer data missing from the bundle is not a
                # usable archive (ETS aborts the import); do not report it as a success. The error
                # below surfaces the reason. Otherwise show the success summary window (set state
                # here on the worker thread; the modal opens on the next UI frame via the flag).
                if not result.missing_references:
                    self._export_summary = result
                    self._export_summary_path = dest
                    self._export_summary_schema = labels.get(
                        used_schema, f"project/{used_schema}"
                    )
                    self._export_summary_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self._export_summary_requested = True
                if result.import_notes:
                    # Remind the user what the source .knxproj carried that this export omits. Set
                    # state here (worker thread); the modal opens on the next UI frame via the flag.
                    self._import_notes = result.import_notes
                    self._import_notes_intro = S.IMPORT_NOTES_INTRO_EXPORT
                    self._import_notes_requested = True
                self._log.info(
                    "project exported", path=dest, certificate=signer is not None
                )
                if result.unverifiable_folders:
                    # The export succeeded, but one or more nested/baggage folders had no signature
                    # and got a best-effort one we cannot verify offline (the folder signature uses
                    # Windows-NLS collation). Surface as an error log record -> the toast system alerts the user.
                    self._log.error(
                        "export: folders without a verifiable signature; ETS may reject the import",
                        folders=", ".join(result.unverifiable_folders),
                        path=dest,
                    )
                if result.missing_references:
                    # The installation references manufacturer data absent from the bundle; the
                    # import converter aborts with "The given key was not present in the dictionary".
                    # Surface as an error log record -> the toast system alerts the user.
                    self._log.error(
                        "export: references missing from the manufacturer bundle; ETS will reject "
                        "the import (add the missing product to the catalog)",
                        references=", ".join(result.missing_references),
                        path=dest,
                    )
            except MyKnxError as e:
                # Clean, actionable text goes to the toast (error=); full server detail + HTTP
                # status stay in the Log record for debugging.
                self._log.error(
                    "export signing failed",
                    path=dest,
                    error=e.user_message,
                    status=e.status,
                    detail=e.detail,
                    exception=f"{type(e).__name__}: {e}",
                )
            except (
                Exception
            ) as e:  # OSError/ValueError from export, or other HTTP errors
                self._log.error(
                    "export failed", path=dest, error=f"{type(e).__name__}: {e}"
                )

        self._myknx_thread = threading.Thread(target=_run, daemon=True)
        self._myknx_thread.start()

    def _start_myknx_login(self) -> None:
        """Log into MyKnx and fetch the account's licenses on a worker thread."""
        if self._myknx_login_thread and self._myknx_login_thread.is_alive():
            return
        self._myknx_login_error = ""
        self._myknx_products = None
        self._myknx_login_thread = threading.Thread(
            target=self._run_myknx_login, daemon=True
        )
        self._myknx_login_thread.start()

    def _run_myknx_login(self) -> None:
        # Worker thread: network only, never imgui. Result is read on the UI thread next frame.
        try:
            products = fetch_myknx_products(self._myknx_username, self._myknx_password)
        except Exception as e:  # network/HTTP/login errors
            self._myknx_login_error = str(e)
            self._myknx_products = []
            return
        items: list[tuple[str, str, bool]] = []
        if products:
            # The label falls back to the raw id when no name field is recognized; log the actual
            # keys once so an unrecognized schema can be mapped without a live capture.
            self._log.info(
                "myknx products", count=len(products), keys=sorted(products[0])
            )
        for p in products:
            pid = str(p.get("id") or p.get("productId") or "")
            if not pid:
                continue
            name = _myknx_product_name(p, pid)
            lic = str(p.get("licenseNumber") or "")
            # A product has no unique name (several share a product-type name), so pair the name
            # with the license number; fall back to the raw id when neither is available.
            label = (
                "  -  ".join(x for x in (name if name != pid else "", lic) if x) or pid
            )
            # Only cloud-capable, non-expired licenses can produce an online certificate (ETS's own
            # filter). The others stay visible but disabled, with the reason appended to the label.
            expired = bool(p.get("isExpired"))
            cloud = bool(p.get("cloud_capable"))
            enabled = cloud and not expired
            if not enabled:
                reason = S.MYKNX_SIGN_EXPIRED if expired else S.MYKNX_SIGN_NO_CLOUD
                label = f"{label}  ({reason})"
            items.append((pid, label, enabled))
        self._myknx_products = items
        enabled_pids = {pid for pid, _, enabled in items if enabled}
        if self._myknx_selected_pid not in enabled_pids:
            # Prefer the first signable license; leave empty (Sign disabled) when none qualifies.
            self._myknx_selected_pid = (
                next(iter(enabled_pids), "") if enabled_pids else ""
            )

    def _render_myknx_sign_modal(self) -> None:
        """After the save dialog, ask whether to also sign the export with a MyKnx certificate.

        The user logs in first; we fetch the account's licenses and let them pick one (they need
        not know the product id). 'Export without certificate' always saves a normally-signed file.
        """
        if self._myknx_prompt_requested:
            imgui.open_popup(S.MYKNX_SIGN_TITLE)
            self._myknx_prompt_requested = False
            self._myknx_products = None  # fresh login each export
            self._myknx_login_error = ""
        # Fixed width so the wrapped text actually wraps and labels aren't pushed off-screen
        # (auto-resize + full-width inputs made this window span the whole screen before).
        imgui.set_next_window_size(px_vec2(600.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.MYKNX_SIGN_TITLE, None)[0]:
            return
        dest = self._export_pending_dest
        imgui.text_wrapped(S.MYKNX_SIGN_PROMPT)
        imgui.spacing()
        # Target format: schema 20 (default) or schema 23 -- matches what those
        # schema versions actually write (CreatedBy/ToolVersion + namespace).
        imgui.text_disabled(S.EXPORT_FORMAT)
        formats = [("23", S.EXPORT_FORMAT_ETS6), ("20", S.EXPORT_FORMAT_ETS5)]
        current = next(lbl for code, lbl in formats if code == self._export_schema)
        imgui.set_next_item_width(-1)
        if imgui.begin_combo("##export_schema", current):
            for code, lbl in formats:
                if imgui.selectable(lbl, code == self._export_schema)[0]:
                    self._export_schema = code
            imgui.end_combo()
        imgui.spacing()
        # Lock the credential fields while a login is in flight so an in-flight response for old
        # credentials can't populate licenses after the fields changed (which would sign with new
        # credentials against the old account's product id).
        logging_in = bool(
            self._myknx_login_thread and self._myknx_login_thread.is_alive()
        )
        # Labels above the fields (imgui's built-in label sits to the right and gets clipped when
        # the input is full-width). Editing credentials invalidates a previous login.
        if logging_in:
            imgui.begin_disabled()
        imgui.text_disabled(S.MYKNX_SIGN_USERNAME)
        imgui.set_next_item_width(-1)
        user_changed, self._myknx_username = imgui.input_text(
            "##myknx_user", self._myknx_username
        )
        imgui.text_disabled(S.MYKNX_SIGN_PASSWORD)
        imgui.set_next_item_width(-1)
        pw_changed, self._myknx_password = imgui.input_text(
            "##myknx_pw", self._myknx_password, imgui.InputTextFlags_.password
        )
        if logging_in:
            imgui.end_disabled()
        if (user_changed or pw_changed) and self._myknx_products is not None:
            self._myknx_products = None  # credentials changed -> require re-login
            self._myknx_login_error = ""

        selected_pid = self._render_myknx_license_section(logging_in)
        imgui.spacing()
        imgui.separator()
        # Alternative to online signing: license the exported .knxproj locally with a dongle.
        # The info popup is opened here (inside this modal) so it stacks as a child modal — closing
        # it returns to the export dialog instead of dismissing the whole export.
        if imgui.button(S.MYKNX_DONGLE_BUTTON, imgui.ImVec2(-1, 0)):
            imgui.open_popup(S.MYKNX_DONGLE_TITLE)
        self._render_dongle_modal()
        # Folder-signing key: open a child modal (stacks on top; closing returns to the export dialog).
        if signing_key_is_placeholder():
            imgui.text_colored(
                imgui.ImVec4(0.85, 0.7, 0.3, 1.0), S.SIGNING_KEY_PLACEHOLDER_HINT
            )
        if imgui.button(S.SIGNING_KEY_BUTTON, imgui.ImVec2(-1, 0)):
            imgui.open_popup(S.SIGNING_KEY_TITLE)
        self._render_signing_key_modal()
        imgui.spacing()

        can_sign = bool(selected_pid) and not logging_in
        if not can_sign:
            imgui.begin_disabled()
        sign = imgui.button(S.MYKNX_SIGN_CONFIRM, px_vec2(150, 0))
        if not can_sign:
            imgui.end_disabled()
        imgui.same_line()
        skip = imgui.button(S.MYKNX_SIGN_SKIP, px_vec2(200, 0))
        imgui.same_line()
        cancel = imgui.button(S.BTN_CANCEL, px_vec2(110, 0))
        if dest is not None and (sign and can_sign):
            self._do_export_knxproj(
                dest,
                myknx_certificate_signer(
                    self._myknx_username, self._myknx_password, selected_pid
                ),
            )
            self._export_pending_dest = None
            imgui.close_current_popup()
        elif dest is not None and skip:
            self._do_export_knxproj(dest)
            self._export_pending_dest = None
            imgui.close_current_popup()
        elif cancel:
            self._export_pending_dest = None
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_myknx_license_section(self, logging_in: bool) -> str:
        """Login button / license dropdown. Returns the selected product id ("" if none)."""
        if logging_in:
            imgui.text_disabled(S.MYKNX_SIGN_LOGGING_IN)
            return ""
        if self._myknx_products is None:
            can_login = bool(self._myknx_username and self._myknx_password)
            if not can_login:
                imgui.begin_disabled()
            if imgui.button(S.MYKNX_SIGN_LOGIN, px_vec2(150, 0)):
                self._start_myknx_login()
            if not can_login:
                imgui.end_disabled()
            return ""
        if self._myknx_login_error:
            imgui.text_colored(
                imgui.ImVec4(1.0, 0.4, 0.4, 1.0),
                S.MYKNX_SIGN_LOGIN_FAILED.format(error=self._myknx_login_error),
            )
            return ""
        if not self._myknx_products:
            imgui.text_disabled(S.MYKNX_SIGN_NO_LICENSES)
            return ""
        imgui.text_disabled(S.MYKNX_SIGN_LICENSE)
        current = next(
            (
                lbl
                for pid, lbl, _enabled in self._myknx_products
                if pid == self._myknx_selected_pid
            ),
            self._myknx_products[0][1],
        )
        imgui.set_next_item_width(-1)
        if imgui.begin_combo("##myknx_license", current):
            for pid, lbl, enabled in self._myknx_products:
                # Non-cloud/expired licenses stay visible but greyed out and unselectable, so the
                # user sees the whole account and why a license cannot sign online.
                if not enabled:
                    imgui.begin_disabled()
                    imgui.selectable(lbl, False)
                    imgui.end_disabled()
                elif imgui.selectable(lbl, pid == self._myknx_selected_pid)[0]:
                    self._myknx_selected_pid = pid
            imgui.end_combo()
        if not any(enabled for _pid, _lbl, enabled in self._myknx_products):
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.85, 0.7, 0.3, 1.0))
            imgui.text_wrapped(S.MYKNX_SIGN_NO_CLOUD_HINT)
            imgui.pop_style_color()
        return self._myknx_selected_pid

    def _do_import_knxproj(self, source: str, dest: str) -> None:
        self._log.info("knxproj import requested", source=source, dest=dest)
        self._import_knxproj_source = source
        self._import_knxproj_dest = dest
        # A network destination asks for consent (work on a local copy) before the import runs.
        self._maybe_consent_then(dest, lambda: self._start_import(None))

    def _start_import(self, password: str | None) -> None:
        """Run the import on a worker thread so the UI stays responsive (the facade holds the shared
        lock, so per-frame reads bail to empty placeholders while it runs)."""
        source, dest = self._import_knxproj_source, self._import_knxproj_dest
        if source is None or dest is None:
            self._log.warning(
                "knxproj import not started",
                source=source or "",
                dest=dest or "",
                reason="missing source or destination",
            )
            return
        if self._import_thread is not None:
            self._log.warning(
                "knxproj import not started; worker busy",
                source=source,
                dest=dest,
                thread=self._import_thread.name,
                alive=self._import_thread.is_alive(),
            )
            return
        self._log.info("importing knxproj", source=source, dest=dest)
        self._import_pw = password
        self._import_needs_password = False
        self._begin_progress(S.IMPORT_PROGRESS_TEXT)
        self._import_thread = threading.Thread(
            target=self._run_import,
            args=(source, dest, password),
            daemon=True,
            name="knxproj-import",
        )
        self._import_thread.start()
        self._log.debug("knxproj import dispatched", thread=self._import_thread.name)

    def _run_import(self, source: str, dest: str, password: str | None) -> None:
        # Worker thread: only touches services + logging (never imgui). Outcome is read in
        # _poll_import on the UI thread once the thread has finished.
        self._import_needs_password = self._try_import_knxproj(source, dest, password)

    def _poll_import(self) -> None:
        thread = self._import_thread
        if thread is None or thread.is_alive():
            return
        self._log.debug("knxproj import worker finished", thread=thread.name)
        self._import_thread = None
        self._progress_running = False
        if self._import_needs_password:
            # Wrong password on retry (a password was supplied); otherwise the first prompt.
            self._import_password_error = (
                S.IMPORT_PASSWORD_WRONG if self._import_pw is not None else None
            )
            self._import_password = ""
            self._password_prompt_requested = True
        else:
            self._clear_import_prompt()
            if self._import_notes:
                self._import_notes_intro = S.IMPORT_NOTES_INTRO_IMPORT
                self._import_notes_requested = True

    def _begin_progress(self, text: str) -> None:
        self._progress_text = text
        self._progress_running = True
        self._progress_requested = True
        self._progress_started_at = time.time()
        self._progress_fraction = None
        self._progress_stage = ""

    def set_progress(self, fraction: float | None, stage: str = "") -> None:
        """Update the progress modal from a worker: ``fraction`` in [0,1] for a determinate bar,
        or None for the indeterminate animation. ``stage`` is an optional sub-step label."""
        self._progress_fraction = fraction
        self._progress_stage = stage

    def _run_bg(self, text: str, fn: "Callable[[], None]") -> None:
        """Run ``fn`` on a worker thread behind the progress modal. ``fn`` must hold the shared IO
        lock while it writes so per-frame UI reads bail (see ProjectService/CatalogService)."""
        if self._bg_thread is not None or self._import_thread is not None:
            busy_threads: dict[str, Any] = {}
            if self._bg_thread is not None:
                busy_threads["background_thread"] = self._bg_thread.name
                busy_threads["background_alive"] = self._bg_thread.is_alive()
            if self._import_thread is not None:
                busy_threads["import_thread"] = self._import_thread.name
                busy_threads["import_alive"] = self._import_thread.is_alive()
            self._log.warning(
                "background task not started; worker busy",
                task=text,
                **busy_threads,
            )
            return
        self._begin_progress(text)

        def worker() -> None:
            try:
                fn()
            except Exception as e:
                self._log.error(
                    "background task failed", error=f"{type(e).__name__}: {e}"
                )

        self._bg_thread = threading.Thread(
            target=worker, daemon=True, name="background-task"
        )
        self._bg_thread.start()
        self._log.debug(
            "background task dispatched", task=text, thread=self._bg_thread.name
        )

    def _poll_bg(self) -> None:
        if self._bg_thread is not None and not self._bg_thread.is_alive():
            self._log.debug(
                "background task worker finished", thread=self._bg_thread.name
            )
            self._bg_thread = None
            self._progress_running = False

    def _render_progress_modal(self) -> None:
        if self._progress_requested:
            imgui.open_popup(S.PROGRESS_TITLE)
            self._progress_requested = False
        # Keep auto-resize (height fits content) but enforce a comfortable minimum width so long
        # device names in the status line don't wrap into a cramped, tall popup.
        imgui.set_next_window_size_constraints(
            px_vec2(560.0, 0.0), imgui.ImVec2(1.0e9, 1.0e9)
        )
        if not imgui.begin_popup_modal(
            S.PROGRESS_TITLE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        imgui.text_wrapped(self._progress_text)
        elapsed = time.time() - self._progress_started_at
        fraction = self._progress_fraction
        # Fixed width: a stretch (-1) bar inside an auto-resizing modal feeds back and shrinks it.
        bar_size = px_vec2(520.0, 0.0)
        if fraction is not None:
            fraction = max(0.0, min(1.0, fraction))
            imgui.progress_bar(fraction, bar_size, f"{fraction * 100:0.0f}%")
        else:
            # ImGui shows an animated indeterminate bar for a negative fraction.
            imgui.progress_bar(-1.0 * float(imgui.get_time()), bar_size, "")
        if self._progress_stage:
            imgui.text_disabled(self._progress_stage)
        imgui.text_disabled(f"{elapsed:0.0f}s")
        if not self._progress_running:
            imgui.close_current_popup()
        imgui.end_popup()

    def _try_import_knxproj(self, source: str, dest: str, password: str | None) -> bool:
        """Attempt the import. Returns ``True`` if a password is required (or wrong) and the caller
        should prompt; ``False`` on success or on any other failure (which is logged)."""

        def report(done: int, total: int, label: str = "") -> None:
            text = S.PROGRESS_DEVICES.format(done=done, total=total)
            self.set_progress(
                done / total if total else None,
                f"{text} — {label}" if label else text,
            )

        self._project_service.build_progress = report
        self._import_notes = []
        try:
            self._project_service.import_knxproj(
                Path(source), Path(dest), password=password
            )
            # Import opens the freshly built .xknx; record it in Open Recent like a normal open.
            self._add_recent(dest)
            # Lossy-import notes detected during the build; _poll_import raises the readable modal on
            # the UI thread when this is non-empty (setting state here is thread-safe; imgui is not).
            self._import_notes = self._project_service.get_import_notes()
            if self._project_service.mirroring_active:
                self._mirror_notice = str(self._project_service.mirror_home)
        except InvalidPasswordException:
            return True
        except ProjectStorageError as e:
            # A location that can't host the SQLite project (network share, read-only). The toast
            # system surfaces this error record; keep the text short and actionable.
            self._log.error("Cannot save the project here", source=source, error=str(e))
        except XknxProjectException as e:
            self._log.error("knxproj import failed", source=source, error=str(e))
        except Exception as e:
            # Runs on a worker thread: never let an unexpected error kill the thread silently, or
            # the UI would wait on a spinner that never clears. Log and report as a plain failure.
            self._log.error(
                "knxproj import error", source=source, error=f"{type(e).__name__}: {e}"
            )
        finally:
            self._project_service.build_progress = None
        return False

    def _render_import_password_modal(self) -> None:
        if self._password_prompt_requested:
            imgui.open_popup(S.IMPORT_PASSWORD_TITLE)
            self._password_prompt_requested = False
        imgui.set_next_window_size(px_vec2(420.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.IMPORT_PASSWORD_TITLE, None)[0]:
            return
        imgui.text_wrapped(S.IMPORT_PASSWORD_PROMPT)
        imgui.set_next_item_width(-1)
        submitted, self._import_password = imgui.input_text(
            "##import-password",
            self._import_password,
            imgui.InputTextFlags_.password | imgui.InputTextFlags_.enter_returns_true,
        )
        if self._import_password_error:
            imgui.text_colored(
                imgui.ImVec4(0.9, 0.4, 0.4, 1.0), self._import_password_error
            )
        imgui.spacing()
        btn_w = px_vec2(120, 0)
        confirm = imgui.button(S.BTN_OK, btn_w) or submitted
        imgui.same_line()
        cancel = imgui.button(S.BTN_CANCEL, btn_w)
        source, dest = self._import_knxproj_source, self._import_knxproj_dest
        if confirm and source is not None and dest is not None:
            # Retry on a worker thread; _poll_import re-opens this modal if the password was wrong.
            imgui.close_current_popup()
            self._start_import(self._import_password)
        elif cancel:
            self._clear_import_prompt()
            imgui.close_current_popup()
        imgui.end_popup()

    def _maybe_consent_then(self, home: str, action: Callable[[], None]) -> None:
        """Run ``action`` now, or — if ``home`` is a location SQLite can't use (network share) —
        ask the user first and run it only on consent. The mirroring itself happens in the facade."""
        if self._project_service.location_needs_local_copy(Path(home)):
            self._network_consent_home = home
            self._network_consent_action = action
            self._network_consent_requested = True
        else:
            action()

    def _maybe_overwrite_then(
        self,
        path: str,
        action: Callable[[], None],
        *,
        save_in_place_ok: bool = False,
        default_suffix: str | None = None,
    ) -> None:
        """Run ``action`` now, or — if ``path`` already exists — ask for overwrite confirmation
        first. The portable file dialog does not confirm replacing an existing file on macOS (it
        wraps AppleScript's "choose file name"), so the guard lives here. ``save_in_place_ok`` skips
        the prompt when the target is the currently-open project itself (a plain save is no loss);
        it is NOT set for New/Import/Export, which would replace that file with different content.
        ``default_suffix`` mirrors the suffix the action appends to a suffixless path (``.xknx`` for
        New/Save-as/Import): without it the existence check would test the raw, suffixless path and
        silently overwrite the real target. Requests queue so a second dialog resolving
        mid-confirmation never drops the first action."""
        if default_suffix is not None and not Path(path).suffix:
            path = str(Path(path).with_suffix(default_suffix))
        target = Path(path)
        current = self._project_service.path
        same_as_open = (
            save_in_place_ok
            and current is not None
            and target.resolve() == current.resolve()
        )
        if target.exists() and not same_as_open:
            self._overwrite_queue.append((path, action))
        else:
            action()

    def _render_overwrite_modal(self) -> None:
        if not self._overwrite_queue:
            return
        if not self._overwrite_popup_open:
            # Don't steal the stage from another modal — the action of a prior confirmation may have
            # opened one (e.g. Export opens the MyKnx sign prompt). Opening our popup at the same
            # level would dismiss it, and its one-shot request flag is already consumed. Wait until
            # no popup is open, then raise the prompt for the next queued item.
            if imgui.is_popup_open("", imgui.PopupFlags_.any_popup):
                return
            imgui.open_popup(S.OVERWRITE_TITLE)
            self._overwrite_popup_open = True
        imgui.set_next_window_size(px_vec2(460.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.OVERWRITE_TITLE, None)[0]:
            return
        path, action = self._overwrite_queue[0]
        target = Path(path)
        imgui.text_wrapped(
            S.OVERWRITE_PROMPT.format(name=target.name, dir=str(target.parent))
        )
        imgui.spacing()
        replace = imgui.button(S.BTN_OVERWRITE)
        imgui.same_line()
        cancel = imgui.button(S.BTN_CANCEL)
        if replace or cancel:
            # Pop and close before running the action: the action may open its own modal (e.g. the
            # export sign prompt), and a queued request re-opens this popup on the next frame.
            self._overwrite_queue.pop(0)
            self._overwrite_popup_open = False
            imgui.close_current_popup()
            if replace:
                action()
        imgui.end_popup()

    def _render_network_consent_modal(self) -> None:
        if self._network_consent_requested:
            imgui.open_popup(S.NETWORK_CONSENT_TITLE)
            self._network_consent_requested = False
        imgui.set_next_window_size(px_vec2(460.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.NETWORK_CONSENT_TITLE, None)[0]:
            return
        imgui.text_wrapped(
            S.NETWORK_CONSENT_PROMPT.format(home=self._network_consent_home or "")
        )
        imgui.spacing()
        use_local = imgui.button(S.NETWORK_CONSENT_USE_LOCAL)
        imgui.same_line()
        cancel = imgui.button(S.BTN_CANCEL)
        if use_local:
            action = self._network_consent_action
            self._network_consent_action = None
            imgui.close_current_popup()
            if action is not None:
                action()
        elif cancel:
            self._network_consent_action = None
            self._clear_import_prompt()  # harmless when opening (import fields are None)
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_url_prompt_modal(self) -> None:
        if self._url_prompt_requested:
            imgui.open_popup(S.URL_PROMPT_TITLE)
            self._url_prompt_requested = False
        imgui.set_next_window_size(px_vec2(560.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.URL_PROMPT_TITLE, None)[0]:
            return
        imgui.text_wrapped(S.URL_PROMPT_HINT)
        imgui.spacing()
        imgui.set_next_item_width(-1)
        submitted, self._url_input = imgui.input_text(
            "##load-url", self._url_input, imgui.InputTextFlags_.enter_returns_true
        )
        imgui.spacing()
        btn_w = px_vec2(120, 0)
        url = self._url_input.strip()
        load = imgui.button(S.URL_PROMPT_LOAD, btn_w) or submitted
        imgui.same_line()
        cancel = imgui.button(S.URL_PROMPT_CANCEL, btn_w)
        if load and url:
            imgui.close_current_popup()
            self._start_url_import(url)
        elif cancel:
            imgui.close_current_popup()
        imgui.end_popup()

    def _start_url_import(self, url: str) -> None:
        """Download ``url`` on a worker thread and import it as a product source (behind the progress
        modal). Handles a .knxprod, an OpenKNX release ZIP, or a raw KNX product XML."""

        def worker() -> None:
            try:
                # OpenKNX/monolithic product XML carries no <MasterData>; we wrap it with the global
                # master (fetched+cached, not bundled). Unavailable offline on a first run.
                master = master_xml_bytes()
                if master is None:
                    raise RuntimeError(
                        "KNX master data unavailable (offline and not cached) — connect to the "
                        "internet once so it can be downloaded, then retry"
                    )
                data = _download_bytes(_resolve_download_url(url))
                # Hold the shared IO lock while ingesting so per-frame catalog reads bail (the
                # established pattern for background imports); then rebuild device resolution.
                with self._catalog_service.io_lock:
                    added = self._catalog_service.import_product_source(data, master)
                self._project_service.refresh_catalog_resolution(rebuild=True)
            except Exception as e:
                self._log.error(
                    "load from url failed", url=url, error=f"{type(e).__name__}: {e}"
                )
                self._flash_toast(S.URL_IMPORT_FAILED.format(error=str(e)))
                return
            self._log.info("loaded from url", url=url, added=len(added))
            self._flash_toast(
                S.URL_IMPORT_OK.format(count=len(added))
                if added
                else S.URL_IMPORT_EMPTY
            )

        self._run_bg(S.URL_DOWNLOADING, worker)

    def _clear_import_prompt(self) -> None:
        self._import_knxproj_source = None
        self._import_knxproj_dest = None
        self._import_password = ""
        self._import_password_error = None

    def _do_new_project(self, path: str) -> None:
        self._project_service.new(Path(path))
        self._add_recent(path)
        self._log.info("project created", path=path)

    def _do_save_as(self, path: str) -> None:
        saved = self._project_service.save_as(Path(path))
        if saved is not None:
            self._add_recent(str(saved))
            self._flash_toast(S.SAVE_AS_OK.format(name=saved.name))

    def _new_untitled_project(self) -> None:
        """Create an unnamed project in a temp dir so closing the Welcome screen never leaves the app
        project-less. It auto-persists there; "Save as..." moves it to a real, user-chosen location."""
        path = Path(tempfile.mkdtemp(prefix="xknx-untitled-")) / "untitled.xknx"
        self._project_service.new(
            path
        )  # not added to Recent — it is a throwaway until saved
        self._log.info("untitled project created", path=str(path))

    def _do_open_project(self, path: str) -> None:
        suffix = Path(path).suffix.lower()
        self._log.info(
            "routing open project",
            path=path,
            is_knxproj=suffix == ".knxproj",
            is_xml=suffix == ".xml",
            is_xknx=suffix == ".xknx",
        )
        # A .knxproj is a project archive, not an .xknx SQLite document. If one is picked here (a common
        # mix-up), route it through the importer instead of trying to open it as a database.
        if suffix == ".knxproj":
            self._log.info(
                "open project route selected", path=path, route="knxproj import"
            )
            self._prompt_import_dest(path)
            return

        # An ETS group-address export is a plain .xml holding only the GA tree. Detect it and route
        # it through its own importer (builds a new project whose GA tree mirrors the export).
        if suffix == ".xml":
            is_ga_export = self._project_service.is_ga_export(Path(path))
            self._log.debug(
                "xml project classification", path=path, ga_export=is_ga_export
            )
            if is_ga_export:
                self._log.info(
                    "open project route selected", path=path, route="ga export import"
                )
                self._prompt_ga_export_dest(path)
                return

        self._log.info(
            "open project route selected",
            path=path,
            route="xknx open" if suffix == ".xknx" else "project open",
        )

        # A recent entry (or a startup default) may point at a file that was moved or deleted. Report
        # it clearly (a red toast via the error log) and drop it from the recent list, instead of
        # failing later on the opaque SQLite "unable to open database file" error. can_open() also
        # accepts a network home whose file is missing but has a local mirror (crash recovery).
        if not self._project_service.can_open(Path(path)):
            self._log.error("project file no longer exists", path=path)
            self._remove_recent(path)
            return

        def report(done: int, total: int, label: str = "") -> None:
            text = S.PROGRESS_DEVICES.format(done=done, total=total)
            self.set_progress(
                done / total if total else None,
                f"{text} — {label}" if label else text,
            )

        def worker() -> None:
            self._project_service.build_progress = report
            try:
                self._project_service.open(Path(path))
                self._add_recent(path)
                if self._project_service.mirroring_active:
                    self._mirror_notice = str(self._project_service.mirror_home)
            except ProjectStorageError as e:
                # The file lives somewhere SQLite can't operate (network share, read-only dir).
                self._log.error("Cannot open the project here", path=path, error=str(e))
            except (ValueError, SQLAlchemyError) as e:
                # A missing/stale/corrupt file must not take down the app (e.g. the demo project
                # opened at startup, or a bad file picked via "Open Project").
                self._log.error("could not open project", path=path, error=str(e))
            finally:
                self._project_service.build_progress = None

        # Open on a worker thread behind the spinner: building a large project's device view is slow.
        # A network location asks for consent first (work on a local copy), then runs the worker.
        self._maybe_consent_then(
            path, lambda: self._run_bg(S.PROGRESS_OPEN_PROJECT, worker)
        )

    def _prompt_import_dest(self, source: str) -> None:
        """Remember the .knxproj source and ask where to save the imported .xknx project."""
        if not self._can_launch_file_dialog():
            return
        self._import_knxproj_source = source
        # Pass only the default file name, not a full path: on macOS a path with "/" in the save
        # dialog's name field gets mangled into ":"-separated segments.
        self._log.debug("opening knxproj import destination dialog", source=source)
        self._import_knxproj_save_dialog = pfd.save_file(
            S.FILE_DIALOG_PROJECT_SAVE_TITLE,
            Path(source).with_suffix(".xknx").name,
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
        )
        self._log.debug("knxproj import destination dialog opened", source=source)

    def _prompt_ga_export_dest(self, source: str) -> None:
        """Remember the GA-export source and ask where to save the imported .xknx project."""
        if not self._can_launch_file_dialog():
            return
        self._import_ga_export_source = source
        self._import_ga_export_save_dialog = pfd.save_file(
            S.FILE_DIALOG_PROJECT_SAVE_TITLE,
            Path(source).with_suffix(".xknx").name,
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
        )

    def _do_import_ga_export(self, source: str, dest: str) -> None:
        # A network destination asks for consent (work on a local copy) before the import runs.
        def worker() -> None:
            self._log.info("importing ga export", source=source, dest=dest)
            try:
                self._project_service.import_ga_export(Path(source), Path(dest))
                # Import opens the freshly built .xknx; record it in Open Recent like a normal open.
                self._add_recent(dest)
                if self._project_service.mirroring_active:
                    self._mirror_notice = str(self._project_service.mirror_home)
            except ProjectStorageError as e:
                self._log.error(
                    "Cannot save the project here", source=source, error=str(e)
                )
            except (OSError, ValueError, SQLAlchemyError) as e:
                self._log.error("ga export import failed", source=source, error=str(e))

        self._maybe_consent_then(
            dest, lambda: self._run_bg(S.PROGRESS_IMPORT_GA_EXPORT, worker)
        )

    def _undo(self) -> None:
        self._project_service.undo()

    def _redo(self) -> None:
        self._project_service.redo()

    def _can_undo(self) -> bool:
        return self._project_service.is_open and self._project_service.can_undo()

    def _can_redo(self) -> bool:
        return self._project_service.is_open and self._project_service.can_redo()

    def _poll_dialogs(self) -> None:
        self._poll_import()
        self._poll_bg()

        if self._open_file_dialog is not None and self._open_file_dialog.ready():
            result = self._open_file_dialog.result()
            self._open_file_dialog = None
            if result:
                self._load_knxprod(result[0])

        if self._save_project_dialog is not None and self._save_project_dialog.ready():
            result = self._save_project_dialog.result()
            self._save_project_dialog = None
            if result:
                self._maybe_overwrite_then(
                    result,
                    lambda r=result: self._do_new_project(r),
                    default_suffix=".xknx",
                )

        if self._save_as_dialog is not None and self._save_as_dialog.ready():
            result = self._save_as_dialog.result()
            self._save_as_dialog = None
            if result:
                self._maybe_overwrite_then(
                    result,
                    lambda r=result: self._do_save_as(r),
                    save_in_place_ok=True,
                    default_suffix=".xknx",
                )

        if self._open_project_dialog is not None and self._open_project_dialog.ready():
            self._log.debug("project file dialog ready")
            result = self._open_project_dialog.result()
            self._log.info(
                "project file dialog result", result=result[0] if result else ""
            )
            self._open_project_dialog = None
            if result:
                self._do_open_project(result[0])
            elif sys.platform == "linux":
                # Empty result on Linux usually means the helper could not display a window (no
                # display server reachable), not a user cancel. Record the environment so the cause
                # is visible in the log.
                self._log.warning(
                    "file dialog returned no selection", **_linux_display_env()
                )

        if (
            self._export_knxproj_dialog is not None
            and self._export_knxproj_dialog.ready()
        ):
            result = self._export_knxproj_dialog.result()
            self._export_knxproj_dialog = None
            if result:
                # Ask about MyKnx certificate signing before running the export.
                self._maybe_overwrite_then(
                    result, lambda r=result: self._begin_export_sign_prompt(r)
                )

        if (
            self._import_knxproj_save_dialog is not None
            and self._import_knxproj_save_dialog.ready()
        ):
            self._log.debug("knxproj import destination dialog ready")
            result = self._import_knxproj_save_dialog.result()
            self._log.info(
                "knxproj import destination dialog result", result=result or ""
            )
            self._import_knxproj_save_dialog = None
            source = self._import_knxproj_source
            self._import_knxproj_source = None
            if result and source is not None:
                self._maybe_overwrite_then(
                    result,
                    lambda r=result, s=source: self._do_import_knxproj(s, r),
                    default_suffix=".xknx",
                )

        if (
            self._import_ga_export_save_dialog is not None
            and self._import_ga_export_save_dialog.ready()
        ):
            result = self._import_ga_export_save_dialog.result()
            self._import_ga_export_save_dialog = None
            source = self._import_ga_export_source
            self._import_ga_export_source = None
            if result and source is not None:
                self._maybe_overwrite_then(
                    result,
                    lambda r=result, s=source: self._do_import_ga_export(s, r),
                    default_suffix=".xknx",
                )

    def _handle_shortcuts(self) -> None:
        io = imgui.get_io()
        ctrl = io.key_ctrl or io.key_super
        # Command palette opens even from a focused text field (Ctrl+P is not a printable char).
        if ctrl and imgui.is_key_pressed(imgui.Key.p):
            self._palette_open = True
            self._palette_focus = True
            self._palette_query = ""
            self._palette_index = 0
        if imgui.get_io().want_text_input:
            return  # don't steal typing in text fields
        if ctrl and imgui.is_key_pressed(imgui.Key.z):
            self._redo() if io.key_shift else self._undo()
        elif ctrl and imgui.is_key_pressed(imgui.Key.y):
            self._redo()
        elif ctrl and imgui.is_key_pressed(imgui.Key.n):
            self._new_project()
        elif ctrl and imgui.is_key_pressed(imgui.Key.o):
            self._open_project()
        elif (
            ctrl and imgui.is_key_pressed(imgui.Key.s) and self._project_service.is_open
        ):
            self._export_knxproj()

    def _load_knxprod(self, path: str) -> None:
        # A .knxproj is a full project archive, not a product catalog. The file filter is only
        # advisory on macOS, so a .knxproj can be picked in the "Load .knxprod" dialog; route it
        # to the project importer (as "Open Project" already does) so its devices get built.
        # Importing it as a catalog would only extract the embedded product XMLs and then show an
        # empty device tree, which looks like the project loaded but has "no devices".
        if path.lower().endswith(".knxproj"):
            self._prompt_import_dest(path)
            return

        def worker() -> None:
            # Hold the catalog lock so per-frame catalog reads bail while it writes (see io_guarded).
            with self._catalog_service.io_lock:
                self._log.info("loading knxprod", path=path)
                try:
                    added = self._catalog_service.import_knxprod(Path(path))
                    name = Path(path).name
                    if added:
                        self._log.info(
                            "added applications to catalog", count=len(added)
                        )
                        self._knxprod_result = S.KNXPROD_IMPORT_DONE.format(
                            count=len(added), name=name
                        )
                    else:
                        # Re-import of an already-known product: the message box below makes the
                        # no-op visible, so this stays an info log.
                        self._log.info(
                            "knxprod already imported — no new products", path=path
                        )
                        self._knxprod_result = S.KNXPROD_IMPORT_NOTHING_NEW.format(
                            name=name
                        )
                except ArchiveError as e:
                    self._log.error("archive error", path=path, error=str(e))
                except (OSError, ValueError) as e:
                    self._log.error(
                        "import error", path=path, error=f"{type(e).__name__}: {e}"
                    )
                except Exception as e:
                    # A product-parser bug on an odd .knxprod (e.g. AssertionError/KeyError, not an
                    # OSError/ValueError) must be reported in the log, not crash the editor.
                    self._log.error(
                        "knxprod load failed (unsupported product data?)",
                        path=path,
                        error=f"{type(e).__name__}: {e}",
                    )

        self._run_bg(S.PROGRESS_LOAD_KNXPROD, worker)

    def gui_status_bar(self) -> None:
        self._connection_plugin.render_status_indicator()

        # Bus-operation indicator: a programming/testing operation is in progress.
        busy = self._connection_service.busy_operation
        if busy is not None:
            kind, address = busy
            if kind == "program":
                text = S.STATUS_PROGRAMMING.format(address=address)
                prog = self._connection_service.busy_progress
                if prog is not None:
                    text += f" ({prog[0]}/{prog[1]})"
            else:
                text = S.STATUS_TESTING.format(address=address)
            imgui.same_line()
            imgui.text_disabled(" | ")
            imgui.same_line()
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.95, 0.75, 0.2, 1.0))
            imgui.text(text)
            imgui.pop_style_color()
        elif self._connection_service.not_connected_notice():
            # A connection-requiring feature (program/test/send) was just refused:
            # flash the reason so the user sees *why* nothing happened.
            imgui.same_line()
            imgui.text_disabled(" | ")
            imgui.same_line()
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.9, 0.35, 0.35, 1.0))
            imgui.text(S.STATUS_NO_CONNECTION)
            imgui.pop_style_color()
        else:
            # Briefly show the last programming outcome after the busy indicator clears.
            notice = self._connection_service.program_notice()
            if notice is not None:
                ok = notice
                color = (
                    imgui.ImVec4(0.4, 0.85, 0.45, 1.0)
                    if ok
                    else imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
                )
                imgui.same_line()
                imgui.text_disabled(" | ")
                imgui.same_line()
                imgui.push_style_color(imgui.Col_.text, color)
                imgui.text(S.STATUS_PROGRAM_DONE if ok else S.STATUS_PROGRAM_FAILED)
                imgui.pop_style_color()

        # Current project summary.
        imgui.same_line()
        imgui.text_disabled(" | ")
        imgui.same_line()
        if self._project_service.is_open:
            meta = self._project_service.get_project_metadata()
            name = meta.name if meta and meta.name else None
            if not name and self._project_service.path is not None:
                name = self._project_service.path.name
            imgui.text_disabled(
                S.STATUS_PROJECT.format(
                    name=name or "?",
                    devices=len(self._project_service.devices),
                    gas=len(self._project_service.group_addresses),
                )
            )
        else:
            imgui.text_disabled(S.STATUS_NO_PROJECT)

        if self._mcp_plugin.is_running:
            imgui.same_line()
            imgui.text_disabled(" | ")
            imgui.same_line()
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.4, 0.85, 0.45, 1.0))
            imgui.text(S.STATUS_MCP_RUNNING)
            imgui.pop_style_color()

        # KNX master data (mask-version procedures) version and date.
        if self._master_info is not None:
            imgui.same_line()
            imgui.text_disabled(" | ")
            imgui.same_line()
            imgui.text_disabled(
                S.STATUS_MASTER_DATA.format(
                    version=self._master_info.version,
                    date=self._master_info.date,
                )
            )

    def gui_menu(self) -> None:
        if imgui.begin_menu(S.MENU_FILE):
            if imgui.menu_item(S.MENU_NEW_PROJECT, S.SHORTCUT_NEW, False)[0]:
                self._new_project()
            if imgui.menu_item(S.MENU_OPEN_PROJECT, S.SHORTCUT_OPEN, False)[0]:
                self._open_project()
            recent = self._recent_files()
            if imgui.begin_menu(S.MENU_OPEN_RECENT, bool(recent)):
                for path in recent:
                    # Append the full path as a hidden id part: two recents can share a filename
                    # (e.g. the same project on a share and locally), and a bare label would give
                    # them the same imgui ID ("conflicting ID" warning, see _render_welcome).
                    if imgui.menu_item(f"{Path(path).name}##{path}", "", False)[0]:
                        self._do_open_project(path)
                    if imgui.is_item_hovered():
                        imgui.set_tooltip(path)
                imgui.end_menu()
            if imgui.menu_item(
                S.MENU_SAVE_AS, "", False, self._project_service.is_open
            )[0]:
                self._save_as()
            if imgui.menu_item(
                S.MENU_EXPORT_KNXPROJ,
                S.SHORTCUT_EXPORT,
                False,
                self._project_service.is_open,
            )[0]:
                self._export_knxproj()
            imgui.separator()
            if (
                imgui.menu_item(S.MENU_LOAD_KNXPROD, "", False)[0]
                and self._can_launch_file_dialog()
            ):
                self._open_file_dialog = pfd.open_file(
                    S.FILE_DIALOG_KNXPROD_TITLE,
                    "",
                    [
                        S.FILE_DIALOG_KNXPROD_FILTER,
                        "*.knxprod",
                        S.FILE_DIALOG_ALL_FILES,
                        "*",
                    ],
                )
            if imgui.menu_item(S.MENU_LOAD_FROM_URL, "", False)[0]:
                self._url_input = ""
                self._url_prompt_requested = True
            imgui.separator()
            if imgui.menu_item(S.MENU_EXIT, "", False)[0]:
                hello_imgui.get_runner_params().app_shall_exit = True
            imgui.end_menu()

        if imgui.begin_menu(S.MENU_EDIT):
            if imgui.menu_item(S.MENU_UNDO, S.SHORTCUT_UNDO, False, self._can_undo())[
                0
            ]:
                self._undo()
            if imgui.menu_item(S.MENU_REDO, S.SHORTCUT_REDO, False, self._can_redo())[
                0
            ]:
                self._redo()
            imgui.end_menu()

        hello_imgui.show_view_menu(hello_imgui.get_runner_params())

        self._connection_plugin.render_menu()
        self._render_mcp_menu()
        self._keyring_plugin.render_menu()
        self._signing_plugin.render_menu()
        self._recover_plugin.render_menu()

        self._render_language_menu()

        if imgui.begin_menu(S.MENU_HELP):
            if imgui.menu_item(S.MENU_ABOUT, "", False)[0]:
                self._about_requested = True
            if imgui.menu_item(S.MENU_CHECK_UPDATES, "", False)[0]:
                self._start_update_check(manual=True)
            changed, enabled = imgui.menu_item(
                S.MENU_UPDATE_ON_STARTUP, "", self._update_check_enabled()
            )
            if changed:
                self._set_update_check_enabled(enabled)
            imgui.separator()
            if imgui.menu_item(S.MENU_OPEN_CONFIG_DIR, "", False)[0]:
                self._open_folder(config_dir())
            if imgui.menu_item(S.MENU_OPEN_CACHE_DIR, "", False)[0]:
                self._open_folder(Path(platformdirs.user_cache_dir("xknx-editor")))
            imgui.end_menu()

        if imgui.menu_item(S.MENU_FEEDBACK, "", False)[0]:
            self._open_feedback()

        self._ui_executor.drain()
        self._poll_dialogs()
        self._handle_shortcuts()

    def _open_feedback(self) -> None:
        """Open the project's GitHub issues page in the default browser."""
        url = "https://github.com/knx-ai/xknx-editor/issues"
        try:
            webbrowser.open(url)
        except Exception as e:
            self._log.error("could not open feedback page", url=url, error=str(e))

    def _open_folder(self, path: Path) -> None:
        """Reveal a directory in the OS file manager (created if missing)."""
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform == "win32":
                getattr(os, "startfile")(str(path))  # noqa: B009 - Windows-only API
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self._log.error("could not open folder", path=str(path), error=str(e))

    def _render_knxprod_result_modal(self) -> None:
        # A finished .knxprod catalog import shows a modal message box (success or "nothing new") so
        # the outcome is explicit and click-to-dismiss. The worker hands the text over via
        # _knxprod_result; latch it into _knxprod_msg, which stays set until the user clicks OK.
        if self._knxprod_result is not None:
            self._knxprod_msg = self._knxprod_result
            self._knxprod_result = None
        if self._knxprod_msg is None:
            return
        if not imgui.is_popup_open(S.KNXPROD_IMPORT_TITLE):
            # Don't steal the stage from another modal (e.g. an auto-update prompt at the same popup
            # depth) — opening ours there would dismiss it. Wait until no popup is open, then raise
            # ours. If ours was itself displaced, this re-opens it once the other closes, so the
            # result is never silently lost.
            if imgui.is_popup_open("", imgui.PopupFlags_.any_popup):
                return
            imgui.open_popup(S.KNXPROD_IMPORT_TITLE)
        imgui.set_next_window_size(px_vec2(420.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.KNXPROD_IMPORT_TITLE, None)[0]:
            return
        imgui.text_wrapped(self._knxprod_msg)
        imgui.spacing()
        if imgui.button(S.BTN_OK, px_vec2(120, 0)):
            self._knxprod_msg = None
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_about_modal(self) -> None:
        if self._about_requested:
            imgui.open_popup(S.ABOUT_TITLE)
            self._about_requested = False
        imgui.set_next_window_size(px_vec2(420.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.ABOUT_TITLE, None)[0]:
            return
        imgui.text(S.APP_TITLE)
        imgui.text_disabled(S.ABOUT_VERSION.format(version=__version__))
        imgui.spacing()
        imgui.text_wrapped(S.ABOUT_TEXT)
        imgui.spacing()
        imgui.text_disabled(S.ABOUT_LICENSE)
        imgui.text(S.ABOUT_WEBSITE)
        imgui.same_line()
        imgui.text_link_open_url(_REPO_URL, _REPO_URL)
        imgui.spacing()
        imgui.separator()
        imgui.spacing()
        if imgui.button(S.MENU_CHECK_UPDATES, px_vec2(170, 0)):
            self._start_update_check(manual=True)
        imgui.same_line()
        if imgui.button(S.BTN_OK, px_vec2(120, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_update_modal(self) -> None:
        if self._update_prompt_requested:
            imgui.open_popup(S.UPDATE_TITLE)
            self._update_prompt_requested = False
        imgui.set_next_window_size(px_vec2(460.0, 0.0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(S.UPDATE_TITLE, None)[0]:
            return
        info = self._update_info
        if info is None:
            imgui.end_popup()
            return
        imgui.text_wrapped(
            S.UPDATE_AVAILABLE.format(latest=info.version, current=__version__)
        )
        if info.notes:
            imgui.spacing()
            imgui.separator()
            imgui.text_disabled(S.UPDATE_NOTES_HEADER)
            if imgui.begin_child("##update-notes", px_vec2(0.0, 220.0), True):
                imgui.text_wrapped(info.notes)
            imgui.end_child()
        imgui.spacing()
        if imgui.button(S.UPDATE_DOWNLOAD, px_vec2(140, 0)):
            try:
                webbrowser.open(info.url)
            except Exception as e:
                self._log.error(
                    "could not open release page", url=info.url, error=str(e)
                )
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.UPDATE_SKIP, px_vec2(140, 0)):
            self._skip_update_version(info.version)
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.UPDATE_LATER, px_vec2(100, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

    def _import_note_text(self, note: ImportLoss) -> str:
        """Localized, human-readable line for one import-loss note (code -> message)."""
        template = {
            import_notes.MULTIPLE_INSTALLATIONS: S.IMPORT_NOTE_MULTIPLE_INSTALLATIONS,
            import_notes.UNASSIGNED_DEVICES: S.IMPORT_NOTE_UNASSIGNED_DEVICES,
            import_notes.COM_OBJECT_TEXT_OVERRIDES: S.IMPORT_NOTE_COM_OBJECT_TEXT_OVERRIDES,
            import_notes.IP_CONFIG: S.IMPORT_NOTE_IP_CONFIG,
            import_notes.MULTI_SEGMENT: S.IMPORT_NOTE_MULTI_SEGMENT,
            import_notes.DROPPED_DUPLICATE_LINES: S.IMPORT_NOTE_DROPPED_DUPLICATE_LINES,
        }.get(note.code)
        if template is None:
            # Unknown code (forward-compat): fall back to the raw code + count so nothing is hidden.
            return f"{note.code} ({note.count})"
        text = template.format(count=note.count)
        if note.detail:
            return f"{text} {S.IMPORT_NOTE_EXAMPLES.format(examples=note.detail)}"
        return text

    def _render_import_notes_modal(self) -> None:
        if self._import_notes_requested:
            imgui.open_popup(S.IMPORT_NOTES_TITLE)
            self._import_notes_requested = False
        imgui.set_next_window_size_constraints(
            px_vec2(520.0, 0.0), imgui.ImVec2(1.0e9, 1.0e9)
        )
        if not imgui.begin_popup_modal(
            S.IMPORT_NOTES_TITLE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        if self._import_notes_intro:
            imgui.text_wrapped(self._import_notes_intro)
            imgui.spacing()
        for note in self._import_notes:
            imgui.bullet()
            imgui.text_wrapped(self._import_note_text(note))
        imgui.spacing()
        imgui.separator()
        if imgui.button(S.IMPORT_NOTES_CLOSE, px_vec2(120, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_export_summary_modal(self) -> None:
        """A success window shown after a completed .knxproj export, listing the archive's details:
        file size, device/building/group-address counts, bundled manufacturer folders, and whether
        it carries a folder signature and a MyKnx certificate."""
        if self._export_summary_requested:
            imgui.open_popup(S.EXPORT_SUMMARY_TITLE)
            self._export_summary_requested = False
        imgui.set_next_window_size_constraints(
            px_vec2(420.0, 0.0), imgui.ImVec2(1.0e9, 1.0e9)
        )
        if not imgui.begin_popup_modal(
            S.EXPORT_SUMMARY_TITLE, None, imgui.WindowFlags_.always_auto_resize
        )[0]:
            return
        summary = self._export_summary
        if summary is None:
            imgui.end_popup()
            return
        imgui.text_wrapped(
            S.EXPORT_SUMMARY_INTRO.format(name=Path(self._export_summary_path).name)
        )
        imgui.spacing()
        yes, no = S.EXPORT_SUMMARY_YES, S.EXPORT_SUMMARY_NO
        rows = [
            (S.EXPORT_SUMMARY_PROJECT, summary.project_name),
            (S.EXPORT_SUMMARY_FILE_SIZE, _human_size(summary.file_size)),
            (S.EXPORT_SUMMARY_FORMAT, self._export_summary_schema),
            (S.EXPORT_SUMMARY_MASTER_VERSION, str(summary.master_version)),
            (S.EXPORT_SUMMARY_EXPORTED_AT, self._export_summary_at),
            (S.EXPORT_SUMMARY_DEVICES, str(summary.device_count)),
            (
                S.EXPORT_SUMMARY_TOPOLOGY,
                f"{summary.area_count} / {summary.line_count}",
            ),
            (S.EXPORT_SUMMARY_BUILDINGS, str(summary.building_count)),
            (
                S.EXPORT_SUMMARY_FLOORS_ROOMS,
                f"{summary.floor_count} / {summary.room_count}",
            ),
            (S.EXPORT_SUMMARY_GROUP_ADDRESSES, str(summary.group_address_count)),
            (S.EXPORT_SUMMARY_GROUP_OBJECT_LINKS, str(summary.com_object_link_count)),
            (S.EXPORT_SUMMARY_FUNCTIONS, str(summary.function_count)),
            (S.EXPORT_SUMMARY_MANUFACTURERS, str(summary.manufacturer_count)),
            (S.EXPORT_SUMMARY_SIGNED, yes if summary.signed else no),
            (S.EXPORT_SUMMARY_CERTIFICATE, yes if summary.certificate else no),
        ]
        if imgui.begin_table("##export_summary", 2, imgui.TableFlags_.sizing_fixed_fit):
            for label, value in rows:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text_disabled(label)
                imgui.table_next_column()
                imgui.text(value)
            imgui.end_table()
        imgui.spacing()
        imgui.separator()
        if imgui.button(S.EXPORT_SUMMARY_OPEN_FOLDER, px_vec2(140, 0)):
            self._open_folder(Path(self._export_summary_path).parent)
        imgui.same_line()
        if imgui.button(S.EXPORT_SUMMARY_CLOSE, px_vec2(120, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

    def watch_db_frame(self) -> None:
        """Dev watchdog (XKNX_DEBUG_DB_FRAMES=1): log when the render thread keeps issuing DB queries
        across frames in which the project did not change — an uncached per-frame read in a panel."""
        msg = frame_db_profiler.end_frame(self._project_service.revision)
        if msg:
            self._log.warning(msg)

    def render_overlays(self) -> None:
        # Log the active OpenGL renderer once, from the frame path (context guaranteed current).
        if not self._gl_logged:
            self._gl_logged = True
            _log_gl_renderer(self._log)
        # Bring the Editor tab to the front when another view (Device Overview, Topology, Health)
        # selected a device. Consumed here (a per-frame global callback) rather than in the Editor
        # panel itself, because a background dock tab is not rendered and would never see the flag.
        # Use hello_imgui's docking API (focus_window_at_next_frame) — calling imgui.set_window_focus
        # by name from this post-render phase re-enters the docking layout and crashes.
        if self._project_service.take_focus_editor():
            # Dockable windows are labelled "Title###name"; focus by the exact current label so the
            # ###editor identity matches after the language-aware title change.
            hello_imgui.get_runner_params().docking_params.focus_dockable_window(
                f"{_DOCK_LABELS['editor']()}###editor"
            )
        if self._project_service.take_focus_group_addresses():
            hello_imgui.get_runner_params().docking_params.focus_dockable_window(
                f"{_DOCK_LABELS['group_addresses']()}###group_addresses"
            )
        self._project_plugin.render_overlays()
        self._render_progress_modal()
        self._render_import_password_modal()
        self._render_network_consent_modal()
        self._render_overwrite_modal()
        self._render_url_prompt_modal()
        self._render_myknx_sign_modal()
        self._render_about_modal()
        self._render_update_modal()
        self._render_import_notes_modal()
        self._render_export_summary_modal()
        self._render_knxprod_result_modal()
        self._keyring_plugin.render_window()
        self._signing_plugin.render_window()
        self._connection_plugin.render_window()
        self._render_welcome()
        # Programming queue: advance it every frame (robust wakeup even if the bus was freed by a
        # non-queue op) and bring the Operations panel to the front when something was queued.
        self._project_plugin.tick_program_queue()
        if self._project_plugin.take_operations_focus():
            hello_imgui.get_runner_params().docking_params.focus_dockable_window(
                f"{_DOCK_LABELS['operations']()}###operations"
            )
        self._render_command_palette()
        self._render_bus_operation_overlay()
        self._render_toasts()

    def _render_bus_operation_overlay(self) -> None:
        """Non-blocking progress overlay shown while a device is being tested. Determinate bar from the
        operation's (done, total); indeterminate animated bar until totals are known."""
        busy = self._connection_service.busy_operation
        if busy is None:
            return
        kind, address = busy
        if kind in ("script", "program"):
            return  # the Operations panel shows this op's progress instead
        label = S.STATUS_TESTING.format(address=address)
        vp = imgui.get_main_viewport()
        pos = imgui.ImVec2(
            vp.work_pos.x + vp.work_size.x * 0.5,
            vp.work_pos.y + vp.work_size.y - px(12.0),
        )
        imgui.set_next_window_pos(pos, imgui.Cond_.always, imgui.ImVec2(0.5, 1.0))
        imgui.set_next_window_bg_alpha(0.9)
        flags = (
            imgui.WindowFlags_.no_decoration
            | imgui.WindowFlags_.no_inputs
            | imgui.WindowFlags_.no_saved_settings
            | imgui.WindowFlags_.always_auto_resize
            | imgui.WindowFlags_.no_focus_on_appearing
            | imgui.WindowFlags_.no_nav
            | imgui.WindowFlags_.no_docking
        )
        if imgui.begin("##bus_operation", None, flags)[0]:
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.95, 0.75, 0.2, 1.0))
            imgui.text(label)
            imgui.pop_style_color()
            bar = hello_imgui.em_to_vec2(18.0, 0.0)
            prog = self._connection_service.busy_progress
            if prog is not None and prog[1] > 0:
                imgui.progress_bar(prog[0] / prog[1], bar, f"{prog[0]}/{prog[1]}")
            else:
                imgui.progress_bar(-1.0 * float(imgui.get_time()), bar, "")
        imgui.end()

    def _render_dongle_modal(self) -> None:
        """Step-by-step 'license with a dongle' -- a child modal of the export dialog.

        Rendered inside the export modal's scope so it stacks on top; closing it returns to the
        export dialog. Each step is on its own wrapped line with spacing for readability."""
        center = imgui.get_main_viewport().get_center()
        imgui.set_next_window_pos(center, imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
        imgui.set_next_window_size(
            hello_imgui.em_to_vec2(66.0, 0.0), imgui.Cond_.appearing
        )
        if not imgui.begin_popup_modal(S.MYKNX_DONGLE_TITLE, None)[0]:
            return
        # Read-only multiline input so the text stays selectable/copyable inline (mouse-drag +
        # Ctrl/Cmd+C). It does NOT word-wrap, so hard-wrap to the box width first (long paths and
        # P-XXXX_M-dummy stay intact) — otherwise long lines are clipped on the right.
        height = hello_imgui.em_to_vec2(0.0, 22.0).y
        avail = imgui.get_content_region_avail().x
        # Average glyph width (not the widest "m") so lines fill the box instead of wrapping early.
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ "
        char_w = (imgui.calc_text_size(alphabet).x / len(alphabet)) or 7.0
        cols = int(
            max(20.0, (avail - px(24.0)) / char_w)
        )  # leave room for the scrollbar/padding
        imgui.input_text_multiline(
            "##dongle_steps",
            _wrap_text(S.MYKNX_DONGLE_STEPS, cols),
            imgui.ImVec2(-1.0, height),
            imgui.InputTextFlags_.read_only,
        )
        imgui.spacing()
        imgui.separator()
        imgui.spacing()
        half = imgui.get_content_region_avail().x / 2 - imgui.get_style().item_spacing.x
        if imgui.button(S.BTN_COPY, imgui.ImVec2(half, 0)):
            imgui.set_clipboard_text(S.MYKNX_DONGLE_STEPS)
        imgui.same_line()
        if imgui.button(S.MYKNX_DONGLE_BACK, imgui.ImVec2(-1, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

    def _render_signing_key_modal(self) -> None:
        """Signing-key manager as a child modal of the export dialog (view/edit/save/extract).

        Rendered inside the export modal's scope so it stacks on top; closing it returns to the
        export dialog without cancelling the export."""
        center = imgui.get_main_viewport().get_center()
        imgui.set_next_window_pos(center, imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
        imgui.set_next_window_size(
            hello_imgui.em_to_vec2(40.0, 0.0), imgui.Cond_.appearing
        )
        if not imgui.begin_popup_modal(S.SIGNING_KEY_TITLE, None)[0]:
            return
        self._signing_plugin.render_contents()
        imgui.spacing()
        if imgui.button(S.SIGNING_KEY_CLOSE, imgui.ImVec2(-1, 0)):
            imgui.close_current_popup()
        imgui.end_popup()

    def _palette_items(self) -> list[tuple[str, Callable[[], None]]]:
        """(label, action) pairs the command palette can jump to: global actions + devices."""
        items: list[tuple[str, Callable[[], None]]] = [
            (f"{S.MENU_NEW_PROJECT}", self._new_project),
            (f"{S.MENU_OPEN_PROJECT}", self._open_project),
        ]
        if self._project_service.is_open:
            items.append((S.FILE_DIALOG_KNXPROJ_SAVE_TITLE, self._export_knxproj))
            for d in self._project_service.devices:
                ia = d.individual_address or "-"

                def _select(dev: object = d) -> None:
                    self._project_service.selected_device = dev  # type: ignore[assignment]

                items.append((f"{ia}  {d.name}", _select))
        return items

    def _render_command_palette(self) -> None:
        if self._palette_open:
            imgui.open_popup("##cmd_palette")
            self._palette_open = False
        center = imgui.get_main_viewport().get_center()
        imgui.set_next_window_pos(
            imgui.ImVec2(center.x, center.y - px(180.0)),
            imgui.Cond_.appearing,
            imgui.ImVec2(0.5, 0.0),
        )
        imgui.set_next_window_size(px_vec2(520.0, 0.0), imgui.Cond_.appearing)
        if not imgui.begin_popup("##cmd_palette"):
            return
        if self._palette_focus:
            imgui.set_keyboard_focus_here()
            self._palette_focus = False
        imgui.set_next_item_width(-1)
        _, self._palette_query = imgui.input_text(
            "##cmd_query", self._palette_query, imgui.InputTextFlags_.auto_select_all
        )
        needle = self._palette_query.lower().strip()
        matches = [
            it for it in self._palette_items() if not needle or needle in it[0].lower()
        ][:40]
        if matches:
            self._palette_index = max(0, min(self._palette_index, len(matches) - 1))
            if imgui.is_key_pressed(imgui.Key.down_arrow):
                self._palette_index = (self._palette_index + 1) % len(matches)
            elif imgui.is_key_pressed(imgui.Key.up_arrow):
                self._palette_index = (self._palette_index - 1) % len(matches)
            chosen = -1
            if imgui.is_key_pressed(imgui.Key.enter) or imgui.is_key_pressed(
                imgui.Key.keypad_enter
            ):
                chosen = self._palette_index
            if imgui.begin_child("##cmd_list", px_vec2(0.0, 320.0)):
                for i, (label, _action) in enumerate(matches):
                    if imgui.selectable(f"{label}##cmd{i}", i == self._palette_index)[
                        0
                    ]:
                        chosen = i
                imgui.end_child()
            if chosen >= 0:
                matches[chosen][1]()
                imgui.close_current_popup()
        else:
            imgui.text_disabled(S.PALETTE_NO_MATCH)
        if imgui.is_key_pressed(imgui.Key.escape):
            imgui.close_current_popup()
        imgui.end_popup()

    def _flash_toast(self, text: str) -> None:
        """Show a short green feedback toast (e.g. 'Copied'). Called from panels via PluginAPI."""
        self._toasts.append((text, "success", time.time() + 2.0))

    def _render_toasts(self) -> None:
        """Surface new warning/error log records as transient bottom-right toasts."""
        now = time.time()
        if self._mirror_notice is not None:
            self._toasts.append(
                (
                    S.MIRROR_NOTICE.format(home=self._mirror_notice),
                    "success",
                    now + 10.0,
                )
            )
            self._mirror_notice = None
        for rec in self._log_service.get_records():
            if rec.timestamp <= self._toast_seen_ts:
                continue
            self._toast_seen_ts = rec.timestamp
            if rec.level in ("warning", "error", "critical"):
                detail = rec.payload.get("error") or ""
                text = f"[{rec.plugin}] {rec.event}" + (f": {detail}" if detail else "")
                self._toasts.append((text, rec.level, now + 6.0))
        self._toasts = [t for t in self._toasts if t[2] > now]
        if not self._toasts:
            return
        vp = imgui.get_main_viewport()
        pos = imgui.ImVec2(
            vp.work_pos.x + vp.work_size.x - px(12.0),
            vp.work_pos.y + vp.work_size.y - px(12.0),
        )
        imgui.set_next_window_pos(pos, imgui.Cond_.always, imgui.ImVec2(1.0, 1.0))
        imgui.set_next_window_bg_alpha(0.9)
        flags = (
            imgui.WindowFlags_.no_decoration
            | imgui.WindowFlags_.no_inputs
            | imgui.WindowFlags_.no_saved_settings
            | imgui.WindowFlags_.always_auto_resize
            | imgui.WindowFlags_.no_focus_on_appearing
            | imgui.WindowFlags_.no_nav
        )
        if imgui.begin("##toasts", None, flags)[0]:
            for text, level, _exp in self._toasts[-5:]:
                if level in ("error", "critical"):
                    color = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
                elif level == "success":
                    color = imgui.ImVec4(0.45, 0.8, 0.5, 1.0)
                else:
                    color = imgui.ImVec4(0.95, 0.75, 0.2, 1.0)
                imgui.text_colored(color, text)
        imgui.end()

    def _render_language_menu(self) -> None:
        """UI language selector (next to Help). Overrides the auto-detected OS locale; persisted."""
        current = get_locale()
        if imgui.begin_menu(S.MENU_LANGUAGE):
            for code, label in _UI_LANGUAGES:
                if imgui.menu_item(label, "", code == current)[0] and code != current:
                    set_locale(code)
                    data = load_settings("app")
                    data["locale"] = code
                    save_settings("app", data)
                    # Re-translate the docked panel/tab titles in place (kept identity via ###name).
                    self._refresh_dock_titles()

                    # Device labels are parsed per language; re-resolve + rebuild the device views in
                    # the new language on a worker thread behind the progress bar, so the (slow)
                    # re-parse of every application doesn't freeze the UI on the next frame.
                    def _reresolve() -> None:
                        def report(done: int, total: int, sub: str = "") -> None:
                            text = S.PROGRESS_DEVICES.format(done=done, total=total)
                            self.set_progress(
                                done / total if total else None,
                                f"{text} — {sub}" if sub else text,
                            )

                        self._project_service.build_progress = report
                        try:
                            self._project_service.refresh_catalog_resolution(
                                rebuild=True
                            )
                        finally:
                            self._project_service.build_progress = None

                    self._run_bg(S.PROGRESS_LANGUAGE, _reresolve)
            imgui.end_menu()

    def _refresh_dock_titles(self) -> None:
        """Update docked panel/tab titles to the current language without recreating windows.

        The visible title is rebuilt from the translated string while the ``###name`` id stays fixed,
        so imgui keeps each window's identity and dock position across the language switch."""
        params = hello_imgui.get_runner_params()
        for w in params.docking_params.dockable_windows:
            name = _dock_window_name(w)
            getter = _DOCK_LABELS.get(name)
            if getter is not None:
                w.label = f"{getter()}###{name}"

    def _render_mcp_menu(self) -> None:
        """MCP menu: shows whether the embedded server runs, toggles it on/off, and its host/port."""
        if not imgui.begin_menu(S.PANEL_MCP):
            return
        running = self._mcp_plugin.is_running
        imgui.text_colored(
            imgui.ImVec4(0.4, 0.85, 0.45, 1.0)
            if running
            else imgui.ImVec4(0.6, 0.6, 0.6, 1.0),
            S.SETTINGS_MCP_RUNNING if running else S.SETTINGS_MCP_STOPPED,
        )
        imgui.separator()
        imgui.set_next_item_width(px(180.0))
        _, self._mcp_host = imgui.input_text(S.SETTINGS_MCP_HOST, self._mcp_host)
        imgui.set_next_item_width(px(180.0))
        _, self._mcp_port = imgui.input_text(S.SETTINGS_MCP_PORT, self._mcp_port)
        imgui.set_next_item_width(px(180.0))
        _, self._mcp_token = imgui.input_text(
            S.SETTINGS_MCP_TOKEN, self._mcp_token, imgui.InputTextFlags_.password
        )
        imgui.separator()
        if running:
            imgui.text_disabled(f"http://{self._mcp_host}:{self._mcp_port}/mcp")
            if imgui.menu_item(S.SETTINGS_MCP_STOP, "", False)[0]:
                self._mcp_plugin.stop()
                self._log.info("MCP server stopped")
        elif imgui.menu_item(S.SETTINGS_MCP_START, "", False)[0]:
            save_settings(
                "mcp",
                {
                    "host": self._mcp_host,
                    "port": self._mcp_port,
                    "token": self._mcp_token,
                },
            )
            try:
                self._mcp_plugin.start(
                    self._mcp_host, int(self._mcp_port or "8765"), self._mcp_token
                )
            except Exception as e:
                self._log.error(
                    "MCP server start failed", error=f"{type(e).__name__}: {e}"
                )
        imgui.end_menu()

    def _render_ki_panel(self) -> None:
        """'AI' tab: explains AI control via the MCP server, lets the user start/stop it, and shows
        example commands to register the server in Claude Code / Codex."""
        imgui.push_text_wrap_pos(0.0)
        imgui.text(S.KI_INFO)
        imgui.pop_text_wrap_pos()
        imgui.separator()

        running = self._mcp_plugin.is_running
        imgui.text_colored(
            imgui.ImVec4(0.4, 0.85, 0.45, 1.0)
            if running
            else imgui.ImVec4(0.6, 0.6, 0.6, 1.0),
            S.SETTINGS_MCP_RUNNING if running else S.SETTINGS_MCP_STOPPED,
        )
        imgui.set_next_item_width(px(240.0))
        _, self._mcp_host = imgui.input_text(S.SETTINGS_MCP_HOST, self._mcp_host)
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.KI_HOST_HINT)
        imgui.pop_text_wrap_pos()
        imgui.set_next_item_width(px(240.0))
        _, self._mcp_port = imgui.input_text(S.SETTINGS_MCP_PORT, self._mcp_port)
        imgui.set_next_item_width(px(240.0))
        _, self._mcp_token = imgui.input_text(
            S.SETTINGS_MCP_TOKEN, self._mcp_token, imgui.InputTextFlags_.password
        )
        url = f"http://{self._mcp_host}:{self._mcp_port}/mcp"
        if running:
            imgui.text_disabled(f"{S.KI_ENDPOINT}:")
            imgui.same_line()
            self._ki_copy_row(url, "endpoint")
            if imgui.button(S.SETTINGS_MCP_STOP):
                self._mcp_plugin.stop()
                self._log.info("MCP server stopped")
        elif imgui.button(S.SETTINGS_MCP_START):
            save_settings(
                "mcp",
                {
                    "host": self._mcp_host,
                    "port": self._mcp_port,
                    "token": self._mcp_token,
                },
            )
            try:
                self._mcp_plugin.start(
                    self._mcp_host, int(self._mcp_port or "8765"), self._mcp_token
                )
            except Exception as e:
                self._log.error(
                    "MCP server start failed", error=f"{type(e).__name__}: {e}"
                )

        imgui.separator()
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(S.KI_COMMANDS_HINT)
        imgui.pop_text_wrap_pos()
        # A bearer token, when set, is woven into the copy-ready command (inline header for Claude,
        # an env var for Codex, which only takes the token by env-var name).
        token = self._mcp_token.strip()
        claude_cmd = (
            S.KI_CLAUDE_CMD_AUTH.format(url=url, token=token)
            if token
            else S.KI_CLAUDE_CMD.format(url=url)
        )
        codex_cmd = (
            S.KI_CODEX_CMD_AUTH.format(url=url, token=token)
            if token
            else S.KI_CODEX_CMD.format(url=url)
        )
        self._ki_command("Claude Code", claude_cmd)
        self._ki_command("Codex", codex_cmd)

    def _ki_copy_row(self, text: str, ident: str) -> None:
        imgui.text_disabled(text)
        imgui.same_line()
        if imgui.small_button(f"{S.KI_COPY}##kicp_{ident}"):
            imgui.set_clipboard_text(text)
            self._flash_toast(S.KI_COPIED)

    def _ki_command(self, label: str, command: str) -> None:
        imgui.spacing()
        imgui.text_disabled(label)
        imgui.same_line()
        if imgui.small_button(f"{S.KI_COPY}##kicmd_{label}"):
            imgui.set_clipboard_text(command)
            self._flash_toast(S.KI_COPIED)
        imgui.push_text_wrap_pos(0.0)
        imgui.text(command)
        imgui.pop_text_wrap_pos()

    def _render_welcome(self) -> None:
        """A centered welcome card shown while no project is open: New / Open / Recent."""
        if self._project_service.is_open or self._progress_running:
            self._welcome_dismissed = False  # show again next time there's no project
            return
        if self._welcome_dismissed:
            return
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(
            viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5)
        )
        imgui.set_next_window_size(px_vec2(580.0, 0.0), imgui.Cond_.appearing)
        flags = imgui.WindowFlags_.no_collapse | imgui.WindowFlags_.no_saved_settings
        # p_open=True gives the window a close (X) button; when clicked it returns open=False.
        expanded, still_open = imgui.begin(S.WELCOME_TITLE, True, flags)
        if expanded:
            imgui.text_wrapped(S.WELCOME_HINT)
            imgui.spacing()
            # New / Open / Recover side by side, split evenly across the card width.
            spacing = imgui.get_style().item_spacing.x
            btn_w = (imgui.get_content_region_avail().x - spacing * 2) / 3.0
            if imgui.button(S.MENU_NEW_PROJECT, imgui.ImVec2(btn_w, 0)):
                self._new_project()
            imgui.same_line()
            if imgui.button(S.MENU_OPEN_PROJECT, imgui.ImVec2(btn_w, 0)):
                self._open_project()
            imgui.same_line()
            # Recover reconstructs a project by reading devices off the bus (no file needed).
            # Recover is a docked tab now, so dismiss the welcome card and focus that tab.
            if imgui.button(S.WELCOME_RECOVER, imgui.ImVec2(btn_w, 0)):
                self._recover_plugin.open_window()
                self._welcome_dismissed = True
            recent = self._recent_files()
            if recent:
                imgui.spacing()
                imgui.separator()
                imgui.text_disabled(S.WELCOME_RECENT)
                for path in recent:
                    # Append the full path as a hidden id part: two recents can share a filename
                    # (e.g. the same project on a share and locally), and a bare label would give
                    # them the same imgui ID ("conflicting ID" warning). Show the full path on hover
                    # so identically-named entries are distinguishable.
                    if imgui.selectable(f"{Path(path).name}##{path}", False)[0]:
                        self._do_open_project(path)
                    if imgui.is_item_hovered():
                        imgui.set_tooltip(path)
            imgui.spacing()
            if imgui.button(S.WELCOME_CLOSE, imgui.ImVec2(-1, 0)):
                still_open = False
        imgui.end()
        if not still_open:
            self._welcome_dismissed = True
            # Closing the Welcome (X or the Close button) without picking New/Open/Recover would
            # otherwise strand the user with no project (every action is then a no-op). Give them a
            # fresh untitled project instead; "Save as..." lets them name it later.
            if not self._project_service.is_open:
                self._new_untitled_project()

    def get_all_panels(self) -> list[PanelDefinition]:
        panels: list[PanelDefinition] = []
        for plugin in self._plugins:
            panels.extend(plugin.panels)
        # App-owned "AI" tab (MCP control + how to drive the editor from Claude/Codex).
        panels.append(
            PanelDefinition(
                name="ki",
                label=S.PANEL_KI,
                dock="MainDockSpace",
                render=self._render_ki_panel,
            )
        )
        return panels


# Bumped whenever the default docking layout changes, so a stored layout is replaced once.
_LAYOUT_VERSION = 4


def create_docking_splits() -> list[hello_imgui.DockingSplit]:
    split_right = hello_imgui.DockingSplit()
    split_right.initial_dock = "MainDockSpace"
    split_right.new_dock = "RightSpace"
    split_right.direction = imgui.Dir.right
    split_right.ratio = 0.19

    split_device = hello_imgui.DockingSplit()
    split_device.initial_dock = "RightSpace"
    split_device.new_dock = "DeviceSpace"
    split_device.direction = imgui.Dir.up
    split_device.ratio = 0.35

    split_bottom = hello_imgui.DockingSplit()
    split_bottom.initial_dock = "MainDockSpace"
    split_bottom.new_dock = "BottomSpace"
    split_bottom.direction = imgui.Dir.down
    split_bottom.ratio = 0.25

    split_left = hello_imgui.DockingSplit()
    split_left.initial_dock = "MainDockSpace"
    split_left.new_dock = "LeftSpace"
    split_left.direction = imgui.Dir.left
    # Wide enough that the stacked navigation tabs (Buildings, Devices, Group Addresses, Catalog)
    # all fit without the tab-bar overflow (">>") arrow.
    split_left.ratio = 0.3

    return [split_right, split_device, split_bottom, split_left]


def _apply_new_layout_once(docking_params: hello_imgui.DockingParams) -> None:
    """Replace a stored layout from an older version with the current default layout."""
    data = load_settings("app")
    if data.get("layout_version") == _LAYOUT_VERSION:
        return
    docking_params.layout_condition = (
        hello_imgui.DockingLayoutCondition.application_start
    )
    data["layout_version"] = _LAYOUT_VERSION
    save_settings("app", data)


# Stable dock id (the panel's ``name``) -> a getter for its translated title. Dockable windows are
# labelled ``"Title###name"`` so the imgui window identity (the part after ``###``) stays fixed while
# the visible title follows the UI language. This lets _refresh_dock_titles() re-translate tab titles
# live on a language change without recreating windows (see _render_language_menu).
def _dock_label_getters() -> "dict[str, Callable[[], str]]":
    from editor_gui.plugins.catalog.strings import S as _cat
    from editor_gui.plugins.cockpit.strings import S as _cockpit
    from editor_gui.plugins.health.strings import S as _health
    from editor_gui.plugins.logger.strings import S as _logger
    from editor_gui.plugins.monitor.strings import S as _monitor
    from editor_gui.plugins.network.strings import S as _network
    from editor_gui.plugins.project.strings import S as _proj
    from editor_gui.plugins.recover.strings import S as _recover
    from editor_gui.plugins.timeline.strings import S as _timeline
    from editor_gui.plugins.topology.strings import S as _topology

    return {
        "devices": lambda: _proj.PANEL_DEVICES,
        "buildings": lambda: _proj.PANEL_BUILDINGS,
        "group_addresses": lambda: _proj.PANEL_GROUP_ADDRESSES,
        "editor": lambda: _proj.PANEL_EDITOR,
        "device": lambda: _proj.PANEL_DEVICE,
        "manufacturer": lambda: _proj.PANEL_MANUFACTURER,
        "mass_linker": lambda: _proj.PANEL_MASS_LINKER,
        "tools": lambda: _proj.PANEL_TOOLS,
        "history": lambda: _proj.PANEL_HISTORY,
        "project_info": lambda: _proj.PANEL_PROJECT_INFO,
        "operations": lambda: _proj.PANEL_OPERATIONS,
        "catalog": lambda: _cat.PANEL_CATALOG,
        "topology": lambda: _topology.PANEL_TOPOLOGY,
        "monitor": lambda: _monitor.PANEL_MONITOR,
        "logger": lambda: _logger.PANEL_LOGGER,
        "health": lambda: _health.PANEL_HEALTH,
        "network": lambda: _network.PANEL_NETWORK,
        "cockpit": lambda: _cockpit.PANEL_COCKPIT,
        "timeline": lambda: _timeline.PANEL_TIMELINE,
        "recover": lambda: _recover.WINDOW_TITLE,
        "ki": lambda: S.PANEL_KI,
    }


_DOCK_LABELS: "dict[str, Callable[[], str]]" = _dock_label_getters()

# Initial tab order per dock space, by stable panel name (order only matters within each space).
_DOCK_TAB_ORDER = [
    "buildings",
    "devices",
    "group_addresses",
    "catalog",
    "editor",
    "cockpit",
    "device",
    "manufacturer",
    "operations",
    "mass_linker",
    "tools",
    "ki",
    "topology",
    "recover",
]


def _dock_window_name(window: hello_imgui.DockableWindow) -> str:
    """The stable id embedded after ``###`` in a dockable window's label."""
    return window.label.split("###")[-1]


def create_dockable_windows(app: KnxGuiApp) -> list[hello_imgui.DockableWindow]:
    windows: list[hello_imgui.DockableWindow] = []
    for panel in app.get_all_panels():
        window = hello_imgui.DockableWindow()
        getter = _DOCK_LABELS.get(panel.name)
        title = getter() if getter is not None else panel.label
        window.label = f"{title}###{panel.name}"
        window.dock_space_name = panel.dock
        window.gui_function = panel.render
        windows.append(window)
    rank = {name: i for i, name in enumerate(_DOCK_TAB_ORDER)}
    windows.sort(key=lambda w: rank.get(_dock_window_name(w), len(_DOCK_TAB_ORDER)))
    # Buildings and Device are the primary views: focus them on first frame.
    for w in windows:
        if _dock_window_name(w) in ("buildings", "device"):
            w.focus_window_at_next_frame = True
    return windows


def _apply_style() -> None:
    """Refine the imgui style on top of the theme: rounded corners + roomier spacing."""
    style = imgui.get_style()
    style.window_rounding = 6.0
    style.child_rounding = 4.0
    style.frame_rounding = 4.0
    style.popup_rounding = 4.0
    style.grab_rounding = 4.0
    style.tab_rounding = 4.0
    style.scrollbar_rounding = 4.0
    style.frame_border_size = 1.0
    style.window_padding = imgui.ImVec2(10.0, 10.0)
    style.frame_padding = imgui.ImVec2(8.0, 4.0)
    style.item_spacing = imgui.ImVec2(8.0, 6.0)
    style.item_inner_spacing = imgui.ImVec2(6.0, 4.0)
    style.scrollbar_size = 12.0
    style.cell_padding = imgui.ImVec2(6.0, 4.0)
    # The theme ships a translucent popup background, so combo/menu dropdowns show the content
    # behind them (distracting). Force the popup background fully opaque, keeping its RGB.
    popup = style.color_(imgui.Col_.popup_bg)
    style.set_color_(imgui.Col_.popup_bg, imgui.ImVec4(popup.x, popup.y, popup.z, 1.0))


# UI languages offered in the Language menu (locale code, native label). Catalogs live under each
# plugin's locales/<code>/LC_MESSAGES; "en" is the untranslated source.
_UI_LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"),
    ("de", "Deutsch"),
    ("nl", "Nederlands"),
]


def _wrap_text(text: str, cols: int) -> str:
    """Hard-wrap each line to ``cols`` characters, preserving blank lines and indentation.

    imgui's read-only multiline input keeps the text selectable/copyable but does NOT word-wrap,
    so long lines get clipped on the right. Pre-wrapping here gives readable, non-clipped text while
    staying copyable. Long tokens (Windows paths, ``P-XXXX_M-dummy``) are never split.
    """
    import textwrap

    out: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            out.append("")
            continue
        indent = line[: len(line) - len(line.lstrip())]
        wrapped = textwrap.wrap(
            line,
            width=max(20, cols),
            break_long_words=False,
            break_on_hyphens=False,
            subsequent_indent=indent + "   ",
        )
        out.extend(wrapped or [line])
    return "\n".join(out)


def _human_size(num_bytes: int) -> str:
    """Human-readable byte size (e.g. '1.2 MB')."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _detect_locale() -> str:
    import locale

    try:
        locale.setlocale(locale.LC_ALL, "")
        lang, _ = locale.getlocale()
        if lang:
            return lang.split("_")[0]
    except (ValueError, locale.Error):
        pass

    return "en"


def main() -> None:
    import sys

    # Before anything can reach the network: the packaged app's OpenSSL points at a trust store
    # that only exists on the build machine, so fall back to the bundled certifi (see certs.py).
    ensure_ca_bundle()

    # Before spawning any external binary (file-dialog helpers, MCP subprocess): drop the frozen
    # bundle's library path so children load system libraries, not PyInstaller's bundled ones.
    _restore_subprocess_library_path()

    if "--profile" in sys.argv:
        import cProfile
        import io
        import pstats

        pr = cProfile.Profile()
        pr.enable()
        try:
            _main()
        finally:
            pr.disable()
            s = io.StringIO()
            ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
            ps.print_stats(80)
            print(s.getvalue())
        return
    _main()


def _main() -> None:
    # Applies the OS locale, whose regional formats are used in every UI language. A persisted
    # choice (Language menu) wins over the auto-detected OS language.
    os_language = _detect_locale()
    saved = load_settings("app").get("locale")
    set_locale(saved if isinstance(saved, str) and saved else os_language)

    # App icon: hello_imgui loads assets/app_settings/icon.png as the window/app icon.
    hello_imgui.set_assets_folder(str(Path(__file__).parent / "assets"))

    # The catalog (and its .knxprod store next to it) lives in the local config/ folder with the
    # other runtime data, rather than cluttering the source tree.
    catalog_path = config_dir() / "catalog.xknxcatalog"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    app = KnxGuiApp(catalog_path)

    # Start on the Welcome screen (New / Open / Recent). We deliberately do NOT auto-open a project
    # so the Welcome is always visible on launch; the recent list gives one-click access.

    runner_params = hello_imgui.RunnerParams()
    runner_params.app_window_params.window_title = S.APP_TITLE
    runner_params.app_window_params.window_geometry.size = (1280, 720)
    # Start maximized by default (fills the screen, keeps the title bar). The 1280x720 size is the
    # fallback when the window is un-maximized. restore_previous_geometry keeps the user's later
    # manual size/position across restarts.
    runner_params.app_window_params.window_geometry.window_size_state = (
        hello_imgui.WindowSizeState.maximized
    )
    runner_params.app_window_params.restore_previous_geometry = True
    # CPU usage: this is an immediate-mode GUI, so without idling it redraws continuously and pins a
    # core. hello_imgui *does* drop to `fps_idle` after `time_active_after_last_event` seconds of no
    # input and then sleeps the thread (releasing the CPU) — but only when idling actually engages.
    # Multi-viewport (enable_viewports) keeps secondary platform windows refreshing and defeats that,
    # so we leave it off (docking still works inside the main window; panels just can't be torn out
    # into separate OS windows). fps_max still caps the active/interactive rate.
    runner_params.fps_idling.enable_idling = True
    runner_params.fps_idling.fps_idle = 6.0  # idle refresh (Hz) once input stops
    runner_params.fps_idling.time_active_after_last_event = (
        1.0  # idle sooner after activity
    )
    runner_params.fps_idling.fps_max = 60.0
    # On the software-OpenGL compat build (Mesa on the CPU, no GPU) 60 fps of full-window redraw is
    # very expensive, so halve the interactive cap. desktop_entry sets XKNX_SOFTWARE_GL for that build.
    if os.environ.get("XKNX_SOFTWARE_GL") == "1":
        runner_params.fps_idling.fps_max = 30.0

    runner_params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner_params.imgui_window_params.enable_viewports = False

    # A polished dark theme + rounded, well-spaced widgets for a modern look.
    runner_params.imgui_window_params.tweaked_theme.theme = (
        hello_imgui.ImGuiTheme_.darcula_darker
    )
    runner_params.callbacks.setup_imgui_style = _apply_style

    runner_params.imgui_window_params.show_menu_bar = True
    runner_params.imgui_window_params.show_menu_app = False
    runner_params.imgui_window_params.show_menu_view = False
    runner_params.callbacks.show_menus = app.gui_menu

    runner_params.imgui_window_params.show_status_bar = True
    runner_params.imgui_window_params.show_status_fps = (
        False  # hide the built-in FPS counter
    )
    runner_params.imgui_window_params.remember_status_bar_settings = False
    runner_params.callbacks.show_status = app.gui_status_bar

    # Keep the imgui layout .ini in the central data dir (with the JSON settings and catalog).
    runner_params.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    runner_params.ini_filename = str(config_dir() / "XKNX_Editor.ini")
    runner_params.docking_params.docking_splits = create_docking_splits()
    runner_params.docking_params.dockable_windows = create_dockable_windows(app)
    _apply_new_layout_once(runner_params.docking_params)

    runner_params.callbacks.post_init = app.setup
    runner_params.callbacks.before_exit = app.shutdown
    runner_params.callbacks.post_render_dockable_windows = app.render_overlays

    # Dev watchdog for uncached per-frame DB reads (set XKNX_DEBUG_DB_FRAMES=1): once per frame it
    # compares the render-thread query count against the project revision and logs a warning if
    # queries keep firing while nothing changed — the signature of a panel render() that reads the DB
    # without revision caching (see the revision-cached reads in ProjectService). No cost when off.
    if frame_db_profiler.enabled:
        runner_params.callbacks.pre_new_frame = app.watch_db_frame

    # Renderer backend: default is OpenGL3. In a VM (UTM/QEMU) or on Windows-on-ARM there is no
    # working OpenGL, but Windows always has a Direct3D software rasterizer (WARP). XKNX_RENDERER
    # (or the compat build's marker, see desktop_entry) can select Direct3D 11/12 instead.
    _renderer = os.environ.get("XKNX_RENDERER", "").strip().lower()
    if _renderer in ("dx11", "directx11", "direct_x11"):
        runner_params.renderer_backend_type = hello_imgui.RendererBackendType.direct_x11
    elif _renderer in ("dx12", "directx12", "direct_x12"):
        runner_params.renderer_backend_type = hello_imgui.RendererBackendType.direct_x12

    try:
        hello_imgui.run(runner_params)
    except (RuntimeError, SystemError) as exc:
        # hello_imgui/GLFW raise this when no OpenGL context can be created (e.g. Windows-on-ARM
        # under x64 emulation, some VMs, or a machine without an OpenGL driver): the C++ side aborts
        # with "Failed to create temporary window". Turn that into a readable message instead of a
        # raw assert traceback.
        if not _is_graphics_init_error(exc):
            raise
        _show_graphics_error(str(exc))
        raise SystemExit(1) from exc


_GRAPHICS_ERROR_MARKERS = (
    "failed to create",
    "opengl",
    "glfw",
    "wgl",
    "temporary window",
    "directx",
    "direct3d",
    "d3d",
    "renderer backend",
    "not available",
)


def _is_graphics_init_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _GRAPHICS_ERROR_MARKERS)


def _show_graphics_error(detail: str) -> None:
    """Show a readable 'graphics/OpenGL could not start' message (a Windows message box when there is
    no console; stderr + the log otherwise)."""
    title = "XKNX-Editor — graphics error"
    body = (
        "XKNX-Editor could not start because the system could not create an OpenGL window.\n\n"
        "This means there is no working OpenGL driver — common in virtual machines "
        "(UTM/QEMU/Parallels), on Windows on ARM, and over remote sessions.\n\n"
        "What helps:\n"
        "- In a VM (UTM/QEMU): the Microsoft OpenGL Compatibility Pack does NOT help, because the "
        "VM exposes no GPU to Windows. Download and use the separate compatibility build of "
        "XKNX-Editor (bundled Mesa software OpenGL), which renders on the CPU without a GPU:\n"
        f"    {_COMPAT_BUILD_URL}\n"
        "- On a physical Windows on ARM PC: install the 'OpenCL and OpenGL Compatibility Pack' "
        "from the Microsoft Store, then restart.\n"
        "- Otherwise update your graphics driver, or run on a machine with a GPU.\n\n"
        f"Technical detail: {detail}"
    )
    import contextlib

    with contextlib.suppress(Exception):
        print(f"{title}\n{body}", file=sys.stderr)
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            # MB_OK | MB_ICONERROR, works without a console window.
            ctypes.windll.user32.MessageBoxW(None, body, title, 0x10)  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
