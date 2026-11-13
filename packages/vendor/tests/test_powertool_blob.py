"""Original saved projects, native conversion and malformed input boundaries."""

import hashlib
import json
import struct

import pytest

from packages.vendor.tests.powertool_fixtures import archive_cases, open_archive
from xknxeditor.vendor.powertool import Association, HostProfile, compile_image
from xknxeditor.vendor.powertool.nrbf import Record, read_document
from xknxeditor.vendor.powertool.project_blob import (
    config_from_project_blob,
    read_project_blob,
)

CASES = archive_cases("original-projects.zip")
HOST_CASES = archive_cases("host-projects.zip")


@pytest.mark.parametrize("case", HOST_CASES, ids=lambda c: c["file"])
def test_host_exported_project(case):
    with open_archive("host-projects.zip") as archive:
        data = archive.read(case["file"])
    assert hashlib.sha256(data).hexdigest() == case["sha256"]
    project = read_project_blob(data)
    assert project.metadata_verified
    assert project.channels
    if case["file"] in ("DI-1.blob", "DI-2.blob"):
        assert config_from_project_blob(data).variant == "02"


def test_host_rtc_pages_and_internal_values_keep_raw_values():
    with open_archive("host-projects.zip") as archive:
        project = read_project_blob(archive.read("DI-12.blob"))
    rtc = next(c for c in project.channels if c.label == "RTC settings")
    counter, size, first_object = rtc.parameters[:3]
    assert (counter.value, size.value, first_object.value) == (47, 104, 257)
    assert all(p.internal for p in (counter, size, first_object))
    assert first_object.label == "Par_DataPoint_RIO_ACTUATING_VALUE_HEATING"
    assert all(
        p.group for p in rtc.parameters if p.value is not None and not p.internal
    )
    assert {p.group for p in rtc.parameters} >= {
        "General",
        "Operating functions",
        "Control heating",
    }
    value = next(
        p
        for p in rtc.parameters
        if p.label == "Operating mode after reset" and not p.internal
    )
    assert value.value == value.display_value == 3
    assert value.default_value == 0
    assert value.options[3]


def test_unknown_metadata_does_not_classify_internal_or_invent_defaults():
    data = blob().replace(
        b"ToolBObjektTasterDatabase.dll", b"ToolBXbjektTasterDatabase.dll"
    )
    project = read_project_blob(data)
    for channel in project.channels:
        for parameter in channel.parameters:
            assert not parameter.internal
            assert not parameter.group
            assert parameter.display_value == parameter.value
            assert parameter.default_value is None


def blob(case: str = "matrix/01-00001-default") -> bytes:
    with open_archive("original-projects.zip") as archive:
        return archive.read(case + "/project.blob")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case"])
def test_original_saved_project(case):
    data = blob(case["case"])
    assert hashlib.sha256(data).hexdigest() == case["sha256"]
    project = read_project_blob(data)
    assert project.metadata_verified
    assert 0 < project.serialized_length <= len(data)
    assert not any(data[project.serialized_length :])
    with open_archive("original-projects.zip") as archive:
        if case["case"].startswith("generic/"):
            capture = json.loads(archive.read(case["case"] + "/capture.json"))
            assert project.code == capture["code"]
            assert project.database == capture["database"] + ".dll"
            assert project.database_version == capture["version"]
            assert project.product_type == capture["productType"]
            return
        config = config_from_project_blob(data)
        spec = json.loads(archive.read(case["case"] + "/input.json"))
        roles = compile_image(config).objects
        links = [
            Association(
                next(
                    o.key
                    for o in roles
                    if o.channel == a["channel"] and o.position == a["position"]
                ),
                a["number"],
                a["send"],
            )
            for a in spec.get("associations", [])
        ]
        result = compile_image(
            config,
            HostProfile(short_associations=spec.get("short_associations", False)),
            links,
        )
        assert result.pid == archive.read(case["case"] + "/image.pid")


def test_unknown_database_preserves_raw_inspection_without_native_conversion():
    # Keep the string length encoded in the original stream.
    data = blob().replace(
        b"ToolBObjektTasterDatabase.dll", b"ToolBXbjektTasterDatabase.dll"
    )
    saved = read_project_blob(data)
    assert not saved.metadata_verified
    assert any(p.value is not None for c in saved.channels for p in c.parameters)
    assert all(c.native_function is None for c in saved.channels)
    with pytest.raises(ValueError, match="not verified"):
        config_from_project_blob(data)


def test_different_family_is_readable_but_has_no_native_conversion():
    data = blob("generic/ToolBSmartTouchDatabase/SmartTouch")
    saved = read_project_blob(data)
    assert len(saved.channels) > 100
    assert any(c.label == "System settings" for c in saved.channels)
    with pytest.raises(ValueError, match="not verified"):
        config_from_project_blob(data)


def test_original_modified_flags_are_readable_but_not_silently_reset():
    data = blob("modified-flags")
    saved = read_project_blob(data)
    assert saved.channels[0].object_flags[0] & 8
    with pytest.raises(ValueError, match="modified PowerTool object flags"):
        config_from_project_blob(data)


@pytest.mark.parametrize("cut", [0, 1, 16, 17, 128, 1000])
def test_truncated_blob(cut):
    with pytest.raises(ValueError):
        read_project_blob(blob()[:cut])


def test_stream_end_and_capacity_padding():
    data = blob()
    end = read_project_blob(data).serialized_length
    assert read_project_blob(data[:end]).product_type == "TasterBA1f"
    for corrupt in (data[: end - 1], data + b"x", data[:end] + b"\x01"):
        with pytest.raises(ValueError):
            read_project_blob(corrupt)


def _header(root=1):
    return b"\x00" + struct.pack("<iiii", root, -1, 1, 0)


@pytest.mark.parametrize(
    "data",
    [
        _header() + b"\x10" + struct.pack("<ii", 1, 2_000_000) + b"\x0b",
        _header() + b"\x10" + struct.pack("<ii", 1, -1) + b"\x0b",
        _header()
        + b"\x10"
        + struct.pack("<ii", 1, 1)
        + b"\x09"
        + struct.pack("<i", 999)
        + b"\x0b",
        _header()
        + b"\x06"
        + struct.pack("<i", 1)
        + b"\x01a"
        + b"\x06"
        + struct.pack("<i", 1)
        + b"\x01b\x0b",
        _header()
        + b"\x10"
        + struct.pack("<ii", 1, 1)
        + b"\x0e"
        + struct.pack("<i", 2)
        + b"\x0b",
        _header() + b"\x06" + struct.pack("<i", 1) + b"\xff\xff\xff\xff\xff\x0b",
    ],
)
def test_rejects_invalid_record_graph(data):
    with pytest.raises(ValueError):
        read_document(data)


def test_cycles_are_retained_as_data_without_recursive_expansion():
    data = (
        _header()
        + b"\x10"
        + struct.pack("<ii", 1, 1)
        + b"\x09"
        + struct.pack("<i", 1)
        + b"\x0b"
    )
    doc = read_document(data)
    assert doc.resolve(doc.root) == [doc.root]
    with pytest.raises(ValueError, match="Project"):
        read_project_blob(data)


def test_parser_never_imports_serialized_classes():
    original = b"BJE.toolB.PT2Logic.Project"
    data = blob().replace(original, b"untrusted.".ljust(len(original), b"x"))
    doc = read_document(data)
    assert isinstance(doc.resolve(doc.root), Record)
    with pytest.raises(ValueError, match="Project"):
        read_project_blob(data)
