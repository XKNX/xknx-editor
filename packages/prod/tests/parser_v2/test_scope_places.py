"""Where a UI finds the refs it looks up in its evaluated state: UIs with the same module scopes
find each ref in the same place (kept for all of them) and group refs by their scopes as they
are found one by one, and a recalculation of stored values hands the calculations the stored
values they ask for, as all the active ones. The refs of a module definition no instance names
are never active."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

import pytest

from xknxeditor.namespaces.intermediate import ApplicationProgram, ModuleInstance
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2 import calculation, dynamic
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI

FIXTURES = Path(__file__).parents[1] / "fixtures"
APP = "M-00FA_A-0005-01-0000"
LAYOUT = f"{APP}_P-1_R-1"
# Refs of the application, of the module instances either layout makes, of a definition and
# one no parameter has, in no order.
REFS = [
    f"{APP}_P-2_R-2",
    f"{APP}_MD-1_M-9_MI-1_P-1_R-1",
    f"{APP}_P-3_R-3",
    f"{APP}_MD-2_M-16_MI-1_P-1_R-1",
    f"{APP}_MD-1_P-1_R-1",
    f"{APP}_P-99_R-99",
    f"{APP}_MD-1_M-9_MI-1_P-1_R-1",
    f"{APP}_P-4_R-4",
]


def _builder() -> tuple[ApplicationProgram, DynamicTreeBuilder]:
    xml = (FIXTURES / "union_late_assign_application.xml").read_bytes()
    program = parse_application_xml(xml, "M-00FA")[0].program
    return program, DynamicTreeBuilder(program)


def _ui(
    built: tuple[ApplicationProgram, DynamicTreeBuilder],
    values: dict[str, str] | None = None,
) -> DynamicUI:
    ui = DynamicUI(built[0], tree_builder=built[1], parameter_values=values)
    ui.ui()
    return ui


def _as_found(ui: DynamicUI, refs: Iterable[str]) -> list[tuple[int, list[str]]]:
    """The refs grouped by their scopes as each is found in the state (by the scope's id)."""
    groups = ui._grouped(refs)  # pyright: ignore[reportPrivateUsage]
    return [(id(scope), locals_) for scope, locals_ in groups]


def _grouped(ui: DynamicUI, refs: Iterable[str]) -> list[tuple[int, list[str]]]:
    groups = ui._group_by_scope(refs)  # pyright: ignore[reportPrivateUsage]
    return [(id(scope), locals_) for scope, locals_ in groups]


def _check_lookups(ui: DynamicUI) -> None:
    for ref in REFS:
        found = ui._find_evaluated_scope(ref)  # pyright: ignore[reportPrivateUsage]
        scope, local = ui._locate(ref)  # pyright: ignore[reportPrivateUsage]
        assert scope is found[0]
        assert local == found[1]


@pytest.mark.parametrize("layout", ["2", "0"])
def test_uis_with_the_same_scopes_find_each_ref_where_it_is(layout: str) -> None:
    builder = _builder()
    first = _ui(builder, {LAYOUT: layout})
    first_groups = _grouped(first, REFS)
    _check_lookups(first)
    second = _ui(builder, {LAYOUT: layout, f"{APP}_P-2_R-2": "1"})
    # The second UI finds the refs where the first found them, in its own scopes.
    assert second._placed().places is first._placed().places  # pyright: ignore[reportPrivateUsage]
    assert _grouped(second, REFS) == _as_found(second, REFS)
    assert first_groups == _as_found(first, REFS)
    _check_lookups(second)


def test_other_scopes_find_the_refs_anew() -> None:
    builder = _builder()
    two = _ui(builder, {LAYOUT: "2"})
    zero = _ui(builder, {LAYOUT: "0"})
    assert two._placed().places is not zero._placed().places  # pyright: ignore[reportPrivateUsage]
    assert two.local_ref_id(REFS[1]) == f"{APP}_MD-1_P-1_R-1"
    assert zero.local_ref_id(REFS[1]) == REFS[1]
    for ui in (two, zero):
        assert _grouped(ui, REFS) == _as_found(ui, REFS)
        _check_lookups(ui)


def test_a_ui_follows_its_scopes_after_a_change() -> None:
    builder = _builder()
    ui = _ui(builder, {LAYOUT: "2"})
    assert ui.local_ref_id(REFS[1]) == f"{APP}_MD-1_P-1_R-1"
    ui.set_parameter_ref(LAYOUT, "0")
    ui.ui()
    assert ui.local_ref_id(REFS[1]) == REFS[1]
    assert _grouped(ui, REFS) == _as_found(ui, REFS)
    _check_lookups(ui)


def test_an_unevaluated_ui_groups_the_refs_as_found() -> None:
    program, builder = _builder()
    # Seeded with values of both module instances: each ref but that of no parameter is in
    # a scope of the state before it is evaluated.
    ui = DynamicUI(
        program,
        tree_builder=builder,
        parameter_values={REFS[1]: "1", REFS[3]: "1"},
    )
    known = [ref for ref in REFS if "P-99" not in ref]
    grouped = ui._group_by_scope(iter(known))  # pyright: ignore[reportPrivateUsage]
    assert not ui.evaluated
    assert [(id(s), refs) for s, refs in grouped] == _as_found(ui, known)


STORED_APP = "M-00FA_A-0002-01-0000"
STORED_VALUES = {
    f"{STORED_APP}_P-5_R-5": "1",
    f"{STORED_APP}_P-4_R-4": "21",
    f"{STORED_APP}_P-2_R-2": "45",
    f"{STORED_APP}_P-7_R-7": "12",
    f"{STORED_APP}_P-12_R-12": "7",
    f"{STORED_APP}_P-99_R-99": "1",
}


def _stored_ui() -> DynamicUI:
    xml = (FIXTURES / "stored_calculations_application.xml").read_bytes()
    ui = DynamicUI(parse_application_xml(xml, "M-00FA")[0].program)
    ui.ui()
    ui.apply_parameter_values(STORED_VALUES)
    ui.ui()
    return ui


def test_a_recalculation_hands_over_the_stored_values_calculations_ask_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calculation, "_failures_logged", set())
    run = calculation.run_calculations_many
    handed: list[tuple[tuple[str, ...], frozenset[str]]] = []

    def capture(
        *args: Any, stored: Collection[str] | None = None, **kwargs: Any
    ) -> None:
        handed.append((tuple(args[1]), frozenset(stored or ())))
        run(*args, stored=stored, **kwargs)

    # As every active one of the values is handed over.
    expected = _stored_ui()
    active = expected.active_parameter_ref_ids()

    def every(*args: Any, stored: Collection[str] | None = None, **kwargs: Any) -> None:
        refs = list(args[1])
        run(*args, stored={ref for ref in refs if ref in active}, **kwargs)

    monkeypatch.setattr(dynamic, "run_calculations_many", every)
    expected_changes = expected.recalculate(list(STORED_VALUES), stored=True)
    monkeypatch.setattr(dynamic, "run_calculations_many", capture)
    ui = _stored_ui()
    changes = ui.recalculate(list(STORED_VALUES), stored=True)
    assert changes == expected_changes
    assert ui.explicit_values() == expected.explicit_values()
    idx = ui.indexer
    for refs, stored in handed:
        # The values on the right side of a calculation, those active among them.
        assert stored == {
            ref for ref in refs if idx.calculations_for_r(ref) and ref in active
        }


# The refs of the module definitions (no instance named) and of the application's own tree.
DEFINITION_REFS = [f"{APP}_MD-1_P-1_R-1", f"{APP}_MD-2_P-1_R-1"]
APPLICATION_REFS = [f"{APP}_P-{n}_R-{n}" for n in range(1, 5)]


@pytest.mark.parametrize("layout", ["0", "1", "2"])
def test_the_refs_of_a_definition_are_never_active(layout: str) -> None:
    built = _builder()
    # Seeded with values of the definitions' refs too.
    ui = _ui(built, {LAYOUT: layout, **dict.fromkeys(DEFINITION_REFS, "1")})
    assert ui.never_active(DEFINITION_REFS)
    assert not set(DEFINITION_REFS) & ui.active_parameter_ref_ids()
    for ref in [*APPLICATION_REFS, REFS[1], REFS[3]]:
        assert not ui.never_active([*DEFINITION_REFS, ref])
    # After a change of the values too.
    ui.set_parameter_ref(LAYOUT, "2" if layout != "2" else "0")
    assert ui.never_active(DEFINITION_REFS)
    assert not set(DEFINITION_REFS) & ui.active_parameter_ref_ids()


def test_an_instance_whose_id_names_none_may_make_any_ref_active() -> None:
    program, builder = _builder()
    ui = DynamicUI(
        program,
        tree_builder=builder,
        module_instances=[ModuleInstance(id=f"{APP}_MD-1", ref_id=f"{APP}_MD-1")],
    )
    ui.ui()
    assert not ui.never_active(DEFINITION_REFS)
    assert _ui((program, builder)).never_active(DEFINITION_REFS)
