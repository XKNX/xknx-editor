# PyInstaller spec for XKNX Editor — build with:  tools/build_desktop.sh
#
# Packages the GUI into a standalone desktop app. imgui_bundle (native libs + fonts),
# fastmcp, xknx, xknxproject and our own xknxeditor/editor_gui packages carry data files and
# do dynamic imports, so we collect them explicitly. Packaging imgui_bundle apps is known to
# work but is unofficial — expect to add hidden imports here if the frozen app misses a module.
# ruff: noqa
# type: ignore
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# Per-OS executable icon: macOS wants .icns (committed), Windows wants .ico (optional — build
# succeeds without it), Linux embeds none. Keeps the cross-platform release build from failing.
if sys.platform == "darwin":
    _icon = "icon.icns"
elif sys.platform == "win32":
    _icon = "icon.ico" if os.path.exists("icon.ico") else None
else:
    _icon = None

_datas = []
_binaries = []
_hiddenimports = []

# Third-party packages that ship native libraries and/or data (fonts, schemas, templates) or do
# dynamic imports PyInstaller's static analysis misses. `mcp` and `uvicorn` are collected in full
# because fastmcp's server (uvicorn's protocol/loop backends, mcp's submodules) imports parts of
# them by name at runtime. NOTE: the earlier "FastMCP server support is not installed" failure was a
# symptom of the Windows OpenSSL load failure (fastmcp's server import chain imports ssl), not of
# missing modules — it is fixed by shipping python.org's OpenSSL, not by over-collecting here.
# FastMCP 4 (on MCP SDK v2) splits the protocol types into the standalone `mcp_types` distribution
# (re-exported as `mcp.types`); `collect_all("mcp")` does not reach it, so collect it alongside `mcp`.
# (httpx2/httpcore2/truststore, which FastMCP 4 pulls in, are plain top-level imports with no data —
# PyInstaller's static analysis finds them; add them here only if a CI freeze proves otherwise.)
# `dukpy` (JS interpreter used by parser_v2's calculation path) loads .js runtime files from its
# jsruntime/ data dir at runtime; without collect_all the frozen app reports "process_runtime.js
# file is missing".
for _pkg in ("imgui_bundle", "fastmcp", "mcp", "mcp_types", "uvicorn", "xknx", "xknxproject", "dukpy", "tzdata", "mpmath"):
    d, b, h = collect_all(_pkg)
    _datas += d
    _binaries += b
    _hiddenimports += h

# Our own namespace packages: dynamic imports (parser_v2, model adapters, plugins) + data
# (KNX XML schemas, i18n .mo locales, the app icon + fonts).
for _pkg in ("xknxeditor", "editor_gui"):
    _hiddenimports += collect_submodules(_pkg)
    _datas += collect_data_files(_pkg, include_py_files=False)

# The browser UI's tray icon imports its operating system's toolkit by name at runtime: AppKit on
# macOS, the D-Bus library on Linux.
if sys.platform == "darwin":
    _hiddenimports += ["objc", "AppKit", "Foundation"]
elif sys.platform.startswith("linux"):
    _hiddenimports += collect_submodules("dbus_fast")

# The frozen app ships no .py sources, so the fingerprint of the code that evaluates applications
# (the key of the device snapshots stored in projects) is taken here, from the sources being built.
from editor_gui.device_snapshot import BUNDLED_FINGERPRINT, source_fingerprint

_fingerprint = os.path.join(workpath, BUNDLED_FINGERPRINT)
os.makedirs(workpath, exist_ok=True)
with open(_fingerprint, "w", encoding="ascii") as _f:
    _f.write(source_fingerprint())
_datas.append((_fingerprint, "editor_gui"))

# Trim collected data we never ship: imgui_bundle's demo playground + its assets (the app has its
# own fonts/icon), and .pyi type stubs (dev-only, not used at runtime). Saves ~6-7 MB. Bytecode
# caches a local test run leaves in the source tree are not data either (their long names also
# came close to the Windows path limit of an installation).
_SKIP_DIRS = ("imgui_bundle/demos_python", "imgui_bundle/demos_assets", "__pycache__")


def _keep(dst):
    norm = dst.replace("\\", "/")
    return not dst.endswith(".pyi") and not any(p in norm for p in _SKIP_DIRS)


# collect_all routes some of these (incl. .pyi stubs) into binaries, not datas — filter both.
_datas = [(src, dst) for (src, dst) in _datas if _keep(dst)]
_binaries = [(src, dst) for (src, dst) in _binaries if _keep(dst)]

block_cipher = None

a = Analysis(
    ["tools/desktop_entry.py"],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    cipher=block_cipher,
)
# Drop ONLY the bundled ucrtbase.dll. A/B-confirmed on the target: when the bundled ucrtbase.dll is
# present the PyInstaller bootloader pre-loads it and then FAILS to load python313.dll ("Failed to
# load Python DLL … FormatMessageW failed"); when it is absent python313.dll loads fine against the
# system UCRT. Keep the api-ms-win-crt-*.dll forwarders — python313.dll/_ssl.pyd/libcrypto-3.dll
# import them by name and they just forward to whatever ucrtbase.dll is loaded (the system one).
# NOTE: this is unrelated to the separate libcrypto-3.dll problem — setup-python's OpenSSL fails to
# load on some machines and is replaced with python.org's official build in the desktop-build workflow
# (post-packaging overlay step), not here.
import os as _os

a.binaries = [
    b for b in a.binaries if _os.path.basename(b[0]).lower() != "ucrtbase.dll"
]

# Linux: do NOT ship our own libstdc++ / libgcc_s. PyInstaller already excludes the GL loader stack
# (libGL/libEGL/libdrm/libxcb-dri) so the app uses the host's, but it still bundles the build
# image's libstdc++.so.6 / libgcc_s.so.1. At runtime the host libGL loads the host Mesa DRI driver,
# which needs a GLIBCXX newer than an old build image (Ubuntu 22.04) provides; the bundled, older
# libstdc++ then shadows the host one and starves the driver, so GLX returns no FBConfig ("No
# GLXFBConfigs returned") and no window is created. Dropping these makes the app use the host C++
# runtime, which is by definition new enough for the host's own driver. The app ELF still links the
# build glibc, so building on an older LTS keeps the lower glibc floor unchanged.
if sys.platform == "linux":
    import re as _re

    _host_cxx = _re.compile(r"^(?:libstdc\+\+|libgcc_s)\.so")
    a.binaries = [
        b for b in a.binaries if not _host_cxx.match(_os.path.basename(b[0]))
    ]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XKNX-Editor",
    # strip is DISABLED: on Windows PyInstaller runs GNU `strip` (present via Git-for-Windows on the
    # runner) over the collected DLLs, which corrupts python3xx.dll -> "Failed to load Python DLL …
    # FormatMessageW failed". PyInstaller itself warns strip is unsafe on Windows.
    strip=False,
    upx=False,  # also disabled: UPX likewise corrupted the Windows python DLL / MSVC runtime.
    console=False,  # windowed app (no terminal)
    # Platform icon (macOS .icns / Windows .ico) — the XKNX logo, drawn by tools/make_icon.py.
    icon=_icon,
)
# UPX disabled (see EXE above): it corrupted the bundled python3xx.dll / MSVC runtime on Windows,
# so the app failed to start. macOS/Linux ignored UPX anyway.
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="XKNX-Editor")

# On macOS, wrap the result in a proper .app bundle (dock icon comes from the bundle icon).
app = BUNDLE(
    coll,
    name="XKNX-Editor.app",
    icon="icon.icns",  # XKNX logo for the dock/Finder icon
    bundle_identifier="org.xknx.editor",
)
