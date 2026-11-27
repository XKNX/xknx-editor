"""Seeding a parameter state with values: each value goes to the first module instance its ref
id names (also among many instances), values given by ref id seed as instance refs do, and the
layout of a state tells two seedings apart. A state is seeded as with each value set one after
another."""

from __future__ import annotations

import random

import pytest

from xknxeditor.namespaces.ids import unpadded_id
from xknxeditor.namespaces.intermediate import ModuleInstance, ParameterInstanceRef
from xknxeditor.prod.parser_v2.state import (
    GlobalState,
    ModuleState,
    ParameterState,
    _instance_key,  # pyright: ignore[reportPrivateUsage]
    _InstanceIds,  # pyright: ignore[reportPrivateUsage]
    _qualified,  # pyright: ignore[reportPrivateUsage]
    _submodule_definition,  # pyright: ignore[reportPrivateUsage]
    layouts_alike,
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


def _listed(*numbers: int, values: dict[str, str] | None = None) -> GlobalState:
    """A state seeded with ``values`` and the instances ``numbers`` listed in that order."""
    return GlobalState.from_project(
        None,
        [ModuleInstance(id=_instance(n), ref_id=f"{APP}_MD-1") for n in numbers],
        app_id=APP,
        parameter_values=values or {},
    )


def test_listed_instances_that_hold_nothing_leave_states_alike() -> None:
    values = {f"{_instance(1)}_P-1_R-1": "1", f"{APP}_P-2_R-2": "2"}
    alone = _listed(values=values)
    # Instance 2 listed without a value, and instance 1 listed after it: an evaluation reads
    # neither the order nor an instance before it reaches it.
    with_empty = _listed(2, 1, values=values)
    assert alone.layout() != with_empty.layout()
    assert layouts_alike(alone.layout(), with_empty.layout())
    assert layouts_alike(with_empty.layout(), alone.layout())


def test_states_holding_other_things_are_not_alike() -> None:
    values = {f"{_instance(1)}_P-1_R-1": "1", f"{APP}_P-2_R-2": "2"}
    alone = _listed(values=values)
    # Another value, in the application or in an instance.
    assert not layouts_alike(
        alone.layout(), _listed(values={**values, f"{APP}_P-2_R-2": "3"}).layout()
    )
    assert not layouts_alike(
        alone.layout(),
        _listed(values={**values, f"{_instance(1)}_P-1_R-1": "2"}).layout(),
    )
    # An instance only one of them has that holds a value.
    assert not layouts_alike(
        alone.layout(),
        _listed(values={**values, f"{_instance(3)}_P-1_R-1": "1"}).layout(),
    )
    # The same instance holding nothing but under another definition.
    other = GlobalState.from_project(
        None,
        [ModuleInstance(id=_instance(1), ref_id=f"{APP}_MD-2")],
        app_id=APP,
        parameter_values=values,
    )
    assert not layouts_alike(alone.layout(), other.layout())


def _one_by_one(
    values: dict[str, str],
    module_instances: list[ModuleInstance],
    padded: dict[str, str],
) -> GlobalState:
    """A state seeded as :meth:`GlobalState.from_project` seeds it, each value set one after
    another."""
    root = GlobalState()
    exact: set[tuple[int, str]] = set()

    def put(scope: ParameterState, ref_id: str, value: str) -> None:
        if padded:
            own = padded.get(unpadded_id(ref_id))
            if own is not None and own != ref_id:
                if (id(scope), own) in exact:
                    return
                ref_id = own
            else:
                exact.add((id(scope), ref_id))
        scope.set(ref_id, value)

    stored: dict[str, tuple[ModuleState, str]] = {}

    def add(instance_id: str, definition: str) -> None:
        key = _qualified(instance_id, APP)
        submodule = _submodule_definition(instance_id)
        if submodule is not None:
            definition, base = submodule
        else:
            base = definition
        ms = ModuleState(key, ref_id=_qualified(definition, APP))
        ms._parent = root  # pyright: ignore[reportPrivateUsage]
        root._children[key] = ms  # pyright: ignore[reportPrivateUsage]
        stored[instance_id] = (ms, base)

    for mi in module_instances:
        assert mi.id is not None and mi.ref_id is not None
        add(mi.id, mi.ref_id)
    known = {_qualified(mid, APP) for mid in stored}
    for ref_id in values:
        match = _instance_key(ref_id, APP)
        if match is None or match[0] in known:
            continue
        known.add(match[0])
        add(match[1], match[2])
    by_length = sorted(stored.items(), key=lambda item: len(item[0]), reverse=True)
    instances = _InstanceIds(tuple(mid for mid, _ in by_length))
    for ref_id, value in values.items():
        owner = instances.owner(ref_id)
        if owner is None:
            put(root, ref_id, value)
            continue
        ms, base = stored[owner]
        ms.module_instance_id, local = instances.place(ref_id, owner, base)
        put(ms, local, value)
    return root


def test_a_state_is_seeded_as_with_each_value_set_one_after_another() -> None:
    rng = random.Random(11)
    instances = [
        *(
            f"{APP}_MD-{d}_M-{m}_MI-{i}"
            for d in (1, 2)
            for m in (1, 2, 12)
            for i in (1, 2)
        ),
        "MD-1_M-3_MI-1",
        f"{APP}_MD-1_M-2_MI-1_SM-1_M-1_MI-1-1",
        "MD-1_SM-1_M-1_MI-1",
    ]
    tails = ["P-1_R-1", "P-01_R-01", "P-2_R-2", "P-02_R-02", "UP-3_R-3"]
    # The application's spelling of the refs a project may store unpadded.
    padded_refs = [f"{APP}_MD-1_P-01_R-01", f"{APP}_P-02_R-02", f"{APP}_MD-2_P-02_R-02"]
    for _ in range(3000):
        values: dict[str, str] = {}
        for _ in range(rng.randint(0, 14)):
            owner = rng.choice([*instances, APP, APP])
            values[f"{owner}_{rng.choice(tails)}"] = str(rng.randint(0, 3))
        listed = [
            ModuleInstance(
                id=instance,
                ref_id=instance.split("_M-", 1)[0]
                if rng.random() < 0.8
                else f"{APP}_MD-2",
            )
            for instance in rng.sample(instances, rng.randint(0, 4))
        ]
        padded = (
            {unpadded_id(ref): ref for ref in padded_refs} if rng.random() < 0.5 else {}
        )
        state = GlobalState.from_project(
            None,
            listed,
            app_id=APP,
            padded_ref_ids=padded,
            parameter_values=values,
        )
        assert state.layout() == _one_by_one(values, listed, padded).layout()
        # Seeded again (the routes of the values are kept), and with other definitions.
        again = GlobalState.from_project(
            None, listed, app_id=APP, padded_ref_ids=padded, parameter_values=values
        )
        assert again.layout() == state.layout()


def test_values_set_at_once_are_set_as_one_after_another() -> None:
    values = {"a": "1", "b": "2", "c": "3"}
    for before in ({}, {"b": "0", "d": "4"}):
        at_once, one_by_one = GlobalState(), GlobalState()
        for state in (at_once, one_by_one):
            for ref_id, value in before.items():
                state.set(ref_id, value)
        at_once.set_values(values)
        for ref_id, value in values.items():
            one_by_one.set(ref_id, value)
        assert at_once.layout() == one_by_one.layout()
        assert at_once.pass_written() == one_by_one.pass_written()
