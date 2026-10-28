# Desktop deployment

Package the XKNX Editor into a standalone desktop app (no Python install needed for users),
using [PyInstaller](https://pyinstaller.org/). Based on the imgui_bundle desktop-deploy guide.

## Build

```bash
apps/editor-gui/tools/build_desktop.sh
```

This regenerates the app icon and runs PyInstaller from [`xknx-editor.spec`](xknx-editor.spec).
Output lands in `apps/editor-gui/dist/` — `XKNX Editor.app` on macOS, an `XKNX Editor/` folder
(with the executable + dependencies) on Windows/Linux. PyInstaller is fetched ephemerally via
`uv --with`, so it is not added to the project dependencies.

## Windows release assets

| Asset | Content |
|---|---|
| `XKNX-Editor-windows-installer-x64.exe` | Installer (recommended), with optional software rendering |
| `XKNX-Editor-windows-installer-arm64.exe` | Installer for Windows on ARM, software rendering selected by default |
| `XKNX-Editor-windows-portable-x64.zip` | Portable folder, start `XKNX-Editor.exe` directly |
| `XKNX-Editor-windows-portable-x64-softwaregl.zip` | Portable folder with Mesa's software OpenGL |
| `XKNX-Editor-windows-portable-arm64.zip` | Portable folder for Windows on ARM, with software OpenGL |

Software rendering (Mesa's `opengl32.dll` next to the exe plus `software_gl.marker`, see
`tools/desktop_entry.py`) is for virtual machines and other hosts without a working OpenGL driver.

## Windows installer

[`packaging/windows/xknx-editor.iss`](packaging/windows/xknx-editor.iss) is an
[Inno Setup](https://jrsoftware.org/isinfo.php) script. The installer:

- installs for the current user without administrator rights (`%LOCALAPPDATA%\Programs\XKNX Editor`),
  or for all users in Program Files (chosen on its first page, with a UAC prompt);
- offers a Start menu entry, a desktop shortcut and the `.xknx` file association (all selected by
  default), opening `.knxproj` files as an import (not selected by default), and software
  rendering;
- registers an uninstaller in Apps & Features, which can also delete the settings, catalog and
  caches (projects and the local copies of network projects are always kept);
- updates in place: a newer installer replaces the whole bundle and asks to close a running app;
- is also run by the app itself: when the update check finds a newer release, "Install Update"
  downloads that release's installer for this architecture, checks it against the SHA-256 digest
  GitHub publishes for the asset, runs it silently (`/RELAUNCH=1`, `/OPEN=<project>`) and exits;
  the installer keeps the folder and the options and starts the app again with the open project
  (`src/editor_gui/self_update.py`). Portable and source runs keep opening the release page;
- removes a folder left by the former `install.bat` (`C:\Program Files\XKNX-Editor`);
- follows the Windows language (English, German, Dutch).

Never change its `AppId`: Windows and WinGet identify the installation by it.

Build it after PyInstaller (Inno Setup is downloaded and installed for the current user when it
is missing):

```powershell
cd apps/editor-gui
powershell -File packaging/windows/build_installer.ps1 -Arch x64 -SourceDir dist/XKNX-Editor `
  -MesaDir <folder with opengl32.dll and libgallium_wgl.dll> -OutputDir .
```

Without `-MesaDir` the installer has no software rendering option. Unattended installation:
`/VERYSILENT /SUPPRESSMSGBOXES /CURRENTUSER` (or `/ALLUSERS`), and optionally
`/TASKS="startmenuicon,desktopicon,fileassoc,fileassocknxproj,softwaregl"` to choose the tasks.

## WinGet

The package is `XKNX.Editor`. For every app release, the `winget` job of the desktop-build workflow
writes its manifests with [`packaging/winget/make_manifests.py`](packaging/winget/make_manifests.py)
and keeps them as the `winget-manifests` artifact. When the repository secret `WINGET_TOKEN` is set
(a classic GitHub token with `public_repo` scope, of an account that has a fork of
[microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs)), the job also submits them there
as a pull request with [wingetcreate](https://github.com/microsoft/winget-create).

The first submission of the package is reviewed by the WinGet moderators. To submit by hand, take
the artifact (or run the script on the release's installers) and:

```powershell
winget validate --manifest <dir>
wingetcreate submit --token <token> <dir>
```

`<dir>` is the version folder `manifests/x/XKNX/Editor/<version>`. Once accepted, users install
and update with `winget install XKNX.Editor` and `winget upgrade XKNX.Editor`.

## What the spec bundles

- **imgui_bundle** — native libraries + its bundled fonts (via `collect_all`).
- **fastmcp / xknx / xknxproject** — data files + submodules.
- **xknxeditor / editor_gui** — our packages: dynamic imports (parser_v2, model adapters, plugins)
  plus data (generated model bindings, i18n `.mo` locales, the app icon + fonts under `assets/`).

## Icons

- **Window icon** (Windows/Linux, and the in-app icon): `src/editor_gui/assets/app_settings/icon.png`
  (512×512), loaded via `hello_imgui.set_assets_folder(...)` in `main.py`.
- **Executable / dock icon**: PyInstaller needs a platform icon — `icon.icns` on macOS, `icon.ico`
  on Windows, both set in `xknx-editor.spec`.
- **Tray icon** of the browser UI: `src/editor_gui/web/tray/icon.png`; the Home Assistant app's
  icon and logo: `homeassistant/xknx-editor/`.
- All of them are drawn by `tools/make_icon.py` (on any OS) and committed. After changing the
  design:

  ```bash
  uv run --with pillow python apps/editor-gui/tools/make_icon.py
  ```

## Note

Packaging imgui_bundle apps is known to work but is **unofficial**. If the frozen app fails with
`ModuleNotFoundError` at startup, add the missing module to `hiddenimports` in the spec (a common
need for dynamically imported plugins/backends). Run the built app from a terminal once to see any
such errors.
