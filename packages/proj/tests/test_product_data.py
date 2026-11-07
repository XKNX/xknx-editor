"""A project stores the product data of its devices: what a device added in the editor uses is
added from its product file, and the stored data is what the catalog of another computer imports."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from xknxeditor.proj import export_knxproj
from xknxeditor.proj.core.manufacturer_xml import DeviceProduct, add_product_members
from xknxeditor.proj.core.project_store_serialize import unzip_members, zip_members
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
            f'<Product Id="{_M}_H-{n}_P-{n}" Text="{n}"/></Products><Hardware2Programs>'
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


def _project(tmp_path: Path, *numbers: str) -> tuple[ProjectService, str]:
    """A project with a device of hardware ``n`` (program ``n0``) per number."""
    svc = ProjectService()
    pid = svc.create(tmp_path / "p.xknx")
    for n in numbers:
        svc.add_device(
            pid,
            None,
            f"{_M}_H-{n}_P-{n}",
            hardware2program_ref_id=f"{_M}_H-{n}_HP-{n}0",
            application_program_ref_id=f"{_M}_A-{n}0",
        )
    return svc, pid


def test_a_new_project_has_no_product_data(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path)
    assert svc.product_data_programs(pid) == set()
    assert svc.product_archives(pid, b"<master/>") == []
    svc.close(pid)


def test_product_data_of_the_devices_is_added(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path, "1")
    assert svc.add_product_data(pid, _SOURCE)
    # Only what the device uses: hardware 2 and its application stay out.
    members = unzip_members(svc.project(pid).added_product_members)
    assert set(members) == {f"{_M}/Hardware.xml", f"{_M}/{_M}_A-10.xml"}
    assert svc.product_data_programs(pid) == {f"{_M}_H-1_HP-10"}
    # Nothing to add a second time.
    assert not svc.add_product_data(pid, _SOURCE)
    svc.close(pid)

    # Stored in the file.
    svc2 = ProjectService()
    pid2 = svc2.open(tmp_path / "p.xknx")
    assert svc2.product_data_programs(pid2) == {f"{_M}_H-1_HP-10"}
    svc2.close(pid2)


def test_a_further_device_adds_its_product_data(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path, "1")
    svc.add_product_data(pid, _SOURCE)
    svc.add_device(
        pid,
        None,
        f"{_M}_H-2_P-2",
        hardware2program_ref_id=f"{_M}_H-2_HP-20",
        application_program_ref_id=f"{_M}_A-20",
    )
    assert svc.add_product_data(pid, _SOURCE)
    assert svc.product_data_programs(pid) == {f"{_M}_H-1_HP-10", f"{_M}_H-2_HP-20"}
    svc.close(pid)


def test_the_archives_carry_a_master(tmp_path: Path) -> None:
    svc, pid = _project(tmp_path, "1")
    svc.add_product_data(pid, _SOURCE)
    assert svc.product_archives(pid, None) == []
    (archive,) = svc.product_archives(pid, b"<master/>")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.read("knx_master.xml") == b"<master/>"
        assert f"{_M}/{_M}_A-10.xml" in zf.namelist()
    # The same data gives the same archive (the catalog keeps one copy).
    assert svc.product_archives(pid, b"<master/>") == [archive]
    # The project's own master (an imported one's) wins.
    svc.project(pid).knx_master_xml = b"<own/>"
    (archive,) = svc.product_archives(pid, b"<master/>")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.read("knx_master.xml") == b"<own/>"
    svc.close(pid)


def test_imported_product_data_stays_as_it_is(tmp_path: Path) -> None:
    """What an imported archive brought is kept exactly; only what its devices need beyond it is
    added, beside it."""
    svc, pid = _project(tmp_path, "1", "2")
    hardware1 = DeviceProduct(
        f"{_M}_H-1_P-1", f"{_M}_H-1_HP-10", f"{_M}_A-10", imported=True
    )
    imported = zip_members(add_product_members({}, _SOURCE, [hardware1]))
    svc.project(pid).imported_product_members = imported
    assert svc.add_product_data(pid, _SOURCE)
    project = svc.project(pid)
    assert project.imported_product_members == imported
    added = unzip_members(project.added_product_members)
    # Hardware 1 is covered by the imported data: only hardware 2's data is added.
    assert f"{_M}/{_M}_A-20.xml" in added
    assert f"{_M}/{_M}_A-10.xml" not in added
    assert svc.product_data_programs(pid) == {f"{_M}_H-1_HP-10", f"{_M}_H-2_HP-20"}
    # One archive each.
    assert len(svc.product_archives(pid, b"<master/>")) == 2
    svc.close(pid)


def test_export_writes_the_added_product_data(tmp_path: Path) -> None:
    """Exported without a catalog, the archive still carries what the project stores."""
    svc, pid = _project(tmp_path, "1")
    svc.add_product_data(pid, _SOURCE)
    svc.close(pid)
    out = tmp_path / "p.knxproj"
    export_knxproj(tmp_path / "p.xknx", out)
    with zipfile.ZipFile(out) as zf:
        assert f"{_M}/{_M}_A-10.xml" in zf.namelist()
        assert f"{_M}/{_M}_A-20.xml" not in zf.namelist()


def test_the_same_members_give_the_same_archive() -> None:
    members = {"b.xml": b"2", "a.xml": b"1"}
    assert zip_members(members) == zip_members(dict(reversed(members.items())))
    assert unzip_members(zip_members(members)) == members
