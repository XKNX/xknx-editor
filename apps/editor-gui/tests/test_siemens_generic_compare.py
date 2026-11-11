"""Comparing two devices, "Percent" scaling and settings of modules that are not fitted, in the
read-only Siemens Generic Plug-in reader.

The fixture is a small, made-up "Demo" description in the plug-in's XML dialect (a base unit with
up to two extension modules): no manufacturer product data or project data is used.
"""

from __future__ import annotations

import base64

from editor_gui.plugin_blob_readers.siemens_generic import (
    GenericView,
    MemoryCheck,
    Row,
    compare_views,
    find_sources,
    read_view,
)
from editor_gui.plugin_blob_readers.siemens_generic.description import (
    Parameter,
    ParameterType,
    TypeKind,
)
from editor_gui.plugin_blob_readers.siemens_generic.view import percent_text

_BASE = 0x5000

DESCRIPTION = f"""<?xml version="1.0" encoding="UTF-8"?>
<ApplicationProgram Id="id-DEMO_0002_01">
 <Static>
  <ParameterTypes>
   <ParameterType Id="t_yn"><TypeRestriction Size="8">
     <Enumeration Id="e_no" Name="dm_No" Text="No" Value="0"/>
     <Enumeration Id="e_yes" Name="dm_Yes" Text="Yes" Value="1"/>
   </TypeRestriction></ParameterType>
   <ParameterType Id="t_count"><TypeNumber Size="8" Type="unsigned"
     minInclusive="0" maxInclusive="2"/></ParameterType>
   <ParameterType Id="t_pct200"><TypeNumber Size="8" Type="unsigned"
     minInclusive="0" maxInclusive="200"/></ParameterType>
   <ParameterType Id="t_byte"><TypeNumber Size="8" Type="unsigned"/></ParameterType>
  </ParameterTypes>
  <Parameters>
   <Parameter Id="p_slaves" Name="DemoSlaveNum" RefId="t_count" Value="2"
     Text="Number of connected submodules"><Memory Offset="{_BASE}"/></Parameter>
   <Parameter Id="p_fb" Name="DemoFeedback" RefId="t_yn" Value="dm_No" Text="Feedback">
     <Memory Offset="{_BASE + 1}"/></Parameter>
   <Parameter Id="p_level" Name="DemoLevel" RefId="t_pct200" Value="100" Text="Level in %"
     Format="Percent"><Memory Offset="{_BASE + 2}"/></Parameter>
   <Parameter Id="p_hyst" Name="DemoHyst" RefId="t_byte" Value="13" Text="Hysteresis in %"
     Format="Percent"><Memory Offset="{_BASE + 3}"/></Parameter>
   <Parameter Id="p_m1" Name="DemoModule1Type" RefId="t_yn" Value="dm_No" Text="Module 1">
     <Memory Offset="{_BASE + 4}"/></Parameter>
   <Parameter Id="p_m2" Name="DemoModule2Type" RefId="t_yn" Value="dm_No" Text="Module 2">
     <Memory Offset="{_BASE + 5}"/></Parameter>
   <Parameter Id="p_mark" Name="DemoMark" RefId="t_yn" Value="dm_No" Text="Mark">
     <Memory Offset="{_BASE + 6}"/></Parameter>
  </Parameters>
  <ParameterRefs>
   <ParameterRef Id="r_slaves" RefId="p_slaves"/>
   <ParameterRef Id="r_fb" RefId="p_fb"/>
   <ParameterRef Id="r_level" RefId="p_level"/>
   <ParameterRef Id="r_hyst" RefId="p_hyst"/>
   <ParameterRef Id="r_m1" RefId="p_m1"/>
   <ParameterRef Id="r_m2" RefId="p_m2"/>
   <ParameterRef Id="r_mark" RefId="p_mark"/>
  </ParameterRefs>
 </Static>
 <Dynamic>
  <Channel Id="c_base" Number="1">
   <ParameterBlock Id="b_base" Text="Base unit">
    <ParameterRefRef RefId="r_fb"/>
    <ParameterRefRef RefId="r_level"/>
    <ParameterRefRef RefId="r_hyst"/>
    <ParameterRefRef RefId="r_slaves"/>
    <choose ParamRefId="r_slaves">
     <when test="1;2"><ParameterRefRef RefId="r_m1"/></when>
     <when test="2">
      <ParameterRefRef RefId="r_m2"/>
      <Assign TargetParamRefRef="r_mark" Value="dm_Yes"/>
     </when>
    </choose>
    <ParameterRefRef RefId="r_mark"/>
   </ParameterBlock>
  </Channel>
  <choose ParamRefId="r_slaves">
   <when test="2">
    <Channel Id="c_mod2" Number="1">
     <ParameterBlock Id="b_mod2" Text="Module 2 settings">
      <ParameterRefRef RefId="r_m2"/>
     </ParameterBlock>
    </Channel>
   </when>
  </choose>
 </Dynamic>
</ApplicationProgram>
""".encode()


def _blob(values: dict[str, str], image: bytes) -> bytes:
    refs = "".join(
        f'<ParameterInstanceRef RefId="{k}"><Value>{v}</Value></ParameterInstanceRef>'
        for k, v in values.items()
    )
    data = base64.b64encode(image).decode()
    return (
        "<ApplicationProgramInstance>"
        f'<CurrentSettings Name="id-DEMO_0002_01"><ParameterInstanceRefs>{refs}'
        "</ParameterInstanceRefs></CurrentSettings>"
        '<DownloadedSettings LastDownload="2025-01-02T03:04:05">'
        f'<MemoryImage Data="{data}"/></DownloadedSettings>'
        "</ApplicationProgramInstance>"
    ).encode("utf-16-le")


# One module fitted, feedback on, level 100 of 200, hysteresis 13 of 255; the image holds them.
_VALUES = {"r_slaves": "1", "r_fb": "dm_Yes", "r_level": "100", "r_m1": "dm_Yes"}
_IMAGE = bytes([1, 1, 100, 13, 1, 0, 0])


def _view(
    values: dict[str, str] | None = None,
    image: bytes = _IMAGE,
    show_unfitted: bool = False,
) -> GenericView:
    files = {"M-DEMO_BG-2-Demo.2Exml": DESCRIPTION}
    sources = find_sources(files, files.get)
    assert sources is not None
    view = read_view(
        sources,
        _blob(_VALUES if values is None else values, image),
        show_unfitted=show_unfitted,
    )
    assert view is not None
    return view


def _rows(view: GenericView) -> dict[str, Row]:
    return {r.ref_id: r for p in view.pages for r in p.rows if not r.separator}


# --- "Percent" ---------------------------------------------------------------------------------


def test_percent_settings_are_scaled_with_the_raw_value_kept() -> None:
    rows = _rows(_view())
    level, hyst = rows["r_level"], rows["r_hyst"]
    assert (level.value, level.value_text, level.scaled) == ("100", "50 %", True)
    # No maxInclusive: the full scale is the largest number the size holds (255).
    assert (hyst.value, hyst.value_text, hyst.scaled) == ("13", "5.1 %", True)
    # The raw value is what memory holds.
    assert level.memory is MemoryCheck.MATCH and hyst.memory is MemoryCheck.MATCH
    assert not rows["r_fb"].scaled and not rows["r_slaves"].scaled


def test_percent_mismatch_shows_the_held_value_scaled() -> None:
    image = bytearray(_IMAGE)
    image[2] = 50
    level = _rows(_view(image=bytes(image)))["r_level"]
    assert level.memory is MemoryCheck.MISMATCH
    assert (level.memory_value, level.memory_value_text) == ("50", "25 %")


def test_percent_text_only_for_percent_numbers() -> None:
    number = ParameterType(id="t", kind=TypeKind.NUMBER, size_bits=8, maximum=200)
    pct = Parameter(
        id="p", name="P", type_id="t", default="0", text="", offset=0, format="Percent"
    )
    plain = Parameter(id="q", name="Q", type_id="t", default="0", text="", offset=0)
    assert percent_text("200", number, pct) == "100 %"
    assert percent_text("1", number, pct) == "0.5 %"
    assert percent_text("x", number, pct) is None
    assert percent_text("50", number, plain) is None
    enum = ParameterType(id="e", kind=TypeKind.ENUM, size_bits=8)
    assert percent_text("1", enum, pct) is None
    wide = ParameterType(id="w", kind=TypeKind.NUMBER, size_bits=40)
    assert wide.full_scale is None and percent_text("1", wide, pct) is None
    assert ParameterType(id="b", kind=TypeKind.NUMBER, size_bits=16).full_scale == 65535


# --- modules not fitted --------------------------------------------------------------------------


def test_settings_of_modules_not_fitted_are_hidden_by_default() -> None:
    view = _view()
    rows = _rows(view)
    assert "r_m1" in rows and "r_m2" not in rows
    assert [p.title for p in view.pages] == ["Base unit"]
    assert view.unfitted_count == 0 and not view.show_unfitted


def test_settings_of_modules_not_fitted_shown_on_request() -> None:
    plain = _view()
    view = _view(show_unfitted=True)
    assert view.show_unfitted
    unfitted = [r for p in view.pages for r in p.rows if not r.fitted]
    # Module 2's type in the base block, and its own block.
    assert [r.ref_id for r in unfitted] == ["r_m2", "r_m2"]
    assert all(r.memory is MemoryCheck.UNCHECKED for r in unfitted)
    assert view.unfitted_count == 2
    titles = [(p.title, p.fitted) for p in view.pages]
    assert titles == [
        ("Base unit", True),
        ("Base unit", False),
        ("Module 2 settings", False),
    ]
    # The counts stay those of the fitted settings, and nothing in the module's branch is
    # assigned: the mark stays at its default.
    for check in MemoryCheck:
        assert view.count(check) == plain.count(check)
    assert _rows(view)["r_mark"].value == "dm_No"
    assert _rows(view)["r_mark"].fitted


def test_fitted_modules_are_not_shown_twice() -> None:
    values = dict(_VALUES, r_slaves="2", r_m2="dm_Yes")
    image = bytes([2, 1, 100, 13, 1, 1, 1])
    view = _view(values, image, show_unfitted=True)
    assert view.unfitted_count == 0
    assert all(p.fitted for p in view.pages)
    assert _rows(view)["r_mark"].value == "dm_Yes"


# --- comparing two devices -----------------------------------------------------------------------


def test_compare_finds_exactly_the_differing_settings() -> None:
    first = _view()
    second = _view(dict(_VALUES, r_fb="dm_No"), bytes([1, 0, 100, 13, 1, 0, 0]))
    comparison = compare_views(first, second)
    assert comparison.same_application
    assert comparison.differences == 1
    [diff] = [r for p in comparison.pages for r in p.rows if r.differs]
    assert diff.row.ref_id == "r_fb"
    assert diff.first is not None and diff.second is not None
    assert (diff.first.value, diff.second.value) == ("dm_Yes", "dm_No")
    assert compare_views(first, _view()).differences == 0


def test_compare_settings_only_one_device_shows() -> None:
    first = _view()
    two = dict(_VALUES, r_slaves="2", r_m2="dm_Yes")
    second = _view(two, bytes([2, 1, 100, 13, 1, 1, 1]))
    comparison = compare_views(first, second)
    by_ref = {(p.title, r.row.ref_id): r for p in comparison.pages for r in p.rows}
    only_second = by_ref[("Base unit", "r_m2")]
    assert only_second.first is None and only_second.differs
    assert by_ref[("Module 2 settings", "r_m2")].first is None
    assert by_ref[("Base unit", "r_mark")].differs  # assigned by module 2's branch
    assert by_ref[("Base unit", "r_slaves")].differs
    # The order follows the first device, then what only the second shows.
    assert [p.title for p in comparison.pages] == ["Base unit", "Module 2 settings"]


def test_compare_other_application() -> None:
    first = _view()
    second = _view()
    second.application_id = "id-OTHER"
    assert not compare_views(first, second).same_application
