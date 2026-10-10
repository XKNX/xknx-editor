"""A Choose on a parameter that is neither shown nor assigned selects no branch."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.nodes.choose import satisfies
from xknxeditor.prod.parser_v2.ui import UiNode, UiParameterBlock, UiSeparator, UiTab

APP = "M-00FA_A-0002-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "inactive_choose_application.xml"
).read_bytes()


def _texts(nodes: list[UiNode] | tuple[UiNode, ...]) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, UiSeparator) and node.text:
            out.add(node.text)
        elif isinstance(node, (UiTab, UiParameterBlock)):
            out |= _texts(node.children)
    return out


def _ui() -> DynamicUI:
    return DynamicUI(parse_application_xml(XML, "M-00FA")[0].program)


def test_choose_on_hidden_parameter_selects_nothing() -> None:
    texts = _texts(_ui().ui())
    assert "hidden-one" not in texts
    assert "hidden-default" not in texts


def test_choose_on_assigned_parameter_follows_the_assign() -> None:
    ui = _ui()
    assert not _texts(ui.ui()) & {"assigned-one", "assigned-default"}
    ui.set_parameter_ref(f"{APP}_P-1_R-1", "1")
    assert "assigned-one" in _texts(ui.ui())
    assert f"{APP}_P-3_R-3" in ui.active_parameter_ref_ids()


def test_choose_before_its_parameter_is_shown() -> None:
    assert "late-zero" in _texts(_ui().ui())


def test_choose_on_block_heading_parameter() -> None:
    assert "heading-default" in _texts(_ui().ui())


def test_numbers_match_regardless_of_leading_zeros() -> None:
    assert satisfies("01", "1")
    assert satisfies("0 01", "1")
    assert not satisfies("01", "2")
    assert satisfies("abc", "abc")
