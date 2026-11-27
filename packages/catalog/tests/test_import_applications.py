"""What importing a product with several application programs stores: its catalog rows and one
parse cache entry per application XML."""

from __future__ import annotations

import io
import pickle
import re
import sqlite3
import zipfile
from collections import OrderedDict
from pathlib import Path
from weakref import WeakValueDictionary

import pytest

from xknxeditor.catalog import CatalogService
from xknxeditor.catalog.core.applications import _load_application
from xknxeditor.namespaces import detect_version
from xknxeditor.prod import application as residency
from xknxeditor.prod import parse_cache
from xknxeditor.prod.archive import Archive
from xknxeditor.prod.data import to_ir
from xknxeditor.prod.errors import ArchiveError

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


def _hardware_xml(media: str = ' MediumTypes="MT-0"') -> str:
    devices = ""
    for number, (hardware_id, programs) in enumerate(_PROGRAMS.items(), start=1):
        refs = "".join(
            f'<Hardware2Program Id="{program_id}"{media}>'
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


def _product(
    master: str = _MASTER, hardware: str | None = None, xmls: list[bytes] | None = None
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("knx_master.xml", master)
        archive.writestr("M-00FA/Hardware.xml", hardware or _hardware_xml())
        archive.writestr("M-00FA/Catalog.xml", _catalog_xml())
        for xml in xmls or _xmls():
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
    entries = {
        entry.name: pickle.loads(entry.read_bytes()) for entry in cache.glob("ir_*.pkl")
    }
    expected: dict[str, object] = {}
    for xml in _xmls():
        version = detect_version(xml)
        path = parse_cache._cache_path(xml, version, cache)  # pyright: ignore[reportPrivateUsage]
        expected[path.name] = to_ir(xml, version)
    assert entries == expected


@pytest.fixture
def isolated_residency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(residency, "_resident", OrderedDict())
    monkeypatch.setattr(residency, "_loading", {})
    monkeypatch.setattr(residency, "_evaluated", WeakValueDictionary())
    monkeypatch.setattr(residency, "_memory", {})
    monkeypatch.setattr(residency, "_budget", [residency.resident_budget()])


@pytest.mark.parametrize("cache_hit", [False, True])
@pytest.mark.parametrize("eager", [False, True])
def test_catalog_makes_room_before_decoding_an_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_residency: None,
    cache_hit: bool,
    eager: bool,
) -> None:
    service = CatalogService(tmp_path / "catalog.xknxcatalog", tmp_path / "cache")
    service.import_knxprod(_product())
    app_id = "M-00FA_A-0002-01-0000"
    if cache_hit:
        service.load_application(app_id)
    first = service.get_application("M-00FA_A-0001-01-0000")
    assert first is not None
    first.resolved()
    residency._memory.pop(app_id)
    monkeypatch.setattr(residency, "_UNKNOWN_MEMORY", 1)
    residency.set_resident_budget(residency._memory[first.id] + 1)
    observations: list[str] = []

    def decoding() -> None:
        observations.extend(residency._loading.values())
        assert first is not None and not first.loaded

    if cache_hit:
        unpickle = pickle.load

        def load_cache(file):
            decoding()
            return unpickle(file)

        def fail_open(*args, **kwargs) -> None:
            raise ArchiveError("cache hit should not validate the archive")

        monkeypatch.setattr(pickle, "load", load_cache)
        monkeypatch.setattr(Archive, "__init__", fail_open)
    else:
        read_xml = Archive.get_application_xml

        def read(archive: Archive, manufacturer_id: str, app_id: str) -> bytes | None:
            decoding()
            return read_xml(archive, manufacturer_id, app_id)

        monkeypatch.setattr(Archive, "get_application_xml", read)

    if eager:
        second = service.get_application(app_id)
        assert second is not None and second.id == app_id
    else:

        def load():
            assert first is not None and first.loaded
            return service.load_application(app_id)

        second = residency.Application.unloaded(app_id, "Second", "23", "M-00FA", load)
        assert second.program.id == app_id
    assert observations == [app_id]
    assert not residency._loading
    service.close()


def test_missing_application_does_not_reserve_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_residency: None
) -> None:
    archive = tmp_path / "app.knxprod"
    archive.write_bytes(_product())

    def unexpected(*args, **kwargs):
        pytest.fail("reserved memory for a missing XML entry")

    monkeypatch.setattr(residency, "reserve_application_memory", unexpected)
    assert (
        _load_application(
            str(archive), "M-00FA", "M-00FA_A-missing", None, None, reserve=True
        )
        is None
    )


def test_catalog_parse_error_releases_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_residency: None
) -> None:
    import xknxeditor.prod

    archive = tmp_path / "app.knxprod"
    archive.write_bytes(_product())
    app_id = "M-00FA_A-0001-01-0000"

    def fail_parse(*args, **kwargs):
        assert list(residency._loading.values()) == [app_id]
        raise ValueError("invalid application XML")

    monkeypatch.setattr(xknxeditor.prod, "parse_application_xml", fail_parse)
    with pytest.raises(ValueError, match="invalid application XML"):
        _load_application(str(archive), "M-00FA", app_id, None, None, reserve=True)
    assert not residency._loading


def test_a_program_that_names_no_media_has_its_mask_versions(tmp_path: Path) -> None:
    db = tmp_path / "cat.xknxcatalog"
    master = _MASTER.replace(
        "<Manufacturers>",
        '<MaskVersions><MaskVersion Id="MV-07B0" MaskVersion="1968" Name="System B" '
        'ManagementModel="SystemB" MediumTypeRefId="MT-0" /></MaskVersions><Manufacturers>',
    )
    service = CatalogService(db, tmp_path / "cache")
    service.import_knxprod(_product(master, _hardware_xml(media="")))
    service.close()

    assert _rows(
        db, "SELECT hardware_program_id, medium_type FROM hardware_program_medium_types"
    ) == [
        (program_id, "MT-0")
        for programs in _PROGRAMS.values()
        for program_id in programs
    ]


def test_another_file_with_an_application_the_catalog_has_leaves_it(
    tmp_path: Path,
) -> None:
    # A second product file declares the same programs and application ids, with other content.
    other = [xml.replace(b'Name="Calc"', b'Name="Calc 2"', 1) for xml in _xmls()]
    assert other != _xmls()
    db = tmp_path / "cat.xknxcatalog"
    service = CatalogService(db, tmp_path / "cache")
    first = service.import_knxprod(_product())
    service.import_knxprod(_product(xmls=other))
    program = "M-00FA_H-1-0_HP-0001-01-0000"
    assert service.get_program_source(program) == (str(first), "M-00FA")
    application = service.get_application("M-00FA_A-0001-01-0000")
    assert application is not None
    assert application.name == "Calc"

    # Without its first file the program takes the other one.
    first.unlink()
    second = service.import_knxprod(_product(xmls=other))
    assert service.get_program_source(program) == (str(second), "M-00FA")
    service.close()
