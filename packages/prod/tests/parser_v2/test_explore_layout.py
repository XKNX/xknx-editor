"""What decides where an explore pass goes: the values of the repeat counts, not those of the
chooses (an explore pass takes every branch); and a pass that is stopped on the way."""

from __future__ import annotations

from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

APP = "M-00FA_A-0003-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "activation_application.xml"
).read_bytes()
# A choose's setting, and the count of the repeat of the module instances.
SLOT = f"{APP}_P-1_R-1"
COUNT = f"{APP}_P-11_R-11"


def _ui(values: dict[str, str]) -> DynamicUI:
    ui = DynamicUI(
        parse_application_xml(XML, "M-00FA")[0].program, parameter_values=values
    )
    ui.ui()
    return ui


def _places(ui: DynamicUI) -> list[tuple[str, tuple[object, ...]]]:
    return [(p.parameter.ref_id, p.gates) for p in ui.explore_parameters()]


def test_the_value_of_a_choose_does_not_change_the_explore_layout() -> None:
    first, second = _ui({SLOT: "0"}), _ui({SLOT: "1"})
    assert first.get_value(SLOT) != second.get_value(SLOT)
    assert first.explore_layout() == second.explore_layout()
    assert _places(first) == _places(second)


def test_the_count_of_a_repeat_changes_the_explore_layout() -> None:
    one, two = _ui({COUNT: "1"}), _ui({COUNT: "2"})
    assert one.explore_layout() != two.explore_layout()
    # The second pass places the module's settings a second time.
    assert len(_places(two)) > len(_places(one))


def test_a_check_that_raises_ends_the_explore_pass() -> None:
    ui = _ui({COUNT: "2"})
    calls: list[int] = []

    def stop() -> None:
        calls.append(1)
        raise RuntimeError("stop")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("xknxeditor.prod.parser_v2.context.Explorer.CHECK_EVERY", 1)
        with pytest.raises(RuntimeError, match="stop"):
            ui.explore_parameters(stop)
    assert calls == [1]
    # Without a check the pass is not stopped.
    assert _places(ui)
