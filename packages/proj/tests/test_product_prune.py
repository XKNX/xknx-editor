"""Pruning of stored product data no device uses (Compact Project / export option)."""

import io
import os
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy.orm import Session

from xknxeditor.proj import (
    CompactCancelled,
    ProjectService,
    export_knxproj,
    import_knxproj,
    prune_product_blob,
    prune_product_members,
)
from xknxeditor.proj.core.knxproj_signing import verify_directory_signature
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import Device, Project

_REAL = Path(__file__).parent / "fixtures" / "xknx_test_project_no_password.knxproj"
_NS = "http://knx.org/xml/project/20"
_HEAD = (
    '﻿<?xml version="1.0" encoding="utf-8"?>\r\n'
    f'<KNX CreatedBy="Tool" ToolVersion="1.0" xmlns="{_NS}">\r\n'
    "  <ManufacturerData>\r\n"
)
_TAIL = "  </ManufacturerData>\r\n</KNX>"


def _doc(mid: str, body: str) -> bytes:
    lines = [
        f'    <Manufacturer RefId="{mid}">',
        body.rstrip("\r\n"),
        "    </Manufacturer>",
    ]
    return (_HEAD + "\r\n".join(lines) + "\r\n" + _TAIL).encode("utf-8")


def _hardware(hw_id: str, products: list[str], programs: dict[str, str]) -> str:
    out = [f'        <Hardware Id="{hw_id}" Name="{hw_id}" SerialNumber="1">']
    out.append("          <Products>")
    for p in products:
        out.append(f'            <Product Id="{p}" Text="{p}" OrderNumber="{p}">')
        out.append("              <Attributes>")
        out.append(
            f'                <Attribute Id="{p}_AT-Series" Name="Series" Value="S" />'
        )
        out.append("              </Attributes>")
        out.append("            </Product>")
    out.append("          </Products>")
    out.append("          <Hardware2Programs>")
    for hp, app in programs.items():
        out.append(f'            <Hardware2Program Id="{hp}" MediumTypes="MT-0">')
        out.append(f'              <ApplicationProgramRef RefId="{app}" />')
        out.append("            </Hardware2Program>")
    out.append("          </Hardware2Programs>")
    out.append("        </Hardware>")
    return "\r\n".join(out)


def _translations(refs: list[str]) -> str:
    out = ["      <Languages>", '        <Language Identifier="de-DE">']
    for ref in refs:
        out += [
            f'          <TranslationUnit RefId="{ref}">',
            f'            <TranslationElement RefId="{ref}">',
            f'              <Translation AttributeName="Text" Text="de {ref}" />',
            "            </TranslationElement>",
            "          </TranslationUnit>",
        ]
    out += ["        </Language>", "      </Languages>"]
    return "\r\n".join(out)


def _app(app_id: str, baggages: list[str]) -> bytes:
    refs = "".join(f'<Baggage RefId="{b}" />' for b in baggages)
    return (
        f'<?xml version="1.0" encoding="utf-8"?><KNX xmlns="{_NS}"><ManufacturerData>'
        f'<Manufacturer RefId="{app_id[:6]}"><ApplicationPrograms>'
        f'<ApplicationProgram Id="{app_id}" Name="App &amp; {app_id[-4:]}" '
        f'ApplicationVersion="18" IconFile="{baggages[0] if baggages else ""}">'
        f"{refs}</ApplicationProgram></ApplicationPrograms></Manufacturer>"
        "</ManufacturerData></KNX>"
    ).encode()


M1 = "M-0001"
H1, H2, H3 = f"{M1}_H-1", f"{M1}_H-2", f"{M1}_H-3"
P1A, P1B, P2, P3 = f"{H1}_P-1a", f"{H1}_P-1b", f"{H2}_P-2", f"{H3}_P-3"
HP1X, HP1Y, HP2, HP3, HP3B = (
    f"{H1}_HP-1x",
    f"{H1}_HP-1y",
    f"{H2}_HP-2",
    f"{H3}_HP-3",
    f"{H3}_HP-3b",
)
A1, A2, A3 = f"{M1}_A-0001", f"{M1}_A-0002", f"{M1}_A-0003"
BG_SHARED, BG_A1, BG_A2, BG_A3, BG_ORPHAN = (
    f"{M1}_BG--shared.2Epng",
    f"{M1}_BG--a1.2Epng",
    f"{M1}_BG-sub.5Cdir-a2.2Ezip",
    f"{M1}_BG--a3.2Epng",
    f"{M1}_BG--orphan.2Epng",
)
M2 = "M-0002"


def _synthetic() -> dict[str, bytes]:
    hardware = "\r\n".join(
        [
            "      <Hardware>",
            _hardware(H1, [P1A, P1B], {HP1X: A1, HP1Y: A2}),
            _hardware(H2, [P2], {HP2: A1}),
            _hardware(H3, [P3], {HP3: A1, HP3B: A3}),
            "      </Hardware>",
            _translations([P1A, P1B, f"{P3}_AT-Series", H3]),
        ]
    )
    catalog = "\r\n".join(
        [
            "      <Catalog>",
            f'        <CatalogSection Id="{M1}_CS-1" Name="One" Number="1">',
            f'          <CatalogItem Id="{M1}_CI-1" ProductRefId="{P1A}" Hardware2ProgramRefId="{HP1X}" />',
            f'          <CatalogItem Id="{M1}_CI-2" ProductRefId="{P1B}" Hardware2ProgramRefId="{HP1X}" />',
            f'          <CatalogItem Id="{M1}_CI-3" ProductRefId="{P1A}" Hardware2ProgramRefId="{HP1Y}" />',
            f'          <CatalogItem Id="{M1}_CI-4" ProductRefId="{P2}" Hardware2ProgramRefId="{HP2}" />',
            "        </CatalogSection>",
            f'        <CatalogSection Id="{M1}_CS-2" Name="Two" Number="2">',
            f'          <CatalogSection Id="{M1}_CS-2-1" Name="Two one" Number="1">',
            f'            <CatalogItem Id="{M1}_CI-5" ProductRefId="{P3}" Hardware2ProgramRefId="{HP3}" />',
            "          </CatalogSection>",
            "        </CatalogSection>",
            "      </Catalog>",
            _translations(
                [f"{M1}_CS-1", f"{M1}_CI-1", f"{M1}_CI-2", f"{M1}_CS-2", f"{M1}_CI-5"]
            ),
        ]
    )

    def bag(target: str, name: str, bg_id: str) -> str:
        return (
            f'        <Baggage TargetPath="{target}" Name="{name}" Id="{bg_id}">\r\n'
            '          <FileInfo TimeInfo="2024-01-01T00:00:00Z" />\r\n'
            "        </Baggage>"
        )

    baggages = "\r\n".join(
        [
            "      <Baggages>",
            bag("", "shared.png", BG_SHARED),
            bag("", "a1.png", BG_A1),
            bag("sub\\dir", "a2.zip", BG_A2),
            bag("", "a3.png", BG_A3),
            bag("", "orphan.png", BG_ORPHAN),
            "      </Baggages>",
        ]
    )
    m2_hw = "\r\n".join(
        [
            "      <Hardware>",
            _hardware(
                f"{M2}_H-9", [f"{M2}_H-9_P-9"], {f"{M2}_H-9_HP-9": f"{M2}_A-0009"}
            ),
            "      </Hardware>",
        ]
    )
    m2_cat = (
        f'      <Catalog>\r\n        <CatalogItem Id="{M2}_CI-9" '
        f'ProductRefId="{M2}_H-9_P-9" Hardware2ProgramRefId="{M2}_H-9_HP-9" />\r\n'
        "      </Catalog>"
    )
    return {
        f"{M1}/Hardware.xml": _doc(M1, hardware),
        f"{M1}/Catalog.xml": _doc(M1, catalog),
        f"{M1}/Baggages.xml": _doc(M1, baggages),
        f"{M1}/{A1}.xml": _app(A1, [BG_SHARED, BG_A1]),
        f"{M1}/{A2}.xml": _app(A2, [BG_SHARED, BG_A2]),
        f"{M1}/{A3}.xml": _app(A3, [BG_A3]),
        f"{M1}/Baggages/shared.png": b"shared",
        f"{M1}/Baggages/a1.png": b"a1",
        f"{M1}/Baggages/sub/dir/a2.zip": b"a2",
        f"{M1}/Baggages/a3.png": b"a3",
        f"{M1}/Baggages/orphan.png": b"orphan",
        f"{M1}.signature": b"c2ln",
        f"{M2}/Hardware.xml": _doc(M2, m2_hw),
        f"{M2}/Catalog.xml": _doc(M2, m2_cat),
        f"{M2}/{M2}_A-0009.xml": _app(f"{M2}_A-0009", []),
        f"{M2}/Baggages/x.png": b"x",
        f"{M2}.signature": b"c2ln",
    }


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _unzip(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _ids(data: bytes, attr: str = "Id") -> set[str]:
    return {el.attrib[attr] for el in ET.fromstring(data).iter() if attr in el.attrib}


_USED_PRODUCTS = {P1A, P2}
_USED_PROGRAMS = {HP1X, HP2}


def test_prune_synthetic_set() -> None:
    members = _synthetic()
    out, report = prune_product_members(members, _USED_PRODUCTS, _USED_PROGRAMS)

    assert sorted(report.application_programs) == [A2, A3, f"{M2}_A-0009"]
    assert sorted(report.hardware) == [H3, f"{M2}_H-9"]
    assert sorted(report.products) == [P1B, P3, f"{M2}_H-9_P-9"]
    assert sorted(report.catalog_items) == [
        f"{M1}_CI-2",
        f"{M1}_CI-3",
        f"{M1}_CI-5",
        f"{M2}_CI-9",
    ]
    assert report.manufacturers == [M2]
    assert report.names[A2] == f"App & {A2[-4:]} V1.2"
    assert report.names[P3] == P3
    assert H3 not in report.names or report.names[H3]
    assert sorted(report.baggages) == [
        f"{M1}/Baggages/a3.png",
        f"{M1}/Baggages/sub/dir/a2.zip",
        f"{M2}/Baggages/x.png",
    ]
    assert report.size_after < report.size_before

    # Whole manufacturer gone, including its signature.
    assert not any(n.startswith(M2) for n in out)
    # Shared application and baggages still referenced stay; an unreferenced one is not touched.
    for kept in (
        f"{M1}/{A1}.xml",
        f"{M1}/Baggages/shared.png",
        f"{M1}/Baggages/a1.png",
        f"{M1}/Baggages/orphan.png",
        f"{M1}.signature",
    ):
        assert out[kept] == members[kept]
    assert f"{M1}/{A2}.xml" not in out and f"{M1}/{A3}.xml" not in out

    hw = out[f"{M1}/Hardware.xml"]
    assert hw.startswith(b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>\r\n')
    assert b"\n" not in hw.replace(b"\r\n", b"")
    hw_ids = _ids(hw)
    assert {H1, H2, P1A, P2, HP1X, HP2} <= hw_ids
    assert not {H3, P1B, P3, HP1Y, HP3, HP3B} & hw_ids
    hw_refs = _ids(hw, "RefId")
    assert P1B not in hw_refs and H3 not in hw_refs and f"{P3}_AT-Series" not in hw_refs
    assert P1A in hw_refs
    # Every application a kept Hardware2Program names is still there.
    for ref in hw_refs:
        if ref.startswith(f"{M1}_A-"):
            assert f"{M1}/{ref}.xml" in out

    cat = out[f"{M1}/Catalog.xml"]
    cat_root = ET.fromstring(cat)
    for item in cat_root.iter(f"{{{_NS}}}CatalogItem"):
        assert item.attrib["ProductRefId"] in hw_ids
        assert item.attrib["Hardware2ProgramRefId"] in hw_ids
    cat_ids = _ids(cat)
    assert cat_ids == {f"{M1}_CS-1", f"{M1}_CI-1", f"{M1}_CI-4"}
    assert _ids(cat, "RefId") - {M1} == {f"{M1}_CS-1", f"{M1}_CI-1"}

    bag_ids = _ids(out[f"{M1}/Baggages.xml"])
    assert bag_ids == {BG_SHARED, BG_A1, BG_ORPHAN}

    # The blob variant yields the same members.
    blob_out, blob_report = prune_product_blob(
        _zip(members), _USED_PRODUCTS, _USED_PROGRAMS
    )
    assert _unzip(blob_out) == out
    assert blob_report.removed_members == report.removed_members
    assert blob_report.size_after < blob_report.size_before


def test_prune_keeps_application_named_by_device() -> None:
    members = _synthetic()
    out, report = prune_product_members(
        members, _USED_PRODUCTS, _USED_PROGRAMS, application_ids=[A3]
    )
    assert f"{M1}/{A3}.xml" in out
    assert A3 not in report.application_programs
    assert f"{M1}/Baggages/a3.png" in out


def test_prune_nothing_unused_is_byte_identical() -> None:
    members = _synthetic()
    products = {P1A, P1B, P2, P3, f"{M2}_H-9_P-9"}
    programs = {HP1X, HP1Y, HP2, HP3, HP3B, f"{M2}_H-9_HP-9"}
    out, report = prune_product_members(members, products, programs)
    assert report.nothing_removed
    assert out == members
    blob = _zip(members)
    blob_out, blob_report = prune_product_blob(blob, products, programs)
    assert blob_out is blob
    assert blob_report.nothing_removed


def test_prune_real_fixture_keeps_everything(tmp_path: Path) -> None:
    dest = tmp_path / "p.xknx"
    import_knxproj(_REAL, dest)
    with Session(make_engine(url_for(dest))) as session:
        blob = session.query(Project).one().imported_product_members
        rows = session.query(Device).all()
        products = {d.product_ref_id for d in rows}
        programs = {d.hardware2program_ref_id or "" for d in rows}
    assert blob
    out, report = prune_product_blob(blob, products, programs)
    assert report.nothing_removed
    assert out is blob


def _inject_unused(path: Path, padding: int = 0) -> dict[str, bytes]:
    """Add unused hardware + application to M-0002 and an unused manufacturer to the stored data."""
    with Session(make_engine(url_for(path))) as session:
        project = session.query(Project).one()
        assert project.imported_product_members
        members = _unzip(project.imported_product_members)
        hw = members["M-0002/Hardware.xml"]
        sep = b"\r\n" if b"\r\n" in hw else b"\n"
        block = sep.join(
            [
                b'        <Hardware Id="M-0002_H-UNUSED" Name="Unused" SerialNumber="0">',
                b"          <Products>",
                b'            <Product Id="M-0002_H-UNUSED_P-UNUSED" Text="Unused" OrderNumber="U" />',
                b"          </Products>",
                b"          <Hardware2Programs>",
                b'            <Hardware2Program Id="M-0002_H-UNUSED_HP-FFFF-01-0000" MediumTypes="MT-0">',
                b'              <ApplicationProgramRef RefId="M-0002_A-FFFF-01-0000" />',
                b"            </Hardware2Program>",
                b"          </Hardware2Programs>",
                b"        </Hardware>",
            ]
        )
        marker = sep + b"      </Hardware>"
        members["M-0002/Hardware.xml"] = hw.replace(marker, sep + block + marker, 1)
        assert b"M-0002_H-UNUSED" in members["M-0002/Hardware.xml"]
        members["M-0002/M-0002_A-FFFF-01-0000.xml"] = _app(
            "M-0002_A-FFFF-01-0000", []
        ) + os.urandom(padding)
        members["M-0003/Hardware.xml"] = _doc(
            "M-0003",
            "      <Hardware>\r\n"
            + _hardware(
                "M-0003_H-1", ["M-0003_H-1_P-1"], {"M-0003_H-1_HP-1": "M-0003_A-0001"}
            )
            + "\r\n      </Hardware>",
        )
        members["M-0003/M-0003_A-0001.xml"] = _app("M-0003_A-0001", [])
        members["M-0003.signature"] = b"c2ln"
        project.imported_product_members = _zip(members)
        session.commit()
    return members


def _archive(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


def _device_refs(path: Path) -> list[tuple[str, str | None, int | None]]:
    with Session(make_engine(url_for(path))) as session:
        return sorted(
            (d.product_ref_id, d.hardware2program_ref_id, d.address)
            for d in session.query(Device).all()
        )


def test_export_remove_unused_products_round_trip(tmp_path: Path) -> None:
    src = tmp_path / "src.xknx"
    import_knxproj(_REAL, src)
    _inject_unused(src)
    with Session(make_engine(url_for(src))) as session:
        stored = session.query(Project).one().imported_product_members

    full = tmp_path / "full.knxproj"
    export_knxproj(src, full)
    assert "M-0003/Hardware.xml" in _archive(full)

    out = tmp_path / "pruned.knxproj"
    result = export_knxproj(src, out, remove_unused_products=True)
    assert not result.missing_references
    assert result.product_prune is not None
    assert result.product_prune.manufacturers == ["M-0003"]
    assert result.product_prune.application_programs == [
        "M-0002_A-FFFF-01-0000",
        "M-0003_A-0001",
    ]

    members = _archive(out)
    assert not any(n.startswith("M-0003") for n in members)
    assert "M-0002/M-0002_A-FFFF-01-0000.xml" not in members
    assert b"M-0002_H-UNUSED" not in members["M-0002/Hardware.xml"]
    source = _archive(_REAL)
    assert members["M-0002/Hardware.xml"] == source["M-0002/Hardware.xml"]
    files = {
        n.split("/", 1)[1]: d for n, d in members.items() if n.startswith("M-0002/")
    }
    assert verify_directory_signature(files, members["M-0002.signature"])

    # The project itself is not changed.
    with Session(make_engine(url_for(src))) as session:
        assert session.query(Project).one().imported_product_members == stored

    # Re-import with the project's own importer: same devices.
    back = tmp_path / "back.xknx"
    import_knxproj(out, back)
    assert _device_refs(back) == _device_refs(src)


def test_compact_project_shrinks_file(tmp_path: Path) -> None:
    path = tmp_path / "p.xknx"
    import_knxproj(_REAL, path)
    _inject_unused(path, padding=2_000_000)
    before = path.stat().st_size

    svc = ProjectService()
    pid = svc.open(path)
    steps: list[str] = []
    analysis = svc.analyse_compaction(pid, lambda s, d, t: steps.append(s))
    assert not analysis.applied
    assert analysis.report.manufacturers == ["M-0003"]
    assert analysis.file_size_before == before
    assert analysis.file_size_after < before - 1_500_000
    assert "devices" in steps and "manufacturer:M-0002" in steps

    with pytest.raises(CompactCancelled):
        svc.compact(pid, cancelled=lambda: True)
    assert path.stat().st_size == before

    steps.clear()
    result = svc.compact(pid, lambda s, d, t: steps.append(s))
    assert result.applied
    assert result.file_size_before == before
    assert result.file_size_after < before - 1_500_000
    assert steps[-1] == "vacuum" and "store" in steps
    assert path.stat().st_size == result.file_size_after

    blob = svc.project(pid).imported_product_members
    assert blob
    stored = _unzip(blob)
    assert not any(n.startswith("M-0003") for n in stored)
    assert "M-0002/M-0002_A-FFFF-01-0000.xml" not in stored
    # Nothing left to remove now.
    again = svc.analyse_compaction(pid)
    assert again.report.nothing_removed
    svc.close(pid)
    assert _device_refs(path)


def test_compact_project_without_product_data(tmp_path: Path) -> None:
    path = tmp_path / "new.xknx"
    svc = ProjectService()
    pid = svc.create(path, "P-0001")
    result = svc.compact(pid)
    assert result.applied
    assert result.report.nothing_removed
    assert abs(result.file_size_after - result.file_size_before) < 65536
    assert svc.devices(pid) == []
    svc.close(pid)
