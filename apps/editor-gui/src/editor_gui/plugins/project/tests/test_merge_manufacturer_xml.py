"""A manufacturer whose products come from several .knxprod files has a Hardware.xml (Catalog.xml)
in each; the export bundles one merged file, so every device's hardware entry is in the archive."""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.project.knxproj_manufacturer import (
    catalog_export_sources,
    collect_manufacturer_bundle,
)
from xknxeditor.proj import export_knxproj
from xknxeditor.proj.core.manufacturer_xml import DeviceProduct, merge_manufacturer_xml
from xknxeditor.proj.core.product_data import zip_members
from xknxeditor.proj.core.service import ProjectService


def _hardware(ns: str, *items: tuple[str, str]) -> bytes:
    """A Hardware.xml in schema ``ns`` with one Hardware element per (hardware id, program id)."""
    hardware = "".join(
        f'<Hardware Id="{hw}" Name="{hw}"><Hardware2Programs>'
        f'<Hardware2Program Id="{hw}_HP-{hp}" MediumTypes="MT-0"/>'
        f"</Hardware2Programs></Hardware>"
        for hw, hp in items
    )
    return (
        f'﻿<?xml version="1.0" encoding="utf-8"?>'
        f'<KNX xmlns="http://knx.org/xml/project/{ns}"><ManufacturerData>'
        f'<Manufacturer RefId="M-00FA"><Hardware>{hardware}</Hardware></Manufacturer>'
        f"</ManufacturerData></KNX>"
    ).encode()


def _program_ids(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    return sorted(
        el.get("Id", "") for el in root.iter() if el.tag.endswith("}Hardware2Program")
    )


def test_hardware_of_another_source_is_added() -> None:
    merged = merge_manufacturer_xml(
        _hardware("23", ("M-00FA_H-1", "A1")),
        _hardware("23", ("M-00FA_H-2", "B1"), ("M-00FA_H-1", "A1")),
    )
    assert _program_ids(merged) == ["M-00FA_H-1_HP-A1", "M-00FA_H-2_HP-B1"]


def test_a_new_program_of_known_hardware_is_added() -> None:
    merged = merge_manufacturer_xml(
        _hardware("23", ("M-00FA_H-1", "A1")), _hardware("23", ("M-00FA_H-1", "A2"))
    )
    assert _program_ids(merged) == ["M-00FA_H-1_HP-A1", "M-00FA_H-1_HP-A2"]


def test_an_older_schema_is_merged_in_the_base_namespace() -> None:
    merged = merge_manufacturer_xml(
        _hardware("23", ("M-00FA_H-1", "A1")),
        _hardware("20", ("M-00FA_H-0xA501", "A502")),
    )
    assert _program_ids(merged) == ["M-00FA_H-0xA501_HP-A502", "M-00FA_H-1_HP-A1"]
    assert b"project/20" not in merged


def test_nothing_new_keeps_the_original_bytes() -> None:
    base = _hardware("23", ("M-00FA_H-1", "A1"), ("M-00FA_H-2", "B1"))
    assert merge_manufacturer_xml(base, _hardware("20", ("M-00FA_H-2", "B1"))) is base


def test_the_bundle_merges_hardware_of_two_sources(tmp_path: Path) -> None:
    sources: dict[str, tuple[str, str]] = {}
    for name, ns, hw, hp in (
        ("all.knxprod", "23", "M-00FA_H-1", "A1"),
        ("interface.knxprod", "20", "M-00FA_H-0xA501", "A502"),
    ):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("M-00FA/Hardware.xml", _hardware(ns, (hw, hp)))
            archive.writestr(f"M-00FA/M-00FA_A-{hp}.xml", b"<KNX/>")
        sources[f"{hw}_HP-{hp}"] = (str(path), "M-00FA")
    catalog: Any = SimpleNamespace(get_program_source=sources.get)

    bundle = collect_manufacturer_bundle(sorted(sources), catalog)

    assert _program_ids(bundle.extra_files["M-00FA/Hardware.xml"]) == [
        "M-00FA_H-0xA501_HP-A502",
        "M-00FA_H-1_HP-A1",
    ]
    assert "M-00FA/M-00FA_A-A502.xml" in bundle.extra_files


def test_full_bundle_merges_duplicate_application_xml_and_reads_baggage_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = "M-00FA/M-00FA_A-A1.xml"
    baggage = "M-00FA/Baggages/logo.png"
    sources: dict[str, tuple[str, str]] = {}
    for ref in ("first", "second"):
        path = tmp_path / f"{ref}.knxprod"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(application, f'<KNX><Item Id="{ref}"/></KNX>')
            archive.writestr(baggage, ref.encode())
        sources[ref] = (str(path), "M-00FA")

    read = Mock(side_effect=zipfile.ZipFile.read)
    monkeypatch.setattr(zipfile.ZipFile, "read", lambda zf, name: read(zf, name))
    catalog = Mock(spec=CatalogService)
    catalog.get_program_source.side_effect = sources.get

    bundle = collect_manufacturer_bundle(sources, catalog)

    assert [
        item.get("Id") for item in ET.fromstring(bundle.extra_files[application])
    ] == [
        "first",
        "second",
    ]
    assert bundle.extra_files[baggage] == b"first"
    assert sum(call.args[1] == baggage for call in read.call_args_list) == 1


def test_catalog_export_includes_an_application_in_another_folder(
    tmp_path: Path,
) -> None:
    device = DeviceProduct("", "M-00FA_H-1_HP-A1", "M-0001_A-OTHER", imported=False)
    members = {
        "M-00FA/Hardware.xml": _hardware("23", ("M-00FA_H-1", "A1")),
        "M-0001/M-0001_A-OTHER.xml": b"<KNX/>",
    }
    master = b'<KNX xmlns="http://knx.org/xml/project/23"><MasterData Signature="AA=="/></KNX>'
    path = tmp_path / "product.knxprod"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("knx_master.xml", master)
        for name, data in members.items():
            archive.writestr(name, data)
    catalog = Mock(spec=CatalogService)
    catalog.get_program_source.return_value = (str(path), "M-00FA")
    log = Mock(spec=Logger)
    stage = Mock()

    assert catalog_export_sources(
        [device], [device], catalog=catalog, log=log, on_collected=stage
    ) == (members, master, True)
    stage.assert_called_once_with()
    log.info.assert_called_once()
    log.warning.assert_not_called()


def test_catalog_export_resolves_product_only_refs_by_the_program_lookup() -> None:
    device = DeviceProduct("M-00FA_H-1_P-1", "", "", imported=False)
    catalog = Mock(spec=CatalogService)
    catalog.get_program_source.return_value = None

    assert catalog_export_sources([device], [device], catalog=catalog) == (
        {},
        None,
        False,
    )
    catalog.get_program_source.assert_called_with(device.product)
    catalog.find_product.assert_not_called()


@pytest.mark.parametrize("malformed_master", [False, True])
def test_failed_desktop_bundle_leaves_provenance_schema_selection(
    tmp_path: Path, malformed_master: bool
) -> None:
    path = tmp_path / "bad.knxprod"
    if malformed_master:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("knx_master.xml", b"<malformed")
            archive.writestr("M-00FA/Hardware.xml", b"<KNX/>")
    catalog = Mock(spec=CatalogService)
    catalog.get_program_source.return_value = (str(path), "M-00FA")
    log = Mock(spec=Logger)
    stage = Mock()

    source = tmp_path / "project.xknx"
    service = ProjectService()
    pid = service.create(source)
    service.add_device(pid, None, "", hardware2program_ref_id="M-00FA_H-1_HP-A1")
    service.close(pid)
    owned = {"M-00FA/Hardware.xml": _hardware("23", ("M-00FA_H-1", "A1"))}
    with sqlite3.connect(source) as db:
        db.execute(
            "UPDATE projects SET imported_product_members = ?", (zip_members(owned),)
        )

    exported = tmp_path / "export.knxproj"
    result = export_knxproj(
        source,
        exported,
        schema="20",
        catalog_sources=partial(
            catalog_export_sources, catalog=catalog, log=log, on_collected=stage
        ),
    )

    assert result.schema == "23"
    stage.assert_called_once_with()
    if malformed_master:
        log.warning.assert_called_once()
    else:
        log.info.assert_called_once_with(
            "manufacturer bundle collected", manufacturers=0, files=0, skipped=1
        )
    with zipfile.ZipFile(exported) as archive:
        assert archive.read("M-00FA/Hardware.xml") == owned["M-00FA/Hardware.xml"]
