"""What a parameter calculation computes from the same values is computed once per application:
the code sees nothing but the values of its parameters (and the product's script and
messages)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import xknxeditor.prod.parser_v2.calculation as calculation
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicTreeBuilder, DynamicUI
from xknxeditor.prod.script.compat.runtime import JScriptEnv

APP = "M-00FA_A-0001-01-0000"
XML = (
    Path(__file__).parents[1] / "fixtures" / "calculations_application.xml"
).read_bytes()


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


def _scripts_started(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the calculations whose code runs from now on (validations run every time)."""
    started = [0]
    run = calculation._compute_now  # pyright: ignore[reportPrivateUsage]

    def counted(*args: Any) -> dict[str, Any] | None:
        started[0] += 1
        return run(*args)

    monkeypatch.setattr(calculation, "_compute_now", counted)
    return started


Program = tuple[Any, DynamicTreeBuilder]


def _computed(
    program: Program, value: str, env: JScriptEnv | None = None
) -> list[str | None]:
    ui = DynamicUI(program[0], tree_builder=program[1])
    ui.script_env = env
    ui.edit_parameter(_ref(1), value)
    return [ui.get_value(_ref(n)) for n in (2, 3, 4)]


@pytest.fixture
def program() -> Program:
    # A builder of its own: the results are kept with its indexer.
    parsed = parse_application_xml(XML, "M-00FA")[0].program
    return parsed, DynamicTreeBuilder(parsed)


def test_the_same_values_are_computed_once(
    program: Program, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _scripts_started(monkeypatch)
    first = _computed(program, "5")
    assert first == ["11", "14", "15"]
    runs = started[0]
    assert runs
    assert _computed(program, "5") == first
    assert started[0] == runs


def test_other_values_are_computed_again(
    program: Program, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _scripts_started(monkeypatch)
    first = _computed(program, "5")
    runs = started[0]
    assert _computed(program, "6") != first
    assert started[0] > runs


def test_a_script_environment_of_its_own_computes_every_time(
    program: Program, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A time zone or locale other than the system's can make the code compute otherwise.
    started = _scripts_started(monkeypatch)
    env = JScriptEnv(locale="de-DE")
    first = _computed(program, "5", env)
    runs = started[0]
    assert _computed(program, "5", env) == first
    assert started[0] == 2 * runs
