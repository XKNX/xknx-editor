import collections
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import imgui_bundle._patch_runners_add_save_screenshot_param as _screenshot_patch

_screenshot_patch._get_caller_filename = lambda depth: ""  # type: ignore[assignment]

from imgui_bundle import hello_imgui, imgui
from imgui_bundle import portable_file_dialogs as pfd
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from xknxproject.exceptions import InvalidPasswordException, XknxProjectException

from editor_gui import __version__, file_dialogs, self_update, web
from editor_gui import signing_key as signing_key_store
from editor_gui import trace_key as trace_key_store
from editor_gui.cache import (
    cache_dir,
    cache_size,
    clear_cache,
    move_product_cache,
    product_cache_dir,
)
from editor_gui.certs import ensure_ca_bundle
from editor_gui.color import warning_red
from editor_gui.concurrency import MainThreadExecutor
from editor_gui.dock_menus import render_section_menus
from editor_gui.instances import (
    BROWSER,
    DESKTOP,
    Instance,
    Registry,
    allow_foreground,
    others,
    request,
    same_file,
    with_project,
)
from editor_gui.log_file import log_dir
from editor_gui.master_data import MasterDataInfo, load_master, master_xml_bytes
from editor_gui.myknx_oauth import (
    load_cached_credentials as load_myknx_oauth_credentials,
)
from editor_gui.myknx_oauth import (
    resolve_credentials as resolve_oauth_credentials,
)
from editor_gui.myknx_oauth import (
    save_credentials as save_myknx_oauth_credentials,
)
from editor_gui.os_open import open_url
from editor_gui.plugins.base import API_VERSION, Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.catalog import CatalogPlugin, CatalogService
from editor_gui.plugins.cockpit import CockpitPlugin
from editor_gui.plugins.connection import ConnectionPlugin
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.connection.strings import S as _connection_strings
from editor_gui.plugins.health import HealthPlugin
from editor_gui.plugins.keyring import KeyringPlugin
from editor_gui.plugins.logger import LoggerPlugin, LogService
from editor_gui.plugins.mcp import McpServerPlugin
from editor_gui.plugins.monitor import MonitorPlugin
from editor_gui.plugins.project import ProjectPlugin, ProjectService
from editor_gui.plugins.project.knxproj_manufacturer import collect_manufacturer_bundle
from editor_gui.plugins.project.ui.spaces import space_type_label
from editor_gui.plugins.recover import RecoverPlugin
from editor_gui.plugins.signing import SigningPlugin
from editor_gui.plugins.timeline import TimelinePlugin
from editor_gui.plugins.topology import TopologyPlugin
from editor_gui.portal_save import PortalSaveDialog
from editor_gui.ports import PortInUseError
from editor_gui.settings import config_dir, load_settings, save_settings
from editor_gui.strings import S, get_locale, set_locale
from editor_gui.theme import (
    ThemeWindow,
    apply_style,
    apply_theme,
    system_theme,
    theme_name,
    themes,
)
from editor_gui.update_check import (
    ReleaseAsset,
    UpdateCheckError,
    UpdateInfo,
    fetch_update,
)
from editor_gui.web import enable_web_mode, web_options
from editor_gui.widgets.checkbox import checkbox, radio_button
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.description import set_secure_devices
from editor_gui.widgets.dialog import (
    begin_dialog,
    dialog_buttons,
    dialog_room,
    end_dialog,
    floating_window_border,
    focus_when_appearing,
    outlined_tabs,
    tab_page,
)
from editor_gui.widgets.dpi import px, px_vec2
from editor_gui.widgets.fonts import bold, fitted_heading, heading, load_fonts
from editor_gui.widgets.parameter_widgets import set_highlight_changes
from editor_gui.widgets.password_form import NewPasswordForm
from editor_gui.widgets.tables import (
    begin_data_table,
    data_table_headers,
    reset_all_tables,
)
from editor_gui.widgets.text_util import fit_text, human_size, text_clipped_tooltip
from editor_gui.widgets.tree import collapsing_header
from editor_gui.window_title import bring_to_front, set_window_title, wake
from xknxeditor.catalog import ProductSummary
from xknxeditor.prod.app_id import parse_app_id
from xknxeditor.prod.errors import ArchiveError
from xknxeditor.proj import (
    MYKNX_DLL_NAME,
    CompactCancelled,
    CompactResult,
    ExportCancelled,
    ExportResult,
    InvalidProjectPassword,
    MyKnxError,
    MyKnxSession,
    ProjectPasswordRequired,
    ProjectStorageError,
    default_myknx_dll_path,
    export_knxproj,
    extract_oauth_credentials,
    fetch_myknx_products,
    myknx_certificate_signer,
    session_certificate_signer,
    session_products,
    signing_key_is_placeholder,
)
from xknxeditor.proj.core import ets_store, import_notes
from xknxeditor.proj.core.addressing import GroupAddressStyle
from xknxeditor.proj.core.ets_store_serialize import (
    next_free_pid,
    store_pid_exists,
    unzip_members,
    write_project_store,
)
from xknxeditor.proj.core.ga_export import CSV_LAYOUTS, CSV_SEPARATORS
from xknxeditor.proj.core.import_notes import ImportLoss
from xknxeditor.proj.core.product_prune import (
    STEP_DEVICES,
    STEP_MANUFACTURER,
    STEP_STORE,
    STEP_VACUUM,
)
from xknxeditor.proj.db import frame_db_profiler, make_engine, url_for
from xknxeditor.proj.models import Device, Installation, Project

_REPO_URL = "https://github.com/xknx/xknx-editor"
# Direct downloads for hosts without a working OpenGL driver (VMs such as UTM/QEMU): the installer,
# whose "Software rendering" option adds Mesa's software OpenGL (pure CPU), and the portable build
# that already contains it. Pick the architecture-matching assets (native ARM64 vs x64).
# /releases/latest/download always points at the newest release's asset.
_WINDOWS_ARCH = "arm64" if platform.machine() == "ARM64" else "x64"
_INSTALLER_URL = f"{_REPO_URL}/releases/latest/download/XKNX-Editor-windows-installer-{_WINDOWS_ARCH}.exe"
_COMPAT_ASSET = (
    "XKNX-Editor-windows-portable-arm64.zip"  # the arm64 build already bundles software OpenGL
    if _WINDOWS_ARCH == "arm64"
    else "XKNX-Editor-windows-portable-x64-softwaregl.zip"
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


def _any_popup_open() -> bool:
    """Whether a popup or dialog is open now."""
    return imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id)


def startup_file(argv: list[str]) -> str | None:
    """The file to open at start: the first argument that is not an option, as an absolute path.
    Opening a project from the file manager (the .xknx file association) passes it this way."""
    for arg in argv[1:]:
        if arg and not arg.startswith("-"):
            return os.path.abspath(arg)
    return None


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


def _file_name(name: str) -> str:
    """``name`` usable as a file name on every operating system."""
    cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in name)
    return cleaned.strip().rstrip(". ")


def _link(url: str) -> None:
    """``url`` as a link that opens it, with a menu to copy it (the built-in one is untranslated)."""
    if imgui.text_link(url):
        open_url(url)
    if imgui.is_item_hovered():
        imgui.set_tooltip(S.LINK_OPEN.format(url=url))
    if imgui.begin_popup_context_item(f"##link_{url}"):
        if imgui.menu_item(S.LINK_COPY, "", False)[0]:
            imgui.set_clipboard_text(url)
        imgui.end_popup()


# Room at 100 % scaling above and below the status bar's text.
_STATUS_PADDING = 7.0

# Refresh rate (Hz) once input stops, and while a progress dialog shows work going on.
_FPS_IDLE = 6.0
_FPS_PROGRESS = 30.0


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


@dataclass
class _ProductRow:
    """One row of the product-import result table: pre-formatted cells for one product version."""

    manufacturer: str
    product: str
    order: str
    application: str
    version: str
    mask: str
    hardware: str
    registered: str


class KnxGuiApp:
    def __init__(self, catalog_path: Path) -> None:
        self._catalog_service_path = catalog_path
        self._catalog_service = CatalogService(
            catalog_path, parse_cache_dir=product_cache_dir()
        )

        self._open_file_dialog: file_dialogs.OpenFileDialog | None = None
        self._save_file_dialog: file_dialogs.SaveFileDialog | None = None
        self._open_project_dialog: file_dialogs.OpenFileDialog | None = None
        self._save_project_dialog: file_dialogs.SaveFileDialog | None = None
        # New-project wizard: collected before the save dialog opens, then applied to the fresh file.
        self._new_project_requested = False
        self._new_project_name = "New project"
        self._new_project_style = GroupAddressStyle.THREE_LEVEL
        self._save_as_dialog: file_dialogs.SaveFileDialog | None = None
        self._import_knxproj_save_dialog: file_dialogs.SaveFileDialog | None = None
        self._import_ga_export_save_dialog: file_dialogs.SaveFileDialog | None = None
        self._import_ga_export_source: str | None = None
        self._export_knxproj_dialog: (
            file_dialogs.SaveFileDialog | PortalSaveDialog | None
        ) = None
        self._export_knxproj_default = ""
        self._export_ga_export_dialog: file_dialogs.SaveFileDialog | None = None
        self._ga_export_options_requested = False
        self._ga_export_options: dict[str, Any] = {}
        # The project has no group addresses: say so instead of exporting an empty file.
        self._ga_export_empty_requested = False
        # Clear Cache…: the confirmation, with the size the cache had when it was asked for.
        self._clear_cache_requested = False
        self._clear_cache_size = 0
        # Folder picker for the ProjectStore export when the default path is not found, and which
        # ETS generation that export is aimed at (set when the export starts, read by the dialogs).
        self._projectstore_dialog: file_dialogs.SelectFolderDialog | None = None
        self._projectstore_format = ets_store.StoreFormat.ETS6
        # File picker for the ETS installer.guid when it is not found locally (e.g. exporting on a
        # Mac for a Windows ETS), plus the store folder resolved in the step before it.
        self._installer_guid_dialog: file_dialogs.OpenFileDialog | None = None
        self._pending_store_path: Path | None = None
        # Set when a P-XXXX folder already exists in the store: (store_root, installer_guid) held
        # until the user picks overwrite vs. new copy in the confirmation modal.
        self._projectstore_confirm: tuple[Path, str] | None = None
        self._projectstore_confirm_open = False
        self._last_export_path: str | None = None
        self._file_dialogs_available: bool | None = None
        # One-shot guard for the startup OpenGL renderer log (see _log_gl_renderer). Done from the
        # frame path, not post_init, so the GL context is guaranteed current (a context-less
        # glGetString segfaults at the C level and would be uncatchable).
        self._gl_logged = False
        self._title_key: tuple[int, int] | None = None
        self._title_name = ""
        self._theme_window = ThemeWindow()
        # FPS and the idling toggle at the end of the status bar.
        self._show_status_fps = False
        self._show_status_bar = True
        # The Manufacturer tab was hidden for a selected space (see _sync_device_section).
        self._manufacturer_hidden = False
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
        self._myknx_username = os.environ.get("MYKNX_USERNAME", "") or str(
            load_settings("app").get("myknx_username", "")
        )
        self._myknx_password = os.environ.get("MYKNX_PASSWORD", "")
        # After login we fetch the account's licenses so the user picks one instead of typing an
        # opaque product id. None = not logged in yet; [] = logged in but no licenses.
        self._myknx_products: list[tuple[str, str, bool]] | None = None
        self._myknx_selected_pid = os.environ.get("MYKNX_PRODUCT_ID", "")
        self._myknx_login_thread: threading.Thread | None = None
        self._myknx_login_error = ""
        # OAuth device-code login for 2FA accounts: an established session (used instead of
        # username/password for the signer) and the user code to display while the browser is open.
        self._myknx_oauth_session: MyKnxSession | None = None
        self._myknx_oauth_user_code = ""
        self._myknx_oauth_url = ""
        # Shown after a 401: ask whether the password was wrong or the account uses 2FA.
        self._myknx_2fa_prompt = False
        # Request to open the OAuth-credentials config modal next frame (set from a button handler).
        self._myknx_open_oauth_config = False
        # The dongle steps wrapped to the width of their box: ((text, width), wrapped text).
        self._dongle_steps_wrapped: tuple[tuple[str, float], str] = (("", 0.0), "")
        # Width of the Sign Export fields' titles column (set each frame the dialog shows).
        self._sign_title_w = 0.0
        # OAuth client credentials (client id/secret) for the device-code flow: entered by hand or
        # extracted from the user's Knx.Ets.MyKnx.dll, cached per-user.
        _oauth_creds = load_myknx_oauth_credentials()
        self._myknx_client_id = _oauth_creds[0] if _oauth_creds else ""
        self._myknx_client_secret = _oauth_creds[1] if _oauth_creds else ""
        self._myknx_oauth_dll_dialog: file_dialogs.OpenFileDialog | None = None
        self._myknx_oauth_extract_thread: threading.Thread | None = None
        self._myknx_oauth_extract_error = ""
        self._import_knxproj_source: str | None = None
        self._import_knxproj_dest: str | None = None
        self._password_prompt_requested = False
        self._import_password = ""
        self._import_password_error: str | None = None
        # The password for an import of an archive with KNX Secure data but without a password.
        self._import_project_password: str | None = None
        # Opening a password-protected project: the file, the entered password and the last error.
        self._open_password_path: str | None = None
        self._open_password_requested = False
        self._open_password = ""
        self._open_password_error: str | None = None
        # The password a project with KNX Secure data needs: set it (``_action``) or give up.
        self._new_password_requested = False
        self._new_password_action: Callable[[str], None] | None = None
        self._new_password_cancel: Callable[[], None] | None = None
        self._new_password_prompt: str | None = None
        self._new_password_form = NewPasswordForm()
        # Network-drive consent: when opening/importing to a location SQLite can't use (SMB/NFS), we
        # ask before working on a local copy. `_action` runs on "Use local copy"; `_home` is shown.
        self._network_consent_requested = False
        self._network_consent_home: str | None = None
        self._network_consent_action: Callable[[], None] | None = None
        # "Load product from URL" prompt (imports a .knxprod / OpenKNX release / product XML).
        self._url_prompt_requested = False
        self._url_input = ""
        # Folder picker launched from that prompt, to import every product source in a folder.
        self._url_folder_dialog: file_dialogs.SelectFolderDialog | None = None
        # In the browser UI, a pick of product files (uploaded or in the file folder) instead.
        self._url_files_dialog: file_dialogs.OpenFileDialog | None = None
        # Background import (keeps the UI responsive during the slow parse/catalog/device build).
        self._import_thread: threading.Thread | None = None
        self._import_pw: str | None = None
        self._import_needs_password = False
        self._import_needs_project_password = False
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
        # Optional cancel action for the running background task (a Cancel button in the modal).
        self._progress_cancel: Callable[[], None] | None = None
        # Compact Project: the analysis shown for confirmation, the result shown at the end, and the
        # cancel request the worker polls before writing (worker->render handoff via the flags).
        self._compact_analysis: CompactResult | None = None
        self._compact_confirm_requested = False
        self._compact_result: CompactResult | None = None
        self._compact_result_requested = False
        self._compact_cancel = False
        # Export option: leave out product data no device uses (persisted).
        self._export_remove_unused = bool(
            load_settings("app").get("export_remove_unused_products", False)
        )
        # MCP tab state (host/port for the embedded MCP server), loaded from the local config.
        _mcp_cfg = load_settings("mcp")
        self._mcp_host = str(_mcp_cfg.get("host") or "127.0.0.1")
        self._mcp_port = str(_mcp_cfg.get("port") or "8765")
        # Optional bearer token; when set, the server requires it on every request. Empty = no auth.
        self._mcp_token = str(_mcp_cfg.get("token") or "")
        # Why the last start failed (port in use, ...), shown with the server status until the next
        # start.
        self._mcp_error: str | None = None
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
        # A failed .knxproj export: (heading, reason, whether getting the certificate
        # failed). Set by the export worker; the Export Failed window shows it.
        self._export_failure: tuple[str, str, bool] | None = None
        self._export_failure_requested = False
        # The certificate signer used for the export, kept so the export-complete window can
        # re-export after removing a device whose product data is too new for the chosen ETS.
        self._export_summary_signer: (
            Callable[[str, bytes, str], bytes | None] | None
        ) = None
        # Devices (node_id, label) the open summary flags as incompatible with the chosen ETS
        # version; computed once when the window opens (not per frame).
        self._export_summary_incompat_rows: list[tuple[int, str]] = []
        # Set by the .knxprod load worker; the render loop opens a modal message box with it so a
        # catalog import gives explicit, click-to-dismiss feedback (not just a log line). _msg holds
        # the text while the modal is open (worker->render handoff via _result, latched into _msg).
        self._knxprod_result: str | None = None
        self._knxprod_msg: str | None = None
        # A detailed product-import result rendered as a table: (header, rows). The worker sets
        # ``_import_rows_result``; the render loop latches it into ``_import_rows`` until OK.
        self._import_rows_result: tuple[str, list[_ProductRow]] | None = None
        self._import_rows: tuple[str, list[_ProductRow]] | None = None
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
        # "user"/"machine" when running from an installation of the Windows installer, which
        # updates in place (self_update.py); None opens the release page instead.
        self._update_scope = self_update.installed_scope()
        # A project passed on the command line, opened once the window is up.
        self.startup_file: str | None = None
        # Other editors running when this one started: asked about first (see instances.py).
        self.running_editors: list[Instance] = []
        # This editor's entry for the others, and their requests (handled on the UI thread).
        self._registry: Registry | None = None
        self._editor_requests: collections.deque[dict[str, Any]] = collections.deque()
        # A project another editor has open, and those editors: asked about before opening it.
        self._elsewhere: tuple[str, list[Instance]] | None = None
        self._update_manual = False  # a user-triggered check also reports "up to date"
        # The dialog of a user-triggered check: "checking", "current", "failed", or None (closed).
        self._update_check_state: str | None = None
        self._update_check_error = ""
        self._update_check_open_requested = False
        # Target schema for .knxproj export. ETS 6 (project/23) is the default; ETS 5 is project/20.
        # Persisted so a chosen ETS version sticks across sessions.
        _schema = str(load_settings("app").get("export_schema") or "23")
        self._export_schema = _schema if _schema in ("20", "23") else "23"
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
            run_background=self._run_bg,
            request_project_password=self._ask_project_password,
        )

        self._catalog_plugin = CatalogPlugin(self._plugin_api)
        self._connection_plugin = ConnectionPlugin(self._plugin_api)
        self._monitor_plugin = MonitorPlugin(self._plugin_api)
        self._keyring_plugin = KeyringPlugin(self._plugin_api)
        # Data Secure: programming/testing take a device's tool key from the project, else from the
        # loaded keyring.
        self._connection_service.keyring = self._keyring_plugin.service
        self._connection_service.project_keys = self._project_service.device_keys
        # KNX IP Secure tunnels and routing, and KNX Data Secure group communication, of the
        # editor's own connection: from the project, else from the loaded keyring.
        self._connection_service.project_tunnels = self._project_service.secure_tunnels
        self._connection_service.project_backbone = (
            self._project_service.secure_backbone
        )
        self._connection_service.project_data_secure = (
            self._project_service.data_secure_tables
        )
        self._signing_plugin = SigningPlugin(self._plugin_api)
        self._recover_plugin = RecoverPlugin(self._plugin_api)
        self._health_plugin = HealthPlugin(self._plugin_api)
        self._cockpit_plugin = CockpitPlugin(self._plugin_api)
        self._topology_plugin = TopologyPlugin(self._plugin_api)
        self._timeline_plugin = TimelinePlugin(
            self._plugin_api,
            get_telegrams=lambda: self._monitor_plugin.service.telegrams(limit=4000),
        )
        self._project_plugin = ProjectPlugin(
            self._plugin_api,
            get_selected_node_ids=lambda: [],
            export_group_addresses=self._export_ga_export,
            get_latest_value=self._monitor_plugin.service.latest_text,
        )
        self._project_plugin.show_context_help = bool(
            load_settings("app").get("show_context_help", True)
        )
        self._highlight_changes = bool(
            load_settings("app").get("highlight_changes", True)
        )
        set_highlight_changes(self._highlight_changes)
        # Device rows mark the devices commissioned with KNX Data Secure with a shield.
        set_secure_devices(
            self._project_service.secure_device_ids, lambda: S.DATA_SECURE_DEVICE
        )
        self.bottom_expanded = bool(load_settings("app").get("bottom_expanded", False))
        # The browser UI's layout for the size it is shown in (see form_factor), the usual one
        # meanwhile remembered, and since when another one would fit.
        self._form = "wide"
        self._wide_layout: dict[str, Any] = {}
        self._form_since: float | None = None
        # The status bar's parts: whether one no longer fitted, and the room kept at the right.
        self._status_full = False
        self._status_reserved = 0.0
        # Width the menus take side by side, per language (see gui_menu).
        self._menus_width: dict[str, float] = {}
        # After a layout switch: the order the tabs are moved into (window ids), from the frame
        # the new layout is built until a later one.
        self._tab_order: list[str] = []
        self._tab_order_from = 0
        self._tab_order_until = 0
        # The usual layout's section sizes, measured while it had its room (a smaller browser
        # squeezes it before the layout changes).
        self._wide_ratios: dict[str, float] = {}
        self._wide_measured = 0.0
        self._monitor_plugin.set_monitor_extended(self.bottom_expanded)
        self._monitor_plugin.set_layout_toggler(self._toggle_bottom_expanded)

        self._log = Logger(self._log_service, "app")
        self._logger_plugin = LoggerPlugin(self._log_service)

        # Embedded MCP server: drives these same live services (no panels, so not in _plugins).
        self._mcp_plugin = McpServerPlugin(
            self._plugin_api,
            self._ui_executor.run,
            self._connection_plugin,
            self._monitor_plugin.service,
            self._keyring_plugin.service,
        )

        self._plugins: list[Any] = [
            self._catalog_plugin,
            self._connection_plugin,
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
        self._log.info("editor started", installation=self._update_scope or "none")
        self._can_launch_file_dialog()
        # In the browser UI, notes about downloads show as this app's toasts.
        if web.active_hub is not None:
            web.active_hub.notify = self._flash_toast
            # An error the browser UI went on after: logged, and shown as a toast.
            web.active_hub.report_error = lambda where, error: self._log.error(
                "internal error", where=where, error=error
            )
        # Apply a previously extracted/saved signing key so exports are signed with it.
        if signing_key_store.apply_cached_key():
            self._log.info("signing key loaded")
        # Apply a previously extracted project-log (trace) key so comments decrypt on open.
        if trace_key_store.apply_cached_key():
            self._log.info("trace key loaded")
        self._register_editor()
        # Best-effort check for a newer release on GitHub (unless the user disabled it).
        if self._update_check_enabled():
            self._start_update_check(manual=False)
        # With other editors running, the user is asked first (_render_running_editors_modal).
        if not self.running_editors:
            self._start_session()

    def _start_session(self) -> None:
        """What a start does besides showing the app: connect, restart the MCP server, and open the
        project given on the command line."""
        # Discover KNX gateways at startup and auto-connect to the last-used (or first) one.
        self._connection_plugin.autostart()
        # Restart the MCP server when it was running at the last exit.
        if load_settings("mcp").get("running"):
            self._start_mcp(save=False)
        if self.startup_file is not None:
            self._do_open_project(self.startup_file)

    # --- other running editors (instances.py) -----------------------------

    def _register_editor(self) -> None:
        """Make this editor known to others started later, and answer their requests."""
        hub = web.active_hub

        def received(command: dict[str, Any]) -> None:
            # On the registry's thread: queue it and wake the idle render loop.
            self._editor_requests.append(command)
            if hub is not None:
                hub.wake.set()
            else:
                wake()

        mode = BROWSER if hub is not None else DESKTOP
        try:
            self._registry = Registry(mode, web.page_url, received)
        except OSError as e:
            self._log.warning("not registered for other editors", error=str(e))

    def _handle_editor_requests(self) -> None:
        """Another editor asked this one to come to the front, and maybe to open a project."""
        if self._registry is not None:
            path = self._project_service.path
            self._registry.set_project(str(path) if path is not None else "")
        while self._editor_requests:
            command = self._editor_requests[0]
            file = command.get("file")
            if isinstance(file, str) and file:
                current = self._project_service.path
                if current is None or not same_file(str(current), file):
                    if self._bg_thread is not None or self._import_thread is not None:
                        return  # opened once the running task is done
                    self._do_open_project(file)
            self._editor_requests.popleft()
            if web.active_hub is None:
                bring_to_front()

    def _switch_to(self, other: Instance, file: str | None) -> bool:
        """Bring ``other`` to the front (or its browser UI), opening ``file`` there; whether it
        answered."""
        allow_foreground(other.pid)
        command: dict[str, Any] = {"cmd": "activate"}
        if file:
            command["file"] = file
        if request(other, command) is None:
            self._flash_toast(S.EDITOR_GONE)
            return False
        if other.mode == BROWSER:
            if web.active_hub is not None:
                web.active_hub.go_to(other.url)
            else:
                open_url(other.url)
        return True

    def _editor_label(self, other: Instance) -> str:
        kind = (
            S.EDITOR_BROWSER.format(url=other.url)
            if other.mode == BROWSER
            else S.EDITOR_WINDOW
        )
        project = Path(other.project).name if other.project else S.EDITOR_NO_PROJECT
        return S.EDITOR_ENTRY.format(kind=kind, project=project)

    def _render_running_editors_modal(self) -> None:
        """At a start while other editors run: switch to one of them, or start this one too."""
        title = f"{S.EDITOR_RUNNING_TITLE}###editor-running"
        # Opened (again) while no other dialog is open: one opened meanwhile would replace it.
        if self.running_editors and not _any_popup_open():
            imgui.open_popup(title)
        if not begin_dialog(title, width=560.0):
            return
        imgui.text_wrapped(S.EDITOR_RUNNING_TEXT)
        imgui.spacing()
        switched = self._render_editor_rows(self.running_editors, self.startup_file)
        imgui.spacing()
        clicked = dialog_buttons(S.EDITOR_START_ANOTHER, S.BTN_CANCEL)
        if switched or clicked == 1:
            imgui.close_current_popup()
            self.running_editors = []
            hello_imgui.get_runner_params().app_shall_exit = True
        elif clicked == 0:
            imgui.close_current_popup()
            self.running_editors = []
            self._start_session()
        end_dialog()

    def _render_editor_rows(self, editors: list[Instance], file: str | None) -> bool:
        """One row per editor with its "Switch to It" button; whether one was switched to."""
        switched = False
        for index, other in enumerate(editors):
            imgui.push_id(f"editor-{index}")
            imgui.align_text_to_frame_padding()
            imgui.bullet_text(self._editor_label(other))
            imgui.same_line()
            if imgui.button(S.EDITOR_SWITCH) and self._switch_to(other, file):
                switched = True
            imgui.pop_id()
        return switched

    def _render_project_elsewhere_modal(self) -> None:
        """Before opening a project another editor has open: switch there, or open it anyway."""
        title = f"{S.PROJECT_ELSEWHERE_TITLE}###project-elsewhere"
        if self._elsewhere is not None and not _any_popup_open():
            imgui.open_popup(title)
        if not begin_dialog(title, width=560.0):
            return
        if self._elsewhere is None:
            imgui.close_current_popup()
            end_dialog()
            return
        path, editors = self._elsewhere
        imgui.text_wrapped(S.PROJECT_ELSEWHERE_TEXT.format(name=Path(path).name))
        imgui.spacing()
        switched = self._render_editor_rows(editors, None)
        imgui.spacing()
        clicked = dialog_buttons(S.PROJECT_OPEN_ANYWAY, S.BTN_CANCEL)
        if switched or clicked == 1:
            imgui.close_current_popup()
            self._elsewhere = None
        elif clicked == 0:
            imgui.close_current_popup()
            self._elsewhere = None
            self._do_open_project(path, open_anyway=True)
        end_dialog()

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
        if manual:
            # A dialog shows the check running and then its result (or the update found).
            self._update_check_state = "checking"
            self._update_check_open_requested = True
        if self._update_thread is not None and self._update_thread.is_alive():
            self._update_manual = self._update_manual or manual
            return
        self._update_manual = manual

        def worker() -> None:
            error = ""
            try:
                info = fetch_update(__version__)
            except UpdateCheckError as e:
                info, error = None, str(e)
            # Marshal the result back onto the UI thread (touches settings + popups).
            self._ui_executor.submit(lambda: self._on_update_result(info, error))

        self._update_thread = threading.Thread(
            target=worker, daemon=True, name="update-check"
        )
        self._update_thread.start()

    def _on_update_result(self, info: UpdateInfo | None, error: str = "") -> None:
        self._update_thread = None
        manual, self._update_manual = self._update_manual, False
        # A user-triggered check ignores a previously skipped version and tells when up to date or
        # when the check failed; the startup check stays silent then.
        if info is not None and (
            manual or info.version != self._skipped_update_version()
        ):
            self._update_check_state = None
            self._update_info = info
            self._update_prompt_requested = True
        elif manual and error:
            self._log.warning("update check failed", error=error)
            self._update_check_state = "failed"
            self._update_check_error = error
        elif manual:
            self._update_check_state = "current"

    def shutdown(self) -> None:
        if self._registry is not None:
            self._registry.close()
        # Stop the MCP server first so in-flight tool calls stop before the bus loop and project they
        # depend on are torn down.
        self._mcp_plugin.stop()
        self._connection_plugin.shutdown()
        if self._project_service.is_open:
            self._project_service.close()

    # --- recent files (persisted in the app settings) ---------------------

    def _recent_files(self) -> list[str]:
        raw = load_settings("app").get("recent_files")
        return (
            [p for p in cast(list[object], raw) if isinstance(p, str)]
            if isinstance(raw, list)
            else []
        )

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
        # Open the wizard first; it collects name + GA style, then opens the save dialog on confirm.
        self._new_project_name = "New project"
        self._new_project_style = GroupAddressStyle.THREE_LEVEL
        self._new_project_requested = True

    def _open_new_project_save_dialog(self) -> None:
        if not self._can_launch_file_dialog():
            return
        self._save_project_dialog = file_dialogs.save_file(
            S.FILE_DIALOG_PROJECT_SAVE_TITLE,
            f"{self._new_project_name.strip() or 'New project'}.xknx",
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
            pfd.opt.force_overwrite,
        )

    def _save_as(self) -> None:
        if not self._project_service.is_open or not self._can_launch_file_dialog():
            return
        self._save_as_dialog = file_dialogs.save_file(
            S.FILE_DIALOG_SAVE_AS_TITLE,
            "",
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
            pfd.opt.force_overwrite,
        )

    def _open_project(self) -> None:
        if not self._can_launch_file_dialog():
            return
        # Accept .knxproj and .xml here too: _do_open_project routes .knxproj archives and ETS
        # group-address exports (ga-export/01 .xml) through their importers.
        self._log.debug("opening project file dialog")
        self._open_project_dialog = file_dialogs.open_file(
            S.FILE_DIALOG_PROJECT_TITLE,
            "",
            [
                S.FILE_DIALOG_OPEN_FILTER,
                "*.xknx *.knxproj *.xml *.csv",
                S.FILE_DIALOG_PROJECT_FILTER,
                "*.xknx",
                S.FILE_DIALOG_KNXPROJ_FILTER,
                "*.knxproj",
                S.FILE_DIALOG_GA_IMPORT_FILTER,
                "*.xml *.csv",
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
        # The project's name, as a file name.
        info = self._project_service.get_project_metadata()
        name = _file_name(info.name) if info is not None else ""
        default = f"{name or 'project'}.knxproj"
        self._export_knxproj_default = default
        # On Linux the file-type list is the ETS version. The portal returns which
        # entry was confirmed; ETS 6 is the initial selection.
        if sys.platform == "linux" and web.active_hub is None:
            ets6 = S.FILE_DIALOG_ETS6_FILTER
            ets5 = S.FILE_DIALOG_ETS5_FILTER
            self._export_knxproj_dialog = PortalSaveDialog(
                S.FILE_DIALOG_KNXPROJ_SAVE_TITLE,
                default,
                [(ets6, "*.knxproj"), (ets5, "*.knxproj")],
                current=ets5 if self._export_schema == "20" else ets6,
            )
            return
        self._export_knxproj_dialog = file_dialogs.save_file(
            S.FILE_DIALOG_KNXPROJ_SAVE_TITLE,
            default,
            [S.FILE_DIALOG_KNXPROJ_FILTER, "*.knxproj", S.FILE_DIALOG_ALL_FILES, "*"],
            pfd.opt.force_overwrite,
        )

    def _export_ga_export(self) -> None:
        if not self._project_service.is_open or not self._can_launch_file_dialog():
            return
        # While a project loads its reads come back empty; then the export goes on as usual.
        if (
            not self._project_service.loading
            and not self._project_service.group_addresses
        ):
            self._ga_export_empty_requested = True
            return
        saved = load_settings("app").get("ga_export", {})
        options = {
            "format": "xml",
            "csv_layout": CSV_LAYOUTS[0],
            "csv_header": False,
            "csv_separator": "tab",
        }
        if isinstance(saved, dict):
            options.update(
                (key, value)
                for key, value in saved.items()  # type: ignore[reportUnknownVariableType]
                if key in options
            )
        if options["format"] not in ("xml", "csv"):
            options["format"] = "xml"
        if options["csv_layout"] not in CSV_LAYOUTS:
            options["csv_layout"] = CSV_LAYOUTS[0]
        if options["csv_separator"] not in CSV_SEPARATORS:
            options["csv_separator"] = "tab"
        options["csv_header"] = bool(options["csv_header"])
        self._ga_export_options = options
        self._ga_export_options_requested = True

    def _render_ga_export_options_modal(self) -> None:
        if self._ga_export_options_requested:
            imgui.open_popup(S.GA_EXPORT_OPTIONS_TITLE)
            self._ga_export_options_requested = False
        if not begin_dialog(S.GA_EXPORT_OPTIONS_TITLE, width=520.0):
            return
        options = self._ga_export_options
        imgui.text_disabled(S.GA_EXPORT_OUTPUT_FORMAT)
        for index, (value, label) in enumerate((("xml", "XML"), ("csv", "CSV"))):
            if index:
                imgui.same_line()
            if radio_button(label, options["format"] == value):
                options["format"] = value
        imgui.spacing()
        imgui.begin_disabled(options["format"] != "csv")
        imgui.text_disabled(S.GA_EXPORT_CSV_FORMAT)
        layouts = dict(
            zip(
                CSV_LAYOUTS,
                (
                    S.GA_EXPORT_CSV_3_1,
                    S.GA_EXPORT_CSV_1_3,
                    S.GA_EXPORT_CSV_1_1,
                    S.GA_EXPORT_CSV_3_3,
                ),
                strict=True,
            )
        )
        for layout, label in layouts.items():
            if radio_button(label, options["csv_layout"] == layout):
                options["csv_layout"] = layout
        imgui.spacing()
        _, options["csv_header"] = checkbox(
            S.GA_EXPORT_CSV_HEADER, options["csv_header"]
        )
        imgui.spacing()
        imgui.text_disabled(S.GA_EXPORT_CSV_SEPARATOR)
        separators = (
            ("tab", S.GA_EXPORT_SEPARATOR_TAB),
            ("comma", S.GA_EXPORT_SEPARATOR_COMMA),
            ("semicolon", S.GA_EXPORT_SEPARATOR_SEMICOLON),
        )
        for index, (key, label) in enumerate(separators):
            if index:
                imgui.same_line()
            if radio_button(f"{label}##ga_sep", options["csv_separator"] == key):
                options["csv_separator"] = key
        imgui.end_disabled()
        clicked = dialog_buttons(S.BTN_EXPORT, S.BTN_CANCEL)
        if clicked == 0:
            imgui.close_current_popup()
            data = load_settings("app")
            data["ga_export"] = dict(options)
            save_settings("app", data)
            self._open_ga_export_save_dialog()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_clear_cache_modal(self) -> None:
        if self._clear_cache_requested:
            imgui.open_popup(S.CLEAR_CACHE_TITLE)
            self._clear_cache_requested = False
        if not begin_dialog(S.CLEAR_CACHE_TITLE, width=460.0):
            return
        imgui.text_wrapped(
            S.CLEAR_CACHE_TEXT.format(size=human_size(self._clear_cache_size))
        )
        clicked = dialog_buttons(
            S.CLEAR_CACHE_CONFIRM,
            S.BTN_CANCEL,
            enabled=[self._clear_cache_size > 0, True],
        )
        if clicked == 0:
            freed = clear_cache()
            self._log.info("cache cleared", freed=freed)
            self._flash_toast(S.CLEAR_CACHE_DONE.format(size=human_size(freed)))
            imgui.close_current_popup()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _render_ga_export_empty_modal(self) -> None:
        title = f"{S.FILE_DIALOG_GA_EXPORT_SAVE_TITLE}###ga_export_empty"
        if self._ga_export_empty_requested:
            imgui.open_popup(title)
            self._ga_export_empty_requested = False
        if not begin_dialog(title, width=420.0):
            return
        imgui.text_wrapped(S.GA_EXPORT_EMPTY)
        if dialog_buttons(S.BTN_OK) == 0:
            imgui.close_current_popup()
        end_dialog()

    def _open_ga_export_save_dialog(self) -> None:
        if not self._project_service.is_open or not self._can_launch_file_dialog():
            return
        suffix = ".csv" if self._ga_export_options.get("format") == "csv" else ".xml"
        default = f"group-addresses{suffix}"
        if self._project_service.path is not None:
            default = f"{self._project_service.path.stem}-ga{suffix}"
        name = (
            S.FILE_DIALOG_GA_EXPORT_CSV_FILTER
            if suffix == ".csv"
            else S.FILE_DIALOG_GA_EXPORT_FILTER
        )
        self._export_ga_export_dialog = file_dialogs.save_file(
            S.FILE_DIALOG_GA_EXPORT_SAVE_TITLE,
            default,
            [name, f"*{suffix}", S.FILE_DIALOG_ALL_FILES, "*"],
            pfd.opt.force_overwrite,
        )

    def _do_export_ga_export(self, dest: str) -> None:
        """Write the open project's group-address tree to ``dest`` (.xml or .csv)."""
        options = self._ga_export_options
        try:
            if options.get("format") == "csv":
                count = self._project_service.export_ga_csv(
                    Path(dest),
                    layout=str(options["csv_layout"]),
                    separator=CSV_SEPARATORS[str(options["csv_separator"])],
                    header=bool(options["csv_header"]),
                )
            else:
                count = self._project_service.export_ga_export(Path(dest))
        except Exception as e:  # surfaced as an error toast via the log
            self._log.error("ga export failed", dest=dest, error=str(e))
            return
        self._last_export_path = dest
        self._flash_toast(S.GA_EXPORT_OK.format(count=count))

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
        remove_unused = self._export_remove_unused
        password = self._project_service.project_password
        # Set by the progress window's Cancel; the export stops at its next step.
        cancel_requested = threading.Event()

        def stage(text: str) -> None:
            if not cancel_requested.is_set():
                self.set_progress(None, text)

        # The signer shows its step in the progress window.
        certificate_signer: Callable[[str, bytes, str], bytes | None] | None = None
        if signer is not None:
            sign = signer

            def sign_with_stage(pid: str, signature: bytes, name: str) -> bytes | None:
                stage(S.EXPORT_STAGE_CERTIFICATE)
                return sign(pid, signature, name)

            certificate_signer = sign_with_stage

        def cancel_export() -> None:
            cancel_requested.set()
            self.set_progress(None, S.EXPORT_STAGE_CANCELLING)

        def _run() -> None:
            try:
                _export()
            finally:
                self._progress_running = False

        def _export() -> None:
            stage(S.EXPORT_STAGE_BUNDLE)
            self._log.debug(
                "export knxproj",
                dest=dest,
                schema=self._export_schema,
                signed=signer is not None,
                remove_unused_products=remove_unused,
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
            stage(S.EXPORT_STAGE_WRITE)
            try:
                result = export_knxproj(
                    source,
                    Path(dest),
                    password=password,
                    schema=self._export_schema,
                    extra_files=extra_files,
                    master_xml=master_xml,
                    # The current signed master, cached with the app's master data for exports
                    # without a download.
                    fetch_master=True,
                    master_cache_dir=config_dir(),
                    certificate_signer=certificate_signer,
                    remove_unused_products=remove_unused,
                    cancelled=cancel_requested.is_set,
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
                    self._export_summary_signer = signer
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
                if result.product_prune is not None:
                    self._log.info(
                        "export: unused product data left out",
                        applications=len(result.product_prune.application_programs),
                        manufacturers=len(result.product_prune.manufacturers),
                        size_before=result.product_prune.size_before,
                        size_after=result.product_prune.size_after,
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
                        dialog=True,
                    )
                    self._show_export_failure(
                        dest,
                        S.EXPORT_FAILED_MISSING.format(
                            references=", ".join(result.missing_references)
                        ),
                        heading=S.EXPORT_FAILED_NOT_IMPORTABLE,
                    )
                if result.schema_incompatible_products:
                    # A product is only available at a newer schema than the chosen ETS version
                    # (e.g. an ETS6 product in an ETS5 export). The target ETS rejects the whole
                    # archive as "created with a newer version". Logged here; the export-complete
                    # window shows the warning and offers to remove the device(s) and re-export.
                    self._log.error(
                        "export: products have no data for the chosen ETS version; that ETS will "
                        "reject the import (remove the device, or export for the newer ETS)",
                        products=", ".join(result.schema_incompatible_products),
                        path=dest,
                    )
                    # Ensure the export-complete window opens even if it was gated out above.
                    self._export_summary = result
                    self._export_summary_path = dest
                    self._export_summary_schema = labels.get(
                        used_schema, f"project/{used_schema}"
                    )
                    self._export_summary_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self._export_summary_signer = signer
                    self._export_summary_requested = True
            except ExportCancelled:
                self._log.info("export cancelled", path=dest)
                self._flash_toast(S.EXPORT_CANCELLED)
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
                    dialog=True,
                )
                self._show_export_failure(dest, e.user_message, signing=True)
            except (
                Exception
            ) as e:  # OSError/ValueError from export, or other HTTP errors
                self._log.error(
                    "export failed",
                    path=dest,
                    error=f"{type(e).__name__}: {e}",
                    dialog=True,
                )
                self._show_export_failure(dest, str(e) or type(e).__name__)

        self._begin_progress(S.EXPORT_PROGRESS_TEXT.format(name=Path(dest).name))
        self._progress_cancel = cancel_export
        self._myknx_thread = threading.Thread(target=_run, daemon=True)
        self._myknx_thread.start()

    def _show_export_failure(
        self,
        dest: str,
        reason: str,
        signing: bool = False,
        heading: str | None = None,
    ) -> None:
        """Open the Export Failed window for ``dest`` with ``reason`` on the next frame (called on
        the export worker thread). ``heading`` (with ``{name}``) defaults to "could not be
        exported"."""
        name = Path(dest).name
        text = (heading or S.EXPORT_FAILED_TEXT).format(name=name)
        self._export_failure = (text, reason, signing)
        self._export_failure_requested = True

    def _set_export_schema(self, code: str) -> None:
        """Set and persist the .knxproj export schema (ETS version format), so it sticks."""
        self._export_schema = code
        data = load_settings("app")
        data["export_schema"] = code
        save_settings("app", data)

    def _ets_projectstore_dir(self, store_format: ets_store.StoreFormat) -> Path:
        """Default project store for ``store_format``: %ProgramData%\\KNX\\ETS{5,6}\\ProjectStore."""
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / "KNX" / store_format.app_dir / "ProjectStore"

    def _begin_projectstore_export(self, store_format: ets_store.StoreFormat) -> None:
        """Resolve the ProjectStore folder for ``store_format`` (default, else ask), then export."""
        self._projectstore_format = store_format
        store = self._ets_projectstore_dir(store_format)
        if store.parent.exists():
            self._resolve_installer_guid_then_export(store)
        else:
            # ETS not at the default location (or non-Windows): ask the user for the store folder.
            ets = "ETS 5" if store_format is ets_store.StoreFormat.ETS5 else "ETS 6"
            self._projectstore_dialog = file_dialogs.select_folder(
                S.MYKNX_DONGLE_PICK_STORE.format(ets=ets), ""
            )

    def _resolve_installer_guid_then_export(self, store: Path) -> None:
        """Resolve the target ETS installation GUID (read it locally, else let the user pick it)."""
        guid = ets_store.read_installer_guid(self._projectstore_format, store=store)
        if guid:
            self._prepare_projectstore_export(store, guid)
        else:
            # The .store descriptor binds the project to the target ETS installation; on a machine
            # without ETS (e.g. a Mac exporting for a Windows ETS), ask for the installer.guid file
            # copied from that ETS version's %ProgramData%/KNX/ETS{5,6}/Installer. ETS 5 and ETS 6
            # do not share a GUID.
            self._pending_store_path = store
            self._installer_guid_dialog = file_dialogs.open_file(
                S.MYKNX_DONGLE_PICK_INSTALLER,
                "",
                [
                    S.MYKNX_DONGLE_INSTALLER_FILTER,
                    "installer.guid *.guid",
                    S.FILE_DIALOG_ALL_FILES,
                    "*",
                ],
            )

    def _prepare_projectstore_export(self, store: Path, installer_guid: str) -> None:
        """Export now, or ask the user when a folder for this project id already exists."""
        pid = self._project_service.pid
        if pid is not None and store_pid_exists(store, pid):
            # Let the user decide: replace that folder or write a separate, renamed copy.
            self._projectstore_confirm = (store, installer_guid)
        else:
            self._export_to_ets6_projectstore(store, installer_guid, create_copy=False)

    def _export_to_ets6_projectstore(
        self, store: Path, installer_guid: str, *, create_copy: bool
    ) -> None:
        """Write the project's ``P-XXXX`` store folder into ``store``.

        The shape follows ``self._projectstore_format``: ETS 5 and ETS 6 do not read each other's
        topology. ``store`` is that version's ProjectStore.

        ETS loads a project from its store without an import signature/certificate check; the user
        then copies/clones it in ETS, which re-signs and re-licenses it with their dongle. The
        folder uses ETS' internal stream format (not a ``.knxproj``), bound to ``installer_guid``.

        ``create_copy`` writes to a fresh id with a tagged name instead of replacing the existing
        folder, so an export can sit next to the original in the store.
        """
        source = self._project_service.working_path
        if source is None or (self._myknx_thread and self._myknx_thread.is_alive()):
            return
        program_refs = self._project_service.program_refs()
        reveal = self._project_service.reveal

        def _run() -> None:
            try:
                catalog_members: dict[str, bytes] = {}
                try:
                    bundle = collect_manufacturer_bundle(
                        program_refs, self._catalog_service
                    )
                    catalog_members = {
                        name: data
                        for name, data in bundle.extra_files.items()
                        if "/" in name and name.split("/", 1)[0].startswith("M-")
                    }
                except Exception as e:
                    self._log.warning(
                        "manufacturer bundle failed", error=f"{type(e).__name__}: {e}"
                    )
                engine = make_engine(url_for(Path(source)))
                with Session(engine, autoflush=False) as session:
                    project = session.query(Project).first()
                    if project is None:
                        self._log.error("ProjectStore export: no project row")
                        return
                    installation = (
                        session.query(Installation).order_by(Installation.index).first()
                    )
                    # Imported manufacturer data (ETS-reissued ids) wins; catalog members fill gaps
                    # for devices added from the catalog rather than imported.
                    members = unzip_members(project.imported_product_members)
                    for name, data in catalog_members.items():
                        members.setdefault(name, data)
                    if create_copy:
                        # A separate copy: free id, name tagged so it is distinguishable in ETS.
                        pid = next_free_pid(store)
                        project_name = f"{project.name} ({S.MYKNX_DONGLE_COPY_SUFFIX})"
                    else:
                        pid, project_name = project.id, None
                    built = write_project_store(
                        store / pid,
                        project,
                        installation,
                        installer_guid,
                        pid=pid,
                        project_name=project_name,
                        product_members=members,
                        overwrite=not create_copy,
                        reveal=reveal,
                        store_format=self._projectstore_format,
                    )
                ets = (
                    "ETS 5"
                    if self._projectstore_format is ets_store.StoreFormat.ETS5
                    else "ETS 6"
                )
                self._log.info(
                    "exported to ETS ProjectStore",
                    ets=ets,
                    path=str(store / built.pid),
                    pid=built.pid,
                )
            except Exception as e:
                self._log.error(
                    "ProjectStore export failed", error=f"{type(e).__name__}: {e}"
                )

        self._myknx_thread = threading.Thread(target=_run, daemon=True)
        self._myknx_thread.start()

    def _start_myknx_login(self) -> None:
        """Log into MyKnx and fetch the account's licenses on a worker thread."""
        if self._myknx_login_thread and self._myknx_login_thread.is_alive():
            return
        self._myknx_login_error = ""
        self._myknx_products = None
        self._myknx_oauth_session = None
        self._myknx_2fa_prompt = False
        self._myknx_login_thread = threading.Thread(
            target=self._run_myknx_login, daemon=True
        )
        self._myknx_login_thread.start()

    def _start_myknx_oauth_login(self) -> None:
        """Run the OAuth device-code (2FA) sign-in on a worker thread (opens the browser)."""
        if self._myknx_login_thread and self._myknx_login_thread.is_alive():
            return
        self._myknx_login_error = ""
        self._myknx_products = None
        self._myknx_oauth_session = None
        self._myknx_oauth_user_code = ""
        self._myknx_oauth_url = ""
        self._myknx_login_thread = threading.Thread(
            target=self._run_myknx_oauth_fallback, daemon=True
        )
        self._myknx_login_thread.start()

    def _run_myknx_login(self) -> None:
        # Worker thread: network only, never imgui. Result is read on the UI thread next frame.
        try:
            products = fetch_myknx_products(self._myknx_username, self._myknx_password)
        except MyKnxError as e:
            # HTTP 401 is ambiguous: the server returns the same generic message for a wrong
            # password and for a 2FA account (which cannot use the username/password login). Ask the
            # user which it is instead of guessing.
            if e.status == 401:
                self._myknx_2fa_prompt = True
                self._myknx_products = []
                return
            self._myknx_login_error = str(e)
            self._myknx_products = []
        except Exception as e:  # network/other errors
            self._myknx_login_error = str(e)
            self._myknx_products = []
        else:
            self._set_myknx_products(products)

    def _run_myknx_oauth_fallback(self) -> None:
        # Worker thread: run the device-code flow (opens the browser via on_verification), then fetch
        # the licenses from the session.
        self._myknx_oauth_user_code = ""
        try:
            client_id, client_secret = resolve_oauth_credentials()
        except Exception as e:
            # No credentials configured and none extractable: point the user at the config modal.
            self._myknx_login_error = str(e)
            self._myknx_products = []
            return
        try:
            session = MyKnxSession(access_token="")
            session.oauth_login(client_id, client_secret, self._on_oauth_verification)
            products = session_products(session)
        except Exception as e:
            self._myknx_login_error = str(e)
            self._myknx_products = []
            return
        self._myknx_oauth_session = session
        self._set_myknx_products(products)

    def _on_oauth_verification(self, url: str, user_code: str) -> None:
        # Called from the login worker: open the sign-in page and surface the code for the modal.
        self._myknx_oauth_user_code = user_code
        self._myknx_oauth_url = url
        try:
            opened = open_url(url)
        except Exception as e:
            opened = False
            self._log.warning("myknx 2fa browser open failed", url=url, error=str(e))
        self._log.info(
            "myknx 2fa verification url", url=url, user_code=user_code, opened=opened
        )

    def _set_myknx_products(self, products: list[dict[str, object]]) -> None:
        # Login succeeded: remember the username (not the password) for next time.
        if self._myknx_username:
            data = load_settings("app")
            if data.get("myknx_username") != self._myknx_username:
                data["myknx_username"] = self._myknx_username
                save_settings("app", data)
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
            # Only cloud-capable, non-expired licenses can produce an online certificate (the cloud
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
        if not begin_dialog(S.MYKNX_SIGN_TITLE, width=700.0):
            return
        dest = self._export_pending_dest
        fitted_heading(S.MYKNX_SIGN_PROMPT)
        imgui.spacing()
        # The fields' titles in front of them, every field from the same column, also the ones on
        # the login tab's page (inside its padding).
        style = imgui.get_style()
        # EXPORT_FORMAT is drawn bold and larger below, so measure it the same way — it is wider than
        # its regular size and the column must be wide enough that the field next to it does not
        # overlap the label.
        with bold(_STRONG_TITLE_SCALE):
            format_label_w = imgui.calc_text_size(S.EXPORT_FORMAT).x
        self._sign_title_w = (
            max(
                format_label_w,
                *(
                    imgui.calc_text_size(label).x
                    for label in (
                        S.MYKNX_SIGN_USERNAME,
                        S.MYKNX_SIGN_PASSWORD,
                        S.MYKNX_SIGN_LICENSE,
                    )
                ),
            )
            + style.item_spacing.x
        )
        # Target format: ETS 6 (schema 23, default) or ETS 5 (schema 20). Bold (strong=True): this is
        # the key export choice and is easy to miss among the dimmed field titles.
        _title_in_front(
            S.EXPORT_FORMAT,
            imgui.get_cursor_start_pos().x
            + style.window_padding.x
            + style.child_border_size
            + self._sign_title_w,
            strong=True,
        )
        formats = [("23", S.EXPORT_FORMAT_ETS6), ("20", S.EXPORT_FORMAT_ETS5)]
        current = next(lbl for code, lbl in formats if code == self._export_schema)
        imgui.set_next_item_width(-1)
        if begin_combo("##export_schema", current):
            for code, lbl in formats:
                if imgui.selectable(lbl, code == self._export_schema)[0]:
                    self._set_export_schema(code)
            imgui.end_combo()
        changed, self._export_remove_unused = checkbox(
            S.EXPORT_REMOVE_UNUSED, self._export_remove_unused
        )
        if changed:
            data = load_settings("app")
            data["export_remove_unused_products"] = self._export_remove_unused
            save_settings("app", data)
        # The note next to the checkbox; it wraps below its start where the line is too short.
        imgui.same_line()
        imgui.push_style_color(
            imgui.Col_.text, imgui.get_style_color_vec4(imgui.Col_.text_disabled)
        )
        imgui.text_wrapped(S.EXPORT_REMOVE_UNUSED_HELP)
        imgui.pop_style_color()
        imgui.spacing()
        # Lock the credential fields while a login is in flight so an in-flight response for old
        # credentials can't populate licenses after the fields changed (which would sign with new
        # credentials against the old account's product id).
        logging_in = bool(
            self._myknx_login_thread and self._myknx_login_thread.is_alive()
        )
        # The two ways to license the export: signing online with a MyKnx login, or through the
        # user's own ETS with a dongle.
        selected_pid = ""
        dongle = False
        # Both tabs share one height so the dialog does not jump on switch, sized to the dongle steps
        # (the taller tab) so they show without an inner scrollbar. The dongle box's text width is the
        # tab page's content width (page padding + the box's own frame padding/border).
        style = imgui.get_style()
        steps_w = (
            imgui.get_content_region_avail().x
            - 2 * style.window_padding.x
            - 2 * style.frame_padding.x
            - 2 * style.frame_border_size
        )
        steps_key = (S.MYKNX_DONGLE_STEPS, steps_w)
        if self._dongle_steps_wrapped[0] != steps_key:
            self._dongle_steps_wrapped = (steps_key, _wrap_text(steps_key[0], steps_w))
        steps_lines = self._dongle_steps_wrapped[1].count("\n") + 1
        tab_page_h = (
            steps_lines * imgui.get_text_line_height()
            + 2 * style.frame_padding.y  # the steps box's own padding
            + imgui.get_frame_height()  # the Copy button row below the box
            + style.item_spacing.y
            + 2 * style.window_padding.y  # the tab page's own padding
        )
        with outlined_tabs():
            if imgui.begin_tab_bar("##export_license"):
                if imgui.begin_tab_item(S.MYKNX_SIGN_TAB)[0]:
                    with tab_page("##myknx_page", tab_page_h):
                        selected_pid = self._render_myknx_login_tab(logging_in)
                    imgui.end_tab_item()
                if imgui.begin_tab_item(S.MYKNX_DONGLE_TAB)[0]:
                    dongle = True
                    with tab_page("##dongle_page", tab_page_h):
                        self._render_dongle_tab()
                    imgui.end_tab_item()
                imgui.end_tab_bar()
        imgui.spacing()
        # The folder-signing key signs every .knxproj export, with and without certificate (the
        # dongle way re-signs in ETS). A child modal: closing it returns to the export dialog.
        if signing_key_is_placeholder():
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.85, 0.7, 0.3, 1.0))
            imgui.text_wrapped(S.SIGNING_KEY_PLACEHOLDER_HINT)
            imgui.pop_style_color()
        if imgui.button(S.SIGNING_KEY_BUTTON, imgui.ImVec2(-1, 0)):
            imgui.open_popup(S.SIGNING_KEY_TITLE)
        self._render_signing_key_modal()
        # OAuth-credentials config modal: opened automatically from "Start 2FA sign-in" when no
        # credentials are available yet (no manual button).
        if self._myknx_open_oauth_config:
            imgui.open_popup(S.MYKNX_OAUTH_TITLE)
            self._myknx_open_oauth_config = False
        self._render_myknx_oauth_modal()

        if dongle:
            # The dongle way writes the project into the ETS 6 ProjectStore instead of a file.
            clicked = dialog_buttons(
                S.MYKNX_DONGLE_EXPORT_STORE, S.MYKNX_SIGN_SKIP, S.BTN_CANCEL
            )
            if clicked == 0:
                # ETS 5 ProjectStore export is disabled for now: a fresh ETS 5 store still needs
                # the split manufacturer cache (H/C/A + binary COC/PC/CPC) this writer does not
                # produce yet, without which ETS 5 can crash opening an area with devices. For
                # ETS 5 use the "Export without Certificate" .knxproj route instead (see the
                # steps). Re-enable when the cache is produced:
                # self._begin_projectstore_export(ets_store.StoreFormat.ETS5)
                self._begin_projectstore_export(ets_store.StoreFormat.ETS6)
                self._export_pending_dest = None
                imgui.close_current_popup()
                end_dialog()
                return
        else:
            clicked = dialog_buttons(
                S.MYKNX_SIGN_CONFIRM,
                S.MYKNX_SIGN_SKIP,
                S.BTN_CANCEL,
                enabled=[bool(selected_pid) and not logging_in, True, True],
            )
        sign, skip, cancel = clicked == 0, clicked == 1, clicked == 2
        if dest is not None and sign and selected_pid:
            # An OAuth (2FA) session is already authenticated; otherwise sign with username/password.
            if self._myknx_oauth_session is not None:
                signer = session_certificate_signer(
                    self._myknx_oauth_session, selected_pid
                )
            else:
                signer = myknx_certificate_signer(
                    self._myknx_username, self._myknx_password, selected_pid
                )
            self._do_export_knxproj(dest, signer)
            self._export_pending_dest = None
            imgui.close_current_popup()
        elif dest is not None and skip:
            self._do_export_knxproj(dest)
            self._export_pending_dest = None
            imgui.close_current_popup()
        elif cancel:
            self._export_pending_dest = None
            imgui.close_current_popup()
        end_dialog()

    def _render_myknx_login_tab(self, logging_in: bool) -> str:
        """Credentials, login and license choice. Returns the selected product id ("" if none)."""
        # An ETS running with the same account claims the license for its own session.
        imgui.push_style_color(imgui.Col_.text, warning_red())
        imgui.text_wrapped(S.MYKNX_SIGN_ETS_RUNNING)
        imgui.pop_style_color()
        imgui.spacing()
        # Titles in front of the fields. Editing credentials invalidates a previous login.
        if logging_in:
            imgui.begin_disabled()
        _title_in_front(S.MYKNX_SIGN_USERNAME, self._sign_value_x())
        imgui.set_next_item_width(-1)
        user_changed, self._myknx_username = imgui.input_text(
            "##myknx_user", self._myknx_username
        )
        _title_in_front(S.MYKNX_SIGN_PASSWORD, self._sign_value_x())
        imgui.set_next_item_width(-1)
        pw_changed, self._myknx_password = imgui.input_text(
            "##myknx_pw", self._myknx_password, imgui.InputTextFlags_.password
        )
        if logging_in:
            imgui.end_disabled()
        if (user_changed or pw_changed) and self._myknx_products is not None:
            self._myknx_products = None  # credentials changed -> require re-login
            self._myknx_login_error = ""
            self._myknx_oauth_session = None
            self._myknx_2fa_prompt = False
        return self._render_myknx_license_section(logging_in)

    def _sign_value_x(self) -> float:
        """Where the Sign Export fields start in the current window (page)."""
        return imgui.get_cursor_start_pos().x + self._sign_title_w

    def _render_myknx_license_section(self, logging_in: bool) -> str:
        """Login button / license dropdown. Returns the selected product id ("" if none)."""
        if logging_in:
            imgui.text_disabled(S.MYKNX_SIGN_LOGGING_IN)
            if self._myknx_oauth_user_code:
                # Device-code flow: the browser is open; show the code the user must confirm.
                imgui.text_wrapped(
                    S.MYKNX_SIGN_2FA_WAITING.format(code=self._myknx_oauth_user_code)
                )
                if self._myknx_oauth_url:
                    # Show the URL too (selectable), in case the browser did not open.
                    imgui.set_next_item_width(-1)
                    imgui.input_text(
                        "##oauth_url",
                        self._myknx_oauth_url,
                        imgui.InputTextFlags_.read_only,
                    )
            return ""
        if self._myknx_2fa_prompt:
            # 401 is ambiguous (wrong password vs. 2FA account); let the user decide.
            imgui.text_colored(imgui.ImVec4(0.85, 0.7, 0.3, 1.0), S.MYKNX_2FA_PROMPT)
            if imgui.button(S.MYKNX_2FA_REENTER, px_vec2(240, 0)):
                self._myknx_2fa_prompt = False
                self._myknx_products = None  # back to the credential fields
            imgui.same_line()
            if imgui.button(S.MYKNX_2FA_START, px_vec2(200, 0)):
                self._myknx_2fa_prompt = False
                # Need the OAuth client credentials first; if none are cached and ETS is not in a
                # standard path, open the config modal so the user can enter/extract them.
                if (
                    load_myknx_oauth_credentials() is None
                    and not default_myknx_dll_path()
                ):
                    self._myknx_open_oauth_config = True
                else:
                    self._start_myknx_oauth_login()
            return ""
        if self._myknx_products is None:
            can_login = bool(self._myknx_username and self._myknx_password)
            if not can_login:
                imgui.begin_disabled()
            imgui.set_cursor_pos_x(self._sign_value_x())
            if imgui.button(S.MYKNX_SIGN_LOGIN, px_vec2(150, 0)):
                self._start_myknx_login()
            if not can_login:
                imgui.end_disabled()
            return ""
        if self._myknx_login_error:
            imgui.text_colored(
                warning_red(),
                S.MYKNX_SIGN_LOGIN_FAILED.format(error=self._myknx_login_error),
            )
            return ""
        if not self._myknx_products:
            imgui.text_disabled(S.MYKNX_SIGN_NO_LICENSES)
            return ""
        _title_in_front(S.MYKNX_SIGN_LICENSE, self._sign_value_x())
        current = next(
            (
                lbl
                for pid, lbl, _enabled in self._myknx_products
                if pid == self._myknx_selected_pid
            ),
            self._myknx_products[0][1],
        )
        imgui.set_next_item_width(-1)
        if begin_combo("##myknx_license", current):
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

    def _start_import(
        self, password: str | None, project_password: str | None = None
    ) -> None:
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
        self._import_project_password = project_password
        self._import_needs_password = False
        self._import_needs_project_password = False
        self._begin_progress(S.IMPORT_PROGRESS_TEXT)
        self._import_thread = threading.Thread(
            target=self._run_import,
            args=(source, dest, password, project_password),
            daemon=True,
            name="knxproj-import",
        )
        self._import_thread.start()
        self._log.debug("knxproj import dispatched", thread=self._import_thread.name)

    def _run_import(
        self,
        source: str,
        dest: str,
        password: str | None,
        project_password: str | None,
    ) -> None:
        # Worker thread: only touches services + logging (never imgui). Outcome is read in
        # _poll_import on the UI thread once the thread has finished.
        self._import_needs_password = self._try_import_knxproj(
            source, dest, password, project_password
        )

    def _poll_import(self) -> None:
        thread = self._import_thread
        if thread is None or thread.is_alive():
            return
        self._log.debug("knxproj import worker finished", thread=thread.name)
        self._import_thread = None
        self._progress_running = False
        if self._import_needs_project_password:
            # An archive with KNX Secure data but no password: the new project needs one.
            password = self._import_pw
            self._request_new_password(
                lambda chosen: self._start_import(password, chosen),
                self._clear_import_prompt,
            )
        elif self._import_needs_password:
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
        self._progress_cancel = None

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
            self._progress_cancel = None

    def _render_progress_modal(self) -> None:
        # Without input the window drops to the idle refresh rate, at which a progress bar looks
        # frozen; it keeps redrawing at the active rate while work is in progress.
        idling = hello_imgui.get_runner_params().fps_idling
        idling.fps_idle = _FPS_PROGRESS if self._progress_running else _FPS_IDLE
        if self._progress_requested:
            imgui.open_popup(S.PROGRESS_TITLE)
            self._progress_requested = False
        # Keep auto-resize (height fits content) but enforce a comfortable minimum width so long
        # device names in the status line don't wrap into a cramped, tall popup.
        imgui.set_next_window_size_constraints(
            px_vec2(560.0, 0.0), imgui.ImVec2(1.0e9, 1.0e9)
        )
        if not begin_dialog(S.PROGRESS_TITLE):
            return
        imgui.text_wrapped(self._progress_text)
        elapsed = time.time() - self._progress_started_at
        fraction = self._progress_fraction
        # The window's content width, at least 520 px: a stretch (-1) bar inside an auto-resizing
        # modal feeds back and shrinks it, a fixed width leaves a wider margin on the right.
        bar_size = imgui.ImVec2(max(px(520.0), imgui.get_content_region_avail().x), 0.0)
        if fraction is not None:
            fraction = max(0.0, min(1.0, fraction))
            imgui.progress_bar(fraction, bar_size, f"{fraction * 100:0.0f}%")
        else:
            # ImGui shows an animated indeterminate bar for a negative fraction.
            imgui.progress_bar(-1.0 * float(imgui.get_time()), bar_size, "")
        # The current step, shortened with "…" where it would reach them, and the seconds so far
        # right-aligned with the bar on the same line.
        seconds = f"{elapsed:0.0f}s"
        seconds_width = imgui.calc_text_size(seconds).x
        right = imgui.get_cursor_pos_x() + bar_size.x - seconds_width
        stage = self._progress_stage
        if stage:
            room = bar_size.x - seconds_width - imgui.get_style().item_spacing.x
            shown = fit_text(stage, room)
            imgui.text_disabled(shown)
            if shown != stage and imgui.is_item_hovered():
                imgui.set_tooltip(stage)
            imgui.same_line()
        imgui.set_cursor_pos_x(max(right, imgui.get_cursor_pos_x()))
        imgui.text_disabled(seconds)
        cancel = self._progress_cancel
        if cancel is not None and dialog_buttons(S.BTN_CANCEL) == 0:
            self._progress_cancel = None
            cancel()
        if not self._progress_running:
            imgui.close_current_popup()
        end_dialog()

    def _try_import_knxproj(
        self,
        source: str,
        dest: str,
        password: str | None,
        project_password: str | None = None,
    ) -> bool:
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
                Path(source),
                Path(dest),
                password=password,
                project_password=project_password,
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
        except ProjectPasswordRequired:
            self._import_needs_project_password = True
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
        if not begin_dialog(S.IMPORT_PASSWORD_TITLE, width=420.0):
            return
        imgui.text_wrapped(S.IMPORT_PASSWORD_PROMPT)
        imgui.set_next_item_width(-1)
        focus_when_appearing()
        submitted, self._import_password = imgui.input_text(
            "##import-password",
            self._import_password,
            imgui.InputTextFlags_.password | imgui.InputTextFlags_.enter_returns_true,
        )
        if self._import_password_error:
            imgui.text_colored(
                imgui.ImVec4(0.9, 0.4, 0.4, 1.0), self._import_password_error
            )
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        confirm = clicked == 0 or submitted
        cancel = clicked == 1
        source, dest = self._import_knxproj_source, self._import_knxproj_dest
        if confirm and source is not None and dest is not None:
            # Retry on a worker thread; _poll_import re-opens this modal if the password was wrong.
            imgui.close_current_popup()
            self._start_import(self._import_password)
        elif cancel:
            self._clear_import_prompt()
            imgui.close_current_popup()
        end_dialog()

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
        confirmed: bool = False,
    ) -> None:
        """Run ``action`` now, or — if ``path`` already exists — ask for overwrite confirmation
        first. This is the one confirmation on every OS: the native save dialogs are opened with
        ``force_overwrite`` (no prompt of their own, which they do not show the same way on every
        OS). ``confirmed`` skips it for a path the save dialog already confirmed (the XDG portal
        always asks), unless a suffix is appended to it here. ``save_in_place_ok`` skips
        the prompt when the target is the currently-open project itself (a plain save is no loss);
        it is NOT set for New/Import/Export, which would replace that file with different content.
        ``default_suffix`` mirrors the suffix the action appends to a suffixless path (``.xknx`` for
        New/Save-as/Import): without it the existence check would test the raw, suffixless path and
        silently overwrite the real target. Requests queue so a second dialog resolving
        mid-confirmation never drops the first action."""
        if default_suffix is not None and not Path(path).suffix:
            path = str(Path(path).with_suffix(default_suffix))
            confirmed = False
        target = Path(path)
        current = self._project_service.path
        same_as_open = (
            save_in_place_ok
            and current is not None
            and target.resolve() == current.resolve()
        )
        if target.exists() and not same_as_open and not confirmed:
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
        if not begin_dialog(S.OVERWRITE_TITLE, width=460.0):
            return
        path, action = self._overwrite_queue[0]
        target = Path(path)
        with heading():
            imgui.text_wrapped(S.OVERWRITE_HEADING.format(name=target.name))
        imgui.text_wrapped(S.OVERWRITE_PROMPT.format(dir=str(target.parent)))
        clicked = dialog_buttons(S.BTN_OVERWRITE, S.BTN_CANCEL)
        replace, cancel = clicked == 0, clicked == 1
        if replace or cancel:
            # Pop and close before running the action: the action may open its own modal (e.g. the
            # export sign prompt), and a queued request re-opens this popup on the next frame.
            self._overwrite_queue.pop(0)
            self._overwrite_popup_open = False
            imgui.close_current_popup()
            if replace:
                action()
        end_dialog()

    def _render_network_consent_modal(self) -> None:
        if self._network_consent_requested:
            imgui.open_popup(S.NETWORK_CONSENT_TITLE)
            self._network_consent_requested = False
        if not begin_dialog(S.NETWORK_CONSENT_TITLE, width=460.0):
            return
        imgui.text_wrapped(
            S.NETWORK_CONSENT_PROMPT.format(home=self._network_consent_home or "")
        )
        clicked = dialog_buttons(S.NETWORK_CONSENT_USE_LOCAL, S.BTN_CANCEL)
        use_local, cancel = clicked == 0, clicked == 1
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
        end_dialog()

    def _render_url_prompt_modal(self) -> None:
        # In the browser UI a folder would be one on the server, so product files are uploaded.
        files = web.active_hub is not None
        title = S.URL_PROMPT_TITLE_FILES if files else S.URL_PROMPT_TITLE
        if self._url_prompt_requested:
            imgui.open_popup(title)
            self._url_prompt_requested = False
        if not begin_dialog(title, width=560.0):
            return
        imgui.text_wrapped(S.URL_PROMPT_HINT)
        imgui.spacing()
        imgui.set_next_item_width(-1)
        submitted, self._url_input = imgui.input_text(
            "##load-url", self._url_input, imgui.InputTextFlags_.enter_returns_true
        )
        url = self._url_input.strip()
        imgui.spacing()
        if files:
            imgui.text_wrapped(S.URL_PROMPT_FILES_HINT)
            if imgui.button(S.URL_PROMPT_CHOOSE_FILES):
                imgui.close_current_popup()
                self._url_files_dialog = file_dialogs.open_file(
                    S.FILES_IMPORT_TITLE,
                    "",
                    [
                        S.FILES_IMPORT_FILTER,
                        "*.knxprod *.zip *.xml",
                        S.FILE_DIALOG_ALL_FILES,
                        "*",
                    ],
                    pfd.opt.multiselect,
                )
        else:
            imgui.text_wrapped(S.URL_PROMPT_FOLDER_HINT)
            if imgui.button(S.URL_PROMPT_BROWSE_FOLDER):
                imgui.close_current_popup()
                self._url_folder_dialog = file_dialogs.select_folder(
                    S.FOLDER_IMPORT_TITLE
                )
        imgui.spacing()
        clicked = dialog_buttons(S.URL_PROMPT_LOAD, S.URL_PROMPT_CANCEL)
        load = clicked == 0 or submitted
        cancel = clicked == 1
        if load and url:
            imgui.close_current_popup()
            self._start_url_import(url)
        elif cancel:
            imgui.close_current_popup()
        end_dialog()

    def _render_new_project_modal(self) -> None:
        if self._new_project_requested:
            imgui.open_popup(S.NEW_PROJECT_TITLE)
            self._new_project_requested = False
        if not begin_dialog(S.NEW_PROJECT_TITLE, width=420.0):
            return
        styles = (
            (GroupAddressStyle.FREE, S.NEW_PROJECT_STYLE_FREE),
            (GroupAddressStyle.TWO_LEVEL, S.NEW_PROJECT_STYLE_TWO_LEVEL),
            (GroupAddressStyle.THREE_LEVEL, S.NEW_PROJECT_STYLE_THREE_LEVEL),
        )
        imgui.text_disabled(S.NEW_PROJECT_NAME_LABEL)
        imgui.set_next_item_width(-1)
        _, self._new_project_name = imgui.input_text(
            "##new_project_name", self._new_project_name
        )
        imgui.spacing()
        imgui.text_disabled(S.NEW_PROJECT_STYLE_LABEL)
        imgui.set_next_item_width(-1)
        current = next(
            label for style, label in styles if style is self._new_project_style
        )
        if begin_combo("##new_project_style", current):
            for style, label in styles:
                if imgui.selectable(label, style is self._new_project_style)[0]:
                    self._new_project_style = style
            imgui.end_combo()
        clicked = dialog_buttons(S.NEW_PROJECT_CREATE, S.BTN_CANCEL)
        if clicked == 0:
            imgui.close_current_popup()
            self._open_new_project_save_dialog()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

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
                    added = self._catalog_service.import_product_source_contents(
                        data, master
                    )
                self._project_service.refresh_catalog_resolution(rebuild=True)
            except Exception as e:
                self._log.error(
                    "load from url failed", url=url, error=f"{type(e).__name__}: {e}"
                )
                self._knxprod_result = S.URL_IMPORT_FAILED.format(error=str(e))
                return
            self._log.info("loaded from url", url=url, added=len(added))
            if added:
                self._import_rows_result = (
                    S.KNXPROD_IMPORT_SUMMARY.format(count=len(added), name=url),
                    self._product_import_rows(added),
                )
            else:
                self._knxprod_result = S.URL_IMPORT_EMPTY

        self._run_bg(S.URL_DOWNLOADING, worker)

    def _start_folder_import(self, folder: str) -> None:
        """Import every supported product source found under ``folder`` on a worker thread: a
        .knxprod, an OpenKNX release ZIP, or a raw KNX product XML. Files that are not a product
        source are skipped."""

        def candidates() -> list[Path]:
            return sorted(
                p
                for p in Path(folder).rglob("*")
                if p.is_file() and p.suffix.lower() in (".knxprod", ".zip", ".xml")
            )

        self._start_product_files_import(
            folder,
            candidates,
            (S.FOLDER_IMPORTING, S.FOLDER_IMPORT_EMPTY, S.FOLDER_IMPORT_FAILED),
        )

    def _start_files_import(self, paths: list[str]) -> None:
        """Import the product files chosen in the browser UI (uploaded, or in its file folder) on a
        worker thread, as :meth:`_start_folder_import` does for a folder's files."""
        self._start_product_files_import(
            ", ".join(Path(p).name for p in paths),
            lambda: [Path(p) for p in paths],
            (S.FILES_IMPORTING, S.FILES_IMPORT_EMPTY, S.FILES_IMPORT_FAILED),
        )

    def _start_product_files_import(
        self,
        source: str,
        candidates: Callable[[], list[Path]],
        texts: tuple[str, str, str],
    ) -> None:
        """Import each product source ``candidates()`` lists on a worker thread; files that are not
        a product source are skipped. The imported versions are listed afterwards under ``source``.
        ``texts``: progress, nothing new, failed."""
        importing, empty, failed = texts

        def worker() -> None:
            try:
                master = master_xml_bytes()
                if master is None:
                    raise RuntimeError(
                        "KNX master data unavailable (offline and not cached) — connect to the "
                        "internet once so it can be downloaded, then retry"
                    )
                added_all: list[ProductSummary] = []
                for path in candidates():
                    try:
                        data = path.read_bytes()
                        with self._catalog_service.io_lock:
                            added = (
                                self._catalog_service.import_product_source_contents(
                                    data, master
                                )
                            )
                    except Exception as e:
                        self._log.debug(
                            "skipped file in product import",
                            path=str(path),
                            error=f"{type(e).__name__}: {e}",
                        )
                        continue
                    added_all.extend(added)
                # Two identical files map to the same stored archive, so drop repeats while keeping
                # first-seen order.
                seen: set[tuple[str, str]] = set()
                deduped: list[ProductSummary] = []
                for product in added_all:
                    key = (product.product_ref_id, product.hardware2program_ref_id)
                    if key not in seen:
                        seen.add(key)
                        deduped.append(product)
                added_all = deduped
                self._project_service.refresh_catalog_resolution(rebuild=True)
            except Exception as e:
                self._log.error(
                    "product import failed",
                    source=source,
                    error=f"{type(e).__name__}: {e}",
                )
                self._knxprod_result = failed.format(error=str(e))
                return
            self._log.info("imported products", source=source, added=len(added_all))
            if added_all:
                self._import_rows_result = (
                    S.KNXPROD_IMPORT_SUMMARY.format(count=len(added_all), name=source),
                    self._product_import_rows(added_all),
                )
            else:
                self._knxprod_result = empty

        self._run_bg(importing, worker)

    def _clear_import_prompt(self) -> None:
        self._import_knxproj_source = None
        self._import_knxproj_dest = None
        self._import_password = ""
        self._import_password_error = None
        self._import_project_password = None

    def _do_new_project(self, path: str) -> None:
        name = self._new_project_name.strip() or "New project"
        self._project_service.new(
            Path(path),
            name=name,
            group_address_style=self._new_project_style,
            building_name=name,
        )
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

    def _do_open_project(self, path: str, *, open_anyway: bool = False) -> None:
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
        if suffix == ".csv":
            self._log.info(
                "open project route selected", path=path, route="ga csv import"
            )
            self._prompt_ga_export_dest(path)
            return
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
        # Another editor has it open: ask first, as two editors would overwrite each other's changes.
        if not open_anyway:
            elsewhere = with_project(path, others())
            if elsewhere:
                self._elsewhere = (path, elsewhere)
                return

        def report(done: int, total: int, label: str = "") -> None:
            text = S.PROGRESS_DEVICES.format(done=done, total=total)
            self.set_progress(
                done / total if total else None,
                f"{text} — {label}" if label else text,
            )

        self._open_xknx(path, report)

    def _open_xknx(
        self,
        path: str,
        report: Callable[[int, int, str], None],
        password: str | None = None,
    ) -> None:
        """Open the ``.xknx`` at ``path`` behind the progress modal. A protected project asks for
        its password; one with KNX Secure data but no password asks to set one."""

        def worker() -> None:
            self._project_service.build_progress = report
            try:
                self._project_service.open(Path(path), password)
                self._add_recent(path)
                if self._project_service.mirroring_active:
                    self._mirror_notice = str(self._project_service.mirror_home)
                self._require_password_if_secure()
            except ProjectPasswordRequired:
                self._request_open_password(path, None)
            except InvalidProjectPassword:
                self._request_open_password(path, S.IMPORT_PASSWORD_WRONG)
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
        if password is not None:
            self._run_bg(S.PROGRESS_OPEN_PROJECT, worker)
            return
        self._maybe_consent_then(
            path, lambda: self._run_bg(S.PROGRESS_OPEN_PROJECT, worker)
        )

    def _request_open_password(self, path: str, error: str | None) -> None:
        # Worker thread: only flags; the modal opens on the UI thread.
        self._open_password_path = path
        self._open_password = ""
        self._open_password_error = error
        self._open_password_requested = True

    def _render_open_password_modal(self) -> None:
        title = f"{S.IMPORT_PASSWORD_TITLE}###open-password"
        # Opened once the open's worker (and its progress modal) is gone.
        if self._open_password_requested and self._bg_thread is None:
            imgui.open_popup(title)
            self._open_password_requested = False
        if not begin_dialog(title, width=420.0):
            return
        imgui.text_wrapped(S.OPEN_PASSWORD_PROMPT)
        imgui.set_next_item_width(-1)
        focus_when_appearing()
        submitted, self._open_password = imgui.input_text(
            "##open-password",
            self._open_password,
            imgui.InputTextFlags_.password | imgui.InputTextFlags_.enter_returns_true,
        )
        if self._open_password_error:
            imgui.text_colored(
                imgui.ImVec4(0.9, 0.4, 0.4, 1.0), self._open_password_error
            )
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL)
        path = self._open_password_path
        if (clicked == 0 or submitted) and path is not None and self._open_password:
            imgui.close_current_popup()
            password, self._open_password = self._open_password, ""

            def report(done: int, total: int, label: str = "") -> None:
                text = S.PROGRESS_DEVICES.format(done=done, total=total)
                self.set_progress(
                    done / total if total else None,
                    f"{text} — {label}" if label else text,
                )

            self._open_xknx(path, report, password)
        elif clicked == 1:
            self._open_password_path = None
            self._open_password = ""
            imgui.close_current_popup()
        end_dialog()

    def _require_password_if_secure(self) -> None:
        """Ask for a password when the open project holds KNX Secure data without one (from an
        earlier version); without it the project is closed again."""
        if not self._project_service.needs_password_setup():
            return

        def protect(password: str) -> None:
            self._run_bg(
                S.PROGRESS_PROTECT_PROJECT,
                lambda: self._project_service.set_project_password(password),
            )

        def give_up() -> None:
            self._log.warning(
                "project closed: a project with KNX Secure data needs a password"
            )
            self._project_service.close()

        self._request_new_password(protect, give_up)

    def _request_new_password(
        self,
        action: Callable[[str], None],
        cancel: Callable[[], None],
        prompt: str | None = None,
    ) -> None:
        self._new_password_action = action
        self._new_password_cancel = cancel
        self._new_password_prompt = prompt
        self._new_password_form.reset()
        self._new_password_requested = True

    def _ask_project_password(
        self, prompt: str, then: Callable[[], None], cancel: Callable[[], None]
    ) -> None:
        """Ask for a new password of the open project (see ``PluginAPI``): it is set behind the
        progress modal, then ``then`` runs on the UI thread."""

        def protect(password: str) -> None:
            def work() -> None:
                self._project_service.set_project_password(password)
                self._ui_executor.submit(then)

            self._run_bg(S.PROGRESS_PROTECT_PROJECT, work)

        self._request_new_password(protect, cancel, prompt)

    def _render_new_password_modal(self) -> None:
        title = f"{S.IMPORT_PASSWORD_TITLE}###new-password"
        # Opened once the worker that asked for it (and its progress modal) is gone.
        if (
            self._new_password_requested
            and self._bg_thread is None
            and self._import_thread is None
        ):
            imgui.open_popup(title)
            self._new_password_requested = False
        if not begin_dialog(title, width=460.0):
            return
        imgui.text_wrapped(self._new_password_prompt or S.NEW_PASSWORD_PROMPT)
        imgui.spacing()
        form = self._new_password_form
        submitted = form.render(
            S.PASSWORD_LABEL, S.PASSWORD_REPEAT_LABEL, S.PASSWORD_MISMATCH
        )
        clicked = dialog_buttons(S.BTN_OK, S.BTN_CANCEL, enabled=[form.valid, True])
        if clicked == 0 or (submitted and form.valid):
            action, password = self._new_password_action, form.password
            self._new_password_action = self._new_password_cancel = None
            form.reset()
            imgui.close_current_popup()
            if action is not None:
                action(password)
        elif clicked == 1:
            cancel = self._new_password_cancel
            self._new_password_action = self._new_password_cancel = None
            form.reset()
            imgui.close_current_popup()
            if cancel is not None:
                cancel()
        end_dialog()

    def _prompt_import_dest(self, source: str) -> None:
        """Remember the .knxproj source and ask where to save the imported .xknx project."""
        if not self._can_launch_file_dialog():
            return
        self._import_knxproj_source = source
        # Pass only the default file name, not a full path: on macOS a path with "/" in the save
        # dialog's name field gets mangled into ":"-separated segments.
        self._log.debug("opening knxproj import destination dialog", source=source)
        self._import_knxproj_save_dialog = file_dialogs.save_file(
            S.FILE_DIALOG_PROJECT_SAVE_TITLE,
            Path(source).with_suffix(".xknx").name,
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
            pfd.opt.force_overwrite,
        )
        self._log.debug("knxproj import destination dialog opened", source=source)

    def _prompt_ga_export_dest(self, source: str) -> None:
        """Remember the GA-export source and ask where to save the imported .xknx project."""
        if not self._can_launch_file_dialog():
            return
        self._import_ga_export_source = source
        self._import_ga_export_save_dialog = file_dialogs.save_file(
            S.FILE_DIALOG_PROJECT_SAVE_TITLE,
            Path(source).with_suffix(".xknx").name,
            [S.FILE_DIALOG_PROJECT_FILTER, "*.xknx", S.FILE_DIALOG_ALL_FILES, "*"],
            pfd.opt.force_overwrite,
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

        if self._url_folder_dialog is not None and self._url_folder_dialog.ready():
            folder = self._url_folder_dialog.result()
            self._url_folder_dialog = None
            if folder:
                self._start_folder_import(folder)

        if self._url_files_dialog is not None and self._url_files_dialog.ready():
            paths = self._url_files_dialog.result()
            self._url_files_dialog = None
            if paths:
                self._start_files_import(paths)

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
            dialog = self._export_knxproj_dialog
            result = dialog.result()
            self._export_knxproj_dialog = None
            if isinstance(dialog, PortalSaveDialog) and dialog.error:
                # The portal dialog never opened. Fall back to the helper dialog; the
                # format combo in the next window still chooses ETS 5 or ETS 6.
                self._log.warning("portal save dialog failed", error=dialog.error)
                self._export_knxproj_dialog = file_dialogs.save_file(
                    S.FILE_DIALOG_KNXPROJ_SAVE_TITLE,
                    self._export_knxproj_default,
                    [
                        S.FILE_DIALOG_KNXPROJ_FILTER,
                        "*.knxproj",
                        S.FILE_DIALOG_ALL_FILES,
                        "*",
                    ],
                    pfd.opt.force_overwrite,
                )
                return
            if isinstance(dialog, PortalSaveDialog):
                schemas = {
                    S.FILE_DIALOG_ETS6_FILTER: "23",
                    S.FILE_DIALOG_ETS5_FILTER: "20",
                }
                chosen = schemas.get(dialog.selected_filter)
                if chosen is not None:
                    self._set_export_schema(chosen)
            if result:
                # Ask about MyKnx certificate signing before running the export.
                self._maybe_overwrite_then(
                    result,
                    lambda r=result: self._begin_export_sign_prompt(r),
                    confirmed=isinstance(dialog, PortalSaveDialog),
                )

        if (
            self._export_ga_export_dialog is not None
            and self._export_ga_export_dialog.ready()
        ):
            result = self._export_ga_export_dialog.result()
            self._export_ga_export_dialog = None
            if result:
                self._maybe_overwrite_then(
                    result,
                    lambda r=result: self._do_export_ga_export(r),
                    default_suffix=".csv"
                    if self._ga_export_options.get("format") == "csv"
                    else ".xml",
                )

        if self._projectstore_dialog is not None and self._projectstore_dialog.ready():
            folder = self._projectstore_dialog.result()
            self._projectstore_dialog = None
            if folder:
                self._resolve_installer_guid_then_export(Path(folder))

        if (
            self._installer_guid_dialog is not None
            and self._installer_guid_dialog.ready()
        ):
            picked = self._installer_guid_dialog.result()
            self._installer_guid_dialog = None
            store = self._pending_store_path
            self._pending_store_path = None
            if picked and store is not None:
                try:
                    guid = Path(picked[0]).read_text(encoding="utf-8-sig").strip()
                except OSError as e:
                    self._log.error("could not read installer.guid", error=str(e))
                    guid = ""
                if guid:
                    self._prepare_projectstore_export(store, guid)

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

    def _product_import_rows(self, added: list[ProductSummary]) -> list[_ProductRow]:
        """One pre-formatted table row per imported product version, with its technical facts
        (order number, application + real version, mask, KNX Secure, size, bus current, media,
        registration), sorted for reading."""
        rows: list[_ProductRow] = []
        for product in sorted(
            added,
            key=lambda p: (
                (p.manufacturer_name or "").lower(),
                (p.name or "").lower(),
                p.order_number or "",
            ),
        ):
            facts = self._catalog_service.product_facts(product.hardware2program_ref_id)
            application = product.application_name or ""
            if facts and facts.is_secure:
                application = (
                    f"{application}  ·  {S.KNXPROD_DETAIL_SECURE}"
                    if application
                    else S.KNXPROD_DETAIL_SECURE
                )
            parsed = parse_app_id(product.application_id or "")
            if parsed is not None:
                version = f"v{parsed.version}"
            elif product.application_version is not None:
                version = f"v{product.application_version}"
            else:
                version = ""
            hardware = [
                part
                for part in (
                    (
                        S.KNXPROD_DETAIL_RAIL
                        if product.is_rail_mounted
                        else S.KNXPROD_DETAIL_FLUSH
                    )
                    if product.is_rail_mounted is not None
                    else "",
                    f"{product.width_mm:g} mm" if product.width_mm else "",
                    f"{product.bus_current:g} mA" if product.bus_current else "",
                    "/".join(facts.mediums) if facts and facts.mediums else "",
                )
                if part
            ]
            registered = [
                part
                for part in (
                    facts.registration_number if facts else None,
                    facts.registration_date.isoformat()
                    if facts and facts.registration_date
                    else None,
                )
                if part
            ]
            rows.append(
                _ProductRow(
                    manufacturer=product.manufacturer_name or product.manufacturer_id,
                    product=product.name
                    or product.application_name
                    or product.product_ref_id,
                    order=product.order_number or "",
                    application=application,
                    version=version,
                    mask=facts.mask_version if facts and facts.mask_version else "",
                    hardware="  ·  ".join(hardware),
                    registered="  ·  ".join(registered),
                )
            )
        return rows

    def _load_knxprod(self, path: str) -> None:
        # A .knxproj is a full project archive, not a product catalog. The file filter is only
        # advisory on macOS, so a .knxproj can be picked in the "Load .knxprod" dialog; route it
        # to the project importer (as "Open Project" already does) so its devices get built.
        # Importing it as a catalog would only extract the embedded product XMLs and then show an
        # empty device tree, which looks like the project loaded but has "no devices".
        if path.lower().endswith(".knxproj"):
            self._prompt_import_dest(path)
            return

        # Legacy ETS3-era product databases (.vd1-.vd5) are not read directly; point the user at
        # ETS's own conversion tools, which turn them into the .knxprod this importer accepts.
        if path.lower().endswith((".vd1", ".vd2", ".vd3", ".vd4", ".vd5", ".vdx")):
            self._knxprod_result = S.LEGACY_VD_HINT
            return

        def worker() -> None:
            # Hold the catalog lock so per-frame catalog reads bail while it writes (see io_guarded).
            with self._catalog_service.io_lock:
                self._log.info("loading knxprod", path=path)
                try:
                    added = self._catalog_service.import_knxprod_contents(Path(path))
                    name = Path(path).name
                    if added:
                        self._log.info(
                            "added applications to catalog", count=len(added)
                        )
                        self._import_rows_result = (
                            S.KNXPROD_IMPORT_SUMMARY.format(
                                count=len(added), name=name
                            ),
                            self._product_import_rows(added),
                        )
                    else:
                        self._log.info("knxprod held no products", path=path)
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

    def _sync_device_section(self) -> None:
        """While a space is selected in the Buildings tree, the Device tab shows it, titled with
        its type (Building, Floor, …), and the Manufacturer tab is hidden and left out of the
        menus; selecting a device again restores both."""
        space = self._project_plugin.selected_space()
        title = (
            space_type_label(space.space_type) or _DOCK_LABELS["device"]()
            if space is not None
            else _DOCK_LABELS["device"]()
        )
        for window in hello_imgui.get_runner_params().docking_params.dockable_windows:
            name = _dock_window_name(window)
            if name == "device":
                label = f"{title}###device"
                if window.label != label:
                    window.label = label
            elif name == "manufacturer":
                window.include_in_view_menu = space is None
                if space is not None and window.is_visible:
                    window.is_visible = False
                    self._manufacturer_hidden = True
                elif space is None and self._manufacturer_hidden:
                    window.is_visible = True
                    self._manufacturer_hidden = False

    def _render_status_bar(self) -> None:
        """The status bar along the bottom of the window, below the docked panels: a thin line on
        top, and as much room above its text as below it. The docked panels end above it."""
        window_params = hello_imgui.get_runner_params().imgui_window_params
        if not self._show_status_bar:
            window_params.full_screen_window_margin_bottom_right = imgui.ImVec2(
                0.0, 0.0
            )
            return
        pad = px(_STATUS_PADDING)
        # One line of text; the idling checkbox of the FPS display fits that line too.
        height = 1.0 + 2 * pad + imgui.get_text_line_height()
        # The margin is in units of the font size.
        window_params.full_screen_window_margin_bottom_right = imgui.ImVec2(
            0.0, height / imgui.get_font_size()
        )
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(
            imgui.ImVec2(viewport.pos.x, viewport.pos.y + viewport.size.y - height)
        )
        imgui.set_next_window_size(imgui.ImVec2(viewport.size.x, height))
        style = imgui.get_style()
        imgui.push_style_var(
            imgui.StyleVar_.window_padding,
            imgui.ImVec2(style.window_padding.x, pad + 1.0),
        )
        imgui.push_style_var(imgui.StyleVar_.window_border_size, 0.0)
        imgui.push_style_var(imgui.StyleVar_.window_rounding, 0.0)
        flags = (
            imgui.WindowFlags_.no_decoration
            | imgui.WindowFlags_.no_move
            | imgui.WindowFlags_.no_saved_settings
            | imgui.WindowFlags_.no_docking
            | imgui.WindowFlags_.no_focus_on_appearing
            | imgui.WindowFlags_.no_bring_to_front_on_focus
            | imgui.WindowFlags_.no_nav
            | imgui.WindowFlags_.no_nav_focus
        )
        if imgui.begin("##status_bar", None, flags)[0]:
            top_left = imgui.get_window_pos()
            top_right = imgui.ImVec2(
                top_left.x + imgui.get_window_width(), top_left.y + 1.0
            )
            draw_list = imgui.get_window_draw_list()
            draw_list.push_clip_rect(top_left, top_right, False)
            draw_list.add_rect_filled(
                top_left, top_right, imgui.get_color_u32(imgui.Col_.border)
            )
            draw_list.pop_clip_rect()
            self.gui_status_bar()
        imgui.end()
        imgui.pop_style_var(3)

    def gui_status_bar(self) -> None:
        self._connection_plugin.render_status_indicator()
        self._status_full = False
        self._status_reserved = (
            self._status_fps_width() if self._show_status_fps else 0.0
        )

        # Bus-operation indicator: a programming/testing operation is in progress.
        busy = self._connection_service.busy_operation
        if busy is not None:
            kind, address = busy
            if kind == "program":
                text = S.STATUS_PROGRAMMING.format(address=address)
                prog = self._connection_service.busy_progress
                if prog is not None:
                    text += f" ({prog[0]}/{prog[1]})"
            elif kind == "device_info":
                text = S.STATUS_READING.format(address=address)
            else:
                text = S.STATUS_TESTING.format(address=address)
            self._status_part(text, imgui.ImVec4(0.95, 0.75, 0.2, 1.0))
        elif self._connection_service.not_connected_notice():
            # A connection-requiring feature (program/test/send) was just refused:
            # flash the reason so the user sees *why* nothing happened.
            self._status_part(
                _connection_strings.STATUS_BUSMONITOR_BLOCKED
                if self._connection_service.busmonitor_active
                else S.STATUS_NO_CONNECTION,
                imgui.ImVec4(0.9, 0.35, 0.35, 1.0),
            )
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
                self._status_part(
                    S.STATUS_PROGRAM_DONE if ok else S.STATUS_PROGRAM_FAILED, color
                )

        # Current project summary.
        if self._project_service.is_open:
            meta = self._project_service.get_project_metadata()
            name = meta.name if meta and meta.name else None
            if not name and self._project_service.path is not None:
                name = self._project_service.path.name
            self._status_part(
                S.STATUS_PROJECT.format(
                    name=name or "?",
                    devices=len(self._project_service.devices),
                    gas=len(self._project_service.group_addresses),
                )
            )
        else:
            self._status_part(S.STATUS_NO_PROJECT)

        if self._mcp_plugin.is_running:
            self._status_part(S.STATUS_MCP_RUNNING, imgui.ImVec4(0.4, 0.85, 0.45, 1.0))

        # KNX master data (mask-version procedures) version and date.
        if self._master_info is not None:
            self._status_part(
                S.STATUS_MASTER_DATA.format(
                    version=self._master_info.version,
                    date=self._master_info.date,
                )
            )

        if self._show_status_fps:
            self._render_status_fps()

    def _status_part(self, text: str, color: imgui.ImVec4 | None = None) -> None:
        """The next part of the status bar after a separator, greyed without ``color``. In a
        narrow window the part that no longer fits is shortened (all of it in its tooltip) and
        the parts after it are left out."""
        if self._status_full:
            return
        imgui.same_line()
        style = imgui.get_style()
        separator = " | "
        room = (
            imgui.get_window_width()
            - style.window_padding.x
            - imgui.get_cursor_pos_x()
            - self._status_reserved
            - imgui.calc_text_size(separator).x
            - style.item_spacing.x
        )
        if room < imgui.calc_text_size("MMM…").x:
            self._status_full = True
            return
        imgui.text_disabled(separator)
        imgui.same_line()
        shown = fit_text(text, room)
        if shown != text:
            self._status_full = True
        if color is None:
            imgui.text_disabled(shown)
        else:
            imgui.text_colored(color, shown)
        if shown != text and imgui.is_item_hovered():
            imgui.set_tooltip(text)

    def _status_fps_width(self) -> float:
        """Width of the FPS display at the right of the status bar, its idling toggle included."""
        style = imgui.get_style()
        width = imgui.calc_text_size(
            S.STATUS_FPS.format(fps=999.9, idling=S.STATUS_IDLING)
        ).x
        if web.active_hub is None:
            width += (
                imgui.get_text_line_height()
                + style.frame_padding.y / 2
                + style.item_inner_spacing.x
                + imgui.calc_text_size(S.MENU_ENABLE_IDLING).x
                + style.item_spacing.x
            )
        return width

    def _render_status_fps(self) -> None:
        """FPS and the idling toggle, right-aligned in the status bar. The web mode paces its frames
        itself, so it has no idling to toggle: only the FPS then."""
        toggle = web.active_hub is None
        idling = hello_imgui.get_runner_params().fps_idling
        fps = S.STATUS_FPS.format(
            fps=hello_imgui.frame_rate(),
            idling=S.STATUS_IDLING if idling.is_idling else "",
        )
        style = imgui.get_style()
        width = self._status_fps_width()
        imgui.same_line(
            max(imgui.get_window_width() - width - style.window_padding.x, 0.0)
        )
        if toggle:
            # The checkbox, a little higher than the text, centred on the text line so the status
            # bar keeps its height.
            imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - style.frame_padding.y / 4)
            _, idling.enable_idling = checkbox(
                S.MENU_ENABLE_IDLING, idling.enable_idling
            )
            imgui.same_line()
        imgui.text_disabled(fps)

    def _render_menu_separator(self) -> None:
        """The line between the menu bar and the docked panels, drawn from the menu bar like the
        line above the status bar: 1 px in the border color over the window background. A menu
        bar in a theme with window borders ends in its own 1 px border, which the line covers; the
        panels start below the rest of it."""
        style = imgui.get_style()
        gap = 1.0
        own = 1.0 if style.window_border_size > 0 else 0.0
        window_params = hello_imgui.get_runner_params().imgui_window_params
        # The margin is in units of the font size.
        window_params.full_screen_window_margin_top_left = imgui.ImVec2(
            0.0, (gap - own) / imgui.get_font_size()
        )
        pos, size = imgui.get_window_pos(), imgui.get_window_size()
        top = pos.y + size.y - own
        top_left = imgui.ImVec2(pos.x, top)
        bottom_right = imgui.ImVec2(pos.x + size.x, top + gap)
        draw_list = imgui.get_window_draw_list()
        draw_list.push_clip_rect(top_left, bottom_right, False)
        draw_list.add_rect_filled(
            top_left, bottom_right, imgui.get_color_u32(imgui.Col_.window_bg)
        )
        draw_list.add_rect_filled(
            top_left, bottom_right, imgui.get_color_u32(imgui.Col_.border)
        )
        draw_list.pop_clip_rect()

    def gui_menu(self) -> None:
        # The menu bar is not a window to switch to with Ctrl+Tab (it would be listed there with
        # an untranslated name).
        imgui.internal.get_current_window().flags |= imgui.WindowFlags_.no_nav_focus
        self._render_menu_separator()
        # Where the menus do not fit side by side (a phone), they are all in one menu. Their
        # width is measured while they are side by side, once per language.
        needed = self._menus_width.get(get_locale())
        collapse = needed is not None and needed > imgui.get_window_width()
        if not collapse:
            self._render_menus()
            style = imgui.get_style()
            self._menus_width[get_locale()] = (
                imgui.get_item_rect_max().x
                - imgui.get_window_pos().x
                + style.item_spacing.x
                + style.window_padding.x
            )
        elif imgui.begin_menu(f"{_ICON_MENU}##all_menus"):
            self._render_menus()
            imgui.end_menu()

        self._ui_executor.drain()
        self._poll_dialogs()
        self._handle_shortcuts()

    def _render_menus(self) -> None:
        """The menus: in the menu bar, or as submenus of the one menu (see gui_menu)."""
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
            if imgui.menu_item(S.MENU_COMPACT_PROJECT, "", False, self._can_compact())[
                0
            ]:
                self._start_compact_analysis()
            imgui.separator()
            if imgui.menu_item(
                S.MENU_EXPORT_GA_EXPORT,
                "",
                False,
                self._project_service.is_open,
            )[0]:
                self._export_ga_export()
            if imgui.menu_item(
                S.MENU_IMPORT_GA,
                "",
                False,
                self._project_service.is_open and self._can_launch_file_dialog(),
            )[0]:
                self._project_plugin.import_group_addresses()
            imgui.separator()
            if (
                imgui.menu_item(S.MENU_LOAD_KNXPROD, "", False)[0]
                and self._can_launch_file_dialog()
            ):
                self._open_file_dialog = file_dialogs.open_file(
                    S.FILE_DIALOG_KNXPROD_TITLE,
                    "",
                    [
                        S.FILE_DIALOG_KNXPROD_FILTER,
                        "*.knxprod *.vd1 *.vd2 *.vd3 *.vd4 *.vd5 *.vdx",
                        S.FILE_DIALOG_ALL_FILES,
                        "*",
                    ],
                )
            load_from_url = (
                S.MENU_LOAD_FROM_URL_FILES
                if web.active_hub is not None
                else S.MENU_LOAD_FROM_URL
            )
            if imgui.menu_item(load_from_url, "", False)[0]:
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

        self._render_view_menu()

        self._connection_plugin.render_menu()
        self._render_mcp_menu()
        self._keyring_plugin.render_menu()
        self._signing_plugin.render_menu()

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
            if imgui.menu_item(S.MENU_OPEN_LOG_DIR, "", False)[0]:
                self._open_folder(log_dir())
            imgui.separator()
            if imgui.menu_item(S.MENU_OPEN_CACHE_DIR, "", False)[0]:
                self._open_folder(cache_dir())
            if imgui.menu_item(S.MENU_CLEAR_CACHE, "", False)[0]:
                self._clear_cache_size = cache_size()
                self._clear_cache_requested = True
            imgui.end_menu()

        if imgui.menu_item(S.MENU_FEEDBACK, "", False)[0]:
            self._open_feedback()

    def _open_feedback(self) -> None:
        """Open the project's GitHub issues page in the default browser."""
        url = "https://github.com/xknx/xknx-editor/issues"
        try:
            open_url(url)
        except Exception as e:
            self._log.error("could not open feedback page", url=url, error=str(e))

    def _open_folder(self, path: Path) -> None:
        """Reveal a directory in the OS file manager (created if missing); in the browser UI, list
        its files for download."""
        if web.active_hub is not None:
            path.mkdir(parents=True, exist_ok=True)
            web.active_hub.show_folder(path)
            return
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
        # A finished catalog import shows a modal: a detailed product table on success, else a short
        # message ("nothing new" / error). The worker hands the result over via _knxprod_result /
        # _import_rows_result; latch into _knxprod_msg / _import_rows, which stay set until OK.
        if self._knxprod_result is not None:
            self._knxprod_msg = self._knxprod_result
            self._knxprod_result = None
        if self._import_rows_result is not None:
            self._import_rows = self._import_rows_result
            self._import_rows_result = None
        if self._knxprod_msg is None and self._import_rows is None:
            return
        if not imgui.is_popup_open(S.KNXPROD_IMPORT_TITLE):
            # Don't steal the stage from another modal (e.g. an auto-update prompt at the same popup
            # depth) — opening ours there would dismiss it. Wait until no popup is open, then raise
            # ours. If ours was itself displaced, this re-opens it once the other closes, so the
            # result is never silently lost.
            if imgui.is_popup_open("", imgui.PopupFlags_.any_popup):
                return
            imgui.open_popup(S.KNXPROD_IMPORT_TITLE)
        width = 1000.0 if self._import_rows is not None else 560.0
        if not begin_dialog(S.KNXPROD_IMPORT_TITLE, width=width):
            return
        if self._import_rows is not None:
            header, product_rows = self._import_rows
            imgui.text_wrapped(header)
            imgui.spacing()
            self._render_product_table(product_rows)
        else:
            assert self._knxprod_msg is not None
            line_h = imgui.get_text_line_height_with_spacing()
            lines = self._knxprod_msg.count("\n") + 1
            height = min(px(380.0), lines * line_h + px(8.0))
            if imgui.begin_child("##knxprod_result_body", imgui.ImVec2(0.0, height)):
                imgui.push_text_wrap_pos(0.0)
                imgui.text_unformatted(self._knxprod_msg)
                imgui.pop_text_wrap_pos()
            imgui.end_child()
        if dialog_buttons(S.BTN_OK) == 0:
            self._knxprod_msg = None
            self._import_rows = None
            imgui.close_current_popup()
        end_dialog()

    def _render_product_table(self, rows: list[_ProductRow]) -> None:
        """The imported product versions as a one-row-per-product table. Short columns keep a tight
        fixed width; the free-text columns stretch to fill the dialog so it needs no sideways scroll
        (the wrapper still scrolls sideways on a narrow phone window)."""
        # (label, getter, stretch): a stretching column's width follows the dialog; a fixed one
        # keeps the px width below. Overflow in any cell is clipped with a hover tooltip.
        columns: tuple[tuple[str, Callable[[_ProductRow], str], bool, float], ...] = (
            (S.KNXPROD_COL_MANUFACTURER, lambda r: r.manufacturer, False, 95.0),
            (S.KNXPROD_COL_PRODUCT, lambda r: r.product, True, 1.4),
            (S.KNXPROD_COL_ORDER, lambda r: r.order, False, 115.0),
            (S.KNXPROD_COL_APPLICATION, lambda r: r.application, True, 1.4),
            (S.KNXPROD_COL_VERSION, lambda r: r.version, False, 50.0),
            (S.KNXPROD_COL_MASK, lambda r: r.mask, False, 72.0),
            (S.KNXPROD_COL_HARDWARE, lambda r: r.hardware, True, 1.0),
            (S.KNXPROD_COL_REGISTERED, lambda r: r.registered, False, 140.0),
        )
        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.reorderable
            | imgui.TableFlags_.scroll_y
        )
        height = min(px(380.0), (len(rows) + 2) * imgui.get_frame_height())
        if not begin_data_table(
            "##product_import", len(columns), flags, imgui.ImVec2(0.0, height)
        ):
            return
        for label, _get, stretch, size in columns:
            if stretch:
                imgui.table_setup_column(
                    label, imgui.TableColumnFlags_.width_stretch, size
                )
            else:
                imgui.table_setup_column(
                    label, imgui.TableColumnFlags_.width_fixed, px(size)
                )
        data_table_headers()
        for row in rows:
            imgui.table_next_row()
            for index, (_label, get, _stretch, _size) in enumerate(columns):
                imgui.table_set_column_index(index)
                text_clipped_tooltip(get(row))
        imgui.end_table()

    def _render_about_modal(self) -> None:
        if self._about_requested:
            imgui.open_popup(S.ABOUT_TITLE)
            self._about_requested = False
        if not begin_dialog(S.ABOUT_TITLE, width=420.0):
            return
        imgui.text(S.APP_TITLE)
        imgui.text_disabled(S.ABOUT_VERSION.format(version=__version__))
        imgui.spacing()
        imgui.text_wrapped(S.ABOUT_TEXT)
        imgui.spacing()
        imgui.text_disabled(S.ABOUT_LICENSE)
        imgui.text(S.ABOUT_WEBSITE)
        imgui.same_line()
        _link(_REPO_URL)
        clicked = dialog_buttons(S.MENU_CHECK_UPDATES, S.BTN_OK)
        if clicked == 0:
            self._start_update_check(manual=True)
        elif clicked == 1:
            imgui.close_current_popup()
        # The check's dialogs open on top of this one, which stays open behind them.
        self._render_update_check_modal()
        self._render_update_modal()
        end_dialog()

    def _render_update_check_modal(self) -> None:
        """A user-triggered update check: running, then up to date or why it failed. A found update
        replaces it with the update dialog."""
        if self._update_check_open_requested:
            imgui.open_popup(S.MENU_CHECK_UPDATES)
            self._update_check_open_requested = False
        if not begin_dialog(S.MENU_CHECK_UPDATES, width=420.0):
            return
        state = self._update_check_state
        if state is None:
            imgui.close_current_popup()
            end_dialog()
            return
        if state == "checking":
            imgui.text(S.UPDATE_CHECKING)
        elif state == "current":
            imgui.text_wrapped(S.UPDATE_UP_TO_DATE.format(version=__version__))
        else:
            imgui.text_wrapped(
                S.UPDATE_CHECK_FAILED.format(error=self._update_check_error)
            )
        label = S.BTN_CANCEL if state == "checking" else S.BTN_OK
        if dialog_buttons(label) == 0:
            self._update_check_state = None
            imgui.close_current_popup()
        end_dialog()

    def _render_update_modal(self) -> None:
        if self._update_prompt_requested:
            imgui.open_popup(S.UPDATE_TITLE)
            self._update_prompt_requested = False
        if not begin_dialog(S.UPDATE_TITLE, width=460.0):
            return
        info = self._update_info
        if info is None:
            end_dialog()
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
        installer = self._update_installer(info)
        if installer is not None:
            imgui.spacing()
            imgui.text_wrapped(S.UPDATE_INSTALL_HINT)
        clicked = dialog_buttons(
            S.UPDATE_INSTALL if installer is not None else S.UPDATE_DOWNLOAD,
            S.UPDATE_SKIP,
            S.UPDATE_LATER,
        )
        if clicked == 0 and installer is not None:
            self._install_update(installer)
            imgui.close_current_popup()
        elif clicked == 0:
            try:
                open_url(info.url)
            except Exception as e:
                self._log.error(
                    "could not open release page", url=info.url, error=str(e)
                )
            imgui.close_current_popup()
        elif clicked == 1:
            self._skip_update_version(info.version)
            imgui.close_current_popup()
        elif clicked == 2:
            imgui.close_current_popup()
        end_dialog()

    def _update_installer(self, info: UpdateInfo) -> ReleaseAsset | None:
        """The release's installer for this machine, when this installation updates in place."""
        if self._update_scope is None:
            return None
        return self_update.pick_installer(info.assets, platform.machine())

    def _install_update(self, installer: ReleaseAsset) -> None:
        """Download ``installer`` behind the progress modal, then run it and exit; it restarts the
        app with the project that is open now."""
        scope = self._update_scope
        if scope is None:
            return
        target = Path(tempfile.mkdtemp(prefix="xknx-update-")) / installer.name
        project = self._project_service.path if self._project_service.is_open else None
        open_project = str(project) if project is not None else None
        megabyte = 1024 * 1024

        def report(done: int, total: int) -> None:
            self.set_progress(
                done / total if total else None,
                S.UPDATE_DOWNLOAD_PROGRESS.format(
                    done=done // megabyte, total=total // megabyte
                )
                if total
                else "",
            )

        def worker() -> None:
            try:
                self_update.download(installer, target, report)
            except Exception as e:
                self._log.error(
                    "update could not be downloaded", url=installer.url, error=str(e)
                )
                return
            self._ui_executor.submit(
                lambda: self._start_update(target, scope, open_project)
            )

        self._run_bg(S.PROGRESS_DOWNLOAD_UPDATE, worker)

    def _start_update(
        self, installer: Path, scope: str, open_project: str | None
    ) -> None:
        try:
            self_update.start_installer(
                installer, self_update.installer_arguments(scope, open_project)
            )
        except OSError as e:
            self._log.error(
                "could not start the installer", path=str(installer), error=str(e)
            )
            return
        self._log.info("update installer started", path=str(installer))
        hello_imgui.get_runner_params().app_shall_exit = True

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
        if not begin_dialog(S.IMPORT_NOTES_TITLE):
            return
        if self._import_notes_intro:
            imgui.text_wrapped(self._import_notes_intro)
            imgui.spacing()
        for note in self._import_notes:
            imgui.bullet()
            imgui.text_wrapped(self._import_note_text(note))
        if dialog_buttons(S.IMPORT_NOTES_CLOSE) == 0:
            imgui.close_current_popup()
        end_dialog()

    def _render_export_summary_modal(self) -> None:
        """A success window shown after a completed .knxproj export, listing the archive's details:
        file size, device/building/group-address counts, bundled manufacturer folders, and whether
        it carries a folder signature and a MyKnx certificate."""
        if self._export_summary_requested:
            # Devices whose product data is too new for the chosen ETS (computed once here, not per
            # frame) so the window can name them and offer to remove them.
            self._export_summary_incompat_rows = (
                self._schema_incompat_devices(
                    self._export_summary.schema_incompatible_products
                )
                if self._export_summary is not None
                else []
            )
            imgui.open_popup(S.EXPORT_SUMMARY_TITLE)
            self._export_summary_requested = False
        imgui.set_next_window_size_constraints(
            px_vec2(420.0, 0.0), imgui.ImVec2(1.0e9, 1.0e9)
        )
        if not begin_dialog(S.EXPORT_SUMMARY_TITLE):
            return
        summary = self._export_summary
        if summary is None:
            end_dialog()
            return
        imgui.text_wrapped(
            S.EXPORT_SUMMARY_INTRO.format(name=Path(self._export_summary_path).name)
        )
        imgui.spacing()
        yes, no = S.EXPORT_SUMMARY_YES, S.EXPORT_SUMMARY_NO
        rows = [
            (S.EXPORT_SUMMARY_PROJECT, summary.project_name),
            (S.EXPORT_SUMMARY_FILE_SIZE, human_size(summary.file_size)),
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
        incompatible = bool(summary.schema_incompatible_products)
        if incompatible:
            # The chosen ETS version cannot read some device's product data; this file will not
            # import. Flag it in red and list the affected devices, with a remove-and-re-export
            # action below.
            imgui.spacing()
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.92, 0.33, 0.33, 1.0))
            imgui.text_wrapped(S.EXPORT_SUMMARY_INCOMPAT)
            imgui.pop_style_color()
            # Only the list scrolls when it is long: the dialog with its buttons stays on screen.
            listed = self._export_summary_incompat_rows
            line_h = imgui.get_text_line_height_with_spacing()
            height = min(len(listed) * line_h, dialog_room(3 * line_h / px(1.0)))
            imgui.push_style_color(
                imgui.Col_.child_bg, imgui.ImVec4(0.0, 0.0, 0.0, 0.0)
            )
            if imgui.begin_child(
                "##export_incompat_devices",
                imgui.ImVec2(0.0, height),
                imgui.ChildFlags_.auto_resize_x,
            ):
                for _nid, label in listed:
                    imgui.bullet_text(label)
            imgui.end_child()
            imgui.pop_style_color()
        if incompatible:
            labels = [
                S.EXPORT_INCOMPAT_REMOVE,
                S.EXPORT_SUMMARY_OPEN_FOLDER,
                S.EXPORT_SUMMARY_CLOSE,
            ]
            clicked = dialog_buttons(*labels)
            if clicked == 0:
                removed = self._export_summary_incompat_rows
                dest, signer = self._export_summary_path, self._export_summary_signer
                imgui.close_current_popup()
                if removed:
                    for nid, _label in removed:
                        self._project_service.remove_device(nid)
                    self._flash_toast(
                        S.EXPORT_INCOMPAT_REMOVED.format(count=len(removed))
                    )
                    self._do_export_knxproj(dest, signer)
            elif clicked == 1:
                self._open_folder(Path(self._export_summary_path).parent)
            elif clicked == 2:
                imgui.close_current_popup()
        else:
            clicked = dialog_buttons(
                S.EXPORT_SUMMARY_OPEN_FOLDER, S.EXPORT_SUMMARY_CLOSE
            )
            if clicked == 0:
                self._open_folder(Path(self._export_summary_path).parent)
            elif clicked == 1:
                imgui.close_current_popup()
        end_dialog()

    def _render_export_failure_modal(self) -> None:
        """The Export Failed window: which file, why, and for a certificate that could not be
        obtained the ways to export without one."""
        if self._export_failure_requested:
            imgui.open_popup(S.EXPORT_FAILED_TITLE)
            self._export_failure_requested = False
        if not begin_dialog(S.EXPORT_FAILED_TITLE, width=520.0):
            return
        failure = self._export_failure
        if failure is None:
            imgui.close_current_popup()
            end_dialog()
            return
        text, reason, signing = failure
        with heading():
            imgui.text_wrapped(text)
        imgui.spacing()
        if signing:
            imgui.text_wrapped(S.EXPORT_FAILED_CERTIFICATE)
        imgui.text_wrapped(reason)
        if signing:
            imgui.spacing()
            imgui.text_wrapped(S.EXPORT_FAILED_CERTIFICATE_HINT)
        if dialog_buttons(S.EXPORT_SUMMARY_CLOSE) == 0:
            self._export_failure = None
            imgui.close_current_popup()
        end_dialog()

    # --- compact project ---------------------------------------------------

    def _can_compact(self) -> bool:
        """Compact needs an open project and no other background task (it rewrites the file)."""
        return (
            self._project_service.is_open
            and self._bg_thread is None
            and self._import_thread is None
            and not (self._myknx_thread is not None and self._myknx_thread.is_alive())
        )

    def _compact_progress(
        self, step: str, done: int, total: int, *, applying: bool
    ) -> None:
        """Map a compaction step to the progress modal: the product data analysis fills the bar (up
        to 80 percent when applying), storing follows, compacting the file runs indeterminate."""
        if step == STEP_DEVICES:
            self.set_progress(0.0, S.COMPACT_STEP_DEVICES)
        elif step.startswith(f"{STEP_MANUFACTURER}:"):
            mid = step.split(":", 1)[1]
            text = S.COMPACT_STEP_REMOVE if applying else S.COMPACT_STEP_ANALYSE
            share = 0.8 if applying else 1.0
            self.set_progress(
                share * done / total if total else None,
                text.format(manufacturer=mid),
            )
        elif step == STEP_STORE:
            self._progress_cancel = None
            self.set_progress(0.85, S.COMPACT_STEP_STORE)
        elif step == STEP_VACUUM:
            self.set_progress(1.0 if done else None, S.COMPACT_STEP_VACUUM)

    def _start_compact_analysis(self) -> None:
        """Analyse in the background what Compact Project would remove, then ask to confirm."""
        if not self._can_compact():
            return

        def worker() -> None:
            result = self._project_service.analyse_compaction(
                lambda step, done, total: self._compact_progress(
                    step, done, total, applying=False
                )
            )
            if result is not None:
                self._compact_analysis = result
                self._compact_confirm_requested = True

        self._run_bg(S.PROGRESS_COMPACT_ANALYSE, worker)

    def _start_compact(self) -> None:
        """Compact the project in the background; cancellable until it starts writing."""
        if not self._can_compact():
            return
        self._compact_cancel = False

        def worker() -> None:
            try:
                result = self._project_service.compact(
                    lambda step, done, total: self._compact_progress(
                        step, done, total, applying=True
                    ),
                    cancelled=lambda: self._compact_cancel,
                )
            except CompactCancelled:
                self._log.info("compact project cancelled")
                self._flash_toast(S.COMPACT_CANCELLED)
                return
            if result is not None:
                self._compact_result = result
                self._compact_result_requested = True

        self._run_bg(S.PROGRESS_COMPACT, worker)
        self._progress_cancel = self._request_compact_cancel

    def _request_compact_cancel(self) -> None:
        self._compact_cancel = True

    def _render_compact_modal(self) -> None:
        """Confirm Compact Project: what will be removed, the file sizes and the warning."""
        if self._compact_confirm_requested:
            imgui.open_popup(S.COMPACT_TITLE)
            self._compact_confirm_requested = False
        if not begin_dialog(S.COMPACT_TITLE, width=600.0):
            return
        analysis = self._compact_analysis
        if analysis is None:
            imgui.close_current_popup()
            end_dialog()
            return
        report = analysis.report
        if report.nothing_removed:
            imgui.text_wrapped(S.COMPACT_NOTHING_UNUSED)
        else:
            imgui.text_wrapped(S.COMPACT_INTRO)
            imgui.spacing()
            counts = [
                (S.COMPACT_APPLICATIONS, report.application_programs),
                (S.COMPACT_PRODUCTS, report.products),
                (S.COMPACT_MANUFACTURERS, report.manufacturers),
            ]
            if imgui.begin_table(
                "##compact_counts", 2, imgui.TableFlags_.sizing_fixed_fit
            ):
                for label, items in counts:
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.text_disabled(label)
                    imgui.table_next_column()
                    imgui.text(str(len(items)))
                imgui.end_table()
            if collapsing_header(S.COMPACT_DETAILS):
                details = [
                    *counts,
                    (S.COMPACT_HARDWARE, report.hardware),
                    (S.COMPACT_CATALOG_ITEMS, report.catalog_items),
                    (S.COMPACT_BAGGAGES, report.baggages),
                ]
                master = self._connection_service.master
                names = {
                    **(dict(master.manufacturers) if master is not None else {}),
                    **report.names,
                }
                imgui.begin_child("##compact_details", px_vec2(0.0, 220.0))
                for label, items in details:
                    if not items:
                        continue
                    imgui.text_disabled(f"{label} ({len(items)})")
                    for item in items:
                        name = names.get(item)
                        if name:
                            imgui.bullet_text(name)
                            imgui.same_line()
                            imgui.text_disabled(item)
                        else:
                            imgui.bullet_text(item)
                imgui.end_child()
        imgui.spacing()
        if imgui.begin_table("##compact_sizes", 2, imgui.TableFlags_.sizing_fixed_fit):
            for label, size in (
                (S.COMPACT_SIZE_NOW, analysis.file_size_before),
                (S.COMPACT_SIZE_ESTIMATE, analysis.file_size_after),
            ):
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text_disabled(label)
                imgui.table_next_column()
                imgui.text(human_size(size))
            imgui.end_table()
        if not report.nothing_removed:
            imgui.spacing()
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(0.85, 0.7, 0.3, 1.0))
            imgui.text_wrapped(S.COMPACT_WARNING)
            imgui.pop_style_color()
        clicked = dialog_buttons(S.COMPACT_CONFIRM, S.BTN_CANCEL)
        if clicked == 0:
            self._compact_analysis = None
            imgui.close_current_popup()
            self._start_compact()
        elif clicked == 1:
            self._compact_analysis = None
            imgui.close_current_popup()
        end_dialog()

    def _render_compact_result_modal(self) -> None:
        """Show the file size before and after Compact Project."""
        if self._compact_result_requested:
            imgui.open_popup(S.COMPACT_RESULT_TITLE)
            self._compact_result_requested = False
        imgui.set_next_window_size_constraints(
            px_vec2(420.0, 0.0), imgui.ImVec2(1.0e9, 1.0e9)
        )
        if not begin_dialog(S.COMPACT_RESULT_TITLE):
            return
        result = self._compact_result
        if result is None:
            imgui.close_current_popup()
            end_dialog()
            return
        imgui.text_wrapped(S.COMPACT_RESULT_TEXT)
        imgui.spacing()
        report = result.report
        rows = [
            (S.COMPACT_SIZE_BEFORE, human_size(result.file_size_before)),
            (S.COMPACT_SIZE_AFTER, human_size(result.file_size_after)),
            (S.COMPACT_APPLICATIONS, str(len(report.application_programs))),
            (S.COMPACT_PRODUCTS, str(len(report.products))),
            (S.COMPACT_MANUFACTURERS, str(len(report.manufacturers))),
        ]
        if imgui.begin_table("##compact_result", 2, imgui.TableFlags_.sizing_fixed_fit):
            for label, value in rows:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text_disabled(label)
                imgui.table_next_column()
                imgui.text(value)
            imgui.end_table()
        if dialog_buttons(S.COMPACT_CLOSE) == 0:
            self._compact_result = None
            imgui.close_current_popup()
        end_dialog()

    def watch_db_frame(self) -> None:
        """Dev watchdog (XKNX_DEBUG_DB_FRAMES=1): log when the render thread keeps issuing DB queries
        across frames in which the project did not change — an uncached per-frame read in a panel."""
        msg = frame_db_profiler.end_frame(self._project_service.revision)
        if msg:
            self._log.warning(msg)

    def _toggle_bottom_expanded(self) -> None:
        if self._form != "wide":
            # The smaller layouts have no section across the full width: only the columns
            # change, and the usual layout has it when it is shown again.
            expanded = not self.bottom_expanded
            self._monitor_plugin.set_monitor_extended(expanded)
            self.bottom_expanded = expanded
            self._wide_layout["expanded"] = expanded
            data = load_settings("app")
            data["bottom_expanded"] = expanded
            data["wide_layout"] = self._wide_layout
            save_settings("app", data)
            return
        params = hello_imgui.get_runner_params().docking_params
        _keep_window_sections(params)
        expanded = not self.bottom_expanded
        self._rebuild_layout(expanded, _current_ratios(params, expanded))

    def _rebuild_layout(self, expanded: bool, ratios: dict[str, float]) -> None:
        """Rebuild the layout; the expanded bottom section shows every Group Monitor column."""
        params = hello_imgui.get_runner_params().docking_params
        params.docking_splits = create_docking_splits(expanded, ratios)
        params.layout_reset = True
        self._monitor_plugin.set_monitor_extended(expanded)
        if expanded != self.bottom_expanded:
            self.bottom_expanded = expanded
            data = load_settings("app")
            data["bottom_expanded"] = expanded
            save_settings("app", data)

    def _update_web_layout(self) -> None:
        """In the browser UI: switch to the layout for the size the app is shown in, once that
        size has settled; not while typing (a touch screen's keyboard makes the app shorter for
        a while) or with a button held."""
        io = imgui.get_io()
        wanted = form_factor(io.display_size.x, io.display_size.y, self._form)
        if wanted == self._form:
            self._form_since = None
            now = time.monotonic()
            if wanted == "wide" and now - self._wide_measured >= 1.0:
                self._wide_measured = now
                params = hello_imgui.get_runner_params().docking_params
                self._wide_ratios = _current_ratios(params, self.bottom_expanded)
            return
        if io.want_text_input or any(imgui.is_mouse_down(b) for b in range(3)):
            return
        now = time.monotonic()
        if self._form_since is None:
            self._form_since = now
        if now - self._form_since < _LAYOUT_SETTLE:
            return
        self._form_since = None
        self._show_form(wanted)

    def _show_form(self, form: str) -> None:
        params = hello_imgui.get_runner_params().docking_params
        _keep_window_sections(params)
        if self._form == "wide":
            # Shown again when there is room, also after a restart (see _restore_wide_layout).
            self._wide_layout = {
                "docks": {
                    _dock_window_name(w): w.dock_space_name
                    for w in params.dockable_windows
                },
                "ratios": self._wide_ratios
                or _current_ratios(params, self.bottom_expanded),
                "expanded": self.bottom_expanded,
                "selected": [
                    _dock_window_name(w)
                    for w in params.dockable_windows
                    if w.focus_window_at_next_frame
                ],
            }
        from_wide = self._form == "wide"
        self._form = form
        key, default = self._context_help_setting()
        self._project_plugin.show_context_help = bool(
            load_settings("app").get(key, default)
        )
        data = load_settings("app")
        if form == "wide":
            data.pop("wide_layout", None)
            save_settings("app", data)
            _restore_wide_layout(params, self._wide_layout)
            self._order_tabs(params)
            return
        data["wide_layout"] = self._wide_layout
        save_settings("app", data)
        self._show_compact_layout(form, from_wide=from_wide)

    def _context_help_setting(self) -> tuple[str, bool]:
        """The setting whether the editor shows its context help, and its default: on a phone
        (navigation and one section) apart and off at first, as there is little room."""
        if self._form in ("rows", "columns"):
            return "show_context_help_phone", False
        return "show_context_help", True

    def _show_compact_layout(self, form: str, *, from_wide: bool) -> None:
        """Build the smaller layout ``form``; its sections take the panels of the usual layout's
        sections (see compact_section). Coming ``from_wide``, each shows the tab its usual
        section of the same name showed; otherwise the tabs shown stay."""
        params = hello_imgui.get_runner_params().docking_params
        docks: dict[str, str] = self._wide_layout.get("docks", {})
        selected: set[str] = set(self._wide_layout.get("selected") or [])
        for w in params.dockable_windows:
            name = _dock_window_name(w)
            wide = docks.get(name, w.dock_space_name)
            w.dock_space_name = compact_section(wide, form)
            if from_wide:
                w.focus_window_at_next_frame = (
                    name in selected and w.dock_space_name == wide
                )
        # A section's tabs come in the order its windows are docked: a shared section lists the
        # main section's tabs first, then those of the top right, the bottom right and the
        # bottom left one (the usual sections keep their own order).
        rank = {section: i for i, section in enumerate(_SECTION_ORDER)}
        params.dockable_windows = sorted(
            params.dockable_windows,
            key=lambda w: rank.get(
                docks.get(_dock_window_name(w), w.dock_space_name), len(rank)
            ),
        )
        # A shared section shows the first of the tabs its sections showed (the main one's).
        shown: set[str] = set()
        for w in params.dockable_windows:
            if w.focus_window_at_next_frame:
                if w.dock_space_name in shown:
                    w.focus_window_at_next_frame = False
                shown.add(w.dock_space_name)
        _forget_tab_order(params)
        self._order_tabs(params)
        params.docking_splits = compact_docking_splits(form)
        params.layout_reset = True

    def _order_tabs(self, params: hello_imgui.DockingParams) -> None:
        """Have the tabs of the layout being built come in the order of the dockable windows."""
        self._tab_order = [_dock_window_name(w) for w in params.dockable_windows]
        # hello_imgui builds it in the next frame.
        self._tab_order_from = imgui.get_frame_count() + 2
        self._tab_order_until = imgui.get_frame_count() + 300

    def _settle_tab_order(self) -> None:
        """After a layout switch: move the tabs of each section into the order of the dockable
        windows, one tab per section and frame. Imgui keeps the place a window's tab had in a
        section whose id the new layout uses again, so the order of docking is not enough."""
        if not self._tab_order or imgui.get_frame_count() < self._tab_order_from:
            return
        if imgui.get_frame_count() > self._tab_order_until:
            self._tab_order = []
            return
        rank = {name: i for i, name in enumerate(self._tab_order)}
        internal = imgui.internal
        seen: set[int] = set()
        settled = True
        for w in hello_imgui.get_runner_params().docking_params.dockable_windows:
            window = internal.find_window_by_name(w.label)
            # The bindings type these as non-optional, but they can be None.
            node = window.dock_node if window is not None else None  # pyright: ignore[reportUnnecessaryComparison]
            bar = node.tab_bar if node is not None else None  # pyright: ignore[reportUnnecessaryComparison]
            if bar is None or bar.id_ in seen:  # pyright: ignore[reportUnnecessaryComparison]
                continue
            seen.add(bar.id_)
            tabs = list(bar.tabs)
            places = [
                i
                for i, tab in enumerate(tabs)
                if tab.window is not None  # pyright: ignore[reportUnnecessaryComparison]
                and tab.window.name.split("###")[-1] in rank
            ]
            wanted = sorted(
                places, key=lambda i: rank[tabs[i].window.name.split("###")[-1]]
            )
            for place, tab_index in zip(places, wanted, strict=True):
                if place != tab_index:
                    internal.tab_bar_queue_reorder(
                        bar, tabs[tab_index], place - tab_index
                    )
                    settled = False
                    break
        if settled and seen:
            self._tab_order = []

    def render_overlays(self) -> None:
        if web.active_hub is not None:
            self._update_web_layout()
        self._handle_editor_requests()
        self._settle_tab_order()
        self._project_service.take_rechecked_devices()
        self._sync_device_section()
        self._render_status_bar()
        render_section_menus()
        set_window_title(self._window_title())
        # Log the active OpenGL renderer once, from the frame path (context guaranteed current).
        # Skip it in the windowless web mode: its null backend has no GL context, so a context-less
        # glGetString would segfault at the C level (uncatchable) and crash startup.
        if not self._gl_logged:
            self._gl_logged = True
            if web.active_hub is None:
                _log_gl_renderer(self._log)
        self._theme_window.render()
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
        if self._recover_plugin.take_focus_request():
            # Shown again when it was closed, and in front of the other tabs of its dock.
            for (
                window
            ) in hello_imgui.get_runner_params().docking_params.dockable_windows:
                if _dock_window_name(window) == "recover":
                    window.is_visible = True
                    window.focus_window_at_next_frame = True
        self._project_plugin.render_overlays()
        self._render_progress_modal()
        self._render_import_password_modal()
        self._render_open_password_modal()
        self._render_running_editors_modal()
        self._render_project_elsewhere_modal()
        self._render_new_password_modal()
        self._render_network_consent_modal()
        self._render_overwrite_modal()
        self._render_ga_export_options_modal()
        self._render_ga_export_empty_modal()
        self._render_clear_cache_modal()
        self._render_projectstore_confirm_modal()
        self._render_url_prompt_modal()
        self._render_new_project_modal()
        self._render_myknx_sign_modal()
        self._render_about_modal()
        self._render_update_check_modal()
        self._render_update_modal()
        self._render_import_notes_modal()
        self._render_export_summary_modal()
        self._render_export_failure_modal()
        self._render_compact_modal()
        self._render_compact_result_modal()
        self._render_knxprod_result_modal()
        self._keyring_plugin.render_window()
        # The Signing Key dialog asked for from the Signing menu.
        if self._signing_plugin.take_open_request():
            imgui.open_popup(S.SIGNING_KEY_TITLE)
        self._render_signing_key_modal()
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
        if kind in ("script", "program", "device_info"):
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

    def _schema_incompat_devices(self, folders: list[str]) -> list[tuple[int, str]]:
        """(node_id, label) of project devices whose product/program is one of ``folders``.

        Reads the model directly (once, when the window opens — not per frame) so the manufacturer
        ref ids are exact, which the GUI device view does not expose."""
        source = self._project_service.working_path
        if source is None:
            return []
        prefixes = tuple(f"{f}_" for f in folders)
        rows: list[tuple[int, str]] = []
        engine = make_engine(url_for(Path(source)))
        with Session(engine) as session:
            for d in session.query(Device).all():
                refs = (d.product_ref_id or "", d.hardware2program_ref_id or "")
                if not any(r.startswith(prefixes) for r in refs):
                    continue
                device_name = d.name or d.product_name or d.product_ref_id
                parts = [p for p in (d.manufacturer_name, device_name) if p]
                label = " - ".join(parts)
                if d.product_name and d.product_name != device_name:
                    label = f"{label} ({d.product_name})"
                rows.append((d.id, label))
        return rows

    def _render_projectstore_confirm_modal(self) -> None:
        """Ask whether to overwrite an existing store folder or add a separate, renamed copy."""
        pending = self._projectstore_confirm
        if pending is None:
            return
        if not self._projectstore_confirm_open:
            # Wait for any prior modal to close before raising ours (same guard as the overwrite
            # prompt), so we don't steal the stage from the export dialog that triggered us.
            if imgui.is_popup_open("", imgui.PopupFlags_.any_popup):
                return
            imgui.open_popup(S.MYKNX_DONGLE_EXISTS_TITLE)
            self._projectstore_confirm_open = True
        if not begin_dialog(S.MYKNX_DONGLE_EXISTS_TITLE, width=480.0):
            return
        store, installer_guid = pending
        imgui.text_wrapped(
            S.MYKNX_DONGLE_EXISTS_PROMPT.format(pid=self._project_service.pid or "")
        )
        clicked = dialog_buttons(
            S.MYKNX_DONGLE_OVERWRITE, S.MYKNX_DONGLE_NEW_COPY, S.BTN_CANCEL
        )
        if clicked is not None:
            self._projectstore_confirm = None
            self._projectstore_confirm_open = False
            imgui.close_current_popup()
            if clicked == 0:
                self._export_to_ets6_projectstore(
                    store, installer_guid, create_copy=False
                )
            elif clicked == 1:
                self._export_to_ets6_projectstore(
                    store, installer_guid, create_copy=True
                )
        end_dialog()

    def _render_dongle_tab(self) -> None:
        """Step-by-step 'license with a dongle' in its tab of the export dialog; the dialog's first
        button writes the project into the ETS 6 ProjectStore."""
        # Read-only multiline input so the text stays selectable/copyable inline (mouse-drag +
        # Ctrl/Cmd+C). It does NOT word-wrap, so hard-wrap to the box width first (long paths and
        # P-XXXX_M-dummy stay intact) — otherwise long lines are clipped on the right.
        style = imgui.get_style()
        # The box's text width: inside its padding and the border (sized to its full content below,
        # so there is no scrollbar to leave room for).
        width = (
            imgui.get_content_region_avail().x
            - 2 * style.frame_padding.x
            - 2 * style.frame_border_size
        )
        key = (S.MYKNX_DONGLE_STEPS, width)
        if self._dongle_steps_wrapped[0] != key:
            self._dongle_steps_wrapped = (key, _wrap_text(key[0], width))
        wrapped = self._dongle_steps_wrapped[1]
        # Size the box to its wrapped line count so every step shows without an inner scrollbar. The
        # read-only multiline advances one font line (no item spacing) per line, so use the plain line
        # height; the surrounding tab page was sized to fit exactly this box plus the Copy button.
        lines = wrapped.count("\n") + 1
        height = lines * imgui.get_text_line_height() + 2 * style.frame_padding.y
        imgui.input_text_multiline(
            "##dongle_steps",
            wrapped,
            imgui.ImVec2(-1.0, height),
            imgui.InputTextFlags_.read_only,
        )
        if imgui.button(S.BTN_COPY):
            imgui.set_clipboard_text(S.MYKNX_DONGLE_STEPS)

    def _render_signing_key_modal(self) -> None:
        """Signing-key manager (view/edit/save/extract) as a dialog, opened from the Signing menu
        or from the export dialog.

        From the export dialog it is rendered inside that modal's scope so it stacks on top;
        closing it returns to the export dialog without cancelling the export."""
        if not begin_dialog(S.SIGNING_KEY_TITLE, width=40.0 * 16.0):
            return
        self._signing_plugin.render_contents()
        if dialog_buttons(S.SIGNING_KEY_CLOSE) == 0:
            imgui.close_current_popup()
        end_dialog()

    def _render_myknx_oauth_modal(self) -> None:
        """Configure the OAuth client credentials for the 2FA sign-in.

        Child modal of the export dialog: enter the client id/secret by hand, or extract them from
        the user's own ``Knx.Ets.MyKnx.dll``. Cached per-user so this is a one-time setup."""
        if not begin_dialog(S.MYKNX_OAUTH_TITLE, width=40.0 * 16.0):
            return
        imgui.text_wrapped(S.MYKNX_OAUTH_HELP)
        imgui.spacing()
        extracting = bool(
            self._myknx_oauth_extract_thread
            and self._myknx_oauth_extract_thread.is_alive()
        )
        if extracting:
            imgui.begin_disabled()
        imgui.text_disabled(S.MYKNX_OAUTH_CLIENT_ID)
        imgui.set_next_item_width(-1)
        _, self._myknx_client_id = imgui.input_text(
            "##oauth_cid", self._myknx_client_id
        )
        imgui.text_disabled(S.MYKNX_OAUTH_CLIENT_SECRET)
        imgui.set_next_item_width(-1)
        _, self._myknx_client_secret = imgui.input_text(
            "##oauth_secret", self._myknx_client_secret, imgui.InputTextFlags_.password
        )
        if extracting:
            imgui.end_disabled()
            imgui.text_disabled(S.MYKNX_OAUTH_EXTRACTING)
        elif imgui.button(S.MYKNX_OAUTH_EXTRACT, imgui.ImVec2(-1, 0)):
            self._myknx_oauth_dll_dialog = file_dialogs.open_file(
                S.MYKNX_OAUTH_EXTRACT,
                default_myknx_dll_path(),
                [MYKNX_DLL_NAME, "*.dll", S.MYKNX_OAUTH_ALL_FILES, "*"],
            )
        self._poll_myknx_oauth_dialog()
        if self._myknx_oauth_extract_error:
            imgui.text_colored(
                imgui.ImVec4(1.0, 0.4, 0.4, 1.0), self._myknx_oauth_extract_error
            )
        clicked = dialog_buttons(S.MYKNX_OAUTH_SAVE, S.SIGNING_KEY_CLOSE)
        if clicked == 0:
            client_id = self._myknx_client_id.strip()
            client_secret = self._myknx_client_secret.strip()
            save_myknx_oauth_credentials(client_id, client_secret)
            imgui.close_current_popup()
            # Continue straight into the browser sign-in once credentials are set.
            if client_id and client_secret:
                self._start_myknx_oauth_login()
        elif clicked == 1:
            imgui.close_current_popup()
        end_dialog()

    def _poll_myknx_oauth_dialog(self) -> None:
        if (
            self._myknx_oauth_dll_dialog is not None
            and self._myknx_oauth_dll_dialog.ready()
        ):
            result = self._myknx_oauth_dll_dialog.result()
            self._myknx_oauth_dll_dialog = None
            if result:
                self._start_myknx_oauth_extract(result[0])

    def _start_myknx_oauth_extract(self, dll_path: str) -> None:
        self._myknx_oauth_extract_error = ""

        def _work() -> None:
            try:
                client_id, client_secret = extract_oauth_credentials(dll_path)
                self._myknx_client_id = client_id
                self._myknx_client_secret = client_secret
                save_myknx_oauth_credentials(client_id, client_secret)
            except Exception as e:
                self._myknx_oauth_extract_error = f"{type(e).__name__}: {e}"

        self._myknx_oauth_extract_thread = threading.Thread(target=_work, daemon=True)
        self._myknx_oauth_extract_thread.start()

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

    def _window_title(self) -> str:
        """The app name, preceded by the open project's name."""
        service = self._project_service
        if not service.is_open:
            return S.APP_TITLE
        key = (service.generation, service.revision)
        if key != self._title_key and not service.loading:
            info = service.get_project_metadata()
            self._title_key = key
            self._title_name = info.name.strip() if info is not None else ""
        name = (
            self._title_name if self._title_key and self._title_key[0] == key[0] else ""
        )
        if not name and service.path is not None:
            name = service.path.stem
        return (
            S.WINDOW_TITLE.format(project=name, app=S.APP_TITLE)
            if name
            else S.APP_TITLE
        )

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
            # Records also shown in a dialog (e.g. a failed export) get no toast.
            if rec.level in ("warning", "error", "critical") and not rec.payload.get(
                "dialog"
            ):
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
            for code, native, name in _UI_LANGUAGES:
                label = name()
                if label != native:
                    label = S.LANGUAGE_ITEM.format(name=label, native=native)
                if (
                    imgui.menu_item(f"{label}##{code}", "", code == current)[0]
                    and code != current
                ):
                    self._switch_language(code)
            imgui.end_menu()

    def _switch_language(self, code: str) -> None:
        """Show the UI in ``code`` from now on and remember it. Cancelling the device rebuild
        returns to the language before."""
        previous = get_locale()
        self._apply_language(code)
        cancel = threading.Event()

        # Device labels are parsed per language; re-resolve + rebuild the device views in
        # the new language on a worker thread behind the progress bar, so the (slow)
        # re-parse of every application doesn't freeze the UI on the next frame.
        def _reresolve() -> None:
            def report(done: int, total: int, sub: str = "") -> None:
                if cancel.is_set():
                    return
                text = S.PROGRESS_DEVICES.format(done=done, total=total)
                self.set_progress(
                    done / total if total else None,
                    f"{text} — {sub}" if sub else text,
                )

            self._project_service.build_progress = report
            try:
                done = self._project_service.refresh_catalog_resolution(
                    rebuild=True, cancelled=cancel.is_set
                )
            finally:
                self._project_service.build_progress = None
            if not done:
                # Back to the language before; its devices are all reused, not built again.
                set_locale(previous)
                self._ui_executor.submit(lambda: self._apply_language(previous))
                _ = self._project_service.devices

        def _cancel() -> None:
            cancel.set()
            self.set_progress(None, S.PROGRESS_CANCELLING)

        self._run_bg(S.PROGRESS_LANGUAGE, _reresolve)
        if self._progress_running:
            self._progress_cancel = _cancel

    def _apply_language(self, code: str) -> None:
        set_locale(code)
        data = load_settings("app")
        data["locale"] = code
        save_settings("app", data)
        # Re-translate the docked panel/tab titles in place (kept identity via ###name).
        self._refresh_dock_titles()

    def _render_view_menu(self) -> None:
        """View menu: the default layout, which panels are shown, and the status bar."""
        if not imgui.begin_menu(S.MENU_VIEW):
            return
        params = hello_imgui.get_runner_params()
        if imgui.menu_item(S.MENU_RESTORE_LAYOUT, "", False)[0]:
            docks = {panel.name: panel.dock for panel in self.get_all_panels()}
            if self._form != "wide":
                # The smaller layout from the start, and the usual one when there is room.
                self._wide_layout = {"docks": docks, "ratios": {}, "expanded": False}
                if self.bottom_expanded:
                    self._toggle_bottom_expanded()
                self._show_compact_layout(self._form, from_wide=True)
            else:
                for w in params.docking_params.dockable_windows:
                    w.dock_space_name = docks.get(
                        _dock_window_name(w), w.dock_space_name
                    )
                self._rebuild_layout(expanded=False, ratios={})
            reset_all_tables()
            self._project_plugin.reset_layout()
        zoom = web.zoom()
        if zoom is not None:
            imgui.separator()
            if imgui.menu_item(S.MENU_ZOOM_IN, "Ctrl++", False, zoom < web.ZOOMS[-1])[
                0
            ]:
                web.zoom_step(1)
            if imgui.menu_item(S.MENU_ZOOM_OUT, "Ctrl+-", False, zoom > web.ZOOMS[0])[
                0
            ]:
                web.zoom_step(-1)
            reset = f"{S.MENU_ZOOM_RESET} ({round(zoom * 100)} %)"
            if imgui.menu_item(reset, "Ctrl+0", False, abs(zoom - 1.0) > 0.001)[0]:
                web.zoom_step(0)
        imgui.separator()
        windows = [
            w for w in params.docking_params.dockable_windows if w.include_in_view_menu
        ]
        if imgui.menu_item(S.MENU_SHOW_ALL, "", False)[0]:
            for w in windows:
                w.is_visible = True
        if imgui.menu_item(S.MENU_HIDE_ALL, "", False)[0]:
            for w in windows:
                if w.can_be_closed:
                    w.is_visible = False
        for w in windows:
            changed, visible = imgui.menu_item(
                w.label, "", w.is_visible, w.can_be_closed
            )
            if changed:
                w.is_visible = visible
        imgui.separator()
        changed, shown = imgui.menu_item(
            S.MENU_HIGHLIGHT_CHANGES, "", self._highlight_changes
        )
        if changed:
            self._highlight_changes = shown
            set_highlight_changes(shown)
            data = load_settings("app")
            data["highlight_changes"] = shown
            save_settings("app", data)
        changed, shown = imgui.menu_item(
            S.MENU_CONTEXT_HELP, "", self._project_plugin.show_context_help
        )
        if changed:
            self._project_plugin.show_context_help = shown
            data = load_settings("app")
            data[self._context_help_setting()[0]] = shown
            save_settings("app", data)
        window_params = params.imgui_window_params
        changed, shown = imgui.menu_item(S.MENU_STATUS_BAR, "", self._show_status_bar)
        if changed:
            self._show_status_bar = shown
        changed, shown = imgui.menu_item(S.MENU_STATUS_FPS, "", self._show_status_fps)
        if changed:
            self._show_status_fps = shown
        # The web mode paces its frames itself: no idling to toggle there.
        if web.active_hub is None:
            changed, enabled = imgui.menu_item(
                S.MENU_ENABLE_IDLING, "", params.fps_idling.enable_idling
            )
            if changed:
                params.fps_idling.enable_idling = enabled
        self._render_theme_menu(window_params.tweaked_theme)
        imgui.end_menu()

    def _render_theme_menu(self, tweaked_theme: hello_imgui.ImGuiTweakedTheme) -> None:
        if not imgui.begin_menu(S.MENU_THEME):
            return
        if imgui.menu_item(S.MENU_THEME_CUSTOMIZE, "", False)[0]:
            self._theme_window.open = True
        imgui.separator()
        for theme in themes():
            selected = tweaked_theme.theme == theme
            if imgui.menu_item(theme_name(theme), "", selected)[0]:
                tweaked_theme.theme = theme
                apply_theme(tweaked_theme)
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

    def _save_mcp_settings(self, running: bool) -> None:
        """Host, port, token and whether the server runs, restored on the next start."""
        save_settings(
            "mcp",
            {
                "host": self._mcp_host,
                "port": self._mcp_port,
                "token": self._mcp_token,
                "running": running,
            },
        )

    def _start_mcp(self, *, save: bool = True) -> None:
        """Start the MCP server; when it cannot start, say why (a toast, and beside the status)."""
        self._mcp_error = None
        try:
            self._mcp_plugin.start(
                self._mcp_host, int(self._mcp_port or "8765"), self._mcp_token
            )
        except PortInUseError as e:
            self._mcp_error = S.MCP_PORT_IN_USE.format(port=e.port)
        except Exception as e:
            self._log.error("MCP server start failed", error=f"{type(e).__name__}: {e}")
            self._mcp_error = S.MCP_START_FAILED.format(error=str(e))
        if self._mcp_error is not None:
            self._flash_toast(self._mcp_error)
        elif save:
            self._save_mcp_settings(running=True)

    def _render_mcp_error(self) -> None:
        """Why the last start failed, while the server is stopped."""
        if self._mcp_error is None or self._mcp_plugin.is_running:
            return
        imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + px(360.0))
        imgui.text_colored(imgui.ImVec4(0.95, 0.45, 0.4, 1.0), self._mcp_error)
        imgui.pop_text_wrap_pos()

    def _stop_mcp(self) -> None:
        self._mcp_plugin.stop()
        self._save_mcp_settings(running=False)
        self._log.info("MCP server stopped")

    def _render_mcp_menu(self) -> None:
        """MCP menu: shows whether the embedded server runs, toggles it on/off, and its host/port."""
        if not imgui.begin_menu(S.PANEL_MCP):
            return
        running = self._mcp_plugin.is_running
        # The status, and the button that starts or stops the server right-aligned beside it.
        imgui.align_text_to_frame_padding()
        imgui.text_colored(
            imgui.ImVec4(0.4, 0.85, 0.45, 1.0)
            if running
            else imgui.ImVec4(0.6, 0.6, 0.6, 1.0),
            S.SETTINGS_MCP_RUNNING if running else S.SETTINGS_MCP_STOPPED,
        )
        label = S.SETTINGS_MCP_STOP if running else S.SETTINGS_MCP_START
        button_w = imgui.calc_text_size(label).x + 2 * imgui.get_style().frame_padding.x
        imgui.same_line()
        right = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x - button_w
        imgui.set_cursor_pos_x(max(right, imgui.get_cursor_pos_x()))
        if imgui.button(label):
            if running:
                self._stop_mcp()
            else:
                self._start_mcp()
        self._render_mcp_error()
        imgui.separator()
        imgui.set_next_item_width(px(180.0))
        _, self._mcp_host = imgui.input_text(S.SETTINGS_MCP_HOST, self._mcp_host)
        imgui.set_next_item_width(px(180.0))
        _, self._mcp_port = imgui.input_text(S.SETTINGS_MCP_PORT, self._mcp_port)
        imgui.set_next_item_width(px(180.0))
        _, self._mcp_token = imgui.input_text(
            S.SETTINGS_MCP_TOKEN, self._mcp_token, imgui.InputTextFlags_.password
        )
        if running:
            imgui.separator()
            imgui.text_disabled(f"http://{self._mcp_host}:{self._mcp_port}/mcp")
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
        self._render_mcp_error()
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
                self._stop_mcp()
        elif imgui.button(S.SETTINGS_MCP_START):
            self._start_mcp()

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
        with floating_window_border():
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
_LAYOUT_VERSION = 16


_SECTIONS = ("RightSpace", "DeviceSpace", "LeftSpace", "BottomSpace")
# The one menu that holds all menus where they do not fit side by side (Font Awesome "bars").
_ICON_MENU = ""
# The usual sections in the order their tabs are listed where they share one section.
_SECTION_ORDER = (
    "LeftSpace",
    "MainDockSpace",
    "DeviceSpace",
    "RightSpace",
    "BottomSpace",
)
_DEFAULT_RATIOS = {
    # Wide enough for the Device panel's action buttons in one row.
    "right": 0.2,
    "device": 0.5,
    # Wide enough that the stacked navigation tabs (Buildings, Devices, Group Addresses, Catalog)
    # all fit without the tab-bar overflow (">>") arrow.
    "left": 0.39,
    "bottom": 0.345,
}


def _split(
    initial: str, new: str, direction: imgui.Dir, ratio: float
) -> hello_imgui.DockingSplit:
    split = hello_imgui.DockingSplit()
    split.initial_dock = initial
    split.new_dock = new
    split.direction = direction
    split.ratio = ratio
    return split


def create_docking_splits(
    bottom_expanded: bool = False, ratios: dict[str, float] | None = None
) -> list[hello_imgui.DockingSplit]:
    """The sections of the layout. The bottom section sits under the left column, or with
    ``bottom_expanded`` across the full width."""
    r = {**_DEFAULT_RATIOS, **(ratios or {})}
    right = _split("MainDockSpace", "RightSpace", imgui.Dir.right, r["right"])
    device = _split("RightSpace", "DeviceSpace", imgui.Dir.up, r["device"])
    left = _split("MainDockSpace", "LeftSpace", imgui.Dir.left, r["left"])
    if bottom_expanded:
        bottom = _split("MainDockSpace", "BottomSpace", imgui.Dir.down, r["bottom"])
        return [bottom, right, device, left]
    bottom = _split("LeftSpace", "BottomSpace", imgui.Dir.down, r["bottom"])
    return [right, device, left, bottom]


# The browser UI's layouts by the size the app is shown in (app units, the browser's size divided
# by its zoom): the usual one from this width on, and smaller ones below it. Switching back and
# forth needs a little more or less than the limit, so a size right at it does not flip.
_WIDE_MIN = 1000.0
_WIDE_MARGIN = 40.0
# How long a new size has to stay before the layout changes (resizing, turning the device).
_LAYOUT_SETTLE = 0.4


def form_factor(width: float, height: float, current: str | None = None) -> str:
    """The layout for an app of this size: ``"wide"`` (the usual one), ``"rows"`` (navigation
    above the rest: a portrait screen), ``"rows3"`` (also the bottom section: a large portrait
    screen) or ``"columns"`` (navigation beside the rest: a small landscape screen)."""
    limit = _WIDE_MIN
    if current == "wide":
        limit -= _WIDE_MARGIN
    elif current is not None:
        limit += _WIDE_MARGIN
    if width >= limit:
        return "wide"
    if height < width:
        return "columns"
    return "rows3" if width >= 600.0 and height >= 800.0 else "rows"


def compact_section(section: str, form: str) -> str:
    """The section of a smaller layout that takes the panels of a section of the usual one: the
    navigation keeps its own, the bottom one too with ``rows3``, all others share the main one."""
    if section == "LeftSpace" or (form == "rows3" and section == "BottomSpace"):
        return section
    return "MainDockSpace"


def compact_docking_splits(form: str) -> list[hello_imgui.DockingSplit]:
    """The sections of a smaller layout (see form_factor)."""
    if form == "columns":
        return [_split("MainDockSpace", "LeftSpace", imgui.Dir.left, 0.42)]
    if form == "rows3":
        return [
            _split("MainDockSpace", "BottomSpace", imgui.Dir.down, 0.28),
            _split("MainDockSpace", "LeftSpace", imgui.Dir.up, 0.45),
        ]
    return [_split("MainDockSpace", "LeftSpace", imgui.Dir.up, 0.42)]


def _restore_wide_layout(
    params: hello_imgui.DockingParams, layout: dict[str, Any]
) -> None:
    """Rebuild the usual layout as remembered when a smaller one replaced it."""
    docks: dict[str, str] = layout.get("docks", {})
    selected: set[str] = set(layout.get("selected") or [])
    for w in params.dockable_windows:
        name = _dock_window_name(w)
        w.dock_space_name = docks.get(name, w.dock_space_name)
        if name in selected:
            w.focus_window_at_next_frame = True
    _forget_tab_order(params)
    params.docking_splits = create_docking_splits(
        bool(layout.get("expanded", False)), layout.get("ratios") or {}
    )
    params.layout_reset = True


def _forget_tab_order(params: hello_imgui.DockingParams) -> None:
    """Let the tabs of a rebuilt layout come in the order of the dockable windows: imgui keeps
    the place a window's tab had in a section whose id the rebuilt layout uses again. Before
    imgui runs (the start) there are no windows yet."""
    if cast("object | None", imgui.get_current_context()) is None:
        return
    for w in params.dockable_windows:
        window = imgui.internal.find_window_by_name(w.label)
        if window is not None:  # pyright: ignore[reportUnnecessaryComparison]
            window.dock_order = -1


def _current_ratios(
    params: hello_imgui.DockingParams, bottom_expanded: bool
) -> dict[str, float]:
    """Split ratios that give the layout for ``bottom_expanded`` the current section sizes, or
    none if a section is missing."""
    nodes: dict[str, imgui.internal.DockNode] = {}
    for name in _SECTIONS:
        node_id = params.dock_space_id_from_name(name)
        node = imgui.internal.dock_builder_get_node(node_id) if node_id else None
        if node is None:
            return {}
        nodes[name] = node
    root = nodes["RightSpace"]
    # The bindings type the dock nodes and windows as non-optional, but they can be None.
    while root.parent_node is not None:  # pyright: ignore[reportUnnecessaryComparison]
        root = root.parent_node
    # A split shares its parent's size minus the separator between the two parts.
    gap = imgui.get_style().docking_separator_size
    width, height = root.size.x - gap, root.size.y - gap
    right = nodes["RightSpace"].size.x
    bottom = nodes["BottomSpace"].size.y
    # The right column is above the expanded bottom section.
    column = height - bottom - gap if bottom_expanded else height
    if width - right - gap <= 0 or column <= 0 or height <= 0:
        return {}
    ratios = {
        "right": right / width,
        "device": nodes["DeviceSpace"].size.y / column,
        "left": nodes["LeftSpace"].size.x / (width - right - gap),
        "bottom": bottom / height,
    }
    return {key: min(max(value, 0.05), 0.95) for key, value in ratios.items()}


def _keep_window_sections(params: hello_imgui.DockingParams) -> None:
    """Make a layout rebuild keep each window in the section it is in now, and the selected tabs."""
    sections = {
        params.dock_space_id_from_name(name): name
        for name in (*_SECTIONS, "MainDockSpace")
    }
    for window in params.dockable_windows:
        imgui_window = imgui.internal.find_window_by_name(window.label)
        if imgui_window is None:  # pyright: ignore[reportUnnecessaryComparison]
            continue
        name = sections.get(imgui_window.dock_id)
        if name is not None:
            window.dock_space_name = name
        node = imgui_window.dock_node
        if (
            window.is_visible
            and node is not None  # pyright: ignore[reportUnnecessaryComparison]
            and node.selected_tab_id == imgui_window.tab_id
        ):
            window.focus_window_at_next_frame = True


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
    "history",
    "health",
    "mass_linker",
    "tools",
    "ki",
    "topology",
    "recover",
    # Bottom bus/log group, then the project info and log.
    "monitor",
    "timeline",
    "logger",
    "project_info",
    "project_log",
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


# UI languages offered in the Language menu: locale code, the name in the language itself and the
# name in the current language. Catalogs live under each plugin's locales/<code>/LC_MESSAGES; "en"
# is the untranslated source.
_UI_LANGUAGES: list[tuple[str, str, Callable[[], str]]] = [
    ("en", "English", lambda: S.LANGUAGE_ENGLISH),
    ("de", "Deutsch", lambda: S.LANGUAGE_GERMAN),
    ("nl", "Nederlands", lambda: S.LANGUAGE_DUTCH),
]


_STRONG_TITLE_SCALE = 1.2


def _title_in_front(
    label: str, value_x: float, *, dim: bool = True, strong: bool = False
) -> None:
    """``label`` as the title of the field that follows, which starts at ``value_x``. ``dim`` greys
    the label (the default for secondary fields); pass ``dim=False`` to keep it at full contrast, and
    ``strong=True`` to draw it bold and larger (``_STRONG_TITLE_SCALE``), for a field that must stand
    out."""
    imgui.align_text_to_frame_padding()
    if strong:
        with bold(_STRONG_TITLE_SCALE):
            imgui.text(label)
    elif dim:
        imgui.text_disabled(label)
    else:
        imgui.text(label)
    imgui.same_line(value_x)


def _wrap_text(text: str, width: float) -> str:
    """Hard-wrap each line to ``width`` pixels of the current font, preserving blank lines and
    indentation. The continuation lines of a numbered step ("1. …") start under its text.

    imgui's read-only multiline input keeps the text selectable/copyable but does NOT word-wrap,
    so long lines get clipped on the right. Pre-wrapping here gives readable, non-clipped text while
    staying copyable. Long tokens (Windows paths, ``P-XXXX_M-dummy``) are never split.
    """
    space = imgui.calc_text_size(" ").x or 1.0
    out: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            out.append("")
            continue
        indent = line[: len(line) - len(line.lstrip())]
        hanging = indent
        marker = re.match(r"\s*\d+\.\s+", line)
        if marker is not None:
            # Spaces as wide as the indentation and the number in front of the text.
            hanging = " " * round(imgui.calc_text_size(marker.group(0)).x / space)
        current = indent
        for word in line.split():
            candidate = f"{current} {word}" if current.strip() else current + word
            if current.strip() and imgui.calc_text_size(candidate).x > width:
                out.append(current)
                current = hanging + word
            else:
                current = candidate
        out.append(current)
    return "\n".join(out)


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
    # Parsed product data lived in the data folder before; it belongs to the cache folder.
    move_product_cache(config_dir() / "ir_cache")
    app = KnxGuiApp(catalog_path)
    app.startup_file = startup_file(sys.argv)
    # Editors running already are offered first; --new-instance starts another without asking.
    if "--new-instance" not in sys.argv:
        app.running_editors = others()

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
    runner_params.fps_idling.fps_idle = _FPS_IDLE  # idle refresh (Hz) once input stops
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

    # The chosen theme is stored in the layout .ini (see below); a first start, with no .ini
    # yet, follows the system's light or dark preference.
    ini_path = config_dir() / "XKNX_Editor.ini"
    runner_params.imgui_window_params.tweaked_theme.theme = (
        hello_imgui.ImGuiTheme_.darcula_darker if ini_path.exists() else system_theme()
    )
    runner_params.callbacks.setup_imgui_style = apply_style
    runner_params.callbacks.load_additional_fonts = load_fonts

    runner_params.imgui_window_params.show_menu_bar = True
    runner_params.imgui_window_params.show_menu_app = False
    runner_params.imgui_window_params.show_menu_view = False
    runner_params.callbacks.show_menus = app.gui_menu

    # The app draws its own status bar (KnxGuiApp._render_status_bar), as high as its content.
    runner_params.imgui_window_params.show_status_bar = False
    runner_params.imgui_window_params.show_status_fps = False
    runner_params.imgui_window_params.remember_status_bar_settings = False

    # Keep the imgui layout .ini in the central data dir (with the JSON settings and catalog).
    runner_params.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    runner_params.ini_filename = str(ini_path)
    runner_params.docking_params.docking_splits = create_docking_splits(
        app.bottom_expanded
    )
    runner_params.docking_params.dockable_windows = create_dockable_windows(app)
    _apply_new_layout_once(runner_params.docking_params)
    # Ended with a smaller layout of the browser UI: start with the usual one again.
    app_settings = load_settings("app")
    wide_layout = app_settings.pop("wide_layout", None)
    if isinstance(wide_layout, dict):
        _restore_wide_layout(
            runner_params.docking_params, cast("dict[str, Any]", wide_layout)
        )
        runner_params.docking_params.layout_reset = False
        runner_params.docking_params.layout_condition = (
            hello_imgui.DockingLayoutCondition.application_start
        )
        save_settings("app", app_settings)

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

    # Browser UI (--web[=host:port]): no window, the browser draws the app.
    options = web_options(sys.argv)
    if options is not None:
        enable_web_mode(runner_params, options)

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
        "VM exposes no GPU to Windows. Run the XKNX-Editor installer again and select "
        "'Software rendering', which renders on the CPU without a GPU:\n"
        f"    {_INSTALLER_URL}\n"
        "  or use the portable build with software rendering:\n"
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
