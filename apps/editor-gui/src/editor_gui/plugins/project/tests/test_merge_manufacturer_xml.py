"""A manufacturer whose products come from several .knxprod files has a Hardware.xml (Catalog.xml)
in each; the export bundles one merged file, so every device's hardware entry is in the archive."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from editor_gui.plugins.project.knxproj_manufacturer import collect_manufacturer_bundle
from xknxeditor.proj.core.manufacturer_xml import merge_manufacturer_xml


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
