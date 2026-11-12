"""What the calculations compute from the values read is found without evaluating the UI again
after they ran, when they wrote no selector (the same parameters are active), or when none of
the values not active is a setting a calculation computes from (none shown runs one again);
the result is the same as with that evaluation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from test_calculated_switches import _CODE, _DELAY, _FUNCTION, _SWITCH
from test_calculated_switches import _XML as _SWITCHES_XML

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_FIXTURES = Path(__file__).parents[2] / "prod" / "tests" / "fixtures"
_APP = "M-00FA_A-0001-01-0000"


def _ref(n: int) -> str:
    return f"{_APP}_P-{n}_R-{n}"


@pytest.fixture(scope="module")
def flat() -> Application:
    """Calculations between settings all shown at once: none of them is a selector."""
    xml = (_FIXTURES / "calculations_application.xml").read_bytes()
    return parse_application_xml(xml, "M-00FA")[0]


@pytest.fixture(scope="module")
def switches() -> Application:
    """A calculation that computes a channel switch (a selector) from a stored setting."""
    return parse_application_xml(_SWITCHES_XML.encode(), "M-00FA")[0]


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """The evaluations of UIs from now on."""
    count = [0]
    build = DynamicUI._build_ui  # pyright: ignore[reportPrivateUsage]

    def counted(self: DynamicUI, *, capture_active: bool) -> None:
        count[0] += 1
        build(self, capture_active=capture_active)

    monkeypatch.setattr(DynamicUI, "_build_ui", counted)
    yield count


def _always_evaluated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never leave out the evaluation after the calculations."""
    monkeypatch.setattr(parameters, "_shows_no_more", lambda *_args: False)


def test_calculations_that_write_no_selector_need_no_second_evaluation(
    flat: Application, builds: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    values = {_ref(2): "8", _ref(4): "9"}
    computed = parameters._calculate(  # pyright: ignore[reportPrivateUsage]
        flat, values, None, None
    )
    left_out = builds[0]
    assert computed[_ref(2)] == "8"
    _always_evaluated(monkeypatch)
    builds[0] = 0
    assert (
        parameters._calculate(  # pyright: ignore[reportPrivateUsage]
            flat, values, None, None
        )
        == computed
    )
    assert builds[0] == left_out + 1


@pytest.mark.parametrize(
    "values",
    [
        {_CODE: "2"},
        {_CODE: "2", _DELAY: "7"},
        {_CODE: "0", _FUNCTION: "1"},
        {_SWITCH: "1", _CODE: "2"},
    ],
)
def test_the_result_is_the_one_the_evaluation_gives(
    switches: Application,
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
) -> None:
    computed = parameters._calculate(  # pyright: ignore[reportPrivateUsage]
        switches, values, None, None
    )
    _always_evaluated(monkeypatch)
    assert (
        parameters._calculate(  # pyright: ignore[reportPrivateUsage]
            switches, values, None, None
        )
        == computed
    )


def test_the_kinds_of_refs(flat: Application, switches: Application) -> None:
    flat_ui, switches_ui = flat.dynamic_ui(), switches.dynamic_ui()
    assert flat_ui is not None and switches_ui is not None
    kinds = parameters._ref_kinds  # pyright: ignore[reportPrivateUsage]
    # No choose in the first: nothing written can change what is active.
    assert not kinds(flat_ui).selector(_ref(1))
    assert kinds(flat_ui).computed_from(_ref(2))
    assert not kinds(flat_ui).computed_from(_ref(5))
    assert kinds(flat_ui).computing(_ref(5))
    assert not kinds(flat_ui).computing(_ref(7))
    assert kinds(switches_ui).selector(_SWITCH)
    shows_no_more = parameters._shows_no_more  # pyright: ignore[reportPrivateUsage]
    assert shows_no_more(flat_ui, [_ref(1), _ref(3)], [_ref(2)])
    assert not shows_no_more(switches_ui, [_SWITCH], [_CODE, _DELAY])
    assert shows_no_more(switches_ui, [_SWITCH], [])


def test_a_tail_is_what_an_instance_keeps_of_a_ref() -> None:
    tail = parameters._ref_tail  # pyright: ignore[reportPrivateUsage]
    assert tail(f"{_APP}_P-5_R-5") == "_P-5_R-5"
    assert tail(f"{_APP}_MD-1_M-2_MI-3_P-5_R-5") == "_P-5_R-5"
    assert tail(f"{_APP}_MD-1_P-5_R-5") == "_P-5_R-5"
    assert tail(f"{_APP}_MD-1_M-2_MI-3_UP-4_R-9") == "_UP-4_R-9"
    assert tail("other") == "other"
