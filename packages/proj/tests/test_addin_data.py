from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from xknxeditor.proj import ProjectService
from xknxeditor.proj.core.addin_data import export_addin_data, normalize_addin_data
from xknxeditor.proj.models import Project


def test_edit_readdress_undo_delete_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    service = ProjectService()
    pid = service.create(path, "P-ABCD")
    area = next(a for a in service.topology(pid, 0).areas if a.address == 1)
    segment = next(line for line in area.lines if line.address == 1).segments[0].id
    device = service.add_device(pid, segment, "P-1", address=31)
    app = "M0083-A0041"
    session = service._state(pid).session
    project = session.get(Project, pid)
    assert project is not None
    project.extra_subtrees = {
        "AddinData": '<AddinData><AddinData Name="4383-Lib" AddinId="M0083-A0041" /></AddinData>'
    }
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(f"AddinData/{app}/4383-Lib.dat", b"\x01")
        archive.writestr("other.bin", b"opaque")
    project.imported_project_files = stream.getvalue()
    session.commit()
    assert normalize_addin_data(session, project)
    session.commit()
    assert not normalize_addin_data(session, project)
    assert service.device_addin_data(pid, device, app) == {"Lib": b"\x01"}
    service.set_device_addin_data(
        pid, device, app, {"Symbol_0_bmp": b"pixels", "Symbol_0_data": b"wire"}
    )
    service.set_individual_address(pid, device, "1.1.32")
    metadata, files = export_addin_data(
        project, service._installation(service._state(pid), 0)
    )
    assert {e.get("Name") for e in metadata} == {
        "4384-Lib",
        "4384-Symbol_0_bmp",
        "4384-Symbol_0_data",
    }
    assert files[f"AddinData/{app}/4384-Symbol_0_data.dat"] == b"wire"
    service.undo(pid)
    service.undo(pid)
    assert service.device_addin_data(pid, device, app) == {"Lib": b"\x01"}
    service.redo(pid)
    service.close(pid)
    pid = service.open(path)
    assert service.device_addin_data(pid, device, app)["Symbol_0_data"] == b"wire"
    service.remove_device(pid, device)
    service.undo(pid)
    assert service.device_addin_data(pid, device, app)["Symbol_0_bmp"] == b"pixels"
    service.set_device_addin_data(pid, device, app, {"Lib": None})
    assert "Lib" not in service.device_addin_data(pid, device, app)
    service.undo(pid)
    assert service.device_addin_data(pid, device, app)["Lib"] == b"\x01"
    service.close(pid)


def test_metadata_and_empty_store_export_follow_edits(tmp_path: Path) -> None:
    import xml.etree.ElementTree as ET

    from xknxeditor.proj.core.project_store_serialize import build_store_streams

    service = ProjectService()
    pid = service.create(tmp_path / "p.xknx", "P-ABCD")
    area = next(a for a in service.topology(pid, 0).areas if a.address == 1)
    segment = next(line for line in area.lines if line.address == 1).segments[0].id
    device = service.add_device(pid, segment, "P-1", address=1)
    session = service._state(pid).session
    project = service.project(pid)
    project.extra_subtrees = {
        "AddinData": '<AddinData><AddinData Name="4353-Lib" AddinId="M0083-A0041" Extra="keep"><Extra /></AddinData></AddinData>'
    }
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("AddinData/M0083-A0041/4353-Lib.dat", b"\x00")
    project.imported_project_files = stream.getvalue()
    normalize_addin_data(session, project)
    session.commit()
    service.set_device_addin_data(pid, device, "M0083-A0041", {"Lib": b"\x01"})
    inst = service._installation(service._state(pid), 0)
    metadata, files = export_addin_data(project, inst)
    assert metadata[0].get("Extra") == "keep"
    assert metadata[0].find("Extra") is not None
    built = build_store_streams(project, inst)
    assert built.addin_data == files
    assert ET.fromstring(built.streams["AI"]).find("AInDat").get("Name") == "4353-Lib"
    service.set_device_addin_data(pid, device, "M0083-A0041", {"Lib": None})
    built = build_store_streams(project, inst)
    assert len(ET.fromstring(built.streams["AI"])) == 0
    assert not built.addin_data
    service.close(pid)
