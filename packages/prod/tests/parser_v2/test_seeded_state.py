"""Seeding a parameter state with values: each value goes to the first module instance its ref
id names (also among many instances), values given by ref id seed as instance refs do, and the
layout of a state tells two seedings apart."""

from __future__ import annotations

import random

import pytest

from xknxeditor.namespaces.intermediate import ModuleInstance, ParameterInstanceRef
from xknxeditor.prod.parser_v2.state import (
    GlobalState,
    _InstanceIds,  # pyright: ignore[reportPrivateUsage]
)

APP = "M-00FA_A-0001-01-0000"


def _instance(n: int) -> str:
    return f"{APP}_MD-1_M-{n}_MI-1"


def _scopes(state: GlobalState) -> dict[str, dict[str, str]]:
    """The values of each scope, the application's under ''."""
    found = {"": dict(state.param_ref_id_to_value)}
    for child in state.module_children():
        found[child.module_instance_id] = dict(child.param_ref_id_to_value)
    return found


@pytest.mark.parametrize("count", [3, 12])
def test_a_value_goes_to_the_instance_its_ref_id_names(count: int) -> None:
    # MI-1 of M-1 is not MI-1 of M-10: the id has to end at an underscore.
    instances = [
        ModuleInstance(id=_instance(n), ref_id=f"{APP}_MD-1")
        for n in range(1, count + 1)
    ]
    values = {f"{_instance(n)}_P-1_R-1": str(n) for n in range(1, count + 1)}
    values[f"{APP}_P-9_R-9"] = "top"
    state = GlobalState.from_project(
        [ParameterInstanceRef(ref_id=k, value=v) for k, v in values.items()],
        instances,
        app_id=APP,
    )
    scopes = _scopes(state)
    assert scopes[""] == {f"{APP}_P-9_R-9": "top"}
    for n in range(1, count + 1):
        assert scopes[_instance(n)] == {f"{APP}_MD-1_P-1_R-1": str(n)}


def _first_named(ref_id: str, ids: list[str]) -> str | None:
    wrapped = f"_{ref_id}_"
    return next((mid for mid in ids if f"_{mid}_" in wrapped), None)


def test_the_lookup_among_many_instances_finds_what_searching_each_finds() -> None:
    pieces = ["M-1", "A-2", "MD-1", "M-10", "MI-1", "MI-2", "P-3", "R-4", "", "SM-1"]
    rng = random.Random(7)
    for _ in range(20000):
        ids = list(
            dict.fromkeys(
                "_".join(rng.choice(pieces) for _ in range(rng.randint(1, 4)))
                for _ in range(rng.randint(0, 14))
            )
        )
        ref_id = "_".join(rng.choice(pieces) for _ in range(rng.randint(1, 9)))
        if ids and rng.random() < 0.3:
            ref_id = f"{rng.choice(['', 'P-1_'])}{rng.choice(ids)}{rng.choice(['', '_P-1_R-1'])}"
        lookup = _InstanceIds(ids)
        assert lookup.owner(ref_id) == _first_named(ref_id, ids)
        # Asked again (the answers are kept).
        assert lookup.owner(ref_id) == _first_named(ref_id, ids)


def test_values_by_ref_id_seed_as_instance_refs_do() -> None:
    instances = [ModuleInstance(id=_instance(n), ref_id=f"{APP}_MD-1") for n in (1, 2)]
    values = {
        f"{_instance(1)}_P-1_R-1": "1",
        f"{APP}_MD-1_M-3_MI-1_P-1_R-1": "3",
        f"{APP}_P-9_R-9": "top",
    }
    by_refs = GlobalState.from_project(
        [ParameterInstanceRef(ref_id=k, value=v) for k, v in values.items()],
        instances,
        app_id=APP,
    )
    by_values = GlobalState.from_project(
        None, instances, app_id=APP, parameter_values=values
    )
    assert by_values.layout() == by_refs.layout()
    # Both together: the values after the instance refs.
    both = GlobalState.from_project(
        [ParameterInstanceRef(ref_id=f"{APP}_P-9_R-9", value="first")],
        instances,
        app_id=APP,
        parameter_values=values,
    )
    assert both.param_ref_id_to_value[f"{APP}_P-9_R-9"] == "top"


def test_the_layout_tells_seedings_apart() -> None:
    def seeded(values: dict[str, str]) -> GlobalState:
        return GlobalState.from_project(None, None, app_id=APP, parameter_values=values)

    a = {f"{APP}_P-1_R-1": "1", f"{APP}_P-2_R-2": "2"}
    assert seeded(a).layout() == seeded(dict(a)).layout()
    assert seeded(a).layout() != seeded({**a, f"{APP}_P-2_R-2": "3"}).layout()
    # The same values in another order.
    assert seeded(a).layout() != seeded(dict(reversed(a.items()))).layout()
    # The same values in another scope.
    assert (
        seeded({f"{_instance(1)}_P-1_R-1": "1"}).layout()
        != GlobalState.from_project(
            None,
            [ModuleInstance(id=_instance(2), ref_id=f"{APP}_MD-1")],
            app_id=APP,
            parameter_values={f"{_instance(1)}_P-1_R-1": "1"},
        ).layout()
    )
