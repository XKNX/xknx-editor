"""A project stores the product data of its devices: what a device added in the editor uses is
stored per hardware program from its product file, beside what an imported archive brought, and
both are what the catalog of another computer imports."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from xknxeditor.proj import export_knxproj
from xknxeditor.proj.core.manufacturer_xml import DeviceProduct, add_product_members
from xknxeditor.proj.core.product_data import unzip_members, zip_members
from xknxeditor.proj.core.service import ProjectService

_NS = "http://knx.org/xml/project/23"
_M = "M-0083"


def _xml(body: str) -> bytes:
    return (
        f'<?xml version="1.0" encoding="utf-8"?><KNX xmlns="{_NS}"><ManufacturerData>'
        f'<Manufacturer RefId="{_M}">{body}</Manufacturer></ManufacturerData></KNX>'
    ).encode()


# A product file: hardware 1 with program 10, hardware 2 with program 20.
_SOURCE = {
    f"{_M}/Hardware.xml": _xml(
        "<Hardware>"
        + "".join(
            f'<Hardware Id="{_M}_H-{n}" Name="{n}"><Products>'
            f'<Product Id="{_M}_H-{n}_P-{n}" Text="{n}"/>'
            f'<Product Id="{_M}_H-{n}_P-{n}b" Text="{n}b"/></Products><Hardware2Programs>'
            f'<Hardware2Program Id="{_M}_H-{n}_HP-{n}0">'
            f'<ApplicationProgramRef RefId="{_M}_A-{n}0"/></Hardware2Program>'
            "</Hardware2Programs></Hardware>"
            for n in ("1", "2")
        )
        + "</Hardware>"
    ),
    f"{_M}/{_M}_A-10.xml": _xml('<ApplicationPrograms Id="10"/>'),
    f"{_M}/{_M}_A-20.xml": _xml('<ApplicationPrograms Id="20"/>'),
}


def _add(svc: ProjectService, pid: str, n: str, product: str = "") -> None:
    """A device of hardware ``n`` (program ``n0``), of its product ``product`` (default ``n``)."""
    svc.add_device(
        pid,
        None,
        f"{_M}_H-{n}_P-{product or n}",
        hardware2program_ref_id=f"{_M}_H-{n}_HP-{n}0",
        application_program_ref_id=f"{_M}_A-{n}0",
    )


def _project(tmp_path: Path, *numbers: str) -> tuple[ProjectService, str]:
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx")
    for n in numbers:
        _add(svc, pid, n)
    return svc, pid


def _row(path: Path, program: str) -> dict[str, bytes]:
    """The members of a program's stored product data, read from the file."""
    import sqlite3

    with sqlite3.connect(path) as db:
        (blob,) = db.execute(
            "SELECT data FROM product_data WHERE hardware2program_ref_id = ?",
            (program,),
        ).fetchone()
    return unzip_members(blob)


def test_a_new_project_has_no_product_data(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    assert svc.product_data_programs(pid) == set()
    assert svc.product_archives(pid, b"<master/>") == []
    svc.close(pid)


def test_each_program_gets_its_own_product_data(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    svc, pid = _project(tmp_path, "1")
    assert svc.add_product_data(pid, _SOURCE)
    # Only what the device uses: hardware 2 and its application stay out.
    assert set(_row(path, f"{_M}_H-1_HP-10")) == {
        f"{_M}/Hardware.xml",
        f"{_M}/{_M}_A-10.xml",
    }
    assert svc.stored_products(pid) == {f"{_M}_H-1_HP-10": frozenset({f"{_M}_H-1_P-1"})}
    # Nothing to add a second time.
    assert not svc.add_product_data(pid, _SOURCE)

    # A further program gets a row of its own; the first stays as it is.
    first = _row(path, f"{_M}_H-1_HP-10")
    _add(svc, pid, "2")
    assert svc.add_product_data(pid, _SOURCE)
    assert _row(path, f"{_M}_H-1_HP-10") == first
    assert f"{_M}/{_M}_A-20.xml" in _row(path, f"{_M}_H-2_HP-20")
    svc.close(pid)

    svc2 = ProjectService()
    pid2 = svc2.open(path)
    assert svc2.product_data_programs(pid2) == {f"{_M}_H-1_HP-10", f"{_M}_H-2_HP-20"}
    svc2.close(pid2)


def test_another_product_of_the_program_renews_its_row(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path, "1")
    svc.add_product_data(pid, _SOURCE)
    _add(svc, pid, "1", product="1b")
    assert svc.add_product_data(pid, _SOURCE)
    assert svc.stored_products(pid) == {
        f"{_M}_H-1_HP-10": frozenset({f"{_M}_H-1_P-1", f"{_M}_H-1_P-1b"})
    }
    hardware = _row(tmp_path / "p.xknx", f"{_M}_H-1_HP-10")[f"{_M}/Hardware.xml"]
    assert f'Id="{_M}_H-1_P-1b"'.encode() in hardware
    svc.close(pid)


def test_a_program_no_source_declares_gets_no_row(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    svc.add_device(
        pid, None, "M-0001_H-9_P-9", hardware2program_ref_id="M-0001_H-9_HP-90"
    )
    assert not svc.add_product_data(pid, _SOURCE)
    assert svc.stored_products(pid) == {}
    svc.close(pid)


def test_the_archives_carry_a_master(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path, "1", "2")
    svc.add_product_data(pid, _SOURCE)
    assert svc.product_archives(pid, None) == []
    (archive,) = svc.product_archives(pid, b"<master/>")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.read("knx_master.xml") == b"<master/>"
        # The rows as one product data: both hardware in one Hardware.xml.
        hardware = zf.read(f"{_M}/Hardware.xml")
        assert f'Id="{_M}_H-1"'.encode() in hardware
        assert f'Id="{_M}_H-2"'.encode() in hardware
        assert {f"{_M}/{_M}_A-10.xml", f"{_M}/{_M}_A-20.xml"} <= set(zf.namelist())
    # The same data gives the same archive (the catalog keeps one copy).
    assert svc.product_archives(pid, b"<master/>") == [archive]
    # The project's own master (an imported one's) wins.
    svc.project(pid).knx_master_xml = b"<own/>"
    (archive,) = svc.product_archives(pid, b"<master/>")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.read("knx_master.xml") == b"<own/>"
    svc.close(pid)


def test_imported_product_data_stays_as_it_is(tmp_path: Path) -> None:
    """What an imported archive brought is kept exactly; only the programs it does not declare get
    a row."""
    svc, pid = _project(tmp_path, "1", "2")
    hardware1 = DeviceProduct(
        f"{_M}_H-1_P-1", f"{_M}_H-1_HP-10", f"{_M}_A-10", imported=True
    )
    imported = zip_members(add_product_members({}, _SOURCE, [hardware1]))
    svc.project(pid).imported_product_members = imported
    assert svc.add_product_data(pid, _SOURCE)
    assert svc.project(pid).imported_product_members == imported
    assert set(svc.stored_products(pid)) == {f"{_M}_H-2_HP-20"}
    assert svc.product_data_programs(pid) == {f"{_M}_H-1_HP-10", f"{_M}_H-2_HP-20"}
    # One archive each, the imported one with its members as they were.
    imported_archive, _stored = svc.product_archives(pid, b"<master/>")
    with zipfile.ZipFile(io.BytesIO(imported_archive)) as zf:
        hardware = zf.read(f"{_M}/Hardware.xml")
    assert hardware == unzip_members(imported)[f"{_M}/Hardware.xml"]
    svc.close(pid)


def test_export_writes_the_stored_product_data(tmp_path: Path) -> None:
    """Exported without a catalog, the archive still carries what the project stores."""
    svc, pid = _project(tmp_path, "1")
    svc.add_product_data(pid, _SOURCE)
    svc.close(pid)
    out = tmp_path / "p.knxproj"
    export_knxproj(tmp_path / "p.xknx", out)
    with zipfile.ZipFile(out) as zf:
        assert f"{_M}/{_M}_A-10.xml" in zf.namelist()
        assert f"{_M}/{_M}_A-20.xml" not in zf.namelist()


def test_compact_removes_the_rows_no_device_uses(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path, "1", "2")
    svc.add_product_data(pid, _SOURCE)
    device = next(
        d for d, p in svc.device_programs(pid).items() if p == f"{_M}_H-2_HP-20"
    )
    svc.remove_device(pid, device)
    analysis = svc.analyse_compaction(pid)
    assert f"{_M}_A-20" in analysis.report.application_programs
    # The analysis changes nothing.
    assert set(svc.stored_products(pid)) == {f"{_M}_H-1_HP-10", f"{_M}_H-2_HP-20"}
    result = svc.compact(pid)
    assert f"{_M}_A-20" in result.report.application_programs
    assert set(svc.stored_products(pid)) == {f"{_M}_H-1_HP-10"}
    svc.close(pid)


def test_the_same_members_give_the_same_archive() -> None:
    members = {"b.xml": b"2", "a.xml": b"1"}
    assert zip_members(members) == zip_members(dict(reversed(members.items())))
    assert unzip_members(zip_members(members)) == members
