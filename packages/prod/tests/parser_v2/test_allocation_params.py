"""The values that can change which allocating module instances a tree makes: a script write
of any other value cannot make an allocator overflow, so it is not settled."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.namespaces.intermediate import ApplicationProgram
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

APP = "M-00FA_A-0004-01-0000"
VARIANT = f"{APP}_P-1_R-1"
EXTRA = f"{APP}_MD-1_P-1_R-1"
XML = (Path(__file__).parents[1] / "fixtures" / "allocator_application.xml").read_text(
    encoding="utf-8"
)


def _program(xml: str) -> ApplicationProgram:
    return parse_application_xml(xml.encode(), "M-00FA")[0].program


def _allocation(xml: str) -> frozenset[str]:
    return DynamicTreeBuilder(_program(xml)).allocation_param_refs


def test_the_selector_of_allocating_modules() -> None:
    # The variant chooses between two allocating modules; a value inside a module chooses nothing.
    assert _allocation(XML) == {VARIANT}


def _with_parameters(xml: str, *names: str) -> str:
    """The fixture with more application parameters of the variant's type."""
    params = "".join(
        f'<Parameter Id="{APP}_P-{n}" Name="P{n}" ParameterType="{APP}_PT-YN" Text="P{n}" '
        f'Value="0" />'
        for n in names
    )
    refs = "".join(
        f'<ParameterRef Id="{APP}_P-{n}_R-{n}" RefId="{APP}_P-{n}" />' for n in names
    )
    xml = xml.replace("</Parameters>", params + "</Parameters>", 1)
    return xml.replace("</ParameterRefs>", refs + "</ParameterRefs>", 1)


def test_what_makes_the_selector_count() -> None:
    # A choose renders only while its parameter is active: the selector the variant is shown
    # under counts too, and so does the source of an assign to the variant; a plain value does not.
    xml = _with_parameters(XML, "2", "3", "4")
    placement = f'<ParameterRefRef RefId="{VARIANT}" />'
    xml = xml.replace(
        placement,
        f'<choose ParamRefId="{APP}_P-2_R-2"><when test="1">{placement}</when></choose>'
        f'<Assign TargetParamRefRef="{VARIANT}" SourceParamRefRef="{APP}_P-3_R-3" />'
        f'<ParameterRefRef RefId="{APP}_P-4_R-4" />',
        1,
    )
    assert _allocation(xml) == {VARIANT, f"{APP}_P-2_R-2", f"{APP}_P-3_R-3"}


def test_no_allocating_module() -> None:
    xml = XML.replace(f' AllocatorRefId="{APP}_L-2"', "").replace(
        f' AllocatorRefId="{APP}_L-1"', ""
    )
    assert _allocation(xml) == frozenset()


@pytest.mark.parametrize(
    ("ref", "settles"), [(VARIANT, True), (f"{APP}_MD-1_M-1_MI-1_P-1_R-1", False)]
)
def test_a_script_write_is_settled_for_a_selector_only(
    monkeypatch: pytest.MonkeyPatch, ref: str, settles: bool
) -> None:
    ui = DynamicUI(_program(XML))
    ui.ui()
    build = DynamicUI._build_ui  # pyright: ignore[reportPrivateUsage]
    settled: list[bool] = []

    def counting(self: DynamicUI, *, capture_active: bool) -> None:
        settled.append(capture_active)
        build(self, capture_active=capture_active)

    monkeypatch.setattr(DynamicUI, "_build_ui", counting)
    ui.write_parameter(ref, "1")
    assert settled == ([True] if settles else [])
    assert ui.get_value(ref) == "1"
