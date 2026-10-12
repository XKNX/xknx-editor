# editor-gui

## Required Reading

Before making changes, read these docs:
- `docs/architecture.md` - Template/instance pattern, data flow, key principles

## Commands

- Run tests: `uv run pytest apps/editor-gui` (plugins with their own tests include `monitor`, `catalog`; root `uv run pytest` covers the whole workspace)
- Run GUI: `uv run python -m editor_gui.main`
- Generate demo project: `uv run generate-demo`
- Generate catalog from knxprod: `uv run generate-catalog [files...]`
- Use `uv run` for all Python commands (not manual venv activation)

## Feature inventory (what ALREADY exists — check here before "adding" a feature)

Before building any feature, assume it may already exist and grep first. Current state:

- **Device configuration** (`project/ui/configure.py`, the "Configure"/editor panel, MainDockSpace):
  - device header: Name, Individual Address; Manufacturer / Application / Order Number / Hardware / Description (`_get_device_info`)
  - tab **Parameters**: full application parameter tree editor (`widgets` `render_ui_tree`, `count_parameters`, `_on_param_change`)
  - tab **Group Objects**: `widgets/group_objects_widgets.py::GroupObjectsTable` — columns `#`, Name, DPT, Group Addresses + flags C/R/W/T/U, link/unlink com-objects
  - **Programming/Download**: `DownloadScope` selector (FULL/partial), load-procedures preview, program-confirm, `memory_preview`, `preflight_result`
- **Catalog** (`catalog/`): local `.knxprod` import; browse tree with search; add device to project; **online catalog** (onlinecatalog.knx.org) — manufacturer list, per-manufacturer product browse + `.knxprod` download+import, search, country/language combo (persisted)
- **Connection** (`connection/`): tunneling/routing connect, `GatewayScanner` discovery, **startup auto-connect** to preferred/first gateway (`autostart()`), settings persisted via `settings.py`; management procedures (individual address read/write, serial write)
- **Monitor** (`monitor/`, panel "Monitor"): tabs "Group Monitor" (group telegrams from/to bus + GA/value Read/Write command bar, Replay/Stop Replay), "Bus Monitor" (real KNXnet/IP bus monitor tunnel, CRI layer 80h, via `connection/busmonitor.py`; closes the normal connection while it runs, sending/downloads are refused), "Bus Activity" (the connection's communication other than group telegrams: every frame the app sends, hooked in `ObservableKNXIPInterface*.send_cemi`, plus every received frame that is not a group telegram and not a positive L_Data.con; Group Monitor + Bus Activity hold every frame of the normal connection) and "Group Objects" (per-project-GA latest-value table + command bar). Recordings: 15 default + 8 optional (hideable) columns, multi-select, Start/Stop/Clear/Import/Export (CommunicationLog XML, telegrams/01) and Print (own PDF writer `monitor/pdf.py`), Copy (Ctrl+C, tab-separated shown columns); double-click a row selects its source device; filter via the shared `filter_box`. MCP `network_*` tools read/drive the Group Monitor + Bus Activity logs together (`mcp/tools/network.py`).
- **Project** (`project/`): Devices tree, Buildings/Spaces (editable: create/rename/set-type/move/delete building→floor→room via context menu, assign devices to rooms + "Without space" section; building functions create/edit), Group Addresses tree (editable: create GAs + create/rename/delete folders = main/middle groups, auto-numbered next-free per level, gated by GA style; delete folder cascades its GAs, undoable), undo/redo History, Project Info, **Project Log** (right-side tab: the ETS `ProjectInformation/ProjectTraces` carried over on import, filterable + sortable by Date/User; Comment shown verbatim = still encrypted until the decryption phase); export to `.knxproj` (with optional MyKnx certificate signer); import `.knxproj`
- **Keyring** (`keyring/`, "XKNX Secure" menu → own window): import a `.knxkeys` (password), browse decrypted backbone/interfaces/GA-keys/devices, and **export/convert** it under a new keyring password. Uses this project's own verified `xknxeditor.datasecure` crypto (decrypt/verify + re-encrypt/re-sign), not xknx's read-only loader. Feeds `device_security(ia)` (tool key) into secure programming. Runtime-only (never persisted into the project). Note: a keyring *from project* export is NOT possible — the project DB does not persist device key material (tool keys/FDSK/backbone); that would require accessing ETS6's project security store internals. **Logger** (`logger/`, BottomSpace log table with filter)
- **Cockpit** (`cockpit/`, MainDockSpace): site-wide device table (address/name/product/status), "needs attention" filter + per-device issue tooltip, click-to-select; rows cached by `ProjectService.revision`. Bulk program/test actions not yet wired.
- **Health** (`health/`, RightSpace): actionable checks (missing/duplicate IA, missing catalog apps, unlinked com-objects, GAs without DPT, no/multiple senders) via `HealthService` (cached by revision); device findings navigate on click. Keyring-derived checks not yet included.
- **Recover** (`recover/`): reconstruct a project by scanning an individual-address range off the bus; opened from the menu or the Welcome screen.
- **Command palette** (`main.py::_render_command_palette`, `Ctrl+P`): fuzzy-jump to devices + global actions (New/Open/Export).
- **Localization**: gettext catalogs per plugin domain under `<plugin>/locales/<lang>/LC_MESSAGES/<domain>.mo` (de + nl); UI language auto-detected from OS locale, overridable via the Language menu (persisted in `app.locale`), runtime-switchable. Device parameter/tab labels are localized from the `.knxprod`'s own translations (`xknxeditor.prod.translate.apply_translations`, language threaded through `catalog.get_application`).
- **App**: XKNX window/app icon + bundled fonts under `src/editor_gui/assets/` (do NOT `set_assets_folder` to a dir without `fonts/`, or the default font breaks); Welcome screen when no project is open; Recent files (File > Open Recent, persisted in `settings.py` `app.recent_files`, startup opens the most recent); shortcuts Ctrl+N/O/S; determinate "opening project" progress bar (per-device, via `ProjectService.build_progress`). (There is no Settings dialog; settings live in JSON under `config/` and are edited in-place / via the relevant plugin UI.)
- **KNX master data**: the global mask-version load/unload procedures + manufacturers + DPTs. NOT bundled (proprietary KNX content); `master_data.py::load_master` fetches the signed `knx_master.xml` for project/23 from `update.knx.org` on first use and caches it per-user under `config_dir()` (`settings.config_dir()`, platformdirs), reading the cache on later launches. It is injected into `ConnectionService.master` and threaded to `download`/`preflight` (required for an UNLOAD scope and default/merged procedure styles); `master_xml_bytes()` returns the same bytes to wrap OpenKNX/monolithic product XML on import. Version + date show in the status bar footer (`STATUS_MASTER_DATA`). Startup already degrades gracefully when the fetch fails and no cache exists (master unset; UNLOAD download and OpenKNX-URL import report a clear error instead of crashing).
- **All search/filter fields** use the shared `editor_gui.widgets.filter_box` (input + clear button): Devices, Group Addresses, Buildings, Catalog (local+online), Parameters, Group Monitor, Bus Monitor, Logs (Logs also has a level dropdown).
- **Group Objects tab**: multi-select com-objects (checkbox per row, All/None) + "Create group addresses (N)" bulk action (auto address + the com-object's DPT, linked as sending).
- **MCP** (`packages/mcp`, `xknxeditor-mcp`): FastMCP 3.4.7 server over Streamable HTTP exposing catalog + project tools; launched/stopped from the app (host/port persisted in `config/mcp.json`, subprocess `uv run xknxeditor-mcp`, shares this app's catalog DB); status shown in the status bar.
- **Filter widget location**: `editor_gui/widgets/filter_box.py` (project's `plugins/project/ui/_filter.py` just re-exports it).

## Plugin architecture

Features live under `src/editor_gui/plugins/<name>/`, one directory per plugin. Current plugins: `catalog`, `project`, `connection`, `monitor`, `keyring`, `recover`, `cockpit`, `health`, `logger` (plus `base`). (The old `proxy`, `virtual`, `cat` and `network` plugins were removed; the network capture is now the Monitor's Group Monitor + Bus Activity.) A plugin typically has:
- `plugin.py` — lifecycle class implementing the `Plugin` protocol (`base/registry.py`): `__init__(self, api: PluginAPI)`, a `panels` property, `on_load`/`on_unload`
- `service.py` — plugin logic decoupled from imgui, sometimes exposed to other plugins as a shared service on `PluginAPI` (e.g. `connection`, `catalog`, `project`, `log`)
- `strings.py` — user-facing strings for this plugin's i18n domain (see below)
- `ui.py` — panel rendering, when the plugin owns a dockable panel

Plugins are instantiated and wired directly in `main.py::KnxGuiApp.__init__` (menus, panels, shutdown order) — `base/registry.PluginRegistry` exists but isn't used for dynamic discovery yet.

Plugins that need to interact (e.g. `proxy` relaying CEMI frames to/from the real KNX connection) only do so through a shared service on `PluginAPI`, never by holding a reference to another plugin instance directly.

## Conventions

- All user-facing strings must be defined in each plugin's `strings.py` (or `src/editor_gui/strings.py` for app-wide strings) for i18n support
- Panels hold their own internal state; shared state is accessed via dependency-injected callbacks
- Catalog stores immutable templates; project stores device instances with overrides
- Visibility (visible_com_objects, visible_parameters) computed at runtime, never baked into templates
