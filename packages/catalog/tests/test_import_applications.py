"""What importing a product with several application programs stores: its catalog rows and one
parse cache entry per application XML."""

from __future__ import annotations

import io
import pickle
import re
import sqlite3
import zipfile
from pathlib import Path

from xknxeditor.catalog import CatalogService
from xknxeditor.namespaces import detect_version
from xknxeditor.prod import parse_cache
from xknxeditor.prod.data import to_ir

_FIXTURES = Path(__file__).parents[3] / "packages/prod/tests/fixtures"
# Hand-made application programs of manufacturer M-00FA, each with its own id.
_APPLICATIONS = ("calculations", "download_invisible", "activation", "allocator")
_NS = 'xmlns="http://knx.org/xml/project/23"'
_MASTER = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX {_NS}>
  <MasterData Id="MD-1" Version="1" Signature="QUJD">
    <Manufacturers>
      <Manufacturer Id="M-00FA" KnxManufacturerId="250" Name="Test" DefaultLanguage="en-US" />
    </Manufacturers>
  </MasterData>
</KNX>"""
# Two devices: the first with two programs, the second with one; the fourth application is
# loaded by no program.
_PROGRAMS = {
    "M-00FA_H-1-0": {
        "M-00FA_H-1-0_HP-0001-01-0000": "M-00FA_A-0001-01-0000",
        "M-00FA_H-1-0_HP-0002-01-0000": "M-00FA_A-0002-01-0000",
    },
    "M-00FA_H-2-0": {"M-00FA_H-2-0_HP-0003-01-0000": "M-00FA_A-0003-01-0000"},
}


def _hardware_xml() -> str:
    devices = ""
    for number, (hardware_id, programs) in enumerate(_PROGRAMS.items(), start=1):
        refs = "".join(
            f'<Hardware2Program Id="{program_id}" MediumTypes="MT-0">'
            f'<ApplicationProgramRef RefId="{app_id}" />'
            f'<RegistrationInfo RegistrationStatus="Registered" '
            f'RegistrationNumber="2026/{number}" RegistrationDate="2026-01-0{number}" />'
            "</Hardware2Program>"
            for program_id, app_id in programs.items()
        )
        devices += (
            f'<Hardware Id="{hardware_id}" Name="Device {number}" SerialNumber="{number}" '
            f'VersionNumber="1" BusCurrent="10" HasIndividualAddress="true" '
            f'HasApplicationProgram="true">'
            f'<Products><Product Id="{hardware_id}_P-{number}" Text="Device {number}" '
            f'OrderNumber="ORDER-{number}" IsRailMounted="true" DefaultLanguage="en-US" />'
            f"</Products><Hardware2Programs>{refs}</Hardware2Programs></Hardware>"
        )
    return (
        f'<?xml version="1.0" encoding="utf-8"?><KNX {_NS}><ManufacturerData>'
        f'<Manufacturer RefId="M-00FA"><Hardware>{devices}</Hardware></Manufacturer>'
        "</ManufacturerData></KNX>"
    )


def _catalog_xml() -> str:
    items = "".join(
        f'<CatalogItem Id="{program_id}_CI-{number}" Name="Device {number}" '
        f'Number="{number}" ProductRefId="{hardware_id}_P-{number}" '
        f'Hardware2ProgramRefId="{program_id}" DefaultLanguage="en-US" />'
        for number, (hardware_id, programs) in enumerate(_PROGRAMS.items(), start=1)
        for program_id in programs
    )
    return (
        f'<?xml version="1.0" encoding="utf-8"?><KNX {_NS}><ManufacturerData>'
        f'<Manufacturer RefId="M-00FA"><Catalog><CatalogSection Id="M-00FA_CS-1" '
        f'Name="Devices" Number="1" DefaultLanguage="en-US">{items}</CatalogSection>'
        "</Catalog></Manufacturer></ManufacturerData></KNX>"
    )


def _xmls() -> list[bytes]:
    return [
        (_FIXTURES / f"{name}_application.xml").read_bytes() for name in _APPLICATIONS
    ]


def _product() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("knx_master.xml", _MASTER)
        archive.writestr("M-00FA/Hardware.xml", _hardware_xml())
        archive.writestr("M-00FA/Catalog.xml", _catalog_xml())
        for xml in _xmls():
            match = re.search(rb'<ApplicationProgram Id="([^"]+)"', xml)
            assert match is not None
            archive.writestr(f"M-00FA/{match.group(1).decode()}.xml", xml)
    return buffer.getvalue()


def _rows(db: Path, query: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(db) as connection:
        return sorted(connection.execute(query).fetchall())


def test_import_of_several_applications(tmp_path: Path) -> None:
    db = tmp_path / "cat.xknxcatalog"
    cache = tmp_path / "cache"
    service = CatalogService(db, cache)
    stored = service.import_knxprod(_product())
    service.close()

    assert _rows(
        db,
        "SELECT id, name, application_number, application_version, mask_version, "
        "is_secure_enabled FROM applications",
    ) == [
        ("M-00FA_A-0001-01-0000", "Calc", 1, 1, "MV-07B0", 0),
        ("M-00FA_A-0002-01-0000", "Invisible", 2, 1, "MV-07B0", 0),
        ("M-00FA_A-0003-01-0000", "Activation", 3, 1, "MV-07B0", 0),
        ("M-00FA_A-0004-01-0000", "Allocators", 4, 1, "MV-07B0", 0),
    ]
    assert _rows(
        db,
        "SELECT id, hardware_id, application_id, knxprod_path, registration_status, "
        "registration_number, registration_date FROM hardware_programs",
    ) == [
        (
            program_id,
            hardware_id,
            app_id,
            str(stored),
            "Registered",
            f"2026/{number}",
            f"2026-01-0{number}",
        )
        for number, (hardware_id, programs) in enumerate(_PROGRAMS.items(), start=1)
        for program_id, app_id in programs.items()
    ]
    assert _rows(
        db, "SELECT id, manufacturer_id, name, order_number FROM hardware"
    ) == [
        ("M-00FA_H-1-0", "M-00FA", "Device 1", "ORDER-1"),
        ("M-00FA_H-2-0", "M-00FA", "Device 2", "ORDER-2"),
    ]
    assert _rows(db, "SELECT id, name FROM manufacturers") == [("M-00FA", "Test")]
    assert _rows(
        db, "SELECT hardware_program_id, medium_type FROM hardware_program_medium_types"
    ) == [
        (program_id, "MT-0")
        for programs in _PROGRAMS.values()
        for program_id in programs
    ]
    assert _rows(
        db, "SELECT section_id, hardware_program_id FROM catalog_section_products"
    ) == [
        ("M-00FA_CS-1", program_id)
        for programs in _PROGRAMS.values()
        for program_id in programs
    ]

    # One parse cache entry per application XML: the pickled parse of it.
    entries = {entry.name: entry.read_bytes() for entry in cache.glob("ir_*.pkl")}
    expected: dict[str, bytes] = {}
    for xml in _xmls():
        version = detect_version(xml)
        path = parse_cache._cache_path(xml, version, cache)  # pyright: ignore[reportPrivateUsage]
        expected[path.name] = pickle.dumps(
            to_ir(xml, version), protocol=pickle.HIGHEST_PROTOCOL
        )
    assert entries == expected
