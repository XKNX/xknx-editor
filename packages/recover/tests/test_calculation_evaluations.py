"""What the calculations compute from the values read is found without evaluating the UI again
after they ran, when they wrote no selector (the same parameters are active), when none of the
values not active is a setting a calculation computes from (none shown runs one again), or when
no evaluation makes one of those active (settings of a module definition no instance names);
the result is the same as with that evaluation."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_calculated_switches import _CODE, _DELAY, _ENABLE, _FUNCTION, _SWITCH
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


def test_the_kinds_asked_again_are_the_same(switches: Application) -> None:
    ui = switches.dynamic_ui()
    assert ui is not None
    kinds = parameters._ref_kinds(ui)  # pyright: ignore[reportPrivateUsage]
    tail = parameters._ref_tail  # pyright: ignore[reportPrivateUsage]
    refs = [*ui.indexer.parameter_refs, f"{_APP}_MD-1_M-2_MI-3_P-5_R-5", "other"]
    for _ in range(2):
        for ref in refs:
            assert kinds.computing(ref) == (tail(ref) in kinds.left)


@pytest.mark.parametrize("fixture", ["flat", "switches"])
def test_the_settings_with_a_copy_come_first(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    application: Application = request.getfixturevalue(fixture)
    ui = application.dynamic_ui()
    assert ui is not None
    ui.ui()
    indexer = ui.indexer
    keeps_no_copy = parameters._keeps_no_copy  # pyright: ignore[reportPrivateUsage]

    def no_copy(ref_id: str) -> bool:
        ref = indexer.parameter_refs.get(ui.local_ref_id(ref_id))
        return ref is not None and keeps_no_copy(indexer.parameters.get(ref.ref_id))

    refs = [*reversed(indexer.parameter_refs), "other", _ref(1)]
    expected = sorted(refs, key=no_copy)
    assert any(no_copy(ref) for ref in refs) or fixture == "flat"
    copies_first = parameters._copies_first  # pyright: ignore[reportPrivateUsage]
    # Asked again, with what was found of each kept.
    assert copies_first(ui, refs) == expected
    assert copies_first(ui, refs) == expected


def test_settings_never_active_need_no_second_evaluation(
    switches: Application, builds: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The enable setting computes the switch (a selector); the value of a module definition's
    # setting with the code's tail is one a calculation could compute from, but no instance
    # names it.
    values = {_ENABLE: "1", f"{_APP}_MD-9_P-4_R-4": "3"}
    ui = switches.dynamic_ui()
    assert ui is not None
    shows_no_more = parameters._shows_no_more  # pyright: ignore[reportPrivateUsage]
    assert shows_no_more(ui, [_SWITCH], [f"{_APP}_MD-9_P-4_R-4"])
    assert not shows_no_more(ui, [_SWITCH], [_CODE])
    computed = parameters._calculate(  # pyright: ignore[reportPrivateUsage]
        switches, values, None, None
    )
    left_out = builds[0]
    monkeypatch.setattr(DynamicUI, "never_active", lambda *_args: False)
    builds[0] = 0
    assert (
        parameters._calculate(  # pyright: ignore[reportPrivateUsage]
            switches, values, None, None
        )
        == computed
    )
    assert builds[0] == left_out + 1


def test_a_write_that_can_overflow_an_allocator_is_evaluated(
    switches: Application,
) -> None:
    ui = switches.dynamic_ui()
    assert ui is not None
    kinds = parameters._ref_kinds(ui)  # pyright: ignore[reportPrivateUsage]
    assert not kinds.allocation
    allocating = dataclasses.replace(kinds, allocation=frozenset({"_P-2_R-2"}))
    assert allocating.allocating(_SWITCH)
    assert not allocating.allocating(_CODE)
