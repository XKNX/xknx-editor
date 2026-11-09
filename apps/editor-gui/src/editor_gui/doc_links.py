"""Resolve product-matched documentation for the GUI and MCP catalog tools.

Search result cards and product detail fields establish the product identity before
any documentation link is returned. Unverified matches return None.
"""

from __future__ import annotations

import json
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from time import monotonic
from typing import Any, cast
from urllib.parse import (
    parse_qs,
    quote,
    quote_plus,
    unquote,
    urlencode,
    urljoin,
    urlparse,
)

import structlog

_log = structlog.get_logger("doc_links")

# Official domains for common KNX manufacturers, keyed by a lowercase substring of the catalog
# manufacturer name. Basis is the online catalog manufacturer list; domains verified 2026-09. This is
# the single source of truth — the MCP catalog tools import it from here.
#
# domain_for returns the FIRST matching key, so order matters: put the more specific key first where
# one name contains another (e.g. "ABB AG - BUSCH-JAEGER" must hit "busch" before "abb"). Short,
# ambiguous keys are qualified (e.g. "ise gmbh", "insta gmbh") so they do not match unrelated names.
MANUFACTURER_DOMAINS: dict[str, str] = {
    "siemens": "siemens.com",
    "busch": "busch-jaeger.de",
    "abb": "abb.com",
    "jung": "jung.de",
    "berker": "berker.com",
    "gira": "gira.com",
    "hager": "hager.com",
    "insta gmbh": "insta.de",
    "legrand": "legrand.com",
    "merten": "merten.de",
    "gewiss": "gewiss.com",
    "feller": "feller.ch",
    "vimar": "vimar.com",
    "theben": "theben.de",
    "somfy": "somfy.com",
    "zennio": "zennio.com",
    "mdt": "mdt.de",
    "weinzierl": "weinzierl.de",
    "elsner": "elsner-elektronik.de",
    "ekinex": "ekinex.com",
    "schneider": "se.com",
    "esylux": "esylux.com",
    "steinel": "steinel.de",
    "enertex": "enertex.de",
    "lingg": "lingg-janke.de",
    "ise gmbh": "ise.de",
    "basalte": "basalte.be",
    "iddero": "iddero.com",
    "arcus": "arcus-eds.de",
    "lunatone": "lunatone.com",
    "eelectron": "eelectron.com",
    "intesis": "intesis.com",
    "divus": "divus.eu",
    "warema": "warema.de",
    "b.e.g": "beg-luxomat.com",
    "peaknx": "peaknx.com",
    "1home": "1home.io",
    "casambi": "casambi.com",
    "dinuy": "dinuy.com",
    "blumotix": "blumotix.it",
    "tapko": "tapko.de",
    "ipas": "ipas-products.com",
    "interra": "interratechnology.com",
}

MANUFACTURER_DOC_DOMAINS: dict[str, tuple[str, ...]] = {
    "gira.com": ("gira.de", "gira-smartbuilding.net"),
    "jung.de": ("jung-group.com",),
    "hager.com": ("hager.ch",),
    "mdt.de": ("mdt-group.com",),
    "basalte.be": ("basalte.world",),
    "busch-jaeger.de": ("search.abb.com", "library.e.abb.com", "library.abb.com"),
}

_UA = {"User-Agent": "Mozilla/5.0 (compatible; xknx-editor)"}
_TIMEOUT = 20.0


@dataclass
class _Budget:
    deadline: float = field(default_factory=lambda: monotonic() + 60)
    requests: int = 0
    texts: dict[str, str | None] = field(default_factory=dict[str, str | None])

    def timeout(self) -> float:
        remaining = self.deadline - monotonic()
        if self.requests >= 32 or remaining <= 0:
            raise TimeoutError("Documentation lookup budget exhausted")
        self.requests += 1
        return min(_TIMEOUT, remaining)


_budget: ContextVar[_Budget | None] = ContextVar("documentation_budget", default=None)


def _timeout() -> float:
    budget = _budget.get()
    return budget.timeout() if budget else _TIMEOUT


def _check_deadline() -> None:
    budget = _budget.get()
    if budget and monotonic() >= budget.deadline:
        raise TimeoutError("Documentation lookup budget exhausted")


def domain_for(manufacturer: str | None) -> str | None:
    if not manufacturer:
        return None
    name = manufacturer.lower()
    return next((d for kw, d in MANUFACTURER_DOMAINS.items() if kw in name), None)


def knx_search_url(order_number: str | None, manufacturer: str | None = None) -> str:
    """Direct KNX device-database search URL (server-rendered, no search engine)."""
    term = (order_number or manufacturer or "").strip()
    return f"https://www.knx.org/de/gerate?title={quote_plus(term)}"


def _get_text(
    url: str,
    params: dict[str, str] | None = None,
    *,
    body: dict[str, Any] | None = None,
) -> str | None:
    import httpx

    budget = _budget.get()
    key = url + repr(params) + repr(body)
    if budget and key in budget.texts:
        return budget.texts[key]
    result = None
    try:
        with (
            httpx.Client(
                follow_redirects=True, timeout=_timeout(), headers=_UA
            ) as client,
            client.stream(
                "GET" if body is None else "POST", url, params=params, json=body
            ) as resp,
        ):
            resp.raise_for_status()
            data = bytearray()
            for chunk in resp.iter_bytes():
                data.extend(chunk)
                if len(data) > 4 * 1024 * 1024 or (
                    budget and monotonic() >= budget.deadline
                ):
                    raise ValueError("Document metadata exceeds lookup limits")
            result = data.decode(resp.encoding or "utf-8", errors="replace")
    except Exception as exc:
        _log.debug("doc fetch failed", url=url, error=str(exc))
    if budget:
        budget.texts[key] = result
    return result


def _json_request(
    url: str,
    params: dict[str, str] | None = None,
    *,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        if body is None:
            data = json.loads(_get_text(url, params) or "null")
        else:
            data = json.loads(_get_text(url, params, body=body) or "null")
        return _object(data)
    except ValueError as exc:
        _log.debug("document metadata unavailable", url=url, error=str(exc))
        return {}


def _object(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _objects(value: Any) -> list[dict[str, Any]]:
    return (
        [_object(item) for item in cast(list[Any], value)]
        if isinstance(value, list)
        else []
    )


def _strings(value: Any) -> list[str]:
    return (
        [item for item in cast(list[Any], value) if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    children: list[_Element] = field(default_factory=list["_Element"])
    chunks: list[str] = field(default_factory=list[str])
    parent: _Element | None = field(default=None, repr=False)

    @property
    def text(self) -> str:
        return " ".join(self.chunks).strip()

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def find(self, tag: str = "", class_name: str = "") -> list[_Element]:
        found: list[_Element] = []
        pending = list(reversed(self.children))
        while pending:
            _check_deadline()
            node = pending.pop()
            if (not tag or node.tag == tag) and (
                not class_name or node.has_class(class_name)
            ):
                found.append(node)
            pending.extend(reversed(node.children))
        return found


class _Page(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("", {})
        self.stack = [self.root]
        self.nodes = 0
        try:
            self.feed(html)
            self.close()
        except (ValueError, TimeoutError):
            self.root = _Element("", {})
            self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _check_deadline()
        self.nodes += 1
        if self.nodes > 20000 or len(self.stack) > 128:
            raise ValueError("Document markup exceeds lookup limits")
        node = _Element(tag, {k: v or "" for k, v in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        _check_deadline()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        _check_deadline()
        if self.stack[-1].attrs.get("type") == "application/json":
            self.stack[-1].chunks.append(data)
            return
        if data.strip() and not any(n.tag in {"script", "style"} for n in self.stack):
            for node in self.stack:
                node.chunks.append(data.strip())


def _normalized(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum())


def _contains_identifier(text: str, identifier: str) -> bool:
    parts = re.findall(r"[^\W_]+", identifier.casefold())
    if not parts:
        return False
    pattern = r"[\W_]*".join(r"\s*".join(re.escape(c) for c in p) for p in parts)
    return (
        re.search(r"(?<![^\W_])" + pattern + r"(?![^\W_])", text.casefold()) is not None
    )


def _model_token(name: str) -> str:
    token = name.split()[0] if name else ""
    if len(token) < 4 or not (any(c.isdigit() for c in token) or "-" in token):
        return ""
    return token


def _specific_order(order: str) -> bool:
    return bool(order) and not re.search(r"[?*]|xx", order, re.I)


def _product_matches(
    order: str, name: str, actual_order: str, actual_name: str
) -> bool:
    if _specific_order(order):
        return bool(actual_order) and _normalized(order) == _normalized(actual_order)
    return bool(name) and _normalized(name) == _normalized(actual_name)


def _manufacturer_matches(expected: str | None, actual: str) -> bool:
    if not expected or not actual:
        return False
    return _normalized(expected) == _normalized(actual) or (
        (domain := domain_for(expected)) is not None and domain == domain_for(actual)
    )


def _knx_candidates(term: str) -> list[str]:
    html = _get_text("https://www.knx.org/de/gerate", {"title": term})
    if not html:
        return []
    urls: list[str] = []
    for grid in _Page(html).root.find(class_name="listings-view__listings-grid"):
        for card in grid.find("article", "node-device-card"):
            for link in card.find("a", "node-device-card__wrapper"):
                href = link.attrs.get("href", "")
                if re.fullmatch(r"/de/gerate/[a-z0-9-]+", href):
                    urls.append("https://www.knx.org" + href)
    return list(dict.fromkeys(urls))[:5]


def _knx_product(
    page: str,
    manufacturer: str | None,
    order: str,
    name: str,
    manufacturer_id: str | None,
) -> _Element | None:
    html = _get_text(page)
    if not html:
        return None
    heroes = _Page(html).root.find(class_name="hero-header__content")
    if not heroes:
        return None
    hero = heroes[0]
    fields: dict[str, str] = {}
    for table in hero.find("table", "project-details-table"):
        for row in table.find("tr"):
            cells = row.find("td")
            if len(cells) == 2:
                fields[cells[0].text] = cells[1].text
    logos = hero.find("img", "hero-header__device-logo")
    actual_manufacturer = logos[0].attrs.get("alt", "") if logos else ""
    ids = {
        match.group(0).upper()
        for image in hero.find("img")
        if (match := re.search(r"M-[0-9A-Fa-f]{4}(?=_)", image.attrs.get("src", "")))
    }
    if manufacturer_id and ids:
        matched_manufacturer = ids == {manufacturer_id.upper()}
    else:
        matched_manufacturer = _manufacturer_matches(manufacturer, actual_manufacturer)
    if not matched_manufacturer or not _product_matches(
        order,
        name,
        fields.get("Bestellnummer", ""),
        fields.get("Katalogartikelname", ""),
    ):
        _log.debug("manual candidate rejected", page=page, reason="product mismatch")
        return None
    return hero


def _knx_documents(
    hero: _Element, manufacturer_id: str | None
) -> list[tuple[int, str]]:
    documents: list[tuple[int, str]] = []
    for section in hero.find("ul", "documentation-download-list"):
        for link in section.find("a"):
            url = link.attrs.get("href", "")
            if not url.startswith("https://"):
                continue
            match = re.search(r"/devices/\d+/(M-[0-9A-Fa-f]{4})_", url)
            if (
                manufacturer_id
                and match
                and match.group(1).upper() != manufacturer_id.upper()
            ):
                continue
            rank = _rank(link.text)
            if rank is None:
                rank = _rank(url)
            if (
                rank is None
                and match
                and _on_domain(url, "knx.org")
                and urlparse(url).path.endswith("/download")
            ):
                rank = 1
            if rank is not None and rank < 3:
                documents.append((rank, url))
    return documents


_KNXDOC_HINTS = (
    "knxappl",
    "applikationsbeschreibung",
    "application-description",
    "thb",
    "technisches-handbuch",
    "technisches_handbuch",
    "technical-manual",
    "_tm_",
    "handbook",
    "technical documentation",
    "technical documantation",
    "technische dokumentation",
    "produktdokumentation",
    "product documentation",
    "configuration instructions",
    "configuration manual",
    "product manual",
    "applikationshandbuch",
    "dokumentationen",
    "software_manual",
    "_sw_",
)
_DATASHEET_HINTS = ("_ds_", "-ds-", "datenblatt", "datasheet", "_db_")
_OTHER_DOC_HINTS = (
    "catalogue",
    "catalog",
    "katalog",
    "brochure",
    "broschüre",
    "declaration",
    "konformität",
    "rohs",
    "drawing",
    "zeichnung",
    "privacy",
    "datenschutz",
    "environmental",
    "safety warning",
    "nutzungsbedingungen",
    "lieferbedingungen",
)


def _duckduckgo_urls(query: str) -> list[str]:
    """Organic result URLs from DuckDuckGo's HTML endpoint, in order (best effort)."""
    html = _get_text("https://html.duckduckgo.com/html/", {"q": query})
    if not html:
        return []
    # Results are wrapped as //duckduckgo.com/l/?uddg=<url-encoded target>.
    urls = [unquote(u) for u in re.findall(r"uddg=([^\"&]+)", html)]
    if not urls:
        urls = re.findall(r'class="result__a"[^>]+href="([^"]+)"', html)
    return list(dict.fromkeys(unescape(url) for url in urls))


def _rank(url: str) -> int | None:
    """Relevance rank of a PDF URL (lower = better), or None if it is not a PDF.

    0 = KNX config doc (application description / technical manual), 1 = other PDF (e.g. a general
    operation manual), 2 = product datasheet."""
    parsed = urlparse(url)
    filenames = [
        value
        for key, values in parse_qs(parsed.query).items()
        if key.casefold() in {"filename", "p_file_name"}
        for value in values
    ]
    if not any(
        unquote(path).lower().endswith(".pdf") for path in [parsed.path, *filenames]
    ):
        return None
    return _document_rank(parsed.path + " " + " ".join(filenames))


def _document_rank(text: str) -> int:
    low = unquote(text).casefold()
    if any(h in low for h in _OTHER_DOC_HINTS):
        return 3
    if any(h in low for h in _KNXDOC_HINTS) or re.search(r"(?<![a-z])handbuch", low):
        return 0
    if any(h in low for h in _DATASHEET_HINTS):
        return 2
    return 1


def _on_domain(url: str, domain: str | None) -> bool:
    if not domain:
        return False
    host = (urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def _official_url(url: str, domain: str) -> bool:
    return urlparse(url).scheme in ("https", "http") and any(
        _on_domain(url, allowed)
        for allowed in (domain, *MANUFACTURER_DOC_DOMAINS.get(domain, ()))
    )


def _language_rank(url: str) -> int:
    if re.search(r"(?:/de(?:-DE)?(?:/|$)|_de[./]|languagecode=de)", url, re.I):
        return 0
    if re.search(r"(?:/en(?:-US)?(?:/|$)|_en[./]|languagecode=en)", url, re.I):
        return 1
    return 2


def _pdf_matches(url: str, identifier: str) -> bool:
    """Check the cover and device overview of a family manual, with bounded downloads."""
    import httpx
    from pypdf import PdfReader

    try:
        data = bytearray()
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=_timeout(), headers=_UA
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                budget = _budget.get()
                if len(data) > 20 * 1024 * 1024 or (
                    budget and monotonic() >= budget.deadline
                ):
                    return False
        if not data.startswith(b"%PDF"):
            return False
        reader = PdfReader(BytesIO(data))
        if not identifier:
            return bool(reader.pages)
        return any(
            _contains_identifier(page.extract_text(), identifier)
            for page in reader.pages[:6]
        )
    except Exception as exc:
        _log.debug("manual identity check failed", url=url, error=str(exc))
        return False


@dataclass(frozen=True)
class _Document:
    url: str
    rank: int
    product_matched: bool = False


def _download_url(url: str) -> bool:
    parsed = urlparse(url)
    return _rank(url) is not None or any(
        marker in parsed.path.lower()
        for marker in ("/downloads/download/", "/download.aspx", "/api/khub/documents/")
    )


def _document_context(link: _Element) -> str:
    if any(
        cls.endswith(("-item", "-card")) for cls in link.attrs.get("class", "").split()
    ):
        return link.text
    ancestors: list[_Element] = []
    parent = link.parent
    while parent and len(parent.text) < 1800:
        ancestors.append(parent)
        if parent.tag in {"article", "tr", "li"} or any(
            cls == "row" or cls.endswith(("-item", "-card"))
            for cls in parent.attrs.get("class", "").split()
        ):
            return parent.text
        parent = parent.parent
    context = link.text
    for ancestor in ancestors:
        if any(
            node.attrs.get("href") != link.attrs.get("href")
            and _download_url(node.attrs.get("href", ""))
            for node in ancestor.find("a")
        ):
            break
        context = ancestor.text
    return context


def _linked_documents(root: _Element, url: str, domain: str) -> list[_Document]:
    documents: dict[str, _Document] = {}
    for link in root.find("a"):
        target = urljoin(url, link.attrs.get("href", ""))
        if not _official_url(target, domain) or not _download_url(target):
            continue
        rank = _document_rank(_document_context(link) + " " + target)
        candidate = _Document(target, rank)
        if target not in documents or rank < documents[target].rank:
            documents[target] = candidate
    return [d for d in documents.values() if d.rank < 3]


def _siemens_documents(url: str, order: str, name: str) -> list[_Document]:
    match = re.fullmatch(r"/v/u/([^/]+)", urlparse(url).path)
    if not match:
        return []
    base = "https://sid.siemens.com"
    resolved = _json_request(
        base + "/internal/api/webapp/pretty-url/viewer",
        body={"prettyUrl": unquote(match[1]), "forcedTocId": None},
    )
    doc_id = resolved.get("documentId")
    if not isinstance(doc_id, str):
        return []
    doc_id = quote(doc_id, safe="")
    metadata = _json_request(base + "/internal/api/webapp/documents/" + doc_id)
    fields = {
        item["key"]: _strings(item.get("values"))
        for item in _objects(metadata.get("metadata"))
        if isinstance(item.get("key"), str)
    }
    identifier = order if _specific_order(order) else name
    identities = (
        fields.get("Products", [])
        if _specific_order(order)
        else fields.get("Article_type", [])
    )
    if (
        metadata.get("mimeType") != "application/pdf"
        or fields.get("Status_custom") != ["Valid"]
        or not any(
            _normalized(value) == _normalized(identifier) for value in identities
        )
    ):
        return []
    rank = _document_rank(
        " ".join(fields.get("Content_type_custom", []))
        + " "
        + str(metadata.get("title", ""))
    )
    return (
        [_Document(base + "/api/khub/documents/" + doc_id + "/content", rank, True)]
        if rank < 3
        else []
    )


def _siemens_index_documents(order: str) -> list[_Document]:
    if not _specific_order(order):
        return []
    base = "https://knx-td.sihub.siemens.com"
    script = _get_text(base + "/l10n/db_en.js") or ""
    match = re.fullmatch(r"\s*db\s*=\s*(\[.*\])\s*;?\s*", script, re.S)
    if not match:
        return []
    try:
        values = json.loads(re.sub(r",\s*\]$", "]", match[1]))
    except ValueError:
        return []
    fields = _strings(values)
    if (
        not isinstance(values, list)
        or len(fields) != len(cast(list[Any], values))
        or len(fields) % 18
    ):
        return []
    rows = [fields[i : i + 18] for i in range(0, len(fields), 18)]
    documents: list[_Document] = []
    for row in sorted(rows, key=lambda r: r[6], reverse=True):
        if row[0] != "ok" or _normalized(row[2]) != _normalized(order):
            continue
        for index, folder, rank in ((9, "apb", 0), (8, "bma", 1), (7, "tpi", 2)):
            target = urljoin(base + "/en/" + folder + "/", row[index])
            if (
                row[index]
                and _official_url(target, "siemens.com")
                and _download_url(target)
            ):
                documents.append(_Document(target, rank, True))
    return documents


def _abb_documents(root: _Element, order: str, name: str) -> list[_Document]:
    documents: list[_Document] = []
    for section in root.find():
        if not section.attrs.get("id", "").startswith("dsDownloadSection"):
            continue
        try:
            options = _object(
                json.loads(section.attrs.get("data-plugin-options", "{}"))
            )
        except ValueError:
            continue
        product = str(
            options.get("productId") or section.attrs.get("data-product-id", "")
        )
        identifier = order if _specific_order(order) else name
        if not product or not identifier:
            continue
        category = str(options.get("cid") or "9AAF627592|9AAF626597")
        data = _json_request(
            "https://ds.library.abb.com/api/downloadsection/documents/public/synopsis/DE/de/c",
            {
                "categoryIds": category,
                "languageCode": "de",
                "countryCode": "DE",
                "clientCode": str(options.get("clientCode") or "bjde"),
                "includeDocumentsFromSubcategories": "true",
                "includeExternalPublicLimitedDocuments": "false",
                "source": "ds",
                "productIds": product,
                "productIdDomains": "*",
            },
        )
        for item in _objects(data.get("DocumentContainer")):
            if product not in _strings(item.get("ProductID")):
                continue
            target = item.get("DocumentURL", "")
            if (
                not isinstance(target, str)
                or not _official_url(target, "abb.com")
                or str(item.get("RepresentationFileNameSuffix", "")).lower() != "pdf"
                or item.get("IsLatestVersion") is not True
            ):
                continue
            rank = _document_rank(
                str(item.get("DocumentKind", "")) + " " + str(item.get("Title", ""))
            )
            if rank < 3:
                documents.append(
                    _Document(
                        target, rank, _normalized(product) == _normalized(identifier)
                    )
                )
    return documents


def _page_documents(url: str, domain: str, order: str, name: str) -> list[_Document]:
    if _on_domain(url, "sid.siemens.com"):
        return _siemens_documents(url, order, name)
    html = _get_text(url)
    if not html:
        return []
    root = _Page(html).root
    candidates = _linked_documents(root, url, domain)
    if domain == "hager.com":
        candidates.extend(_hager_documents(root, order))
    if domain == "se.com":
        candidates.extend(_schneider_documents(html))
    if domain in {"abb.com", "busch-jaeger.de"}:
        candidates.extend(_abb_documents(root, order, name))
    return candidates


def _hager_documents(root: _Element, order: str) -> list[_Document]:
    if not _specific_order(order):
        return []
    documents: list[_Document] = []
    for script in root.find("script"):
        if script.attrs.get("id") != "__NEXT_DATA__":
            continue
        try:
            data = _object(json.loads("".join(script.chunks)))
        except ValueError:
            continue
        for key in (
            "props",
            "pageProps",
            "serverState",
            "initialResults",
            "downloadcenter",
        ):
            data = _object(data.get(key))
        for result in _objects(data.get("results")):
            for hit in _objects(result.get("hits")):
                target = hit.get("url")
                if (
                    not isinstance(target, str)
                    or not _official_url(target, "hager.com")
                    or str(hit.get("fileType", "")).lower() != "pdf"
                    or not any(
                        _normalized(ref) == _normalized(order)
                        for ref in _strings(hit.get("commercialRef"))
                    )
                ):
                    continue
                types = _strings(hit.get("assetType"))
                ranks = [
                    rank
                    for kind, rank in (
                        ("ProductApplicationDescription", 0),
                        ("ProductInstallationManual", 1),
                        ("GeneratedProductDatasheet", 2),
                    )
                    if kind in types
                ]
                if ranks:
                    documents.append(_Document(target, min(ranks), True))
    return documents


def _schneider_documents(html: str) -> list[_Document]:
    quoted = r'"(?:[^"\\]|\\.)*"'
    pattern = (
        rf"documentReference:({quoted}),documentName:({quoted}),"
        rf"documentType:({quoted})"
    )
    documents: list[_Document] = []
    for match in re.finditer(pattern, html):
        try:
            reference, filename, kind = (
                str(json.loads(value)) for value in match.groups()
            )
        except ValueError:
            continue
        if not filename.lower().endswith(".pdf"):
            continue
        rank = _document_rank(kind + " " + filename)
        if rank >= 3:
            continue
        target = "https://download.se.com/files?" + urlencode(
            {"p_enDocType": kind, "p_File_Name": filename, "p_Doc_Ref": reference}
        )
        documents.append(_Document(target, rank))
    return documents


def _manufacturer_pages(domain: str, order: str, name: str) -> list[str]:
    if domain == "theben.de" and (order or name):
        return [
            "https://www.theben.de/theben-searchfrontend/en/search?searchTerm="
            + quote_plus(order if _specific_order(order) else name)
        ]
    if not _specific_order(order):
        return []
    if domain == "hager.com":
        return [
            f"https://hager.com/{locale}/suchen?q={quote_plus(order)}"
            for locale in ("de", "de-ch")
        ]
    if domain == "se.com":
        return [
            f"https://www.se.com/{locale}/product/{quote(order, safe='')}/"
            for locale in ("de/de", "uk/en")
        ]
    if domain == "gira.com":
        return [
            "https://partner.gira.de/service/download/download.html"
            "?modus=suche_result&swert=" + quote_plus(order)
        ]
    if domain == "jung.de":
        return [
            "https://www.jung-group.com/de-DE/downloads/product/"
            + quote(order, safe="")
        ]
    if domain == "busch-jaeger.de" and re.fullmatch(r"2CKA[0-9A-Z]+", order):
        return ["https://www.busch-jaeger.de/online-katalog/detail/" + order]
    return []


def _select_document(
    candidates: list[_Document], identifier: str, checked: set[tuple[str, str]]
) -> str | None:
    for candidate in sorted(
        dict.fromkeys(candidates), key=lambda d: (d.rank, _language_rank(d.url))
    ):
        check = (candidate.url, "" if candidate.product_matched else identifier)
        if candidate.rank >= 3 or check in checked:
            continue
        if len(checked) >= 8:
            break
        checked.add(check)
        if _pdf_matches(*check):
            return candidate.url
    return None


def _mdt_document(order: str) -> str | None:
    if not _specific_order(order):
        return None
    base = "https://www.mdt.de"
    html = _get_text(base + "/suche.html", {"q": order})
    if not html:
        return None
    urls: list[str] = []
    for result in _Page(html).root.find(class_name="search-result"):
        for heading in result.find(class_name="results-topic"):
            for link in heading.find("a"):
                url = urljoin(base, link.attrs.get("href", ""))
                if (
                    _on_domain(url, "mdt.de")
                    and "/produkte/produktdetail/" in urlparse(url).path
                ):
                    urls.append(url)
    candidates: list[tuple[int, str]] = []
    for url in list(dict.fromkeys(urls))[:5]:
        html = _get_text(url)
        if not html:
            continue
        page = _Page(html).root
        if not any(
            _normalized(number.text) == _normalized(order)
            for variant in page.find(class_name="variant")
            for number in variant.find(class_name="articlenumber")
        ):
            continue
        for table in page.find("table", "mdt-downloads"):
            for row in table.find("tr"):
                if "Technisches Handbuch" not in row.text:
                    continue
                for link in row.find("a"):
                    document = urljoin(url, link.attrs.get("href", ""))
                    if _on_domain(document, "mdt.de") and _rank(document) is not None:
                        candidates.append((0, document))
    return _select_document(
        [_Document(url, rank) for rank, url in candidates], order, set()
    )


def _zennio_document(order: str, name: str) -> str | None:
    seen: set[str] = set()
    for term in dict.fromkeys(t for t in (_model_token(name), name, order) if t):
        html = _get_text("https://www.zennio.com/", {"s": term})
        if not html:
            continue
        urls: list[str] = []
        for archive in _Page(html).root.find(class_name="elementor-location-archive"):
            for card in archive.find(class_name="type-producto"):
                for link in card.find("a"):
                    url = link.attrs.get("href", "")
                    if (
                        _on_domain(url, "zennio.com")
                        and "/product/" in urlparse(url).path
                    ):
                        urls.append(url)
        for url in list(dict.fromkeys(urls))[:5]:
            if url in seen:
                continue
            seen.add(url)
            product_html = _get_text(url)
            if not product_html:
                continue
            products = _Page(product_html).root.find(
                class_name="elementor-location-single"
            )
            if not products:
                continue
            page = products[0]
            titles = page.find("h1")
            headings = page.find("div", "elementor-heading-title")
            actual_order = headings[0].text if headings else ""
            if not titles or not _product_matches(
                order, name, actual_order, titles[0].text
            ):
                continue
            documents: list[tuple[int, str]] = []
            for section in page.find("ul", "datasheet-manual-module-technical-note"):
                for item in section.find("li"):
                    if not (item.has_class("manual") or item.has_class("datasheet")):
                        continue
                    for link in item.find("a"):
                        document = urljoin(url, link.attrs.get("href", ""))
                        if _on_domain(document, "zennio.com"):
                            documents.append(
                                (0 if item.has_class("manual") else 2, document)
                            )
            if documents:
                return min(documents, key=lambda d: (d[0], _language_rank(d[1])))[1]
    return None


def _resolve_manual_url(
    manufacturer: str | None,
    order_number: str | None,
    product_name: str | None = None,
    *,
    manufacturer_id: str | None = None,
) -> str | None:
    """Return a product-matched document URL, or None when the identity is uncertain.

    Manufacturer and order number identify a product; an exact model identifies products
    whose order number is absent or a placeholder. Search pages and homepages are left to
    the caller's fallback links. Family manuals without a product match need manual selection.
    """
    order, name = (order_number or "").strip(), (product_name or "").strip()
    terms = list(dict.fromkeys(t for t in (order, name, _model_token(name)) if t))
    if not terms:
        return None
    domain = domain_for(manufacturer)
    documents: list[tuple[int, str]] = []
    seen: set[str] = set()
    for term in terms:
        for url in _knx_candidates(term):
            if url in seen:
                continue
            seen.add(url)
            hero = _knx_product(url, manufacturer, order, name, manufacturer_id)
            if hero is None:
                continue
            documents.extend(_knx_documents(hero, manufacturer_id))
            if domain == "zennio.com":
                for table in hero.find("table", "project-details-table"):
                    for row in table.find("tr"):
                        cells = row.find("td")
                        if len(cells) == 2 and cells[0].text == "Katalogartikelname":
                            name = cells[1].text
        if documents:
            break
    if (
        domain == "zennio.com"
        and (not manufacturer_id or manufacturer_id.upper() == "M-0071")
        and (document := _zennio_document(order, name))
    ):
        _log.info("manual resolved", source="manufacturer", url=document)
        return document
    if documents:
        document = min(documents, key=lambda d: (d[0], _language_rank(d[1])))[1]
        _log.info("manual resolved", source="knx.org", url=document)
        return document
    identifier = order if _specific_order(order) else name
    if not domain or not identifier:
        return None
    checked: set[tuple[str, str]] = set()
    candidates: list[_Document] = []
    pages: list[str] = []
    official_pages = _manufacturer_pages(domain, order, name)
    for page in official_pages:
        candidates.extend(_page_documents(page, domain, order, name))
        if document := _select_document(
            [c for c in candidates if c.rank == 0], identifier, checked
        ):
            _log.info("manual resolved", source="manufacturer", url=document)
            return document
    keys = list(dict.fromkeys(t for t in (order, name) if t))
    manufacturer_name = (manufacturer or "").strip()
    manufacturer_word = (
        manufacturer_name.split()[0].casefold() if manufacturer_name else ""
    )
    for suffix in ("Applikationsbeschreibung", "Technisches Handbuch", "manual", ""):
        for key in keys:
            prefix = (
                ""
                if manufacturer_word and manufacturer_word in key.casefold()
                else manufacturer_name
            )
            query = f"{prefix} {key} {suffix}".strip()
            for url in _duckduckgo_urls(query):
                if not _official_url(url, domain):
                    continue
                if _download_url(url):
                    rank = _rank(url)
                    if rank is None or rank < 3:
                        candidates.append(
                            _Document(url, rank if rank is not None else 1)
                        )
                elif url not in pages:
                    pages.append(url)
        if document := _select_document(
            [c for c in candidates if c.rank == 0], identifier, checked
        ):
            _log.info("manual resolved", source="manufacturer search", url=document)
            return document
    if domain == "basalte.be":
        for url in _duckduckgo_urls(f"site:basalte.world {name or order} manual"):
            if _official_url(url, domain) and _download_url(url):
                rank = _rank(url)
                candidates.append(_Document(url, rank if rank is not None else 1))
    pages = [page for page in pages if page not in official_pages]
    for page in pages[:5]:
        candidates.extend(_page_documents(page, domain, order, name))
    if domain == "siemens.com":
        candidates.extend(_siemens_index_documents(order))
    if document := _select_document(candidates, identifier, checked):
        _log.info("manual resolved", source="manufacturer search", url=document)
        return document
    if (
        domain == "mdt.de"
        and (not manufacturer_id or manufacturer_id.upper() == "M-0083")
        and (document := _mdt_document(order))
    ):
        _log.info("manual resolved", source="manufacturer", url=document)
        return document
    _log.info("manual not resolved", manufacturer=manufacturer, order=order)
    return None


def resolve_manual_url(
    manufacturer: str | None,
    order_number: str | None,
    product_name: str | None = None,
    *,
    manufacturer_id: str | None = None,
) -> str | None:
    """Find a product document through verified product pages, portals or PDF contents.

    Search failures and unverified matches return None. The lookup uses a shared request
    and time budget; callers can use their search links when a source is unavailable.
    """
    token = _budget.set(_Budget())
    try:
        return _resolve_manual_url(
            manufacturer, order_number, product_name, manufacturer_id=manufacturer_id
        )
    except TimeoutError:
        _log.info("manual lookup budget exhausted", manufacturer=manufacturer)
        return None
    finally:
        _budget.reset(token)
