# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository. `CLAUDE.md` only refers to this file, so keep all instructions here.

## Commands

```bash
uv sync                             # Install dependencies
uv run pytest                       # Run all package tests
uv run pytest packages/namespaces       # Run tests for a single package
uv run ruff check                   # Lint
uv run ruff format                  # Format
uv run pyright                      # Type check (strict mode)
```

All Python commands must use `uv run` — do not activate the venv manually.

## Architecture

UV monorepo: `packages/` holds five core libraries; `apps/` holds applications.

**Package dependency order** (bottom to top):
- `xknxeditor-namespaces` — foundation: KNX XML dataclass bindings for versions 10–14 and 20–23, plus `detect_version()`, `load_xml()`, `serialize_xml()`
- `xknxeditor-prod` — reads `.knxprod` archives (ZIP files), validates structure, parses manufacturer/catalog/hardware/application XMLs via models
- `xknxeditor-catalog` — SQLite catalog of imported `.knxprod` hardware/applications, depends on product
- `xknxeditor-proj` — project state management, depends on models
- `xknxeditor-datasecure` — parses/serializes KNX keyring XML, depends on models
- `apps/editor-gui` — project editor GUI app; consumes all packages; see `apps/editor-gui/AGENTS.md` (also for the browser UI, `--web`, which every GUI feature has to support)

**Import paths** use the `xknxeditor` namespace package (src layout):
- `from xknxeditor.namespaces import ...`
- `from xknxeditor.prod import ...`
- `from xknxeditor.catalog import ...`
- `from xknxeditor.proj import ...`
- `from xknxeditor.datasecure import ...`

**Generated code** lives under `**/files/` and `**/intermediate/` (e.g. `xknxeditor.namespaces.files`) — never edit these directly. Both are excluded from linting and type checking.

## Code Style

- Pyright strict mode; all public APIs must be fully typed
- Ruff rules: E, W, F, I, UP, B, SIM, RUF — double quotes, 2-space indent, LF line endings

## UI Texts

These rules apply to every English text the GUI shows (the msgids in each `strings.py`):

- **Title case** for window and dialog titles, menu and context-menu items, buttons, tab names, section headers and separator texts, table column headers, and labels beside or above fields.
  - Capitalize every major word.
  - Keep minor words in lower case unless they are the first or last word: a, an, the, and, but, or, nor, as, at, by, for, from, in, into, of, off, on, onto, per, to, via, with.
  - Write abbreviations and names as they are spelled: URL, KNX, OpenKNX, .knxprod.
  - Examples: "Load OpenKNX from URL or Folder…", "Choose Folder…", "Restore Default Layout", "Replace with (Template)", "Index of Access Identification Code".
- **Sentence case** for everything else: option texts in radio buttons and drop-downs, checkbox texts, hints, tooltips, messages, toasts and log texts. They start with an upper case letter, and the rest stays as written ("Not accepted", "Nothing new imported (already in the catalog)").
- **Ellipsis:** a button or menu item that opens a dialog or a file picker ends with "…" (the single character U+2026, not three dots), e.g. "Save…", "Load .knxprod…".
- **Texts from data** (KNX master data, xknx field names) shown as labels go through `editor_gui.widgets.dpt_value.title_case`.
- **Translations** (`locales/<language>`, one folder per UI language in `editor_gui.languages`) follow the conventions of their own language, not these rules.
  - When a msgid changes, update the entry in the `.po` file of every language and compile them to `.mo` again.
  - Compile with Babel, which needs no gettext installation (same on Windows, Linux and macOS): `uv run --with babel pybabel compile -d <locales dir> -D <domain>` compiles every language of a domain, `-l <language>` only one. The domain is the name of the `.po` file, e.g. `project` in `apps/editor-gui/src/editor_gui/plugins/project/locales`, or `editor_gui` in `apps/editor-gui/src/editor_gui/locales`.
  - `apps/editor-gui/tests/test_translations.py` checks that every msgid is translated; `test_text_properties.py` that every text can be shown in every language, keeps the `{placeholders}` of the English text, and that a label per field (a dict) has no label twice.

## MCP

Every new UI feature gets MCP access in the same change:
- **Read and write:** whatever the user can see in the GUI, an MCP client can read; whatever the user can change, it can change too. Add or extend the tools in `apps/editor-gui/src/editor_gui/plugins/mcp/tools`, plus a `project_batch` op where bulk edits are likely.
- **Same behaviour as the GUI:** use the service methods the GUI uses, so every edit is one undo step with the same checks and messages.
- **Documented and tested:** describe parameters and results in the tool's docstring (the client only sees that), and test the tools in `apps/editor-gui/src/editor_gui/plugins/mcp/tests`.

## Adding a New Package

1. Create `packages/<name>/` with `pyproject.toml` (`name = "xknx-<name>"`), `src/xknxeditor/<name>/__init__.py`, `src/xknxeditor/<name>/py.typed`, and `tests/`
2. Add `"xknx-<name>"` to root `pyproject.toml` dependencies
3. Run `uv sync`
