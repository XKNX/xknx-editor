"""Documentation-link helpers: manufacturer-domain map + best-effort manual resolver (mocked HTTP)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import Any

import httpx
import pytest

from editor_gui import doc_links


def _pdf_bytes(*texts: str) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=600, height=800)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    }
                )
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 750 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _patch_pdfs(monkeypatch: Any, pdfs: dict[str, bytes]) -> list[str]:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        return (
            httpx.Response(200, content=pdfs[url])
            if url in pdfs
            else httpx.Response(404)
        )

    @contextmanager
    def stream(method: str, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
        with (
            httpx.Client(transport=httpx.MockTransport(respond), **kwargs) as client,
            client.stream(method, url) as response,
        ):
            yield response

    monkeypatch.setattr(httpx, "stream", stream)
    return requested


@pytest.fixture(autouse=True)
def no_pdf_network(monkeypatch: Any) -> None:
    _patch_pdfs(monkeypatch, {})


def test_domain_for_substring_and_ordering() -> None:
    # more specific key wins where one catalog name contains another (regression guard)
    assert doc_links.domain_for("ABB AG - BUSCH-JAEGER") == "busch-jaeger.de"
    assert doc_links.domain_for("ABB AG - STOTZ-KONTAKT") == "abb.com"
    # qualified keys do not leak into unrelated names
    assert doc_links.domain_for("Instell") is None
    assert doc_links.domain_for("MDT technologies") == "mdt.de"
    assert doc_links.domain_for(None) is None


def test_knx_search_url() -> None:
    assert (
        doc_links.knx_search_url("AKH-0400.02")
        == "https://www.knx.org/de/gerate?title=AKH-0400.02"
    )


def _patch_http(monkeypatch: Any, pages: dict[str, str]) -> None:
    from urllib.parse import urlencode

    def fake(url: str, params: dict[str, str] | None = None) -> str | None:
        full = url + ("?" + urlencode(params) if params else "")
        return pages.get(full)

    monkeypatch.setattr(doc_links, "_get_text", fake)


_KNX = "https://www.knx.org"
_SPOTLIGHT = '<div class="listings-view-spotlight-carousel"><a href="/de/gerate/klic-mdi">KLIC-MDI</a></div>'


def _results(*slugs: str) -> str:
    cards = "".join(
        f'<article class="node-device-card"><a class="node-device-card__wrapper" href="/de/gerate/{slug}">{slug}</a></article>'
        for slug in slugs
    )
    return _SPOTLIGHT + f'<div class="listings-view__listings-grid">{cards}</div>'


def _product(
    order: str = "JAL-0810M.02",
    name: str = "JAL-0810M.02 Shutter Actuator",
    manufacturer: str = "MDT technologies",
    manufacturer_id: str = "M-0083",
    docs: tuple[tuple[str, str], ...] = (),
) -> str:
    links = "".join(f'<li><a href="{url}">{label}</a></li>' for label, url in docs)
    return f'''<div class="hero-header__content">
        <img src="/sites/default/files/devices/131/{manufacturer_id}_H-x.jpg">
        <img class="hero-header__device-logo" alt="{manufacturer}">
        <table class="project-details-table">
          <tr><td>Katalogartikelname</td><td>{name}</td></tr>
          <tr><td>Bestellnummer</td><td>{order}</td></tr>
        </table>
        <ul class="documentation-download-list">{links}</ul>
      </div>'''


def test_spotlight_is_not_a_search_result(monkeypatch: Any) -> None:
    url = f"{_KNX}/devices/131/M-0083_H-x/de-DE/download"
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=JAL-0810M.02": _results("jal"),
            f"{_KNX}/de/gerate/jal": _product(docs=(("Manual.pdf", url),)),
            f"{_KNX}/de/gerate/klic-mdi": _product(
                "ZCLMDI", "KLIC-MDI", "Zennio", "M-0071"
            ),
        },
    )
    assert (
        doc_links.resolve_manual_url(
            "MDT technologies", "JAL-0810M.02", manufacturer_id="M-0083"
        )
        == url
    )


def test_empty_search_with_spotlight_returns_none(monkeypatch: Any) -> None:
    _patch_http(monkeypatch, {f"{_KNX}/de/gerate?title=nonexistent": _results()})
    assert doc_links.resolve_manual_url("MDT", "nonexistent") is None


def test_knx_document_prefers_german_without_changing_model(monkeypatch: Any) -> None:
    base = f"{_KNX}/devices/2/M-0002_H-x"
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=2CDG+110+126+R0011": _results("abb"),
            f"{_KNX}/de/gerate/abb": _product(
                "2CDG 110 126 R0011",
                "JRA/S8.230.5.1",
                "ABB",
                "M-0002",
                (
                    ("Manual_EN.pdf", base + "/en-US/download"),
                    ("Manual_DE.pdf", base + "/de-DE/download"),
                ),
            ),
        },
    )
    assert (
        doc_links.resolve_manual_url("ABB", "2CDG 110 126 R0011")
        == base + "/de-DE/download"
    )


def test_knx_download_does_not_require_a_pdf_filename(monkeypatch: Any) -> None:
    url = f"{_KNX}/devices/131/M-0083_H-x/de-DE/download"
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=JAL-0810M.02": _results("jal"),
            f"{_KNX}/de/gerate/jal": _product(docs=(("Technical documentation", url),)),
        },
    )
    assert (
        doc_links.resolve_manual_url("MDT", "JAL-0810M.02", manufacturer_id="M-0083")
        == url
    )


def test_search_does_not_repeat_the_manufacturer(monkeypatch: Any) -> None:
    _patch_http(monkeypatch, {})
    queries: list[str] = []

    def search(query: str) -> list[str]:
        queries.append(query)
        return []

    monkeypatch.setattr(doc_links, "_duckduckgo_urls", search)
    assert doc_links.resolve_manual_url("STEINEL", "?", "STEINEL sensIQ S KNX") is None
    assert "STEINEL sensIQ S KNX Technisches Handbuch" in queries
    assert all("STEINEL STEINEL" not in query for query in queries)


def test_later_matching_candidate_beats_wrong_manufacturer(monkeypatch: Any) -> None:
    pdf = "https://www.mdt.de/JAL-0810M.02.pdf"
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=JAL-0810M.02": _results("wrong", "right"),
            f"{_KNX}/de/gerate/wrong": _product(
                manufacturer="Zennio",
                manufacturer_id="M-0071",
                docs=(("Manual.pdf", "https://www.zennio.com/wrong.pdf"),),
            ),
            f"{_KNX}/de/gerate/right": _product(docs=(("Manual.pdf", pdf),)),
        },
    )
    assert (
        doc_links.resolve_manual_url("MDT", "JAL-0810M.02", manufacturer_id="M-0083")
        == pdf
    )


def test_wrong_generation_is_not_a_product_match(monkeypatch: Any) -> None:
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=JAL-0810M.02": _results("older"),
            f"{_KNX}/de/gerate/older": _product(
                order="JAL-0810M.01",
                docs=(("Manual.pdf", "https://www.mdt.de/old.pdf"),),
            ),
        },
    )
    assert doc_links.resolve_manual_url("MDT", "JAL-0810M.02") is None


def test_missing_pdf_does_not_take_related_products_document(monkeypatch: Any) -> None:
    related = _product(
        "ZCLMDI",
        "KLIC-MDI",
        "Zennio",
        "M-0071",
        (("Manual.pdf", "https://www.zennio.com/wrong.pdf"),),
    )
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=JAL-0810M.02": _results("jal"),
            f"{_KNX}/de/gerate/jal": _product() + f"<aside>{related}</aside>",
        },
    )
    assert doc_links.resolve_manual_url("MDT", "JAL-0810M.02") is None


def test_pdf_manufacturer_id_must_match_the_product(monkeypatch: Any) -> None:
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=JAL-0810M.02": _results("jal"),
            f"{_KNX}/de/gerate/jal": _product(
                docs=(("Manual.pdf", f"{_KNX}/devices/113/M-0071_H-x/de-DE/download"),)
            ),
        },
    )
    assert (
        doc_links.resolve_manual_url("MDT", "JAL-0810M.02", manufacturer_id="M-0083")
        is None
    )


def test_placeholder_order_uses_exact_model() -> None:
    assert doc_links._product_matches(
        "40078410040xx", "sensIQ S KNX", "040000", "sensIQ S KNX"
    )
    assert not doc_links._product_matches(
        "207 9 00*", "thePrema P360 KNX", "208000", "thePassa P360 KNX"
    )
    assert not doc_links._product_matches("?", "KLIC-MDI v2", "ZCLMDI", "KLIC-MDI")


def _zennio_product(order: str = "ZCLMDI", name: str = "KLIC-MDI") -> str:
    return f"""<div class="elementor-location-single type-producto">
      <h1>{name}</h1><div class="elementor-heading-title">{order}</div>
      <a href="https://www.zennio.com/documents/catalog_en">Catalog</a>
      <ul class="datasheet-manual-module-technical-note">
        <li class="download-item datasheet"><a href="https://www.zennio.com/documents/datasheet_klic-mdi_de/">Data</a></li>
        <li class="download-item module"><a href="https://www.zennio.com/documents/module_binary_inputs_en/">Inputs</a></li>
        <li class="download-item manual"><a href="https://www.zennio.com/documents/manual_klic-mdi_en/">Manual</a></li>
      </ul></div>"""


def test_zennio_prefers_official_product_manual_to_knx_datasheet(
    monkeypatch: Any,
) -> None:
    _patch_http(
        monkeypatch,
        {
            f"{_KNX}/de/gerate?title=ZCLMDI": _results("klic-mdi"),
            f"{_KNX}/de/gerate/klic-mdi": _product(
                "ZCLMDI",
                "KLIC-MDI",
                "Zennio",
                "M-0071",
                (("Datenblatt.pdf", f"{_KNX}/devices/113/M-0071_H-x/de-DE/download"),),
            ),
            "https://www.zennio.com/?s=KLIC-MDI": '<div class="elementor-location-archive"><div class="type-producto"><a href="https://www.zennio.com/product/klic-mdi/">KLIC-MDI</a></div></div>',
            "https://www.zennio.com/product/klic-mdi/": _zennio_product(),
        },
    )
    assert (
        doc_links.resolve_manual_url(
            "Zennio", "ZCLMDI", "KLIC-MDI", manufacturer_id="M-0071"
        )
        == "https://www.zennio.com/documents/manual_klic-mdi_en/"
    )


def test_zennio_rejects_wrong_product(monkeypatch: Any) -> None:
    _patch_http(
        monkeypatch,
        {
            "https://www.zennio.com/?s=KLIC-MDI": '<div class="elementor-location-archive"><div class="type-producto"><a href="https://www.zennio.com/product/klic-mdi/">KLIC-MDI</a></div></div>',
            "https://www.zennio.com/product/klic-mdi/": _zennio_product("ZCLOTHER")
            + '<aside><div class="elementor-heading-title">ZCLMDI</div></aside>',
        },
    )
    assert doc_links.resolve_manual_url("Zennio", "ZCLMDI", "KLIC-MDI") is None


def test_zennio_requires_matching_manufacturer_id(monkeypatch: Any) -> None:
    _patch_http(monkeypatch, {})

    def unexpected(*_: Any) -> None:
        pytest.fail("Zennio lookup must not run for another manufacturer ID")

    monkeypatch.setattr(doc_links, "_zennio_document", unexpected)
    assert (
        doc_links.resolve_manual_url(
            "Zennio", "ZCLMDI", "KLIC-MDI", manufacturer_id="M-0083"
        )
        is None
    )


@pytest.mark.parametrize("identifier", ["JAL-0810M.02", "2089 00", "KLIC-MDI v2"])
def test_web_search_preserves_full_identifier(identifier: str) -> None:
    assert doc_links._contains_identifier(f"THB_{identifier}_DE.pdf", identifier)
    assert not doc_links._contains_identifier(f"THB_{identifier}0_DE.pdf", identifier)


def test_web_search_does_not_reduce_a_placeholder_to_first_word(
    monkeypatch: Any,
) -> None:
    _patch_http(monkeypatch, {})
    monkeypatch.setattr(
        doc_links, "_duckduckgo_urls", lambda _: ["https://www.zennio.com/KLIC-MDI.pdf"]
    )
    assert doc_links.resolve_manual_url("Zennio", "?", "KLIC-MDI v2") is None


def test_web_search_requires_manufacturer_and_model(monkeypatch: Any) -> None:
    _patch_http(monkeypatch, {})
    monkeypatch.setattr(
        doc_links,
        "_duckduckgo_urls",
        lambda _: [
            "https://www.zennio.com/JAL-0810M.02.pdf",
            "https://www.mdt.de/MDT_THB_JAL-0810M.01.pdf",
            "https://www.mdt.de/MDT_THB_JAL-0810M.02.pdf",
        ],
    )
    requested = _patch_pdfs(
        monkeypatch,
        {
            "https://www.zennio.com/JAL-0810M.02.pdf": _pdf_bytes("JAL-0810M.02"),
            "https://www.mdt.de/MDT_THB_JAL-0810M.01.pdf": _pdf_bytes("JAL-0810M.01"),
            "https://www.mdt.de/MDT_THB_JAL-0810M.02.pdf": _pdf_bytes("JAL-0810M.02"),
        },
    )
    assert (
        doc_links.resolve_manual_url("MDT", "JAL-0810M.02")
        == "https://www.mdt.de/MDT_THB_JAL-0810M.02.pdf"
    )
    assert all("zennio.com" not in url for url in requested)


def test_duckduckgo_family_manual_is_validated_by_pdf_content(monkeypatch: Any) -> None:
    _patch_http(monkeypatch, {})
    datasheet = "https://www.mdt.de/STC_DS.pdf"
    manual = "https://www.mdt.de/STC-xxx0-01_MDT_TM_V11_DE.pdf"
    queries: list[str] = []

    def search(query: str) -> list[str]:
        queries.append(query)
        return [manual] if "Technisches Handbuch" in query else [datasheet]

    monkeypatch.setattr(doc_links, "_duckduckgo_urls", search)
    _patch_pdfs(
        monkeypatch,
        {
            datasheet: _pdf_bytes("STC-1280.01"),
            manual: _pdf_bytes("STC-xxx0.01", "Contents", "STC-0640.01 STC-1280.01"),
        },
    )
    assert (
        doc_links.resolve_manual_url("MDT", "STC-1280.01", "Bus Power Supply") == manual
    )
    assert any("STC-1280.01" in q for q in queries)
    assert any("Bus Power Supply" in q for q in queries)


def test_mdt_fallback_selects_variant_manual_when_search_is_unavailable(
    monkeypatch: Any,
) -> None:
    product = "https://www.mdt.de/produkte/produktdetail/jalousieaktor.html"
    wrong = "https://www.mdt.de/MDT_THB_Jalousieaktor_02.pdf"
    right = "https://www.mdt.de/MDT_THB_JAL_Fahrzeitmessung.pdf"
    _patch_http(
        monkeypatch,
        {
            "https://www.mdt.de/suche.html?q=JAL-0810M.02": f'''<div class="search-result">
          <h3 class="results-topic"><a href="{product}">Jalousieaktor</a></h3></div>''',
            product: f'''<div class="variant"><p class="articlenumber">JAL-0810M.02</p></div>
          <table class="mdt-downloads">
            <tr><td>Technisches Handbuch</td><td><a href="{wrong}">pdf</a></td></tr>
            <tr><td>Technisches Handbuch mit Fahrzeitmessung</td><td><a href="{right}">pdf</a></td></tr>
          </table>''',
        },
    )
    _patch_pdfs(
        monkeypatch,
        {
            wrong: _pdf_bytes("JAL-0810.02 JAL-0810D.02"),
            right: _pdf_bytes("JAL-0410M.02 JAL-0810M.02"),
        },
    )
    assert (
        doc_links.resolve_manual_url("MDT", "JAL-0810M.02", manufacturer_id="M-0083")
        == right
    )
    assert (
        doc_links.resolve_manual_url("MDT", "JAL-0810M.01", manufacturer_id="M-0083")
        is None
    )


@pytest.mark.parametrize(
    "content",
    [b"<html>not a PDF</html>", b"%PDF-invalid", b"%PDF" + b"x" * (20 * 1024 * 1024)],
)
def test_invalid_or_oversized_pdf_is_not_a_match(
    monkeypatch: Any, content: bytes
) -> None:
    url = "https://www.mdt.de/manual.pdf"
    _patch_pdfs(monkeypatch, {url: content})
    assert not doc_links._pdf_matches(url, "JAL-0810M.02")


@pytest.mark.parametrize(
    "manufacturer,order,name,manufacturer_id",
    [
        ("MDT", "JAL-0810M.02", "JAL-0810M.02", "M-0083"),
        ("MDT", "BE-06001.02", "BE-06001.02", "M-0083"),
        ("MDT", "STC-1280.01", "STC-1280.01", "M-0083"),
        ("Theben", "207 9 00*", "thePrema P360 KNX", "M-00E8"),
        ("Gira", "2089 00", "S1", "M-0008"),
        ("Gira", "2343 00", "Dual-Q", "M-0008"),
    ],
)
def test_ticket_products_never_resolve_spotlight(
    monkeypatch: Any, manufacturer: str, order: str, name: str, manufacturer_id: str
) -> None:
    monkeypatch.setattr(doc_links, "_get_text", lambda *args: _SPOTLIGHT)
    assert (
        doc_links.resolve_manual_url(
            manufacturer, order, name, manufacturer_id=manufacturer_id
        )
        is None
    )


def test_no_manufacturer_homepage_is_returned_as_a_document(monkeypatch: Any) -> None:
    _patch_http(monkeypatch, {})
    assert doc_links.resolve_manual_url("MDT", "unknown") is None
    assert doc_links.resolve_manual_url("MDT", None) is None


def test_rank_classifies_documents() -> None:
    # KNX config docs (application description OR technical manual) rank best (0)
    assert (
        doc_links._rank("https://steinel.de/knxappl/KNX_Applikationsbeschreibung.pdf")
        == 0
    )
    assert doc_links._rank("https://www.mdt.de/download/MDT_THB_AKD.pdf") == 0
    # a general operation manual / other PDF ranks 1, a datasheet ranks 2 (worst)
    assert (
        doc_links._rank("https://www.steinel.de/out/media/operationmanual/BDAL.pdf")
        == 1
    )
    assert (
        doc_links._rank("https://cdn.mdt-group.com/x/AKD-0424R-02_MDT_DS_DE.PDF") == 2
    )
    # not a PDF
    assert (
        doc_links._rank("https://www.mdt.de/produktdetail/led-controller-akd.html")
        is None
    )


def test_on_domain() -> None:
    assert doc_links._on_domain("https://www.mdt.de/x.pdf", "mdt.de") is True
    assert doc_links._on_domain("https://cdn.reseller.com/x.pdf", "mdt.de") is False
    assert doc_links._on_domain("https://x/y.pdf", None) is False


def test_resolve_returns_none_when_nothing_found(monkeypatch: Any) -> None:
    _patch_http(monkeypatch, {})  # every fetch returns None
    assert doc_links.resolve_manual_url("Totally Unknown Maker", "X-1") is None


def test_gira_search_prefers_technical_documentation(monkeypatch: Any) -> None:
    manual = "https://partner.gira.de/data3/20893810.pdf?download=1&lang=de"
    assistant = "https://partner.gira.de/data3/assistant.pdf"
    wrong = "https://partner.gira.de/data3/other.pdf"
    _patch_http(
        monkeypatch,
        {
            "https://partner.gira.de/service/download/download.html?modus=suche_result&swert=2089+00": f'''
            <article class="dl5-card">Technical documentation
              <a href="{wrong}">Gira X1 2096 00</a></article>
            <article class="dl5-card">Set up voice assistant
              <a href="{assistant}">Gira S1 2089 00</a></article>
            <article class="dl5-card">Gira S1 Technische Dokumentation
              <a href="{manual}">Download</a><a href="{manual}">2089 00</a></article>'''
        },
    )
    requested = _patch_pdfs(
        monkeypatch,
        {
            wrong: _pdf_bytes("Gira X1 2096 00"),
            assistant: _pdf_bytes("Gira S1 2089 00"),
            manual: _pdf_bytes("Gira S1 2089 00"),
        },
    )
    assert doc_links.resolve_manual_url("Gira", "2089 00", "S1") == manual
    assert assistant not in requested


def test_download_cards_keep_their_document_type(monkeypatch: Any) -> None:
    page = "https://www.jung-group.com/de-DE/downloads/product/2308.16REGHE"
    base = "https://application.jung.de/downloads/download/"
    operation, manual, english = base + "1/de", base + "2/de", base + "2/en"
    _patch_http(
        monkeypatch,
        {
            page: f'''<div>
          <div class="download-item"><div class="row">Bedienungsanleitung
            <div><a href="{operation}">DE</a></div></div></div>
          <div class="download-item"><div class="row">Produktdokumentation
            <div><a href="{manual}">DE</a><a href="{english}">EN</a></div></div></div>
          <div class="download-item">Konformitätserklärung
            <a href="/declaration.pdf">DE</a></div></div>'''
        },
    )
    requested = _patch_pdfs(
        monkeypatch,
        {
            operation: _pdf_bytes("2308.16 REG HE"),
            manual: _pdf_bytes("2308.16 REG HE"),
        },
    )
    assert doc_links.resolve_manual_url("Jung", "2308.16REGHE") == manual
    assert requested == [manual]


def test_hager_page_uses_link_context_and_validates_family(monkeypatch: Any) -> None:
    page = "https://hager.com/product/TXA206C"
    manual = "https://assets.hager.com/family.pdf"
    _patch_http(
        monkeypatch,
        {
            page: f'''<ul>
        <li>GeneratedProductDatasheet<a href="https://assets.hager.com/ds.pdf">DE</a></li>
        <li>Dokumentationen<a href="{manual}">DE</a></li>
        <li>Nutzungsbedingungen<a href="https://assets.hager.com/policy.pdf">DE</a></li>
      </ul>'''
        },
    )
    monkeypatch.setattr(doc_links, "_duckduckgo_urls", lambda _: [page])
    requested = _patch_pdfs(monkeypatch, {manual: _pdf_bytes("TXA204 TXA206C TXA208")})
    assert doc_links.resolve_manual_url("Hager", "TXA206C") == manual
    assert requested == [manual]


@pytest.mark.parametrize(
    "actual", ["2308.16 REG HM", "2308.16 REG HE2", "2308.16REGHE0"]
)
def test_spacing_tolerance_preserves_product_variant(actual: str) -> None:
    assert not doc_links._contains_identifier(actual, "2308.16REGHE")


def test_extensionless_download_must_be_a_pdf(monkeypatch: Any) -> None:
    binary = "https://application.jung.de/downloads/download/1/de"
    manual = "https://application.jung.de/downloads/download/2/de"
    _patch_pdfs(
        monkeypatch,
        {binary: b"PK\x03\x04archive", manual: _pdf_bytes("2308.16 REG HE")},
    )
    assert (
        doc_links._select_document(
            [doc_links._Document(binary, 0), doc_links._Document(manual, 0)],
            "2308.16REGHE",
            set(),
        )
        == manual
    )


def _siemens_metadata(products: Any = None, status: Any = None) -> dict[str, Any]:
    return {
        "mimeType": "application/pdf",
        "title": "KNX/DALI Gateway",
        "metadata": [
            {
                "key": "Products",
                "values": products if products is not None else ["5WG1141-1AB31"],
            },
            {
                "key": "Status_custom",
                "values": status if status is not None else ["Valid"],
            },
            {"key": "Content_type_custom", "values": ["Configuration Instructions"]},
        ],
    }


def test_siemens_portal_resolves_public_metadata(monkeypatch: Any) -> None:
    page = "https://sid.siemens.com/v/u/A6V15814370"
    document = "https://sid.siemens.com/api/khub/documents/document-id/content"
    _patch_http(monkeypatch, {})
    monkeypatch.setattr(doc_links, "_duckduckgo_urls", lambda _: [page])
    calls: list[tuple[str, Any]] = []

    def metadata(url: str, params: Any = None, *, body: Any = None) -> dict[str, Any]:
        calls.append((url, body))
        return {"documentId": "document-id"} if body else _siemens_metadata()

    monkeypatch.setattr(doc_links, "_json_request", metadata)
    _patch_pdfs(
        monkeypatch, {document: _pdf_bytes("Family configuration instructions")}
    )
    assert doc_links.resolve_manual_url("Siemens", "5WG1141-1AB31") == document
    assert calls[0][1] == {"prettyUrl": "A6V15814370", "forcedTocId": None}


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"metadata": None},
        {"metadata": "invalid"},
        _siemens_metadata(["5WG1141-1AB03"]),
        _siemens_metadata(status=["Withdrawn"]),
        _siemens_metadata(products="5WG1141-1AB31"),
    ],
)
def test_siemens_rejects_unverified_metadata(monkeypatch: Any, metadata: Any) -> None:
    monkeypatch.setattr(
        doc_links,
        "_json_request",
        lambda url, **kw: {"documentId": "id"} if kw else metadata,
    )
    assert (
        doc_links._siemens_documents(
            "https://sid.siemens.com/v/u/test", "5WG1141-1AB31", ""
        )
        == []
    )


def test_siemens_index_uses_current_exact_product(monkeypatch: Any) -> None:
    import json

    def row(status: str, order: str, filename: str) -> list[str]:
        fields = [""] * 18
        fields[0], fields[2], fields[6], fields[9] = status, order, "2024/04", filename
        return fields

    rows = [
        *row("old", "5WG1141-1AB31", "old.pdf"),
        *row("ok", "5WG1141-1AB03", "other.pdf"),
        *row("ok", "5WG1141-1AB31", "configuration.pdf"),
    ]
    _patch_http(
        monkeypatch,
        {
            "https://knx-td.sihub.siemens.com/l10n/db_en.js": "db = "
            + json.dumps(rows)
            + ";"
        },
    )
    manual = "https://knx-td.sihub.siemens.com/en/apb/configuration.pdf"
    _patch_pdfs(monkeypatch, {manual: _pdf_bytes("Family manual")})
    assert doc_links.resolve_manual_url("Siemens", "5WG1141-1AB31") == manual


@pytest.mark.parametrize(
    "script", ["db = [null];", 'db = ["ok"];', 'db = ["ok",]; alert(1)', "db = [];"]
)
def test_siemens_index_rejects_malformed_data(monkeypatch: Any, script: str) -> None:
    _patch_http(monkeypatch, {"https://knx-td.sihub.siemens.com/l10n/db_en.js": script})
    assert doc_links._siemens_index_documents("5WG1141-1AB31") == []


def test_abb_library_rejects_unrelated_and_non_pdf_documents(monkeypatch: Any) -> None:
    product = "2CKA006120A0074"
    url = (
        "https://search.abb.com/library/Download.aspx?DocumentID=manual&LanguageCode=de"
    )

    def doc(**changes: Any) -> dict[str, Any]:
        return {
            "ProductID": [product],
            "DocumentKind": "Operating instruction",
            "Title": "6120/12-101",
            "DocumentURL": url,
            "RepresentationFileNameSuffix": "pdf",
            "IsLatestVersion": True,
            **changes,
        }

    monkeypatch.setattr(
        doc_links,
        "_json_request",
        lambda *a, **kw: {
            "DocumentContainer": [
                doc(ProductID=[]),
                doc(ProductID=["other"]),
                doc(DocumentKind="Catalogue"),
                doc(RepresentationFileNameSuffix="svg"),
                doc(IsLatestVersion=False),
                doc(DocumentURL="https://unrelated.example/manual.pdf"),
                doc(),
            ]
        },
    )
    root = doc_links._Page(
        f'<div id="dsDownloadSection" data-product-id="{product}"></div>'
    ).root
    assert doc_links._abb_documents(root, product, "") == [
        doc_links._Document(url, 1, True)
    ]
    assert doc_links._abb_documents(root, "6120/12-101", "") == [
        doc_links._Document(url, 1, False)
    ]
    _patch_pdfs(monkeypatch, {url: _pdf_bytes("6120/12-101")})
    assert (
        doc_links._select_document(
            doc_links._abb_documents(root, "6120/12-101", ""), "6120/12-101", set()
        )
        == url
    )
    assert (
        doc_links._select_document(
            doc_links._abb_documents(root, "6120/12-102", ""), "6120/12-102", set()
        )
        is None
    )


@pytest.mark.parametrize(
    "data",
    [{"DocumentContainer": None}, {"DocumentContainer": [None, {"ProductID": None}]}],
)
def test_abb_library_handles_malformed_metadata(monkeypatch: Any, data: Any) -> None:
    monkeypatch.setattr(doc_links, "_json_request", lambda *a, **kw: data)
    root = doc_links._Page(
        '<div id="dsDownloadSection" data-product-id="123"></div>'
    ).root
    assert doc_links._abb_documents(root, "123", "") == []


def test_search_decodes_entities_and_deduplicates(monkeypatch: Any) -> None:
    _patch_http(
        monkeypatch,
        {
            "https://html.duckduckgo.com/html/?q=product": '<a class="result__a" href="https://gira.de/x.pdf?a=1&amp;b=2">Manual</a>'
            * 2,
        },
    )
    assert doc_links._duckduckgo_urls("product") == ["https://gira.de/x.pdf?a=1&b=2"]


def test_lookup_budget_limits_requests_and_restores_context(monkeypatch: Any) -> None:
    budget = doc_links._Budget(requests=32)
    with pytest.raises(TimeoutError):
        budget.timeout()
    with pytest.raises(TimeoutError):
        doc_links._Budget(deadline=0).timeout()
    token = doc_links._budget.set(budget)
    try:
        _patch_http(monkeypatch, {})
        assert doc_links.resolve_manual_url("Unknown", "missing") is None
        assert doc_links._budget.get() is budget
    finally:
        doc_links._budget.reset(token)


def test_plain_product_search_can_supply_an_official_download_page(
    monkeypatch: Any,
) -> None:
    page = "https://www.theben.de/en/theprema-p360"
    manual = "https://www.theben.de/handbook.pdf"
    _patch_http(
        monkeypatch,
        {page: f'<article>Technical documentation<a href="{manual}">PDF</a></article>'},
    )
    monkeypatch.setattr(
        doc_links,
        "_duckduckgo_urls",
        lambda query: [page] if query == "Theben thePrema P360 KNX" else [],
    )
    _patch_pdfs(monkeypatch, {manual: _pdf_bytes("thePrema P360 KNX")})
    assert (
        doc_links.resolve_manual_url("Theben", "207 9 00*", "thePrema P360 KNX")
        == manual
    )


def test_metadata_http_is_bounded_cached_and_supports_post(monkeypatch: Any) -> None:
    client_type = httpx.Client
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/large":
            return httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
        if request.url.path == "/invalid":
            return httpx.Response(200, text="not JSON")
        return httpx.Response(200, json={"documentId": "test"})

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: client_type(transport=httpx.MockTransport(respond), **kw),
    )
    token = doc_links._budget.set(doc_links._Budget())
    try:
        for _ in range(2):
            assert doc_links._json_request(
                "https://sid.siemens.com/resolve", body={"prettyUrl": "test"}
            ) == {"documentId": "test"}
        assert len(calls) == 1
        assert calls[0].method == "POST"
        assert doc_links._get_text("https://gira.de/large") is None
        assert doc_links._json_request("https://gira.de/invalid") == {}
        budget = doc_links._budget.get()
        assert budget is not None
        budget.requests = 32
        assert doc_links._get_text("https://gira.de/new") is None
        assert len(calls) == 3
    finally:
        doc_links._budget.reset(token)


def test_theben_official_search_uses_full_model_for_placeholder_order(
    monkeypatch: Any,
) -> None:
    manual = "https://www.theben.de/thePrema_Handbuch.pdf"
    search = "https://www.theben.de/theben-searchfrontend/en/search?searchTerm=thePrema+P360+KNX"
    _patch_http(
        monkeypatch, {search: f'<article><a href="{manual}">Download</a></article>'}
    )
    _patch_pdfs(monkeypatch, {manual: _pdf_bytes("thePrema P360 KNX")})
    assert (
        doc_links.resolve_manual_url("Theben", "207 9 00*", "thePrema P360 KNX")
        == manual
    )


def test_excessively_nested_html_is_discarded() -> None:
    root = doc_links._Page(
        "<div>" * 1500 + '<a href="wrong.pdf">manual</a>' + "</div>" * 1500
    ).root
    assert root.find("a") == []


def test_html_traversal_preserves_document_order_and_obeys_deadline() -> None:
    root = doc_links._Page(
        "<div><a>first</a><div><a>second</a></div></div><a>third</a>"
    ).root
    assert [node.text for node in root.find("a")] == ["first", "second", "third"]
    token = doc_links._budget.set(doc_links._Budget(deadline=0))
    try:
        with pytest.raises(TimeoutError):
            root.find("a")
        assert doc_links._Page("<a>expired</a>").root.find("a") == []
    finally:
        doc_links._budget.reset(token)


def test_expired_parse_budget_returns_no_document(monkeypatch: Any) -> None:
    def expired(*args: Any, **kwargs: Any) -> None:
        raise TimeoutError("expired")

    monkeypatch.setattr(doc_links, "_resolve_manual_url", expired)
    assert doc_links.resolve_manual_url("Gira", "2089 00") is None


def _hager_search_page(hits: list[dict[str, Any]]) -> str:
    import json

    data: dict[str, Any] = {"results": [{"hits": hits}]}
    for key in reversed(
        ("props", "pageProps", "serverState", "initialResults", "downloadcenter")
    ):
        data = {key: data}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script>"
    )


def _hager_hit(target: str, **changes: Any) -> dict[str, Any]:
    return {
        "url": target,
        "fileType": "PDF",
        "commercialRef": ["TXA206C"],
        "assetType": ["ProductApplicationDescription"],
        **changes,
    }


def test_hager_official_search_uses_exact_metadata_before_fuzzy_results(
    monkeypatch: Any,
) -> None:
    manual = "https://assets.hager.com/family_application_DE.pdf"
    wrong = "https://assets.hager.com/other_application_DE.pdf"
    _patch_http(
        monkeypatch,
        {
            "https://hager.com/de/suchen?q=TXA206C": _hager_search_page([]),
            "https://hager.com/de-ch/suchen?q=TXA206C": _hager_search_page(
                [
                    _hager_hit(wrong, commercialRef=["TXA204C"]),
                    _hager_hit(
                        "https://assets.hager.com/data.pdf",
                        assetType=["GeneratedProductDatasheet"],
                    ),
                    _hager_hit(manual),
                ]
            ),
        },
    )
    requested = _patch_pdfs(
        monkeypatch, {manual: _pdf_bytes("TXA204 206 207 208 Application description")}
    )
    assert doc_links.resolve_manual_url("Hager", "TXA206C") == manual
    assert requested == [manual]


@pytest.mark.parametrize(
    "changes",
    [
        {"commercialRef": ["TXA206D"]},
        {"commercialRef": "TXA206C"},
        {"commercialRef": None},
        {"fileType": "ZIP"},
        {"assetType": ["GeneralCatalogue"]},
        {"assetType": None},
        {"url": "https://unrelated.example/manual.pdf"},
    ],
)
def test_hager_metadata_rejects_unverified_documents(changes: dict[str, Any]) -> None:
    root = doc_links._Page(
        _hager_search_page(
            [_hager_hit("https://assets.hager.com/manual.pdf", **changes)]
        )
    ).root
    assert doc_links._hager_documents(root, "TXA206C") == []


@pytest.mark.parametrize(
    "data", ["null", '{"props":null}', '{"props":{"pageProps":[]}}', "invalid"]
)
def test_hager_metadata_handles_incomplete_payload(data: str) -> None:
    root = doc_links._Page(
        '<script id="__NEXT_DATA__" type="application/json">' + data + "</script>"
    ).root
    assert doc_links._hager_documents(root, "TXA206C") == []


def test_embedded_json_stays_out_of_document_text() -> None:
    root = doc_links._Page(
        '<article>Installation manual<script type="application/json">{"type":"catalogue"}</script></article>'
    ).root
    assert root.find("article")[0].text == "Installation manual"
    assert root.find("script")[0].text == '{"type":"catalogue"}'


def test_schneider_query_download_and_isolated_card_select_software_manual(
    monkeypatch: Any,
) -> None:
    manual = "https://download.se.com/files?p_File_Name=MTN6700-0008_SW_EN.pdf&p_Doc_Ref=software"
    install = "https://download.se.com/files?p_File_Name=MTN6700-0008_HW_DE.pdf"
    datasheet = "https://www.se.com/uk/en/product/download-pdf/MTN6700-0008?filename=MTN6700-0008.pdf"
    _patch_http(
        monkeypatch,
        {
            "https://www.se.com/de/de/product/MTN6700-0008/": f'<a class="document-item" href="{install}">Benutzerhandbuch</a>',
            "https://www.se.com/uk/en/product/MTN6700-0008/": f'''<div>
          <div><a class="document-item" href="{datasheet}">Product Datasheet</a></div>
          <div><a class="document-item" href="{manual}">User guide</a></div>
          <div><a class="document-item" href="https://download.se.com/files?p_File_Name=environmental.pdf">Environmental Disclosure</a></div>
        </div>''',
        },
    )
    requested = _patch_pdfs(
        monkeypatch, {manual: _pdf_bytes("MTN6700-0008 Software manual")}
    )
    assert doc_links.resolve_manual_url("Schneider Electric", "MTN6700-0008") == manual
    assert requested == [manual]


@pytest.mark.parametrize(
    "url",
    [
        "https://download.se.com/files?p_File_Name=manual.pdf&p_Doc_Ref=manual",
        "https://www.se.com/product/download-pdf/1?filename=manual.PDF",
    ],
)
def test_query_filename_download_still_requires_valid_product_pdf(
    monkeypatch: Any, url: str
) -> None:
    assert doc_links._download_url(url)
    _patch_pdfs(monkeypatch, {url: _pdf_bytes("MTN6705-0008")})
    assert (
        doc_links._select_document([doc_links._Document(url, 0)], "MTN6700-0008", set())
        is None
    )
    _patch_pdfs(monkeypatch, {url: b"<html>Sign in</html>"})
    assert (
        doc_links._select_document([doc_links._Document(url, 0)], "MTN6700-0008", set())
        is None
    )


def test_basalte_public_manual_search_validates_article_and_rejects_login(
    monkeypatch: Any,
) -> None:
    software = "https://basalte.world/media/downloads/Controls/Auro_KNX/Auro_KNX_software_manual_EN.pdf"
    manual = "https://basalte.world/media/downloads/Controls/Auro_KNX/Auro_KNX_installation_manual_EN.pdf"
    portal = "https://basalte.world/en_US/controls/auro-knx.html"
    _patch_http(monkeypatch, {portal: "<h1>Log in to discover Basalte World</h1>"})
    monkeypatch.setattr(
        doc_links,
        "_duckduckgo_urls",
        lambda q: (
            [software, manual] if q.startswith("site:basalte.world") else [portal]
        ),
    )
    _patch_pdfs(
        monkeypatch,
        {
            software: _pdf_bytes("Auro KNX Manual"),
            manual: _pdf_bytes("AURO KNX Model No. 180-02 White / 180-03 Black"),
        },
    )
    assert doc_links.resolve_manual_url("Basalte", "180-02", "Auro KNX") == manual
    assert doc_links.resolve_manual_url("Basalte", "180-04", "Auro KNX") is None
    assert doc_links.resolve_manual_url("Basalte", "200-02", "Sentido KNX") is None


def test_schneider_dropdown_uses_embedded_file_metadata(monkeypatch: Any) -> None:
    manual = "https://download.se.com/files?p_enDocType=User+guide&p_File_Name=device_SW_EN.pdf&p_Doc_Ref=MTN6705-0008S_SW"
    _patch_http(
        monkeypatch,
        {
            "https://www.se.com/de/de/product/MTN6705-0008S/": """<script>
        data={files:[{url:null,extension:"pdf",documentReference:"MTN6705-0008S_SW",documentName:"device_SW_EN.pdf",documentType:"User guide"}],
        other:{documentReference:"env",documentName:"environmental.pdf",documentType:"Environmental Disclosure"}};
        </script>""",
        },
    )
    requested = _patch_pdfs(
        monkeypatch, {manual: _pdf_bytes("MTN6705-0008S Application description")}
    )
    assert doc_links.resolve_manual_url("Schneider Electric", "MTN6705-0008S") == manual
    assert requested == [manual]


@pytest.mark.parametrize(
    "metadata",
    [
        'documentReference:"id",documentName:"archive.zip",documentType:"User guide"',
        'documentReference:"id",documentName:"bad\\x.pdf",documentType:"User guide"',
        'documentReference:"id",documentName:computeName(),documentType:"User guide"',
    ],
)
def test_schneider_dropdown_ignores_non_pdf_and_non_literal_metadata(
    metadata: str,
) -> None:
    assert doc_links._schneider_documents(metadata) == []


def test_schneider_dropdown_still_checks_product_identity(monkeypatch: Any) -> None:
    candidates = doc_links._schneider_documents(
        'documentReference:"id",documentName:"manual_SW.pdf",documentType:"User guide"'
    )
    _patch_pdfs(monkeypatch, {candidates[0].url: _pdf_bytes("MTN6705-0008S")})
    assert doc_links._select_document(candidates, "MTN6700-0008", set()) is None
