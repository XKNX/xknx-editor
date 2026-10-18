"""An export writes the project's own product data exactly and adds from the devices' product files
only what its devices need beyond it; a catalog entry only for a device added in the editor."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from xknxeditor.proj.core.manufacturer_xml import DeviceProduct, combine_product_members

_NS = "http://knx.org/xml/project/23"
_M = "M-0083"


def _xml(body: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="utf-8"?><KNX xmlns="{_NS}"><ManufacturerData>'
        f'<Manufacturer RefId="{_M}">{body}</Manufacturer></ManufacturerData></KNX>'
    ).encode()


def _hardware(*hardware: tuple[str, str, list[str]]) -> bytes:
    """A Hardware.xml: (hardware, product, [program suffix]) per hardware; program ``X`` runs the
    application ``A-X``."""
    body = "".join(
        f'<Hardware Id="{_M}_H-{hw}" Name="{hw}"><Products>'
        f'<Product Id="{_M}_H-{hw}_P-{product}" Text="{product}"/></Products>'
        "<Hardware2Programs>"
        + "".join(
            f'<Hardware2Program Id="{_M}_H-{hw}_HP-{p}">'
            f'<ApplicationProgramRef RefId="{_M}_A-{p}"/></Hardware2Program>'
            for p in programs
        )
        + "</Hardware2Programs></Hardware>"
        for hw, product, programs in hardware
    )
    return _xml(f"<Hardware>{body}</Hardware>")


def _catalog(*items: tuple[str, str, str]) -> bytes:
    """A Catalog.xml with one item per (hardware, product, program suffix)."""
    body = "".join(
        f'<CatalogItem Id="{_M}_CI-{hw}-{p}" Name="{product}" '
        f'ProductRefId="{_M}_H-{hw}_P-{product}" '
        f'Hardware2ProgramRefId="{_M}_H-{hw}_HP-{p}"/>'
        for hw, product, p in items
    )
    return _xml(
        f'<Catalog><CatalogSection Id="{_M}_CS-1" Name="All">{body}</CatalogSection></Catalog>'
    )


def _app(suffix: str) -> tuple[str, bytes]:
    return f"{_M}/{_M}_A-{suffix}.xml", _xml(f'<ApplicationPrograms Id="{suffix}"/>')


def _device(hw: str, product: str, program: str, *, imported: bool) -> DeviceProduct:
    return DeviceProduct(
        product=f"{_M}_H-{hw}_P-{product}",
        hardware2program=f"{_M}_H-{hw}_HP-{program}",
        application=f"{_M}_A-{program}",
        imported=imported,
    )


# The project's own product data: hardware 1 with program 10, catalogued.
_OWN = {
    f"{_M}/Hardware.xml": _hardware(("1", "One", ["10"])),
    f"{_M}/Catalog.xml": _catalog(("1", "One", "10")),
    _app("10")[0]: _app("10")[1],
}
# A product file: hardware 1 with programs 10 and 11, hardware 2 with program 20, hardware 3 unused.
_SOURCE = {
    f"{_M}/Hardware.xml": _hardware(
        ("1", "One", ["10", "11"]), ("2", "Two", ["20"]), ("3", "Three", ["30"])
    ),
    f"{_M}/Catalog.xml": _catalog(
        ("1", "One", "10"), ("1", "One", "11"), ("2", "Two", "20"), ("3", "Three", "30")
    ),
    **dict(_app(s) for s in ("10", "11", "20", "30")),
}


def _ids(xml: bytes, tag: str) -> set[str]:
    return {
        e.get("Id", "")
        for e in ET.fromstring(xml).iter()
        if e.tag == f"{{{_NS}}}{tag}" and "Id" in e.attrib
    }


def test_the_own_data_is_kept_exactly_when_it_covers_every_device() -> None:
    devices = [_device("1", "One", "10", imported=True)]
    assert combine_product_members(_OWN, _SOURCE, devices) == _OWN


def test_a_replaced_application_adds_its_data_but_no_catalog_entry() -> None:
    devices = [_device("1", "One", "11", imported=True)]
    result = combine_product_members(_OWN, _SOURCE, devices)

    assert _ids(result[f"{_M}/Hardware.xml"], "Hardware2Program") == {
        f"{_M}_H-1_HP-10",
        f"{_M}_H-1_HP-11",
    }
    assert result[f"{_M}/Catalog.xml"] == _OWN[f"{_M}/Catalog.xml"]
    assert _app("11")[0] in result
    assert _app("20")[0] not in result


def test_a_device_added_in_the_editor_adds_its_catalog_entry() -> None:
    devices = [
        _device("1", "One", "10", imported=True),
        _device("2", "Two", "20", imported=False),
    ]
    result = combine_product_members(_OWN, _SOURCE, devices)

    assert _ids(result[f"{_M}/Hardware.xml"], "Hardware") == {f"{_M}_H-1", f"{_M}_H-2"}
    assert _ids(result[f"{_M}/Catalog.xml"], "CatalogItem") == {
        f"{_M}_CI-1-10",
        f"{_M}_CI-2-20",
    }
    assert {_app("10")[0], _app("20")[0]} <= set(result)
    assert _app("11")[0] not in result
    assert _app("30")[0] not in result


def test_without_own_data_the_product_files_are_written_unchanged() -> None:
    devices = [_device("2", "Two", "20", imported=False)]
    assert combine_product_members({}, _SOURCE, devices) == _SOURCE


def test_a_new_manufacturer_brings_only_what_its_devices_use() -> None:
    other = {
        name.replace(_M, "M-00FA"): data.replace(_M.encode(), b"M-00FA")
        for name, data in _SOURCE.items()
    }
    devices = [
        _device("1", "One", "10", imported=True),
        DeviceProduct(
            product="M-00FA_H-2_P-Two",
            hardware2program="M-00FA_H-2_HP-20",
            application="M-00FA_A-20",
            imported=False,
        ),
    ]
    result = combine_product_members(_OWN, {**_SOURCE, **other}, devices)

    assert {n for n in result if n.startswith("M-00FA/")} == {
        "M-00FA/Hardware.xml",
        "M-00FA/Catalog.xml",
        "M-00FA/M-00FA_A-20.xml",
    }
    assert {n: d for n, d in result.items() if n.startswith(_M)} == _OWN
