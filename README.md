# XKNX Editor


XKNX Editor is a **multi-platform** desktop app (Windows, Linux, macOS) with native AI support for KNX building-automation projects based on KNX Standard Vol. 3, built on the open-source [xknx](https://github.com/XKNX/xknx)
library. Import and export `.knxproj` projects natively by [XKNXProject](https://github.com/XKNX/xknxproject) and [OpenKNXProducer](https://github.com/OpenKNX/OpenKNXproducer), browse product catalogs, **program KNX
devices** over a real bus based on [XKNX Project XKNXToolkit](https://github.com/XKNX/xknxtoolkit), and drive it all through an **integrated MCP server for AI-assisted**
workflows which can auto-download PDF manuals from the manufacturers.


> [!WARNING]
> **Not affiliated with the KNX Association.** See
> [Disclaimer](#disclaimer) and [Purpose](#purpose-and-interoperability)
>
>XKNX Editor comes with no  guarantees. It is tested with real devices but still a download can leave a device unavailable until it
> is reprogrammed. Do not use it on a installation you cannot access directly and
> keep a ETS backup of any project before opening it here.
>
> Verified device coverage for downloading is good but not complete. Please report if you have issues with a device.

## Download

Prebuilt apps for **Windows, Linux, and macOS** are attached to every release — grab the latest from
the [Releases page](https://github.com/xknx/xknx-editor/releases/latest) and start it directly.

On Windows, run `XKNX-Editor-windows-installer-x64.exe` (or `-arm64.exe` on Windows on ARM). It
installs for the current user without administrator rights, adds a Start menu entry, a desktop
shortcut and the `.xknx` file association (optionally also `.knxproj`, opened as an import), and
can be removed in Apps & Features. In a virtual
machine without a graphics driver, select "Software rendering" in the installer. The
`XKNX-Editor-windows-portable-*.zip` archives run without installation.

On macOS the app is not signed by Apple yet, so the first launch is blocked:

<img src="docs/images/macwarning.png" alt="macOS Gatekeeper warning" width="200">

To allow it, open it once, then go to **System Settings → Privacy & Security**, scroll down, and
click **Open Anyway** (confirm on the next launch). Alternatively, remove the quarantine flag in a
terminal:

```bash
xattr -dr com.apple.quarantine "/Applications/XKNX Editor.app"
```

On Linux, make the AppImage executable first (a double-click without this may open it in a
disk-image tool instead of launching):

```bash
chmod +x XKNX-Editor-x86_64.AppImage && ./XKNX-Editor-x86_64.AppImage
```

## Features

<!-- Images are placeholders — replace the files in docs/images/ with real screenshots. -->

**Project editor** — a tree for the installation (topology, group addresses, buildings and rooms), a
detail table, and a properties panel. Communication objects are linked to group addresses with live
compatibility checks, and every change has full undo/redo.

![Project editor](docs/images/editor.png)

**Import and export both work.** Open an existing project file and edit it, or export one that opens
in ETS. Exports bundle the required product data so the project is self-contained, are signed so ETS
accepts them. Based on [XKNXProject](https://github.com/XKNX/xknxproject) and [OpenKNXproducer](https://github.com/OpenKNX/OpenKNXproducer)

The **Open Project** dialog accepts three formats and routes each automatically: an XKNX project
(`.xknx`), an ETS project archive (`.knxproj`, imported into a new project), and an ETS group-address
export (`.xml`, `ga-export/01`). A group-address export carries only the group-address tree (main and
middle group folders with their addresses and datapoint types, no devices); it is imported into a new
project whose group-address tree mirrors the export.

![Import and export](docs/images/import-export.png)

**Product catalog** — import product files into a searchable catalog of manufacturers, hardware, and
applications, independent of any project, and drag entries in as new devices. You can also browse the
online catalog, cached locally for offline use.

![Product catalog](docs/images/catalog.png)

**Programming real devices** — commission a device end-to-end over a live connection (tunneling or
routing, with gateway discovery), including setting a new device's address. A read-only **preflight**
and **Test Programming** read the device back and show the exact changes before anything is
written. Vendor-independent by design.

![Programming and preflight](docs/images/programming.png)

**Cockpit and health checks** — a site-wide device table with live status, plus a panel that flags
issues (missing or duplicate addresses, applications missing from the catalog, unlinked objects,
group addresses without a type or sender); click a finding to jump to the device.



**Bus monitors and logs** — record and inspect bus traffic in the Group Monitor, Bus Monitor and Bus
Activity alongside a filterable application log;
with a project open, values are shown with the group address' name and decoded type.



**Recover from the bus** — reconstruct an installation without a project file by scanning an address
range and reading each device back read-only, then adding it to the project. Uncertain values are
marked rather than guessed, and a read-only verify diffs the result against the device. Still
maturing — review a recovered project before relying on it.

![Recover from the bus](docs/images/recover.png)

**MCP server (LLM control)** — an embedded [Model Context Protocol](https://modelcontextprotocol.io)
server lets an LLM drive the editor in the same live session as the GUI (shared project, catalog, and
connection). Start it from **Settings → MCP**. It can edit the project and program hardware, so only
expose it to clients you trust.

![MCP server](docs/images/mcp.png)

## Running from source

Only running from source is supported for now (developer-focused software).

```bash
uv sync
uv run python -m editor_gui.main
```

The app (distribution `xknxeditor-gui`, import package `editor_gui`) is a workspace member the root
project depends on, so a plain `uv sync` installs it and its `imgui-bundle` dependency.

## Packages

Standalone, typed Python libraries (the `xknxeditor` namespace), usable independently of the GUI:

| Package | Import | Description |
|---------|--------|-------------|
| `xknxeditor-namespaces` | `xknxeditor.namespaces` | XML schema bindings and version detection (foundation) |
| `xknxeditor-prod` | `xknxeditor.prod` | Reads and validates `.knxprod` product archives |
| `xknxeditor-catalog` | `xknxeditor.catalog` | Product catalog built from imported `.knxprod` archives |
| `xknxeditor-proj` | `xknxeditor.proj` | Project state management (incl. `.knxproj` import/export and offline signing) |
| `xknxeditor-datasecure` | `xknxeditor.datasecure` | Parses and serializes keyring XML (IP Secure keys) |
| `xknxeditor-download` | `xknxeditor.download` | Programs applications and individual addresses into real devices |
| `xknxeditor-recover` | `xknxeditor.recover` | Reconstructs a project by reading devices back from the bus |

## Development

```bash
uv sync                        # Install dependencies
uv run pytest                  # Run all tests (or: uv run pytest packages/namespaces)
uv run ruff check              # Lint
uv run pyright                 # Type check
```

See `CLAUDE.md` and `apps/editor-gui/CLAUDE.md` for architecture notes and conventions.

To test unreleased `xknx` changes, clone it as a sibling and add a `[tool.uv.sources]` override in
`apps/editor-gui/pyproject.toml` (`xknx = { path = "../../../xknx", editable = true }`), then
`uv lock`; revert both files before committing.

## Requirements

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)

## Purpose and interoperability

XKNX Editor is an independent, open-source tool that aims to improve interoperability within the KNX
ecosystem: reading, editing and exchanging KNX project data (`.knxproj`) and programming KNX devices
over the bus, without being tied to a single vendor's toolchain.

The project is guided by the principle — reflected in EU law — that interoperability between
independently created software should be possible (Directive 2009/24/EC on the legal protection of
computer programs, Article 6), and that a strong market position should not be used to foreclose
competition (Article 102 TFEU). XKNX Editor seeks to broaden choice and interoperability in KNX
tooling in that spirit.

This is a statement of the project's aims and not a legal determination about any third party.

## Disclaimer

An independent, open-source project built on the [xknx](https://github.com/XKNX/xknx) library. **Not
affiliated with, endorsed by, or connected to** the KNX Association or its ETS software. "KNX" and
"ETS" are trademarks of the KNX Association, used here only to state this non-affiliation and to
describe interoperability with the published standard and file formats.

## License

XKNX Editor is licensed under the **GNU General Public License v2.0 only** (`GPL-2.0-only`) — see
[LICENSE](LICENSE). Copyright (C) 2026 xknx

Based on [xknxtoolkit](https://github.com/XKNX/xknxtoolkit) by kewde.

The copyleft is inherited from [xknxproject](https://github.com/XKNX/xknxproject) (`GPL-2.0-only`),
used for `.knxproj` import: as a combined, distributed work XKNX Editor is therefore `GPL-2.0-only`.
Other bundled libraries are permissive (e.g. [xknx](https://github.com/XKNX/xknx), MIT).
