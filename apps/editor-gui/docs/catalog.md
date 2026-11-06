# Catalog Architecture

## Overview

The catalog is a searchable database of KNX hardware and application programs, built by importing
`.knxprod` archives. It's independent of any single project, so devices can be browsed and added
without re-importing a knxprod every time.

The catalog is **not** GUI code: it's the standalone `xknxeditor-catalog` package
(`packages/catalog/src/xknxeditor/hardware/`). The GUI only wraps it in a thin adapter,
`plugins/catalog/service.py`.

## Two Layers

- **Library layer** — pure Python, no FastAPI required. The caller owns a SQLAlchemy engine
  (`xknxeditor.catalog.make_engine`) and calls query/ingest functions directly, or uses the
  higher-level `CatalogService` (`xknxeditor.catalog.core.service`) which owns the engine for you —
  this is what the GUI uses.

## Database Schema

SQLAlchemy models live in `xknxeditor/hardware/models.py`. The schema is created automatically
(`Base.metadata.create_all(engine)` on connect) — there are no Alembic migrations for this package.

| Table | Purpose |
|-------|---------|
| `manufacturers` | Manufacturer id (`M-XXXX`) and name |
| `applications` | An application program: id, name, application number/version, mask version |
| `hardware` | A physical device as described in a manufacturer's `Hardware.xml` |
| `hardware_programs` | Links a `hardware` row to an `application` and the source `.knxprod` file (`knxprod_path`); this is what a project device's `hardware2program_ref_id` points at |
| `hardware_program_medium_types` | Medium types (TP/IP/RF/...) a hardware program supports |
| `catalog_sections` | The manufacturer's browsing hierarchy (tree of sections) |
| `catalog_section_products` | An orderable product (`product_ref_id`) within a section, running a specific hardware program — the product-centric browse/selection unit |

## Usage

### Populating the Catalog

```bash
uv run generate-catalog device1.knxprod device2.knxprod
```

### Programmatic Access

```python
from pathlib import Path
from xknxeditor.catalog import CatalogService

catalog = CatalogService(
    Path("catalog.db")
)  # opens/creates; owns the engine + .knxprod store

added_path = catalog.import_knxprod(Path("device.knxprod").read_bytes())

products = catalog.list_products()  # product-first view (what add_device needs)
app = catalog.get_application(application_id)  # parsed, IR-backed Application
```

### GUI Adapter

`plugins/catalog/service.py`'s `CatalogService` wraps the package's `CatalogService` and adds only
what the panel needs on top: a `list_products()` cache (the panel re-reads it every frame),
path-based `import_knxprod`, and reporting which product refs were newly added by an import.

## Alternative Catalog

`plugins/catalog/alternative.py` implements the configurable HTTPS catalog at
`https://reko.x3muha.de`. Its public `/api/catalog/index.json` is fetched with gzip and
`If-None-Match`, cached atomically in a compressed file per server URL, and searched offline.
Only `.knxprod` entries are exposed. Failed updates retain the last valid snapshot.

The alternative tab starts an hourly background refresh. Reads of the published snapshot do
not wait for network requests. HTTP operations are serialized and spaced by at least 550 ms;
`Retry-After` blocks further requests without a retry loop. A daily download limit blocks
downloads until the server's `resetAt` / `Retry-After`, while public index updates remain usable.
Other 403, 404 and 503 failures are surfaced to the caller.

Downloads obtain a bearer token through `/api/auth/token` using locally saved credentials.
Expiration is based on `expiresIn`; `401 INVALID_TOKEN` renews and retries exactly once.
Redirects are refused, and the download path must remain on the configured server. Size and
SHA-256 must match the index before the existing `.knxprod` import runs under the shared IO lock.
Network requests run on workers. The verified bytes are imported on the UI thread, where the
caller also invalidates open-project catalog resolution; MCP marshals this step with `run_locked`.
Shutting down the editor discards pending results and stops scheduling requests without waiting for a
stalled HTTP response. Background network workers cannot import into a project during shutdown.

The index's program `version` is a hexadecimal byte. The table displays major.minor through the
editor's shared formatter (`23` becomes `2.3`, `2A` becomes `2.10`). Metadata keeps the raw value
and exposes `version_text` alongside it.

Registration follows the site's session/CSRF API: `/api/me`, `/api/auth/register`,
`/api/auth/register/verify`, `/api/auth/register/resend`, and `/api/auth/login` to resume with
saved credentials. Verification completes the email step; administrator approval remains separate.
Settings are saved with the shared private settings writer, tokens and session cookies are only
held in memory. The GUI and `mcp/tools/alternative_catalog.py` use the same client and service.

## Product / Application / Hardware IDs

- `application_id` (e.g. `"M-0001_A-0001-00-0001"`) identifies an application program, unique across
  all KNX products.
- `hardware2program_ref_id` (a `HardwareProgram.id`, e.g. `"HP-..."`) is what a project's `Device`
  row actually stores — it resolves to both the hardware and the application via the
  `hardware_programs` table.
- `product_ref_id` (on `catalog_section_products`) is the orderable product identity; this is what a
  device's `product_ref_id` column stores (what was "bought", as opposed to what program is loaded).

## Integration with Projects

A project device stores `product_ref_id` and `hardware2program_ref_id` — references into the
catalog, not a copy of the application data. On every read, the GUI's `ProjectService` resolves
`hardware2program_ref_id` back to an `Application` via the catalog (see `docs/architecture.md`). If
the referenced program isn't in the catalog (e.g. a fresh/emptied `catalog.db`), the device is
skipped with a warning log rather than crashing — see "Limitations" in `docs/project.md`.

## File Location

Default catalog used by the GUI: `config/catalog.xknxcatalog` (local runtime data), generated by `uv run
generate-catalog` (see `src/editor_gui/scripts/generate_catalog.py`). The `.xknxcatalog` extension is
just a naming convention — it's a plain SQLite file and can be inspected with any SQLite tool.
