"""Small examples exercise XML compatibility and dependency evaluation."""

import base64
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from xknxeditor.vendor.siemens_generic import (
    build_view,
    compare_views,
    parse_description,
    parse_settings,
)
from xknxeditor.vendor.siemens_generic.blob import decode_text
from xknxeditor.vendor.siemens_generic.codec import encode
from xknxeditor.vendor.siemens_generic.configuration import (
    default_config,
    edit,
    evaluate,
    validate_value,
    write_values,
)
from xknxeditor.vendor.siemens_generic.view import MemoryCheck

DESCRIPTION = b"""<ApplicationProgram Id="id-DEMO" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:noNamespaceSchemaLocation="GenericPlugIn_4.xsd"><Static>
 <CodeSegments><CodeSegment Id="s" Address="4096"/></CodeSegments>
 <ParameterTypes>
 <ParameterType Id="enum"><TypeRestriction Size="8"><Enumeration Id="off" Name="Off" Value="0" Text="Off"/>
 <Enumeration Id="on" Name="On" Value="1" Text="On"/></TypeRestriction></ParameterType>
 <ParameterType Id="word"><TypeNumber Size="16" IntelOrderSwap="true" minInclusive="0" maxInclusive="1000"/></ParameterType>
 </ParameterTypes><Parameters>
 <Parameter Id="mode" RefId="enum" Value="0"><Memory Offset="10" CodeSegment="s"/></Parameter>
 <Parameter Id="time" RefId="word" Value="300"><Memory Offset="12" CodeSegment="s"/></Parameter>
 <Parameter Id="derived" RefId="word" Value="0"><Memory Offset="14" CodeSegment="s"/></Parameter>
 </Parameters><ParameterRefs>
 <ParameterRef Id="a" RefId="mode" Value="1"/>
 <ParameterRef Id="b" RefId="mode" Value="0"/>
 <ParameterRef Id="t" RefId="time"/>
 <ParameterRef Id="d" RefId="derived"/>
 </ParameterRefs></Static><Dynamic>
 <choose ParamRefId="b" SearchForActiveReferenceValue="true"><when test="1">
 <Channel Id="optional"><ParameterBlock Id="optional"><ParameterRefRef RefId="t"/>
 <Assign TargetParamRefRef="d" SourceParamRefRef="t"/></ParameterBlock></Channel>
 </when></choose>
 <Channel Id="common"><ParameterBlock Id="common"><ParameterRefRef RefId="a"/></ParameterBlock></Channel>
 </Dynamic></ApplicationProgram>"""


def description(data=DESCRIPTION):
    result = parse_description(data)
    assert result is not None
    return result


@pytest.mark.parametrize("compact", [False, True])
def test_edit_preserves_dialect_and_download_record(compact):
    d = replace(description(), compact_blob=compact)
    original = default_config(d)
    root = ET.fromstring(decode_text(original))
    downloaded = root.find("DownloadedSettings")
    ET.SubElement(
        downloaded,
        "MemoryImage",
        Data=base64.b64encode(b"abcd").decode(),
        Mask="/////w==",
    )
    original = (
        '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")
    ).encode("utf-16-le")
    assert edit(d, original, "t", "300") == original
    changed = edit(d, original, "t", "513")
    assert decode_text(changed).startswith('<?xml version="1.0" encoding="UTF-8"?>')
    state = parse_settings(changed)
    assert state.compact is compact
    assert state.values["t"] == state.values["d"] == "513"
    assert state.memory_image == b"abcd"
    assert ET.tostring(
        ET.fromstring(decode_text(changed)).find("DownloadedSettings")
    ) == ET.tostring(downloaded)
    with pytest.raises(ValueError, match="not editable"):
        edit(d, original, "d", "20")
    with pytest.raises(ValueError, match="between"):
        edit(d, original, "t", "1001")


def test_numeric_enum_and_hex_values_have_named_choices_and_same_bytes():
    d = description()
    for value in ("1", "On", "0x01"):
        validate_value(d, "a", value)
        assert encode(value, d.types["enum"]) == b"\x01"
    blob = default_config(d)
    assert edit(d, blob, "a", "On") == blob
    settings = parse_settings(blob)
    aliases = replace(settings, values={**settings.values, "a": "On", "t": "0x12c"})
    assert (
        compare_views(build_view(d, settings), build_view(d, aliases)).differences == 0
    )
    assert encode("0x0201", d.types["word"]) == b"\x01\x02"
    with pytest.raises(ValueError, match="Invalid choice"):
        validate_value(d, "a", "2")
    assert (
        next(
            r.value_text
            for p in build_view(d, parse_settings(blob)).pages
            for r in p.rows
            if r.ref_id == "a"
        )
        == "On"
    )


def test_forward_active_reference_and_optional_channel_identity():
    d = description()
    settings = parse_settings(default_config(d))
    first = build_view(d, settings)
    unfitted = build_view(d, settings, show_unfitted=True)
    assert [p.id for p in first.pages] == ["optional", "common"]
    assert first.pages == unfitted.pages
    second = build_view(d, replace(settings, values={"a": "0"}))
    comparison = compare_views(first, second)
    assert comparison.differences == 2
    [common] = [p for p in comparison.pages if p.id == "common"]
    assert common.rows[0].first is not None and common.rows[0].second is not None


def test_masks_offsets_byte_order_and_all_values_changed():
    d = description()
    settings = parse_settings(default_config(d))
    settings = replace(
        settings,
        memory_image=bytes([0, 0, 0, 0, 0, 0]),
        memory_mask=b"\xff\x00\xff\xff\xff\xff",
    )
    view = build_view(d, settings)
    assert d.memory_base == 4106
    assert view.image_layout_known
    assert view.count(MemoryCheck.MISMATCH) == 2
    assert view.count(MemoryCheck.UNCHECKED) == 0
    masked = build_view(d, replace(settings, memory_mask=b"\x00" * 6))
    assert masked.count(MemoryCheck.UNCHECKED) == 2


def test_rejects_invalid_identity_values_and_unknown_dynamic():
    d = description()
    settings = parse_settings(default_config(d))
    for application in ("", "id-OTHER"):
        with pytest.raises(ValueError, match="another application"):
            evaluate(d, replace(settings, application_id=application))
    with pytest.raises(ValueError, match="Unknown stored"):
        evaluate(d, replace(settings, values={"missing": "1"}))
    with pytest.raises(ValueError, match="Invalid choice"):
        evaluate(d, replace(settings, values={"a": "2"}))
    d = description(DESCRIPTION.replace(b"</Dynamic>", b"<Script/></Dynamic>"))
    with pytest.raises(ValueError, match="Unsupported dynamic"):
        evaluate(d, settings)


def test_full_config_replace_discards_hidden_target_values():
    d = description()
    data = default_config(d)
    with_hidden = write_values(data, {"b": "1"})
    assert parse_settings(with_hidden).values["b"] == "1"
    restored = write_values(
        with_hidden, parse_settings(data).values, replace_current=True
    )
    assert parse_settings(restored).values == parse_settings(data).values


def test_union_memory_uses_parent_segment_and_offset():
    d = description(
        DESCRIPTION.replace(
            b'<Parameter Id="time" RefId="word" Value="300"><Memory Offset="12" CodeSegment="s"/></Parameter>',
            b'<Union Offset="20" CodeSegment="s"><Parameter Id="time" RefId="word" Value="300"/></Union>',
        )
    )
    assert d.parameters["time"].offset == 4116
    assert d.memory_base == 4106
    settings = replace(
        parse_settings(default_config(d)),
        memory_image=b"\x01" + b"\x00" * 9 + b"\x2c\x01",
        memory_mask=b"\xff" * 12,
    )
    view = build_view(d, settings)
    assert view.count(MemoryCheck.MATCH) == 2


@pytest.mark.parametrize(
    "refs",
    [
        '<P R="a"><V>1</V></P><P R="a"/>',
        '<P R="a"><V>1</V></P><ParameterInstanceRef RefId="b"><Value>0</Value></ParameterInstanceRef>',
    ],
)
def test_ambiguous_blob_records_are_rejected(refs):
    xml = f'<ApplicationProgramInstance><CurrentSettings Name="id-DEMO"><ParameterInstanceRefs>{refs}</ParameterInstanceRefs></CurrentSettings></ApplicationProgramInstance>'
    assert parse_settings(xml.encode()) is None


def test_complete_memory_defaults_assignments_and_holes():
    from xknxeditor.vendor.siemens_generic.image import compile_image

    d = replace(description(), allocation_size=256)
    data = default_config(d)
    compiled = compile_image(d, parse_settings(data))
    assert compiled.base == 4106
    assert len(compiled.data) == len(compiled.mask) == 65535
    assert compiled.data[:6] == b"\x01\xab\x2c\x01\x2c\x01"
    assert compiled.mask[:7] == b"\xff\0\xff\xff\xff\xff\0"
    assert compiled.used_length == 6
    changed = edit(d, data, "t", "513")
    assert compile_image(d, parse_settings(changed)).data[2:6] == b"\x01\x02\x01\x02"
    with pytest.raises(ValueError, match="exceeds"):
        compile_image(replace(d, allocation_size=4), parse_settings(data))
    with pytest.raises(ValueError, match="Unsupported memory layout"):
        compile_image(replace(d, image_unsupported=("Property",)), parse_settings(data))


def test_complete_original_image_captures():
    import json
    from pathlib import Path

    from xknxeditor.vendor.siemens_generic.image import compile_image

    root = next(p for p in Path(__file__).parents if (p / "packages/vendor").is_dir())
    oracle = root / ".references/siemens-generic/oracle"
    blobs = sorted((oracle / "blobs").glob("*/*.blob"))
    if not blobs:
        pytest.skip("Optional Siemens original captures absent")
    assert len(blobs) >= 21
    for path in blobs:
        d = parse_description((path.parent / "description.xml").read_bytes())
        info = json.loads(
            (path.parent / (path.stem.split("-")[0] + ".json")).read_text()
        )
        compiled = compile_image(
            d,
            parse_settings(path.read_bytes()),
            {"DeviceOrderNumber": info["order_number"]},
        )
        captured = oracle / "images" / ("linux-" + path.stem)
        assert compiled.data == (captured / "memory.bin").read_bytes(), path.stem
        assert compiled.mask == (captured / "mask.bin").read_bytes(), path.stem
        assert compiled.base == int((captured / "base.txt").read_text())
        assert {o.id for o in compiled.state.objects} == {
            line.split("\t")[1]
            for line in (captured / "objects.tsv").read_text().splitlines()
        }
