# Plugin Architecture

## Overview

The application organizes functionality into self-contained plugins under
`src/editor_gui/plugins/<name>/`. Each plugin owns its own UI panels and state, and communicates with
other plugins only through shared services on `PluginAPI` — never by holding a direct reference to
another plugin instance.

## Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         PluginAPI                                │
│  Shared context passed to (most) plugins                         │
│                                                                   │
│  ├── project:    ProjectService     (devices, topology, undo)    │
│  ├── catalog:    CatalogService     (device templates)           │
│  ├── connection: ConnectionService  (KNX I/O, CEMI dispatch)      │
│  ├── log:        LogService         (structured logging)         │
│  └── callbacks:  notify, warn, main_thread, run_background, ...  │
└─────────────────────────────────────────────────────────────────┘
                              │
      ┌───────────┬──────────┼──────────┬───────────┬─────────────┐
      ▼           ▼          ▼          ▼           ▼             ▼
 ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ ┌─────────┐ ┌─────────┐
 │ Catalog │ │ Project │ │ Connect-│ │Monitor │ │ Health  │ │   MCP   │  ...
 │ Plugin  │ │ Plugin  │ │   ion   │ │ Plugin │ │ Plugin  │ │ Server  │
 └─────────┘ └─────────┘ └─────────┘ └────────┘ └─────────┘ └─────────┘
```

Besides the four services, `PluginAPI` (`plugins/base/api.py`) carries UI callbacks set by
`main.py` (`notify`, `warn`, `main_thread`, `run_background`, `request_project_password`) and some
set by the project plugin (`ask_device_certificate`, `queue_program`, `queue_action`,
`cancel_operation`).

`main.py::KnxGuiApp.__init__` instantiates and wires every plugin directly — there is no dynamic
plugin loading despite `base/registry.PluginRegistry` (entry-point discovery scaffolding) existing;
it isn't called anywhere.

## Plugin Protocol

Every plugin implements the `Plugin` protocol (`plugins/base/registry.py`):

```python
class Plugin(Protocol):
    name: str

    @property
    def panels(self) -> list[PanelDefinition]: ...
    def on_load(self) -> None: ...
    def on_unload(self) -> None: ...
```

Plugins that own background resources (an event loop, a socket server, a real KNX connection) also
expose a `shutdown()` method, called explicitly from `main.py::KnxGuiApp.shutdown()` (order matters —
e.g. the real connection is torn down before the process exits).

## Panel Definition

Plugins declare their UI panels via `PanelDefinition`:

```python
@dataclass
class PanelDefinition:
    name: str  # unique identifier
    label: str  # display name (use S.PANEL_*)
    dock: str  # dock space name
    render: Callable[[], None]  # render function
```

**Dock spaces** (defined in `main.py::create_docking_splits`, default ratios in `_DEFAULT_RATIOS`):
- `MainDockSpace` — central area (editor, Bus Diagram, Cockpit, Mass Linker, Tools, Recover)
- `LeftSpace` (left of main) — topology, buildings, group addresses, catalog
- `RightSpace` (right of main) — history, operations, health
- `DeviceSpace` (split above `RightSpace`) — device, manufacturer, comment
- `BottomSpace` (below the left column, or the full width when expanded) — monitor, timeline, logs,
  project info, project log

Narrow screens (tablets and phones in the browser UI) use `compact_docking_splits`.

A plugin with no panels (e.g. `ConnectionPlugin`, `KeyringPlugin`, `BackupsPlugin`, `SigningPlugin`,
`McpServerPlugin`) returns `[]` (or has no `panels` at all) and instead renders into the menu bar, a
window or a status area via its own methods, called directly from `main.py`.

## Current Plugins

| Plugin | Panels | Purpose |
|--------|--------|---------|
| `CatalogPlugin` | `catalog` (LeftSpace) | Browse the local and the online catalog, import products, add devices |
| `ProjectPlugin` | `topology`, `buildings`, `group_addresses` (LeftSpace); `editor`, `mass_linker`, `tools` (MainDockSpace); `device`, `manufacturer`, `comment` (DeviceSpace); `history`, `operations` (RightSpace); `project_info`, `project_log` (BottomSpace) | Project editing, device configuration, programming queue, undo history |
| `BusDiagramPlugin` | `bus_diagram` (MainDockSpace) | Bus Diagram: areas, lines and devices, buildings, and which devices talk to which |
| `CockpitPlugin` | `cockpit` (MainDockSpace) | Site-wide device table with status |
| `RecoverPlugin` | `recover` (MainDockSpace) | Reconstruct a project by reading devices back from the bus |
| `HealthPlugin` | `health` (RightSpace) | Commissioning checks; device findings navigate on click |
| `ConnectionPlugin` | *(none)* | Real KNX connection (tunneling/routing), gateway discovery, status indicator + menu |
| `MonitorPlugin` | `monitor` (BottomSpace) | Group Monitor, Bus Monitor, Bus Activity recordings and group object values |
| `TimelinePlugin` | `timeline` (BottomSpace) | Decoded numeric group values plotted over time |
| `LoggerPlugin` | `logger` (BottomSpace) | Structured, filterable application log viewer |
| `KeyringPlugin` | *(none, own window)* | KNX Secure keyring import, browse and export |
| `BackupsPlugin` | *(none, own window)* | Project backups (by hand, on close, at an interval) and restore |
| `SigningPlugin` | *(none, dialog)* | Signing key for `.knxproj` export |
| `McpServerPlugin` | *(none)* | Embedded MCP server (`plugins/mcp`), not in `_plugins`; started and stopped by `main.py` |

`LoggerPlugin` is constructed with a `LogService` directly rather than a full `PluginAPI` — it
predates (or simply doesn't need) the shared-service pattern the others use. Several plugins also
take callbacks from `main.py` (e.g. `TimelinePlugin` the Monitor's telegrams, `ProjectPlugin` the
Monitor's latest values and the connection's interfaces).

## Services

### ConnectionService (`plugins/connection/service.py`)

The hub other plugins use to send/receive CEMI frames and run bus operations without depending on
`ConnectionPlugin` directly — e.g. the Monitor records frames purely through this service.

```python
connection.add_raw_cemi_listener(callback: (bytes, TelegramSource) -> None)
connection.add_sent_cemi_listener(callback)        # every frame the app sends
connection.add_connected_listener(callback) / add_disconnected_listener(callback)
connection.add_busmonitor_listener(callback)       # Bus Monitor tunnel frames
connection.dispatch_raw_cemi(raw_cemi)             # from the real connection
connection.send_cemi(raw_cemi) -> Future | None    # send out the real connection
connection.send_group_value(...) / send_group_telegram(...)
connection.program_device(...) / evaluate_device(...) -> Future | None
connection.run_async(coro) -> Future | None        # run on the KNX-Async event loop
connection.xknx -> XKNX | None
```

`TelegramSource` (`editor_gui.net`) tags every dispatched frame with where it came from
(`CONNECTION`, the bus connection).

### ProjectService / CatalogService

Both are thin GUI-facing facades over the standalone `xknxeditor.proj`/`xknxeditor.catalog` packages
— see `docs/architecture.md`, `docs/project.md`, and `docs/catalog.md` for their actual data model
and API surface; they're intentionally not duplicated here.

## Creating a New Plugin

1. Create the plugin directory: `plugins/myplugin/`

2. Implement the plugin class:

```python
# plugins/myplugin/plugin.py
from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.myplugin.strings import S


class MyPlugin:
    name = "myplugin"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._panel = MyPanel(...)
        self._panels = [
            PanelDefinition(
                name="mypanel",
                label=S.PANEL_MYPANEL,
                dock="BottomSpace",
                render=self._panel.render,
            ),
        ]

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
```

3. Export from `__init__.py`:

```python
# plugins/myplugin/__init__.py
from editor_gui.plugins.myplugin.plugin import MyPlugin

__all__ = ["MyPlugin"]
```

4. Instantiate and register in `main.py::KnxGuiApp.__init__`:

```python
self._myplugin = MyPlugin(self._plugin_api)
self._plugins.append(self._myplugin)
```

If it owns background resources, also call `self._myplugin.shutdown()` from
`KnxGuiApp.shutdown()`.

5. Create plugin strings with translations (see Translations section below)

## Dependencies Between Plugins

Prefer routing through a shared `PluginAPI` service (see `ConnectionService` above). When a plugin
genuinely needs another specific plugin's data — not just a general service — pass a callback at
construction instead of holding a reference:

```python
# One plugin exposes a method
class SelectionPlugin:
    def get_selected_ids(self) -> list[int]:
        return self._panel.get_selected_ids()


# Another takes it as a callback (main.py wires the two together)
self._project_plugin = ProjectPlugin(
    self._plugin_api,
    get_selected_node_ids=self._selection_plugin.get_selected_ids,
)
```

This avoids circular imports while allowing cross-plugin coordination for the one-off cases a shared
service doesn't fit.

## Translations (i18n)

Each plugin manages its own translations using gettext.

### Plugin Structure

```
plugins/myplugin/
  plugin.py
  strings.py              # plugin strings
  locales/
    nl/LC_MESSAGES/
      myplugin.po         # Dutch translations source
      myplugin.mo         # compiled translations
```

### Creating strings.py

```python
from pathlib import Path
from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("myplugin", _locale_dir)


class MyPluginStrings:
    @property
    def PANEL_TITLE(self) -> str:
        return _("My Panel")

    @property
    def BTN_DO_THING(self) -> str:
        return _("Do Thing")


S = MyPluginStrings()
```

To inherit common strings (buttons like Add, Close, Cancel), extend `BaseStrings`:

```python
from editor_gui.strings import BaseStrings, create_translator


class MyPluginStrings(BaseStrings):
    # now has BTN_ADD, BTN_CLOSE, BTN_CANCEL, etc.
    ...
```

### Creating Translation Files

1. Create `.po` file at `locales/<lang>/LC_MESSAGES/<domain>.po`:

```
msgid "My Panel"
msgstr "Mijn Paneel"

msgid "Do Thing"
msgstr "Doe Ding"
```

2. Compile to `.mo` with Babel (no gettext installation needed; see "UI Texts" in the root
`AGENTS.md`):

```bash
uv run --with babel pybabel compile -d src/editor_gui/plugins/myplugin/locales -D myplugin
```

A missing catalog falls back to the literal string (`create_translator` catches
`FileNotFoundError`), but `tests/test_translations.py` requires every msgid to be translated in every
language of `editor_gui.languages`.

### Language Detection

Language is detected from system locale at startup (falls back to English); a language chosen in
the Language menu is saved in `app.locale` and wins over the detected one.
