"""A round of the memory inference takes the values of other module instances that do as
well as its best value together with it, where they fit together as they do alone. Where
they do not, the round takes its best value alone, and the next rounds take the others one
by one."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from test_memory_values_tried_again import _SEGMENTS, _XML, _high, _kind, _low

import xknxeditor.recover.parameters as parameters
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_Rounds = tuple[dict[str, str], list[str], dict[str, str]]


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _rounds(application: Application) -> _Rounds:
    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, {}, fixed={}
    )
    return parameters._infer_rounds(  # pyright: ignore[reportPrivateUsage]
        application,
        _SEGMENTS,
        None,
        None,
        set(),
        {},
        {},
        set(),
        None,
        values,
        unknown,
        {},
    )


def _first_round(application: Application) -> Mapping[str, str]:
    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, {}, fixed={}
    )
    return parameters._infer_from_memory(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, values, unknown, set(), {}, None, {}
    )


def _picks(monkeypatch: pytest.MonkeyPatch) -> list[Mapping[str, str]]:
    """What each round of the memory inference takes from now on."""
    picks: list[Mapping[str, str]] = []
    infer = parameters._infer_from_memory  # pyright: ignore[reportPrivateUsage]

    def recorded(*args: object, **kw: object) -> Mapping[str, str]:
        chosen = infer(*args, **kw)  # pyright: ignore[reportArgumentType]
        if chosen:
            picks.append(chosen)
        return chosen

    monkeypatch.setattr(parameters, "_infer_from_memory", recorded)
    return picks


def _one_by_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """From now on a round takes its best value alone."""

    def best_alone(*args: object, **_kw: object) -> object:
        return args[6]

    monkeypatch.setattr(parameters, "_with_alike_values", best_alone)


def _only_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """From now on the first two channels' high kinds fit alone but not together: a union
    member has no value of its type then. Every value is evaluated anew (a value tried in a
    round before is taken again with the union fit of a decode, which this one does not
    change)."""
    fit: Callable[..., tuple[int, list[str]]] = parameters._union_fit  # pyright: ignore[reportPrivateUsage]
    infer = parameters._infer_from_memory  # pyright: ignore[reportPrivateUsage]

    def anew(*args: object, **kw: object) -> object:
        kw.pop("evaluated", None)
        return infer(*args[:9], **kw)  # pyright: ignore[reportArgumentType, reportCallIssue]

    def paired(
        ui: DynamicUI, decoded: Mapping[str, str | None]
    ) -> tuple[int, list[str]]:
        fitting, misfits = fit(ui, decoded)
        if ui.get_value(_kind(1)) == "1" and ui.get_value(_kind(2)) == "1":
            misfits = [*misfits, "pair"]
        return fitting, misfits

    monkeypatch.setattr(parameters, "_union_fit", paired)
    monkeypatch.setattr(parameters, "_infer_from_memory", anew)


def test_a_round_takes_the_same_setting_of_every_channel_doing_as_well(
    application: Application,
) -> None:
    # Each of the first three channels holds a value of its high member: the kind of each
    # one does as well, and all three fit together.
    assert _first_round(application) == {_kind(1): "1", _kind(2): "1", _kind(3): "1"}


def test_the_rounds_end_as_one_by_one_in_one_round(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    picks = _picks(monkeypatch)
    together = _rounds(application)
    assert picks == [{_kind(1): "1", _kind(2): "1", _kind(3): "1"}]

    picks.clear()
    _one_by_one(monkeypatch)
    assert _rounds(application) == together
    assert picks == [{_kind(1): "1"}, {_kind(2): "1"}, {_kind(3): "1"}]

    values, _unknown, inferred = together
    assert inferred == {_kind(1): "1", _kind(2): "1", _kind(3): "1"}
    assert [values[_high(n)] for n in (1, 2, 3)] == ["42"] * 3
    assert values[_low(4)] == "5"


def test_values_fitting_only_alone_are_taken_one_by_one(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    _only_alone(monkeypatch)
    # Together the three kinds leave a member with no value of its type, where each one
    # alone leaves none of its own: the round takes the best one alone.
    assert _first_round(application) == {_kind(1): "1"}

    picks = _picks(monkeypatch)
    fallen_back = _rounds(application)
    # The second round takes the third channel's kind (the second's does no better with
    # the first's), the third round nothing.
    assert picks == [{_kind(1): "1"}, {_kind(3): "1"}]
    assert fallen_back[2] == {_kind(1): "1", _kind(3): "1"}

    picks.clear()
    _one_by_one(monkeypatch)
    assert _rounds(application) == fallen_back
    assert picks == [{_kind(1): "1"}, {_kind(3): "1"}]


def test_values_hiding_a_linked_object_together_are_taken_one_by_one(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: Callable[..., set[int]] = parameters._shown_objects  # pyright: ignore[reportPrivateUsage]

    def hidden_together(
        ui: DynamicUI, sizes: Mapping[int, str] | None = None
    ) -> set[int]:
        # Object 7 is shown unless the first three channels all have the high kind.
        objects = shown(ui, sizes) | {7}
        if all(ui.get_value(_kind(n)) == "1" for n in (1, 2, 3)):
            objects.discard(7)
        return objects

    monkeypatch.setattr(parameters, "_shown_objects", hidden_together)
    values, unknown = parameters._decode(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, {}, fixed={}
    )
    chosen = parameters._infer_from_memory(  # pyright: ignore[reportPrivateUsage]
        application, _SEGMENTS, None, None, values, unknown, {7}, {}, None, {}
    )
    assert chosen == {_kind(1): "1"}
