# The @mcp.tool decorator registers each tool via its side effect, not a direct call.
# pyright: reportUnusedFunction=false
"""Catalog tools: local product browsing/import and the KNX online catalog.

Local reads/imports touch the catalog SQLAlchemy session and run via ``ctx.run_on_ui``. The online
(HTTP) fetches that do not touch the DB run on the MCP request thread so a slow network call does not
freeze the UI frame.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus, urlparse

from fastmcp.exceptions import ToolError

from editor_gui.cache import docs_cache_dir
from editor_gui.doc_links import MANUFACTURER_DOMAINS as _MANUFACTURER_DOMAINS
from editor_gui.doc_links import domain_for as _domain_for
from editor_gui.doc_links import resolve_manual_url as _resolve_manual_url
from editor_gui.plugins.mcp.context import McpContext, make_tool

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from xknxeditor.catalog import ProductSummary

_SLOW = 300.0
_MAX_DOC_BYTES = (
    40 * 1024 * 1024
)  # refuse to pull absurdly large files into the process
# Cap for an inline (base64) .knxprod upload. A single-device knxprod is usually < 1 MB; only large
# manufacturer bundles approach this. Keeps a remote client from streaming an unbounded blob into RAM.
_MAX_KNXPROD_BYTES = 25 * 1024 * 1024
# Documentation file extensions we read as plain text/markdown (besides PDF), e.g. OpenKNX
# Applikationsbeschreibungen hosted as .md in the GitHub module repos.
_TEXT_EXTS = (".md", ".markdown", ".txt")

# _MANUFACTURER_DOMAINS and _domain_for live in editor_gui.doc_links (single source of truth, also
# used by the project "Download PDF manual" button); imported above. _DOC_HOSTS (below) is built from
# the map values plus the extra hosts catalog_docs_fetch is allowed to download from.


def _added(products: list[ProductSummary]) -> dict[str, Any]:
    """The import result: product refs and each new product version."""
    return {
        "added_refs": sorted({p.product_ref_id for p in products}),
        "added_versions": [
            {
                "product_ref_id": p.product_ref_id,
                "hardware2program_ref_id": p.hardware2program_ref_id,
                "application_id": p.application_id,
                "application_version": p.application_version,
            }
            for p in products
        ],
    }


def _search_url(terms: str, *, site: str | None = None) -> str:
    # DuckDuckGo over Google: no cookie-consent wall (so the link opens straight to results, and an
    # automated client is less likely to be blocked) and it honours the ``site:`` operator, which
    # reliably surfaces the exact KNX device page / manufacturer product page.
    q = f"site:{site} {terms}" if site else terms
    return f"https://duckduckgo.com/?q={quote_plus(q)}"


# Extra documentation hosts beyond the primary manufacturer domains: some makers host manuals under a
# different TLD/portal than their main site, and KNX distributors host application descriptions for
# makers who do not publish direct PDFs themselves. (Subdomains of a listed domain are covered by
# _host_allowed, so e.g. partner.gira.com is already allowed via gira.com.) Verified 2026-09.
_EXTRA_DOC_HOSTS: frozenset[str] = frozenset(
    {
        # manufacturer alternate / documentation domains (subdomains of a map domain, e.g.
        # downloads.jung.de, assets.hager.com, *.feller.ch, *.zennio.com, are already covered)
        "basalte.world",  # Basalte downloads (primary basalte.be)
        "hager.ch",  # Hager CH manuals (primary hager.com)
        "gira-smartbuilding.net",  # Gira documentation portal
        "jung-group.com",  # JUNG download portal (primary jung.de)
        # KNX distributor documentation portals
        "futurasmus-knxgroup.org",
        "futurasmus-knxgroup.com",
        "futurasmus-knxgroup.de",
        "futurasmus-knxgroup.es",
        "siblik.com",  # hosts MDT and other application descriptions
        "nevalux.swiss",  # hosts STEINEL and other application descriptions
        "interra-rus.com",  # Interra datasheets/manuals
    }
)

# Hosts of documents the tool itself resolved via catalog_docs (resolve_manual_url). Whatever our own
# best-effort resolver produced is trusted to be fetchable, so catalog_docs_fetch can read it without
# us having to hardcode every manufacturer's alternate/reseller domain. It is NOT an open proxy: only
# hosts the resolver actually returned this session are added (never arbitrary LLM-supplied URLs).
_RESOLVED_DOC_HOSTS: set[str] = set()

# Hosts catalog_docs_fetch may download from: knx.org, the known manufacturer domains, GitHub
# (OpenKNX), and the extra documentation hosts above. An allowlist keeps the server-side fetch a
# documentation tool, not an open SSRF proxy.
_DOC_HOSTS: frozenset[str] = frozenset(
    {"knx.org", "github.com", "raw.githubusercontent.com"}
    | _EXTRA_DOC_HOSTS
    | set(_MANUFACTURER_DOMAINS.values())
)


def _host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in _RESOLVED_DOC_HOSTS:  # a host our own resolver produced this session
        return True
    return any(host == d or host.endswith("." + d) for d in _DOC_HOSTS)


def _normalize_doc_url(url: str) -> str:
    """Rewrite a GitHub ``blob`` (HTML view) URL to its raw-file URL so we fetch the document itself.

    github.com/<owner>/<repo>/blob/<ref>/<path> -> raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>.
    Other URLs are returned unchanged."""
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.lower() in ("github.com", "www.github.com"):
        parts = parsed.path.split("/")  # ['', owner, repo, 'blob', ref, ...path]
        if len(parts) >= 6 and parts[3] == "blob":
            raw_path = "/".join([parts[1], parts[2], *parts[4:]])
            return f"https://raw.githubusercontent.com/{raw_path}"
    return url


def _is_text_url(url: str) -> bool:
    """True if the URL points at a markdown/text document (read as text, not parsed as PDF)."""
    return urlparse(url).path.lower().endswith(_TEXT_EXTS)


# Redirects are followed by hand so every hop can be re-checked against the allowlist. Following
# them automatically would make the allowlist a check on the URL we *asked* for rather than the host
# we actually fetch from: any allowlisted site (or one that has been taken over, or simply an open
# redirector) could bounce us to an intranet address or a cloud metadata endpoint, and the body would
# come back to the model as if it were a manufacturer's manual.
_MAX_REDIRECTS = 5


def _http_get(url: str) -> bytes:
    import httpx

    try:
        with httpx.Client(follow_redirects=False, timeout=30.0) as client:
            for _ in range(_MAX_REDIRECTS):
                resp = client.get(url, headers={"User-Agent": "xknx-editor"})
                if resp.next_request is None:
                    resp.raise_for_status()
                    return resp.content
                url = str(
                    resp.next_request.url
                )  # resolved against the current URL by httpx
                if not _host_allowed(url):
                    raise ToolError(
                        f"refusing to follow a redirect to a host that is not allowlisted: {url}"
                    )
            raise ToolError(f"too many redirects while downloading {url}")
    except httpx.HTTPError as exc:
        raise ToolError(f"could not download {url}: {exc}") from exc


# Persistent, on-disk cache of documentation PDFs. Device manuals are a small, stable set of
# important facts, so we keep ALL fetched PDFs (not an LRU): once downloaded, later reads are instant
# and work offline. A per-process byte cache sits on top for hot reuse (outline → find → page).
_MEM_CACHE: dict[str, bytes] = {}


def _docs_cache_dir() -> Path:
    d = docs_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(url: str) -> Path:
    # Human-recognisable filename (so it can be found by device/manual name) + short hash for
    # uniqueness across hosts/versions.
    base = re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1]) or "document"
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    return _docs_cache_dir() / f"{digest}_{base}"


def _index_path() -> Path:
    return _docs_cache_dir() / "index.json"


def _read_index() -> dict[str, str]:
    try:
        return json.loads(_index_path().read_text())
    except (OSError, ValueError):
        return {}


def _download_doc(url: str, refresh: bool = False) -> bytes:
    """Return a documentation file's bytes, cached on disk (allowlisted hosts, size-capped).

    Accepts PDF manuals and markdown/text docs (e.g. OpenKNX Applikationsbeschreibungen on GitHub).
    Downloaded once and kept; ``refresh=True`` forces a re-download (e.g. after a doc update)."""
    if not _host_allowed(url):
        raise ToolError(
            "url host not allowed; catalog_docs_fetch only downloads from knx.org, known "
            "manufacturer sites or github. Use catalog_docs to get a valid documentation link."
        )
    if not refresh and url in _MEM_CACHE:
        return _MEM_CACHE[url]
    path = _cache_path(url)
    if not refresh and path.exists():
        data = path.read_bytes()
    else:
        data = _http_get(url)
        if len(data) > _MAX_DOC_BYTES:
            raise ToolError(
                f"document is larger than {_MAX_DOC_BYTES // (1024 * 1024)} MB"
            )
        if not (data[:5].startswith(b"%PDF") or _is_text_url(url)):
            raise ToolError(
                "the URL did not return a PDF or a markdown/text document (it may be an HTML page). "
                "Use catalog_docs to find the actual document link, or open the page yourself."
            )
        path.write_bytes(data)
        index = _read_index()
        index[path.name] = url
        _index_path().write_text(json.dumps(index, indent=2, sort_keys=True))
    _MEM_CACHE[url] = data
    return data


def _pdf_outline(reader: Any) -> list[dict[str, Any]]:
    """Flatten a PDF's bookmark outline into ``{title, page}`` entries (empty if none)."""
    entries: list[dict[str, Any]] = []

    def _walk(items: Any) -> None:
        for item in items:
            if isinstance(item, list):
                _walk(item)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
                entries.append({"title": str(item.title), "page": page})
            except Exception:
                continue

    try:
        _walk(reader.outline)
    except Exception:
        return []
    return entries


def _markdown_outline(text: str) -> list[dict[str, Any]]:
    """Markdown headings as ``{level, title, line}`` entries — a table of contents for a text doc."""
    entries: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            entries.append(
                {"level": len(m.group(1)), "title": m.group(2).strip(), "line": i}
            )
    return entries


def _read_text_doc(
    url: str, data: bytes, find: str | None, max_chars: int
) -> dict[str, Any]:
    """Read a markdown/text doc: heading outline + capped text, or ``find`` line matches."""
    content = data.decode("utf-8", errors="replace")
    result: dict[str, Any] = {
        "url": url,
        "content_type": "text",
        "total_chars": len(content),
    }
    if find:
        needle = find.lower()
        matches = [
            {"line": i, "text": line.strip()[:200]}
            for i, line in enumerate(content.splitlines(), start=1)
            if needle in line.lower()
        ]
        return result | {
            "find": find,
            "match_count": len(matches),
            "matches": matches[:50],
        }
    return result | {
        "outline": _markdown_outline(content),
        "text": content[:max_chars],
        "truncated": len(content) > max_chars,
        "hint": "Text/markdown doc (no pages): use find='term' to locate a keyword, or read the returned text.",
    }


def register(mcp: FastMCP, ctx: McpContext) -> None:
    tool = make_tool(mcp, ctx)
    catalog = ctx.api.catalog

    @tool(read_only=True)
    def catalog_list_products(
        name_contains: str | None = None, manufacturer_contains: str | None = None
    ) -> dict[str, Any]:
        """List local catalog product versions. name_contains matches product names/order numbers;
        manufacturer_contains matches manufacturer names. Both filters are case-insensitive.
        Returns {items, count}; each item has {product_ref_id, hardware2program_ref_id, name,
        order_number, application_id, application_version, manufacturer_id, manufacturer_name}.
        Pass product_ref_id and optional hardware2program_ref_id to project_add_device; pass
        application_id to catalog_get_application. Filter large catalogs to limit the response."""

        def _matches(p: Any) -> bool:
            name = (name_contains or "").lower()
            mfr = (manufacturer_contains or "").lower()
            name_ok = not name or (
                name in (p.name or "").lower() or name in (p.order_number or "").lower()
            )
            mfr_ok = not mfr or mfr in (p.manufacturer_name or "").lower()
            return name_ok and mfr_ok

        products = ctx.run_locked(
            lambda: [
                {
                    "product_ref_id": p.product_ref_id,
                    "hardware2program_ref_id": p.hardware2program_ref_id,
                    "name": p.name,
                    "order_number": p.order_number,
                    "application_id": p.application_id,
                    "application_version": p.application_version,
                    "manufacturer_id": p.manufacturer_id,
                    "manufacturer_name": p.manufacturer_name,
                }
                for p in catalog.get_products()
                if _matches(p)
            ]
        )
        return {"items": products, "count": len(products)}

    @tool(read_only=True)
    def catalog_get_application(application_id: str) -> dict[str, Any]:
        """Resolve a catalog application by `application_id` (from a catalog_list_products item's
        `application_id`). Returns {id, name}."""

        def _read() -> dict[str, Any]:
            app = catalog.get_application(application_id)
            if app is None:
                raise KeyError(f"application {application_id!r} not found")
            return {"id": app.id, "name": app.name}

        return ctx.run_locked(_read)

    @tool(read_only=True, open_world=True)
    def catalog_docs(
        query: str,
        product_ref_id: str | None = None,
        manufacturer: str | None = None,
    ) -> dict[str, Any]:
        """Find device documentation for a topic such as "dimming curve". Supply product_ref_id
        from catalog_list_products to resolve product details, or pass manufacturer directly.
        Returns {query, manufacturer, order_number, document_url, references, note}; each reference
        has {title, kind, url} and optionally hint.

        document_url is a best-effort manual link and may be null. Try it with catalog_docs_fetch,
        verify the exact order number and series/generation, then use references as fallbacks.
        Read only relevant sections (manuals are often large PDFs). The references are ordered
        best-source-first and are fallbacks for each other (manufacturer site / KNX device database /
        web search); if none is fetchable, ask the user to open a link and paste the relevant section
        rather than guessing. For configuration IDs and allowed values, use
        project_list_parameters/project_list_com_objects."""
        resolved_mfr = manufacturer
        order_number: str | None = None
        product_name: str | None = None
        if product_ref_id:

            def _lookup() -> tuple[str | None, str | None, str | None]:
                product = next(
                    (
                        p
                        for p in catalog.get_products()
                        if p.product_ref_id == product_ref_id
                    ),
                    None,
                )
                if product is None:
                    raise ToolError(
                        f"no catalog product {product_ref_id!r}; call catalog_list_products"
                    )
                return product.manufacturer_name, product.order_number, product.name

            resolved_mfr, order_number, product_name = ctx.run_locked(_lookup)

        # Best-effort resolved manual PDF, same resolver the GUI "Download PDF manual" button uses:
        # KNX application description / technical manual, preferring the manufacturer's own site
        # (parses knx.org, else a documentation web search). Network, on the request thread; may be
        # None. This is what the LLM should fetch first, instead of guessing a URL from the links.
        document_url = (
            _resolve_manual_url(resolved_mfr, order_number, product_name)
            if (order_number or product_name)
            else None
        )
        if document_url:
            # trust our own resolution so catalog_docs_fetch can read it, whatever host it landed on
            host = urlparse(document_url).hostname
            if host:
                _RESOLVED_DOC_HOSTS.add(host.lower())

        knx_terms = " ".join(t for t in (resolved_mfr, order_number, query) if t)
        web_terms = knx_terms
        domain = _domain_for(resolved_mfr)
        is_openknx = "openknx" in f"{resolved_mfr or ''} {query}".lower()
        references: list[dict[str, str]] = [
            {
                # The KNX device page carries the official DE/EN technical-documentation PDF. We link a
                # search (not the direct PDF): the PDF URL needs the device's catalog-item ref and exact
                # program version, which the catalog's hardware2program_ref_id does not fully carry, so a
                # constructed direct link would risk 404s. The device page is one click from the PDF.
                "title": "KNX device database (official documentation)",
                "kind": "knx_database",
                "url": _search_url(knx_terms, site="knx.org/de/gerate"),
                "hint": "Open the device page: it has the official DE/EN documentation PDF only if the manufacturer uploaded it; if it just links the manufacturer website, use the manufacturer result below.",
            }
        ]
        if domain:
            references.append(
                {
                    "title": f"{resolved_mfr} website",
                    "kind": "manufacturer_site",
                    "url": _search_url(order_number or query, site=domain),
                    "hint": "Product page: Datenblatt (short overview), Funktionsbeschreibung (medium), Technisches Handbuch (large/full). If the exact order number/series is not here, fall back to the KNX device database result above.",
                }
            )
        if is_openknx:
            references.append(
                {
                    "title": "OpenKNX hardware repository",
                    "kind": "openknx",
                    "url": "https://github.com/OpenKNX/OpenKNX/tree/main/Hardware",
                    "hint": "DIY/OpenKNX modules; each hardware folder has its own README/docs.",
                }
            )
            references.append(
                {
                    "title": "OpenKNX org search",
                    "kind": "openknx",
                    "url": f"https://github.com/search?q=org%3AOpenKNX+{quote_plus(query)}&type=code",
                }
            )
        references.append(
            {"title": "Web search", "kind": "web", "url": _search_url(web_terms)}
        )
        return {
            "query": query,
            "manufacturer": resolved_mfr,
            "order_number": order_number,
            "document_url": document_url,
            "references": references,
            "note": (
                "``document_url`` is best-effort (may be null, a wrong series/datasheet, or a host "
                "catalog_docs_fetch refuses) — fetch and VERIFY it first, otherwise fall back to "
                "``references``. See this tool's description for the full guidance."
            ),
        }

    @tool(open_world=True)
    def catalog_docs_fetch(
        url: str,
        page: int | None = None,
        find: str | None = None,
        max_chars: int = 6000,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Read a PDF or text/Markdown manual incrementally using a URL from catalog_docs. Verify
        the device's exact order number and series. GitHub blob URLs become raw-file URLs; downloads
        use allowed documentation hosts. Files are cached on disk; refresh=true downloads again.

        PDF results always include {url, total_pages}, plus:
        - page=N (1-based, takes precedence over find): {page, text, truncated}.
        - find="term": {find, pages}, with matching 1-based page numbers.
        - neither: {outline, hint}; outline entries have {title, page}, or the list is empty.

        Text/Markdown results always include {url, content_type: "text", total_chars}; page is ignored.
        - find="term": adds {find, match_count, matches}; matches has at most 50 {line, text} entries
          with 1-based line numbers and snippets up to 200 characters; match_count counts all matches.
        - no find: adds {outline, text, truncated, hint}; outline entries have {level, title, line}.

        max_chars caps returned page/document text. find is a case-insensitive substring search;
        try German/English terms when needed. Use find when a PDF has no outline. Returns the source
        url for verification; this does not change the project."""
        from pypdf import PdfReader

        # max_chars slices the returned text; a negative value would return almost the whole
        # document (Python's text[:negative]). Clamp to a positive value with a hard ceiling.
        max_chars = max(1, min(max_chars, 200_000))
        url = _normalize_doc_url(url)
        data = _download_doc(url, refresh=refresh)
        if not data[:5].startswith(b"%PDF"):
            return _read_text_doc(url, data, find, max_chars)

        reader = PdfReader(io.BytesIO(data))
        total = len(reader.pages)
        result: dict[str, Any] = {"url": url, "total_pages": total}
        if page is not None:
            if not 1 <= page <= total:
                raise ToolError(f"page {page} out of range (1..{total})")
            text = reader.pages[page - 1].extract_text() or ""
            return result | {
                "page": page,
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
            }
        if find:
            needle = find.lower()
            pages = [
                i + 1
                for i, pg in enumerate(reader.pages)
                if needle in (pg.extract_text() or "").lower()
            ]
            return result | {"find": find, "pages": pages}
        return result | {
            "outline": _pdf_outline(reader),
            "hint": "Pick a page from the outline and call again with page=N, or find='term' to locate a keyword.",
        }

    @tool(read_only=True)
    def catalog_docs_cached() -> dict[str, Any]:
        """List documentation files cached on the editor host. Returns {items, count}; each item has
        {filename, url, size_bytes}. Pass url to catalog_docs_fetch to reuse a PDF or text/Markdown
        file offline without searching or downloading again."""
        index = _read_index()
        cache_dir = _docs_cache_dir()
        items: list[dict[str, Any]] = []
        for name, src in sorted(index.items()):
            path = cache_dir / name
            if path.exists():
                items.append(
                    {"filename": name, "url": src, "size_bytes": path.stat().st_size}
                )
        return {"items": items, "count": len(items)}

    @tool
    def catalog_import_knxprod(path: str) -> dict[str, Any]:
        """Import a .knxprod at path on the editor host into the local catalog, keeping existing
        versions. For a file on a remote client, use catalog_import_knxprod_bytes.
        Returns {added_refs, added_versions}: added_refs lists distinct product refs for added versions;
        each added_versions item has {product_ref_id, hardware2program_ref_id, application_id,
        application_version}. Pass product_ref_id and optional hardware2program_ref_id to
        project_add_device. Catalog imports are outside project_undo history and do not program
        devices."""
        added = ctx.run_locked(
            lambda: catalog.import_knxprod(Path(path)), timeout=_SLOW
        )
        return _added(added)

    @tool
    def catalog_import_knxprod_bytes(filename: str, data_base64: str) -> dict[str, Any]:
        """Import a .knxprod from a remote client: pass its filename and base64 bytes as data_base64
        (decoded size at most 25 MB). Prefer catalog_import_knxprod for files on the editor host.
        Returns {added_refs, added_versions, filename, size_bytes}; filename is sanitized and
        size_bytes is the decoded size. added_refs lists distinct product refs for added versions;
        each added_versions item has {product_ref_id, hardware2program_ref_id, application_id,
        application_version}. Pass product_ref_id and optional hardware2program_ref_id to
        project_add_device. This changes the local catalog, outside project_undo history."""
        try:
            raw = base64.b64decode(data_base64, validate=True)
        except ValueError as exc:
            raise ToolError(f"data_base64 is not valid base64: {exc}") from exc
        if not raw:
            raise ToolError("data_base64 is empty")
        if len(raw) > _MAX_KNXPROD_BYTES:
            raise ToolError(
                f"knxprod is larger than {_MAX_KNXPROD_BYTES // (1024 * 1024)} MB"
            )
        if raw[:2] != b"PK":  # .knxprod is a ZIP archive
            raise ToolError("data is not a .knxprod archive (ZIP signature missing)")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename.rsplit("/", 1)[-1]) or "upload"
        if not safe.lower().endswith(".knxprod"):
            safe += ".knxprod"
        tmp_dir = Path(tempfile.mkdtemp(prefix="xknxeditor-mcp-"))
        tmp = tmp_dir / safe
        try:
            tmp.write_bytes(raw)
            added = ctx.run_locked(lambda: catalog.import_knxprod(tmp), timeout=_SLOW)
        finally:
            tmp.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                tmp_dir.rmdir()
        return {**_added(added), "filename": safe, "size_bytes": len(raw)}

    @tool(destructive=True)
    def catalog_delete_product(product_ref_id: str) -> dict[str, Any]:
        """Delete all versions of a local catalog product using product_ref_id from
        catalog_list_products. Also removes exclusively used programs, hardware, applications,
        sections, manufacturers and stored archives. This is irreversible with project_undo.
        Returns {product_ref_id, removed_programs, removed_applications, removed_hardware,
        removed_sections, removed_manufacturers, removed_files, affected_devices}. The removed lists
        contain IDs, except removed_files contains filenames. affected_devices counts open-project
        devices that cannot be loaded until the product is reimported; hardware is unchanged."""
        project = ctx.api.project

        def _delete() -> dict[str, Any]:
            plan = catalog.plan_delete_product(product_ref_id)
            if plan is None:
                raise ToolError(
                    f"product {product_ref_id!r} is not in the local catalog"
                )
            affected = project.count_devices_losing_product(plan)
            deletion = catalog.delete_product(product_ref_id)
            if deletion is None:
                raise ToolError(
                    f"product {product_ref_id!r} is not in the local catalog"
                )
            project.forget_deleted_product(deletion)
            return {
                "product_ref_id": product_ref_id,
                "removed_programs": deletion.removed_programs,
                "removed_applications": deletion.removed_applications,
                "removed_hardware": deletion.removed_hardware,
                "removed_sections": deletion.removed_sections,
                "removed_manufacturers": deletion.removed_manufacturers,
                "removed_files": [Path(f).name for f in deletion.removed_files],
                "affected_devices": affected,
            }

        return ctx.run_locked(_delete, timeout=_SLOW)

    @tool(read_only=True)
    def catalog_get_product_details(hardware2program_ref_id: str) -> dict[str, Any]:
        """The technical facts of a local catalog product version, using hardware2program_ref_id
        from catalog_list_products. Returns {hardware2program_ref_id, mask_version, is_secure,
        registration_number, registration_date, mediums}; registration_date is ISO 8601, a value
        the product does not state is null."""

        def _read() -> dict[str, Any]:
            facts = catalog.product_facts(hardware2program_ref_id)
            if facts is None:
                raise ToolError(
                    f"product version {hardware2program_ref_id!r} is not in the local catalog"
                )
            return {
                "hardware2program_ref_id": hardware2program_ref_id,
                "mask_version": facts.mask_version,
                "is_secure": facts.is_secure,
                "registration_number": facts.registration_number,
                "registration_date": (
                    facts.registration_date.isoformat()
                    if facts.registration_date is not None
                    else None
                ),
                "mediums": list(facts.mediums),
            }

        return ctx.run_locked(_read)

    @tool(destructive=True)
    def catalog_export_knxprod(
        hardware2program_ref_id: str, dest: str
    ) -> dict[str, Any]:
        """Save the .knxprod archive a local catalog product version was imported from to the
        file ``dest`` (a path on the editor's machine), using hardware2program_ref_id from
        catalog_list_products. The archive is copied as imported, so a manufacturer bundle is
        exported whole. Returns {hardware2program_ref_id, dest}."""
        import shutil

        source = ctx.run_locked(
            lambda: catalog.get_program_source(hardware2program_ref_id)
        )
        if source is None:
            raise ToolError(
                f"no stored .knxprod archive for product version {hardware2program_ref_id!r}"
            )
        try:
            shutil.copyfile(source[0], dest)
        except OSError as exc:
            raise ToolError(f"cannot save {dest!r}: {exc}") from exc
        return {"hardware2program_ref_id": hardware2program_ref_id, "dest": dest}

    # --- online catalog ---------------------------------------------------

    @tool(read_only=True)
    def catalog_online_language() -> dict[str, Any]:
        """Return {language, languages}: the languages online products are downloaded in, the
        editor's language first (e.g. "de-DE"), then its other languages."""
        return {
            "language": catalog.online_language,
            "languages": catalog.online_languages,
        }

    @tool
    def catalog_set_online_language(code: str) -> dict[str, str]:
        """Download online products in code only (such as "de-DE") for the rest of the session,
        instead of the editor's languages. Returns {language}, containing code. This setting is
        outside project_undo history."""
        catalog.set_online_language(code)
        return {"language": code}

    @tool(open_world=True)
    def catalog_refresh_online_manufacturers() -> dict[str, Any]:
        """Refresh the online manufacturer list over the network. Returns {items, count}; each item
        has {id, name}. Pass id as manufacturer_id to catalog_online_items."""
        items = [
            {"id": m.id, "name": m.name} for m in catalog.refresh_online_manufacturers()
        ]
        return {"items": items, "count": len(items)}

    @tool(read_only=True, open_world=True)
    def catalog_online_items(manufacturer_id: int) -> dict[str, Any]:
        """List a manufacturer's online catalog products over the network. Pass id from
        catalog_refresh_online_manufacturers as manufacturer_id. Returns {items, count}; each item
        has {id, name, order_number, downloadable}. Collect id values from downloadable items into
        catalog_item_ids for catalog_download_online_products."""
        items = [
            {
                "id": item.id,
                "name": item.name,
                "order_number": item.order_number,
                "downloadable": item.downloadable,
            }
            for item in catalog.online_catalog_items(manufacturer_id)
        ]
        return {"items": items, "count": len(items)}

    @tool(open_world=True)
    def catalog_download_online_products(
        catalog_item_ids: list[str], language_ids: list[str] | None = None
    ) -> dict[str, Any]:
        """Download and import online products into the local catalog. catalog_item_ids must be a
        nonempty list of id values from downloadable catalog_online_items entries. Omitted or empty
        language_ids uses the languages of catalog_online_language; otherwise use codes such as
        "de-DE".
        Returns {added_refs, added_versions}; added_refs lists distinct product refs for added versions;
        each added_versions item has {product_ref_id, hardware2program_ref_id, application_id,
        application_version}. Pass product_ref_id and optional hardware2program_ref_id to
        project_add_device. The import is outside project_undo history and does not program devices."""
        if not catalog_item_ids:
            raise ToolError("catalog_item_ids must not be empty")
        added = ctx.run_locked(
            lambda: catalog.download_online_products(catalog_item_ids, language_ids),
            timeout=_SLOW,
        )
        return _added(added)
