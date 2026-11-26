"""Catalog fallback for the project data used by the MCP archive export."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from xknxeditor.proj import export_knxproj
from xknxeditor.proj.core.product_data import zip_members
from xknxeditor.proj.core.service import ProjectService

from .test_server import _build_ctx, _data

_MID = "M-00FA"
_HARDWARE = f"{_MID}/Hardware.xml"
_CATALOG = f"{_MID}/Catalog.xml"
_BAGGAGES = f"{_MID}/Baggages.xml"


def _xml(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<KNX xmlns="http://knx.org/xml/project/23"><ManufacturerData>'
        f'<Manufacturer RefId="{_MID}">{body.replace("/>", " />")}</Manufacturer></ManufacturerData></KNX>'
    ).encode()


def _product(number: int) -> tuple[str, str, str]:
    hw = f"{_MID}_H-{number}"
    return f"{hw}_P-1", f"{hw}_HP-1", f"{_MID}_A-{number}"


def _members(*numbers: int) -> dict[str, bytes]:
    hardware, catalog, baggages = [], [], []
    files: dict[str, bytes] = {}
    for n in numbers:
        product, program, app = _product(n)
        hardware.append(
            f'<Hardware Id="{_MID}_H-{n}"><Products><Product Id="{product}"/></Products>'
            f'<Hardware2Programs><Hardware2Program Id="{program}">'
            f'<ApplicationProgramRef RefId="{app}"/></Hardware2Program>'
            "</Hardware2Programs></Hardware>"
        )
        catalog.append(
            f'<CatalogItem Id="{_MID}_CI-{n}" ProductRefId="{product}" '
            f'Hardware2ProgramRefId="{program}"/>'
        )
        baggages.append(f'<Baggage Id="{_MID}_BG-{n}" Name="{n}.png"/>')
        files[f"{_MID}/{app}.xml"] = _xml(
            f'<ApplicationPrograms><ApplicationProgram Id="{app}" Icon="{_MID}_BG-{n}"/>'
            "</ApplicationPrograms>"
        )
        files[f"{_MID}/Baggages/{n}.png"] = bytes([n])
    return {
        _HARDWARE: _xml(f"<Hardware>{''.join(hardware)}</Hardware>"),
        _CATALOG: _xml(
            f'<Catalog><CatalogSection Id="{_MID}_CS-1">{"".join(catalog)}</CatalogSection></Catalog>'
        ),
        _BAGGAGES: _xml(f"<Baggages>{''.join(baggages)}</Baggages>"),
        **files,
    }


@pytest.mark.parametrize(
    "coverage", ["imported", "stored", "mixed", "missing app", "missing product"]
)
async def test_export_reads_only_missing_catalog_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coverage: str
) -> None:
    # A manufacturer's archive contains two products with separate application and baggage files.
    catalog_members = _members(1, 2)
    master = b'<KNX xmlns="http://knx.org/xml/project/20"><MasterData Signature="AA=="/></KNX>'
    archive = tmp_path / "catalog.knxprod"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("knx_master.xml", master)
        for name, content in catalog_members.items():
            zf.writestr(name, content)

    source = tmp_path / "project.xknx"
    service = ProjectService()
    pid = service.create(source)
    for n in (1, 2) if coverage == "mixed" else (1,):
        product, program, app = _product(n)
        service.add_device(
            pid,
            None,
            product,
            hardware2program_ref_id=program,
            application_program_ref_id=app,
        )
    service.ensure_guid(pid)
    owned = _members(1)
    app_member = f"{_MID}/{_product(1)[2]}.xml"
    if coverage == "missing app":
        del owned[app_member]
    if coverage == "missing product":
        owned[_HARDWARE] = owned[_HARDWARE].replace(
            f'<Product Id="{_product(1)[0]}" />'.encode(), b""
        )
    if coverage == "stored":
        product, program, _ = _product(1)
        service.store_product_rows(pid, {program: ([product], zip_members(owned))})
    service.close(pid)
    with sqlite3.connect(source) as db:
        db.execute(
            "UPDATE projects SET last_modified = ?, imported_product_members = ?",
            (
                "2026-01-01T00:00:00.0000000Z",
                None if coverage == "stored" else zip_members(owned),
            ),
        )

    ctx = _build_ctx(tmp_path)
    monkeypatch.setattr("editor_gui.master_data.master_xml_bytes", lambda: None)
    ctx.api.project.open(source)
    monkeypatch.setattr(
        ctx.api.catalog, "get_program_source", lambda _: (str(archive), _MID)
    )
    monkeypatch.setattr(
        "editor_gui.plugins.mcp.tools.project.export_schema", lambda: "20"
    )
    monkeypatch.setattr(
        "xknxeditor.proj.core.knxproj_export._newest_master",
        lambda _schema, _cache, candidates: candidates[0],
    )

    # Read the unfiltered sources as a reference. Fix the project timestamp and GUID so every
    # archive member can be checked, including the project and manufacturer signatures.
    reference = tmp_path / "reference.knxproj"
    export_knxproj(
        source, reference, schema="20", master_xml=master, extra_files=catalog_members
    )
    reads: set[str] = set()
    read = zipfile.ZipFile.read

    def record(
        zf: zipfile.ZipFile, name: str | zipfile.ZipInfo, pwd: bytes | None = None
    ) -> bytes:
        if zf.filename == str(archive):
            reads.add(name if isinstance(name, str) else name.filename)
        return read(zf, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", record)
    exported = tmp_path / "out.knxproj"
    try:
        async with Client(build_server(ctx)) as client:
            assert (await _data(client, "project_export_knxproj", dest=str(exported)))[
                "status"
            ] == "exported"
    finally:
        ctx.api.project.close()

    expected_reads = {"knx_master.xml"}
    if coverage not in {"imported", "stored"}:
        expected_reads.update(_members(2 if coverage == "mixed" else 1))
    assert reads == expected_reads
    with zipfile.ZipFile(reference) as old, zipfile.ZipFile(exported) as new:
        assert new.namelist() == old.namelist()
        assert {name: new.read(name) for name in new.namelist()} == {
            name: old.read(name) for name in old.namelist()
        }
        assert app_member in new.namelist()
        assert _product(1)[0].encode() in new.read(_HARDWARE)
