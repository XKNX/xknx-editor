"""A value a project stores under the unpadded spelling of a parameter ref the application writes
with leading zeros (``UP-99823_R-9982301`` for ``UP-0099823_R-009982301``, as in OpenKNX
applications) is the value of that parameter ref.

In the fixture three module switches (all on by default) each show a channel with a group
object; the device switched two of them off."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiNode, UiTab

APP = "M-00FA_A-0008-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "padded_ids_application.xml"
).read_bytes()
NET = f"{APP}_UP-0099811_R-009981101"
SHC = f"{APP}_UP-0099823_R-009982301"
PM = f"{APP}_UP-0099830_R-009983001"
TIME = f"{APP}_P-0023001_R-002300101"
STORED_NET = f"{APP}_UP-99811_R-9981101"
STORED_SHC = f"{APP}_UP-99823_R-9982301"
STORED_TIME = f"{APP}_P-23001_R-2300101"


def _ui(values: list[tuple[str, str]]) -> DynamicUI:
    program = parse_application_xml(XML, "M-00FA")[0].program
    return DynamicUI(
        program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=r, value=v) for r, v in values
        ],
    )


def _channels(nodes: list[UiNode] | tuple[UiNode, ...]) -> set[str]:
    return {n.text for n in nodes if isinstance(n, UiTab) and not n.independent}


def test_values_under_the_unpadded_ids_switch_the_modules_off() -> None:
    ui = _ui([(STORED_NET, "0"), (STORED_SHC, "0")])
    assert (ui.get_value(NET), ui.get_value(SHC), ui.get_value(PM)) == ("0", "0", "1")
    assert _channels(ui.ui()) == {"Presence"}
    assert ui.active_parameter_driven_com_object_ref_ids() == {f"{APP}_O-3_R-3"}


def test_without_stored_values_every_module_is_on() -> None:
    assert _channels(_ui([]).ui()) == {"Network", "Shutter Control", "Presence"}


def test_a_value_under_the_unpadded_id_of_a_plain_parameter_counts() -> None:
    ui = _ui([(STORED_TIME, "90")])
    assert ui.get_value(TIME) == "90"


@pytest.mark.parametrize("padded_first", [True, False])
def test_the_value_under_the_applications_spelling_wins(padded_first: bool) -> None:
    values = [(SHC, "1"), (STORED_SHC, "0")]
    ui = _ui(values if padded_first else values[::-1])
    assert ui.get_value(SHC) == "1"
    assert "Shutter Control" in _channels(ui.ui())


def test_an_edit_of_a_parameter_stored_unpadded_switches_its_module_on() -> None:
    ui = _ui([(STORED_NET, "0"), (STORED_SHC, "0")])
    ui.set_parameter_ref(SHC, "1")
    assert ui.get_value(SHC) == "1"
    assert _channels(ui.ui()) == {"Shutter Control", "Presence"}
