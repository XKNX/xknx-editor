"""Client for the anonymous KNX online catalog service.

It is a plain HTTP file-style REST service with no authentication:

- ``GET {base}/Download/Manufacturers`` -> XML, one ``<unsignedShort>`` per manufacturer
- the manufacturer *names* come from the public master data file
  (``https://update.knx.org/data/XML/project-23/knx_master.xml``), the same file shipped
  in every ``.knxprod`` and our ``.knxproj`` export already uses

Beyond the manufacturer list, a manufacturer's product index
(``GET {base}/Download/Index/{mfr}``) and the ``.knxprod`` download
(``POST {base}/Download/DownloadProduct`` with ``{"CatalogIds": [...], "LanguageIds": [...]}``)
are also anonymous, so the GUI can browse and download products without a MyKnx login.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeGuard, cast

from editor_gui.languages import LANGUAGES, culture

DEFAULT_BASE_URL = "https://onlinecatalog.knx.org"
MASTER_DATA_URL = "https://update.knx.org/data/XML/project-23/knx_master.xml"
_CACHE_FILE = "online_catalog_manufacturers.json"
_INDEX_CACHE_FILE = "online_catalog_index.json"
# Raised when the cached items' meaning changes, so an older cache is rebuilt (2: market check,
# 3: plugin and incomplete-information flags no longer block, 4: an empty market list offers
# nothing, 5: each item's text languages).
_INDEX_FORMAT = 5
_INDEX_SAVE_EVERY = (
    10  # persist the growing index every N manufacturers, for resumability
)
_TIMEOUT_SECONDS = 30.0


def download_languages(locale: str) -> list[str]:
    """The languages products are downloaded in for the editor language ``locale``: that one
    first, then the editor's other languages, so the texts are there after switching."""
    first = culture(locale)
    return [first, *(lang.culture for lang in LANGUAGES if lang.culture != first)]


# The market of each country the online catalog has one for (ISO 3166 code -> its market's name).
MARKET_REGIONS: dict[str, str] = {
    "AE": "United Arab Emirates", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "BE": "Belgium", "BH": "Bahrain", "BN": "Brunei", "BR": "Brazil", "BY": "Belarus",
    "CH": "Switzerland", "CL": "Chile", "CN": "China", "CO": "Colombia", "CZ": "Czech Republic",
    "DE": "Germany", "DK": "Denmark", "EE": "Estonia", "ES": "Spain", "FI": "Finland",
    "FR": "France", "GB": "United Kingdom", "GR": "Greece", "HK": "Hong Kong", "HR": "Croatia",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IN": "India", "IS": "Iceland",
    "IT": "Italy", "JP": "Japan", "KH": "Cambodia", "KR": "Korea", "KW": "Kuwait",
    "KZ": "Kazakhstan", "LA": "Laos", "LB": "Lebanon", "LI": "Liechtenstein", "LT": "Lithuania",
    "LU": "Luxemburg", "LV": "Latvia", "MM": "Myanmar", "MO": "Macau", "MX": "Mexico",
    "MY": "Malaysia", "NL": "Netherlands", "NO": "Norway", "NZ": "New Zealand", "PH": "Philippines",
    "PL": "Poland", "PT": "Portugal", "QA": "Qatar", "RO": "Romania", "RS": "Serbia",
    "RU": "Russia", "SA": "Saudi Arabia", "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
    "SK": "Slovakia", "TH": "Thailand", "TL": "Timor-Leste", "TR": "Turkey", "TW": "Taiwan",
    "UA": "Ukraine", "US": "USA", "UY": "Uruguay", "VN": "Vietnam", "ZA": "South Africa",
}  # fmt: skip


def market_for_region(markets: Iterable[OnlineMarket], region: str) -> str | None:
    """The id of the market of the country ``region`` ("DE"), when the catalog has one."""
    name = MARKET_REGIONS.get(region.upper())
    return next((m.id for m in markets if m.name == name), None) if name else None


def offered(item: OnlineCatalogItem, market: str | None, language: str | None) -> bool:
    """Whether ``item`` is offered in ``market`` (an id; None for all markets) and has texts in
    ``language`` ("de-DE", any region; None for all). Unknown markets or languages count as
    offered, so an older index hides nothing."""
    if market is not None and item.markets is not None and market not in item.markets:
        return False
    if language is not None and item.languages:
        base = language.split("-")[0].lower()
        return any(code.split("-")[0].lower() == base for code in item.languages)
    return True


def with_variants(wanted: list[str], offered: Iterable[str]) -> list[str]:
    """``wanted`` language ids plus the variants of the same languages a product ``offered``
    ("de-DE" also asks for "de" and "de-AT" when the product has those), each behind its language."""
    offered = list(offered)
    result: list[str] = []
    for language in wanted:
        base = language.split("-")[0].lower()
        for code in (language, *offered):
            if code.split("-")[0].lower() == base and code.lower() not in {
                r.lower() for r in result
            }:
                result.append(code)
    return result


class OnlineCatalogError(Exception):
    """Raised when the online catalog service cannot be reached or returns bad data."""


@dataclass(frozen=True)
class OnlineManufacturer:
    id: int
    name: str


@dataclass(frozen=True)
class OnlineCatalogSection:
    """A section of a manufacturer's online catalog, which files products; sections nest."""

    id: str
    name: str  # in the catalog's default language
    translations: tuple[tuple[str, str], ...] = ()  # (language such as "de-DE", name)

    def name_in(self, locale: str) -> str:
        """The name in ``locale`` ("de" or "de-DE", or several in order of preference, comma
        separated), else the default name."""
        return _name_in(self.name, self.translations, locale)


@dataclass(frozen=True)
class OnlineMarket:
    """A market (a country) of the online catalog, which offers some of a manufacturer's products."""

    id: str
    name: str  # in the catalog's default language
    translations: tuple[tuple[str, str], ...] = ()  # (language such as "de-DE", name)

    def name_in(self, locale: str) -> str:
        """The name in ``locale`` ("de" or "de-DE", or several in order of preference, comma
        separated), else the default name."""
        return _name_in(self.name, self.translations, locale)


def _name_in(name: str, translations: tuple[tuple[str, str], ...], locale: str) -> str:
    for language in locale.lower().split(","):
        language = language.strip()
        for wanted in (language, language.split("-")[0]):
            for code, text in translations:
                code = code.lower()
                if text and wanted in (code, code.split("-")[0]):
                    return text
    return name


@dataclass(frozen=True)
class OnlineCatalogItem:
    """A downloadable product entry from a manufacturer's online catalog index."""

    id: str
    name: str
    order_number: str
    downloadable: bool
    manufacturer_id: int = 0  # 0 = unknown (older cache entries)
    manufacturer_name: str = ""
    application_version: int | None = None  # application program version, e.g. 40
    application_program_name: str = ""  # e.g. "Dimmen 4fach, HSV/RGBW LED, REG"
    download_block_reason: str = (
        ""  # why the online download is refused (empty = downloadable)
    )
    # The sections filing the product, from the top one down (empty when none does).
    sections: tuple[OnlineCatalogSection, ...] = ()
    # The languages its texts come in, as the manufacturer labels them ("de-DE", "de", "es-MX").
    languages: tuple[str, ...] = ()
    # The ids of the markets offering it; None when the manufacturer's index lists no markets.
    markets: tuple[str, ...] | None = None


@dataclass(frozen=True)
class OnlineIndexStatus:
    """Summary of the on-disk product index cache (for the UI)."""

    manufacturers: int
    products: int
    fetched_at: str | None


def _local_name(tag: object) -> str:
    """The element's tag without its XML namespace (both feeds use namespaces)."""
    return str(tag).rsplit("}", 1)[-1]


def parse_manufacturer_ids(xml_bytes: bytes) -> list[int]:
    """Parse the ``Download/Manufacturers`` XML into a sorted list of manufacturer ids."""
    root = ET.fromstring(xml_bytes)
    ids = [
        int(cast(str, e.text))
        for e in root
        if _local_name(e.tag) == "unsignedShort" and (e.text or "").strip()
    ]
    if not ids:
        raise OnlineCatalogError("manufacturer list is empty")
    return sorted(ids)


def parse_manufacturer_names(xml_bytes: bytes) -> dict[int, str]:
    """Parse ``knx_master.xml`` into ``{manufacturer_id: name}``."""
    root = ET.fromstring(xml_bytes)
    names: dict[int, str] = {}
    for element in root.iter():
        if _local_name(element.tag) != "Manufacturer":
            continue
        # The numeric KNX manufacturer id is ``KnxManufacturerId``; ``Id`` is ``M-xxxx``.
        mid = element.get("KnxManufacturerId") or (
            element.get("Id") or ""
        ).removeprefix("M-")
        if not mid.isdigit():
            continue
        names[int(mid)] = element.get("Name") or f"M-{int(mid):04d}"
    if not names:
        raise OnlineCatalogError("master data contains no manufacturers")
    return names


_USER_AGENT = "xknx-editor/0.1"


def _unwrap_products_knxprod(response: bytes) -> bytes:
    """The DownloadProduct response is a ZIP wrapping the real archive as ``products.knxprod`` (plus
    an ``id-mapping.xml``). Return the inner ``.knxprod`` bytes. If the response is already a bare
    ``.knxprod`` (a ZIP with ``knx_master.xml`` at the root and no wrapper entry), return it
    unchanged, so this stays safe whatever shape the server sends."""
    try:
        with zipfile.ZipFile(io.BytesIO(response)) as zf:
            inner = next(
                (
                    n
                    for n in zf.namelist()
                    if n.rsplit("/", 1)[-1] == "products.knxprod"
                ),
                None,
            )
            if inner is not None:
                return zf.read(inner)
    except zipfile.BadZipFile:
        pass  # not a ZIP at all -> let the caller's import surface a clear error
    return response


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    """The server's error-response body (truncated), where a 4xx explains itself. ``urllib`` would
    otherwise reduce it to a bare ``reason`` like 'Bad Request', hiding the actual cause."""
    try:
        body = exc.read(2048).decode("utf-8", "replace").strip()
    except Exception:
        return ""
    body = " ".join(body.split())  # collapse whitespace/newlines for a one-line log
    return f" - {body}" if body else ""


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise OnlineCatalogError(
            f"{url}: {exc.code} {exc.reason}{_http_error_detail(exc)}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise OnlineCatalogError(f"{url}: {reason}") from exc


def _http_post(url: str, body: bytes, content_type: str = "application/json") -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": _USER_AGENT, "Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise OnlineCatalogError(
            f"{url}: {exc.code} {exc.reason}{_http_error_detail(exc)}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise OnlineCatalogError(f"{url}: {reason}") from exc


def _is_dict(value: object) -> TypeGuard[dict[str, object]]:
    """Whether a decoded JSON value is an object."""
    return isinstance(value, dict)


def _ci_get(d: dict[str, object], key: str) -> object:
    """Case-insensitive dict lookup (the server's JSON casing is not pinned here)."""
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return None


def _app_version(app_ident: object) -> int | None:
    """Application program version from an ``ApplicationIdentifier``.

    In the online index this is a list ``[_, manufacturerId, _, applicationNumber, version]``
    (e.g. ``[0, 131, 0, 64, 40]`` -> 40). Also accepts the string id form
    ``M-XXXX_A-NNNN-VV-...`` where ``VV`` is the version in hex. Returns None if unknown."""
    if isinstance(app_ident, list) and app_ident:
        last = cast(list[object], app_ident)[-1]
        return last if isinstance(last, int) else None
    if isinstance(app_ident, str):
        parts = app_ident.split("-")
        if len(parts) >= 4:
            try:
                return int(parts[3], 16)
            except ValueError:
                return None
    return None


def _download_block_reason(entry: dict[str, object]) -> str:
    """Why the online catalog refuses to serve this product's ``.knxprod`` (empty = downloadable).

    The server refuses products no market offers ("NotInAnyMarket", see
    :func:`parse_catalog_items`). It still serves end-of-life products (``CatalogLifeType``) and
    those flagged ``NoDownloadWithoutPlugin`` or ``DownloadInfoIncomplete``: those flags concern
    programming the device, not the catalog download."""
    if bool(_ci_get(entry, "RequiresExternalSoftware")):
        return "requires external software"
    return ""


def _section_paths(
    data: dict[str, object],
) -> dict[str, tuple[OnlineCatalogSection, ...]]:
    """Each entry id of the index with the sections filing it, from the top one down.

    ``Sections`` nest by ``ChildSections``; each lists the ids of its entries in
    ``IndexFileEntrieIds`` (also ids of entries the index no longer has) and its names in
    ``Translations``. An entry filed twice keeps its first section."""
    paths: dict[str, tuple[OnlineCatalogSection, ...]] = {}

    def walk(raw: object, above: tuple[OnlineCatalogSection, ...]) -> None:
        if not _is_dict(raw):
            return
        section_id = _ci_get(raw, "Id")
        if not (isinstance(section_id, str) and section_id):
            return
        translations: list[tuple[str, str]] = []
        for translation in cast(Iterable[object], _ci_get(raw, "Translations") or []):
            if _is_dict(translation):
                language = _ci_get(translation, "Language")
                text = _ci_get(translation, "Text")
                if isinstance(language, str) and isinstance(text, str) and text:
                    translations.append((language, text))
        name = _ci_get(raw, "Name")
        section = OnlineCatalogSection(
            id=section_id,
            name=name if isinstance(name, str) and name else section_id,
            translations=tuple(translations),
        )
        path = (*above, section)
        for entry_id in cast(
            Iterable[object], _ci_get(raw, "IndexFileEntrieIds") or []
        ):
            if isinstance(entry_id, str):
                paths.setdefault(entry_id, path)
        for child in cast(Iterable[object], _ci_get(raw, "ChildSections") or []):
            walk(child, path)

    for raw in cast(Iterable[object], _ci_get(data, "Sections") or []):
        walk(raw, ())
    return paths


def parse_markets(index_bytes: bytes) -> list[OnlineMarket]:
    """The markets a ``Download/Index/{mfr}`` response lists, with their translated names."""
    try:
        data = json.loads(index_bytes)
    except ValueError as exc:
        raise OnlineCatalogError(f"index is not valid JSON: {exc}") from exc
    markets = _ci_get(data, "Markets") if _is_dict(data) else None
    found: list[OnlineMarket] = []
    for market in cast(list[object], markets) if isinstance(markets, list) else []:
        if not _is_dict(market):
            continue
        mid = _ci_get(market, "Id")
        if not (isinstance(mid, str) and mid):
            continue
        translations = _ci_get(market, "Translations")
        found.append(
            OnlineMarket(
                id=mid,
                name=str(_ci_get(market, "DefaultName") or mid),
                translations=tuple(
                    (str(_ci_get(t, "Language") or ""), str(_ci_get(t, "Text") or ""))
                    for t in (
                        cast(list[object], translations)
                        if isinstance(translations, list)
                        else []
                    )
                    if _is_dict(t)
                ),
            )
        )
    return found


def _markets_by_item(data: dict[str, object]) -> dict[str, tuple[str, ...]] | None:
    """The ids of the markets offering each catalog item, or None without a market list."""
    markets = _ci_get(data, "Markets")
    if not isinstance(markets, list):
        return None
    by_item: dict[str, list[str]] = {}
    for market in cast(list[object], markets):
        if not _is_dict(market):
            continue
        mid = _ci_get(market, "Id")
        catalog_ids = _ci_get(market, "CatalogIds")
        if isinstance(mid, str) and isinstance(catalog_ids, list):
            for cid in cast(list[object], catalog_ids):
                if isinstance(cid, str):
                    by_item.setdefault(cid, []).append(mid)
    return {cid: tuple(ids) for cid, ids in by_item.items()}


def _market_catalog_ids(data: dict[str, object]) -> set[str] | None:
    """The catalog items some market offers, or None when the index has no market list.

    An empty list offers nothing: the server refuses all of that manufacturer's items."""
    markets = _ci_get(data, "Markets")
    if not isinstance(markets, list):
        return None
    ids: set[str] = set()
    for market in cast(list[object], markets):
        if _is_dict(market):
            catalog_ids = _ci_get(market, "CatalogIds")
            if isinstance(catalog_ids, list):
                ids.update(
                    i for i in cast(list[object], catalog_ids) if isinstance(i, str)
                )
    return ids


def parse_catalog_items(index_bytes: bytes) -> list[OnlineCatalogItem]:
    """Parse a ``Download/Index/{mfr}`` response into downloadable catalog items.

    The index is Newtonsoft-JSON ``IndexFileData`` with an ``Entries`` array; each entry carries an
    ``Id``, ``CatalogItemName``/``ProductName``, ``OrderNumber`` and download-gating flags
    (``NoDownloadWithoutPlugin``, ``DownloadInfoIncomplete``, ``RequiresExternalSoftware``), and
    the catalog ``Sections`` file the entries. Entries no ``Markets`` entry lists are not
    downloadable. Sorted by display name.
    """
    try:
        data = json.loads(index_bytes)
    except ValueError as exc:
        raise OnlineCatalogError(f"index is not valid JSON: {exc}") from exc

    mfr_id = 0
    if _is_dict(data):
        raw_mid = _ci_get(data, "ManufacturerId")
        if isinstance(raw_mid, int):
            mfr_id = raw_mid
        elif isinstance(raw_mid, str) and raw_mid.strip().lstrip("-").isdigit():
            mfr_id = int(raw_mid)
    mfr_name = str(_ci_get(data, "ManufacturerName") or "") if _is_dict(data) else ""

    entries = _ci_get(data, "Entries") if _is_dict(data) else None
    sections = _section_paths(data) if _is_dict(data) else {}
    in_market = _market_catalog_ids(data) if _is_dict(data) else None
    markets = _markets_by_item(data) if _is_dict(data) else None
    items: list[OnlineCatalogItem] = []
    if isinstance(entries, list):
        for entry in cast(list[object], entries):
            if not _is_dict(entry):
                continue
            cid = _ci_get(entry, "Id")
            if not (isinstance(cid, str) and cid):
                continue
            name = (
                _ci_get(entry, "CatalogItemName")
                or _ci_get(entry, "ProductName")
                or cid
            )
            order = _ci_get(entry, "OrderNumber") or ""
            block_reason = _download_block_reason(entry)
            if not block_reason and in_market is not None and cid not in in_market:
                # The server refuses these with "NotInAnyMarket".
                block_reason = "not offered in any market"
            items.append(
                OnlineCatalogItem(
                    id=cid,
                    name=str(name),
                    order_number=str(order),
                    downloadable=not block_reason,
                    manufacturer_id=mfr_id,
                    manufacturer_name=mfr_name,
                    application_version=_app_version(
                        _ci_get(entry, "ApplicationIdentifier")
                    ),
                    application_program_name=str(
                        _ci_get(entry, "ApplicationProgramName") or ""
                    ),
                    download_block_reason=block_reason,
                    sections=sections.get(cid, ()),
                    languages=_entry_languages(entry),
                    markets=None if markets is None else markets.get(cid, ()),
                )
            )
    items.sort(key=lambda i: i.name.lower())
    return items


def _entry_languages(entry: dict[str, object]) -> tuple[str, ...]:
    """The languages of an index entry's texts: its default languages and its translations."""
    codes: list[str] = []
    translations = _ci_get(entry, "Translations")
    candidates: list[object] = [
        _ci_get(entry, "ProductDefaultLanguage"),
        _ci_get(entry, "CatalogItemDefaultLanguage"),
    ]
    if isinstance(translations, list):
        candidates += [
            _ci_get(t, "Language")
            for t in cast(list[object], translations)
            if _is_dict(t)
        ]
    for code in candidates:
        if isinstance(code, str) and code and code not in codes:
            codes.append(code)
    return tuple(codes)


def _markets_from_cache(stored: object) -> dict[str, OnlineMarket] | None:
    """The markets of a cached index; None when it does not list them."""
    if not _is_dict(stored):
        return None
    markets: dict[str, OnlineMarket] = {}
    for mid, entry in stored.items():
        if not _is_dict(entry):
            continue
        pairs = entry.get("tr")
        translations = tuple(
            (str(pair[0]), str(pair[1]))
            for pair in (
                cast(list[list[object]], pairs) if isinstance(pairs, list) else []
            )
            if len(pair) == 2
        )
        markets[mid] = OnlineMarket(mid, str(entry.get("name") or mid), translations)
    return markets


def _item_to_dict(item: OnlineCatalogItem) -> dict[str, object]:
    """Slim on-disk form of a catalog item (only what search/display needs; drops translations)."""
    return {
        "id": item.id,
        "name": item.name,
        "order": item.order_number,
        "dl": item.downloadable,
        "mid": item.manufacturer_id,
        "mname": item.manufacturer_name,
        "ver": item.application_version,
        "appname": item.application_program_name,
        "dl_reason": item.download_block_reason,
        "langs": list(item.languages),
        **({} if item.markets is None else {"mk": list(item.markets)}),
    }


def _item_from_dict(d: dict[str, object]) -> OnlineCatalogItem:
    ver = d.get("ver")
    return OnlineCatalogItem(
        id=str(d.get("id") or ""),
        name=str(d.get("name") or ""),
        order_number=str(d.get("order") or ""),
        downloadable=bool(d.get("dl")),
        manufacturer_id=int(cast(int | str, d.get("mid") or 0)),
        manufacturer_name=str(d.get("mname") or ""),
        application_version=ver if isinstance(ver, int) else None,
        application_program_name=str(d.get("appname") or ""),
        download_block_reason=str(d.get("dl_reason") or ""),
        languages=tuple(str(c) for c in cast(list[object], d.get("langs") or [])),
        markets=(
            tuple(str(m) for m in cast(list[object], d["mk"]))
            if isinstance(d.get("mk"), list)
            else None
        ),
    )


def search_index(
    index: dict[int, list[OnlineCatalogItem]],
    query: str,
    limit: int = 300,
    keep: Callable[[OnlineCatalogItem], bool] | None = None,
) -> tuple[list[OnlineCatalogItem], int]:
    """Flat cross-manufacturer product search over a built index.

    Matches ``query`` (case-insensitive) against order number, name and application program name.
    Returns ``(results, total_matches)``; ``results`` is capped at ``limit`` and ordered so the
    most relevant come first (order-number prefix > order-number contains > name/other). ``total``
    is the full match count before capping, so the UI can say "showing 300 of N". ``keep`` leaves
    out the items it rejects (the market and language filters)."""
    key = query.strip().lower()
    if not key:
        return [], 0

    def rank(item: OnlineCatalogItem) -> int:
        order = item.order_number.lower()
        if order == key:
            return 0
        if order.startswith(key):
            return 1
        if key in order:
            return 2
        if item.name.lower().startswith(key):
            return 3
        return 4

    matches = [
        item
        for items in index.values()
        for item in items
        if key in item.order_number.lower()
        or key in item.name.lower()
        or key in item.application_program_name.lower()
        if keep is None or keep(item)
    ]
    matches.sort(key=lambda i: (rank(i), i.order_number.lower(), i.name.lower()))
    return matches[:limit], len(matches)


class OnlineCatalogClient:
    """Fetches the manufacturer list, with a small on-disk cache.

    The cache lives next to the catalog database. ``cached_manufacturers`` is a pure read
    (for per-frame UI use); ``refresh_manufacturers`` hits the network and replaces the cache.
    """

    def __init__(
        self,
        cache_dir: Path,
        base_url: str = DEFAULT_BASE_URL,
        master_xml: Callable[[], bytes | None] | None = None,
    ) -> None:
        """``master_xml`` gives the master data the app already has, for the manufacturer names;
        without it (or when it gives None) they are downloaded."""
        self._cache_path = cache_dir / _CACHE_FILE
        self._index_path = cache_dir / _INDEX_CACHE_FILE
        self._base_url = base_url
        self._master_xml = master_xml
        self._lock = threading.Lock()
        # Full product index (all manufacturers), kept in memory once loaded so per-frame UI reads
        # are cheap; persisted to disk (slim fields only) so it survives restarts.
        self._index_lock = threading.Lock()
        self._index: dict[int, list[OnlineCatalogItem]] | None = None
        self._index_fetched_at: str | None = None
        self._index_loaded = False
        # The markets of all manufacturers by id; None when the cache does not list them.
        self._markets: dict[str, OnlineMarket] | None = None

    def _load_cache(self) -> list[OnlineManufacturer] | None:
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            return [OnlineManufacturer(item["id"], item["name"]) for item in raw]
        except (OSError, ValueError, KeyError):
            return None

    def _save_cache(self, manufacturers: list[OnlineManufacturer]) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(
                    [{"id": m.id, "name": m.name} for m in manufacturers],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # a missing cache only costs a re-download, never a failure

    def cached_manufacturers(self) -> list[OnlineManufacturer] | None:
        """Return the manufacturer list from the cache, or None; never touches the network."""
        with self._lock:
            return self._load_cache()

    def refresh_manufacturers(self) -> list[OnlineManufacturer]:
        """Download the manufacturer list and replace the cache."""
        with self._lock:
            return self._fetch_locked()

    def download_index(self, manufacturer_id: int) -> bytes:
        """Download a manufacturer's catalog index (JSON ``IndexFileData``)."""
        return _http_get(f"{self._base_url}/Download/Index/{manufacturer_id}")

    def catalog_items(self, manufacturer_id: int) -> list[OnlineCatalogItem]:
        """Return the downloadable catalog items offered for ``manufacturer_id`` (hits the network)."""
        return parse_catalog_items(self.download_index(manufacturer_id))

    def download_product(
        self, catalog_item_ids: list[str], language_ids: list[str] | None = None
    ) -> bytes:
        """Download a ``.knxprod`` bundle for the given catalog items.

        Mirrors the ``POST Download/DownloadProduct`` endpoint with a JSON body
        ``{"CatalogIds": [...], "LanguageIds": [...]}``. The response is a ZIP that WRAPS the actual
        ``.knxprod`` as an entry named ``products.knxprod`` (alongside an ``id-mapping.xml``), so the
        inner archive is extracted before returning.
        """
        if not catalog_item_ids:
            raise OnlineCatalogError("no catalog item ids given")
        body = json.dumps(
            {"CatalogIds": catalog_item_ids, "LanguageIds": language_ids or ["en-US"]}
        ).encode("utf-8")
        response = _http_post(
            f"{self._base_url}/Download/DownloadProduct", body, content_type="text/json"
        )
        return _unwrap_products_knxprod(response)

    def _fetch_locked(self) -> list[OnlineManufacturer]:
        ids = parse_manufacturer_ids(
            _http_get(f"{self._base_url}/Download/Manufacturers")
        )
        names = self._manufacturer_names()
        manufacturers = [
            OnlineManufacturer(mid, names.get(mid, f"M-{mid:04d}")) for mid in ids
        ]
        self._save_cache(manufacturers)
        return manufacturers

    def _manufacturer_names(self) -> dict[int, str]:
        """Names from the app's master data, else the downloaded one, else the names cached last
        time (the update server can be down while the catalog service works)."""
        local = self._master_xml() if self._master_xml is not None else None
        if local:
            try:
                return parse_manufacturer_names(local)
            except (OnlineCatalogError, ET.ParseError):
                pass
        try:
            return parse_manufacturer_names(_http_get(MASTER_DATA_URL))
        except (OnlineCatalogError, ET.ParseError):
            cached = self._load_cache()
            if not cached:
                raise
            return {m.id: m.name for m in cached}

    # --- full product index (all manufacturers) --------------------------

    def cached_index(self) -> dict[int, list[OnlineCatalogItem]] | None:
        """The full product index from disk (loaded once into memory), or None if never built.
        Never touches the network. The returned dict must not be mutated by callers."""
        with self._index_lock:
            if not self._index_loaded:
                self._index, self._index_fetched_at, self._markets = self._load_index()
                self._index_loaded = True
            return self._index

    def index_status(self) -> OnlineIndexStatus:
        index = self.cached_index() or {}
        products = sum(len(items) for items in index.values())
        return OnlineIndexStatus(len(index), products, self._index_fetched_at)

    def refresh_index(
        self,
        progress_cb: Callable[[int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        *,
        force: bool = False,
    ) -> OnlineIndexStatus:
        """Build/refresh the full product index. Resumable: unless ``force``, manufacturers already
        in the cache are skipped, so an aborted build continues where it stopped; those cached
        without their markets are fetched again. Persists every few manufacturers so a cancel
        keeps its progress. ``should_cancel`` stops the loop early."""
        ids = parse_manufacturer_ids(
            _http_get(f"{self._base_url}/Download/Manufacturers")
        )
        with self._index_lock:
            if not self._index_loaded:
                self._index, self._index_fetched_at, self._markets = self._load_index()
                self._index_loaded = True
            index: dict[int, list[OnlineCatalogItem]] = dict(self._index or {})
            markets: dict[str, OnlineMarket] = dict(self._markets or {})
        if force:
            index, markets = {}, {}
        total = len(ids)
        since_save = 0
        for done, mid in enumerate(ids, start=1):
            if should_cancel is not None and should_cancel():
                break
            if mid not in index or any(i.markets is None for i in index[mid]):
                try:
                    data = self.download_index(mid)
                    index[mid] = parse_catalog_items(data)
                    markets.update((m.id, m) for m in parse_markets(data))
                except OnlineCatalogError:
                    # Transient error: leave this manufacturer uncached so a later (resumable)
                    # build retries it, instead of caching [] and skipping it forever.
                    if progress_cb is not None:
                        progress_cb(done, total)
                    continue
                since_save += 1
                if since_save >= _INDEX_SAVE_EVERY:
                    self._publish_index(index, markets)
                    since_save = 0
            if progress_cb is not None:
                progress_cb(done, total)
        self._publish_index(index, markets)
        return OnlineIndexStatus(
            len(index), sum(len(v) for v in index.values()), self._index_fetched_at
        )

    def _publish_index(
        self,
        index: dict[int, list[OnlineCatalogItem]],
        markets: dict[str, OnlineMarket],
    ) -> None:
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self._index_lock:
            self._index = dict(index)
            self._markets = dict(markets)
            self._index_fetched_at = fetched_at
            self._index_loaded = True
        self._save_index(index, fetched_at, markets)

    def cached_markets(self) -> list[OnlineMarket] | None:
        """The markets of the cached index, sorted by default name; None when the cache does not
        list them (an index fetched before they were kept). Never touches the network."""
        self.cached_index()
        with self._index_lock:
            if self._markets is None:
                return None
            return sorted(self._markets.values(), key=lambda m: m.name.lower())

    def _load_index(
        self,
    ) -> tuple[
        dict[int, list[OnlineCatalogItem]] | None,
        str | None,
        dict[str, OnlineMarket] | None,
    ]:
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            if raw.get("format") != _INDEX_FORMAT:
                return None, None, None
            entries = raw["index"]
            index = {
                int(mid): [_item_from_dict(d) for d in items]
                for mid, items in entries.items()
            }
            markets = _markets_from_cache(raw.get("markets"))
            return index, raw.get("fetched_at"), markets
        except (OSError, ValueError, KeyError, TypeError):
            return None, None, None

    def _save_index(
        self,
        index: dict[int, list[OnlineCatalogItem]],
        fetched_at: str,
        markets: dict[str, OnlineMarket] | None = None,
    ) -> None:
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, object] = {
                "format": _INDEX_FORMAT,
                "fetched_at": fetched_at,
                "index": {
                    str(mid): [_item_to_dict(i) for i in items]
                    for mid, items in index.items()
                },
            }
            if markets is not None:
                payload["markets"] = {
                    mid: {"name": m.name, "tr": [list(pair) for pair in m.translations]}
                    for mid, m in markets.items()
                }
            self._index_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # a missing cache only costs a re-download, never a failure
