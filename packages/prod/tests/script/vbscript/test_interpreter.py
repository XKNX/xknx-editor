"""The VBScript interpreter API: variables, errors, limits and number formats."""

from __future__ import annotations

import time

import pytest

from xknxeditor.prod.script import (
    AbortToken,
    ScriptAborted,
    ScriptCompileError,
    ScriptError,
)
from xknxeditor.prod.script.vbscript import (
    Culture,
    VBCompileError,
    culture_for,
    from_variant,
    run_script,
    run_variants,
    to_variant,
)
from xknxeditor.prod.script.vbscript.syntax import (
    Binary,
    Call,
    Compare,
    Literal,
    Name,
    Unary,
    parse,
    tokenize,
)
from xknxeditor.prod.script.vbscript.variant import (
    EMPTY,
    NULL,
    Byte,
    Integer,
    Single,
    type_name,
)


def test_inputs_and_outputs() -> None:
    assert run_script("y = x * 2 + 1", {"x": 5}, ["y"]) == {"y": 11}


def test_outputs_start_with_the_given_values() -> None:
    code = "If x Then y = y Or 4"
    assert run_script(code, {"x": 1}, {"y": 1}) == {"y": 5}
    assert run_script(code, {"x": 0}, {"y": 1}) == {"y": 1}
    assert run_script(code, {"x": 0}, ["y"]) == {"y": None}


def test_names_are_case_insensitive() -> None:
    assert run_script("OUT = Inp + 1", {"inp": 1}, ["Out"]) == {"Out": 2}


def test_input_changes_are_not_returned() -> None:
    assert run_script("x = 9: y = x", {"x": 1}, ["y"]) == {"y": 9}


def test_declared_alias_is_local() -> None:
    assert run_script("Dim y\ny = 5", {}, {"y": 1}) == {"y": 1}


def test_host_values() -> None:
    assert type_name(to_variant(5)) == "Long"
    assert type_name(to_variant(2**31)) == "Double"
    assert type_name(to_variant(1.0)) == "Double"
    assert type_name(to_variant("x")) == "String"
    assert to_variant(None) is EMPTY
    assert from_variant(Integer(3)) == 3 and type(from_variant(Integer(3))) is int
    assert from_variant(Byte(3)) == 3
    assert type(from_variant(Single(1.5))) is float
    assert from_variant(True) is True
    assert from_variant(EMPTY) is None
    assert from_variant(NULL) is None


def test_results_keep_their_types() -> None:
    values = run_variants(
        'a = 1\nb = 70000\nc = 1 / 2\nd = CSng(1)\ne = "x"\nf = True\ng = CByte(1)',
        {},
        list("abcdefgh"),
    )
    assert {k: type_name(v) for k, v in values.items()} == {
        "a": "Integer",
        "b": "Long",
        "c": "Double",
        "d": "Single",
        "e": "String",
        "f": "Boolean",
        "g": "Byte",
        "h": "Empty",
    }


def test_compile_error() -> None:
    with pytest.raises(ScriptCompileError) as err:
        run_script("y = 1\nIf x Then\ny = 2", {"x": 1}, ["y"])
    assert err.value.message == "Expected 'If'"
    assert err.value.number == 1012
    with pytest.raises(VBCompileError):
        run_variants("y = (1", {}, ["y"])


def test_runtime_error() -> None:
    with pytest.raises(ScriptError) as err:
        run_script('y = CInt("x")', {}, ["y"])
    assert err.value.message == "Type mismatch: 'CInt'"
    assert err.value.number == 13
    assert not isinstance(err.value, ScriptCompileError)


@pytest.mark.parametrize("names", [["a-b"], ["1a"], ["If"], [""]])
def test_alias_that_is_no_name_does_not_compile(names: list[str]) -> None:
    with pytest.raises(ScriptCompileError) as err:
        run_script("y = 1", dict.fromkeys(names, 1), ["y"])
    assert err.value.message == "Expected identifier"


def test_alias_on_both_sides_does_not_compile() -> None:
    with pytest.raises(ScriptCompileError) as err:
        run_script("y = 1", {"y": 1}, ["Y"])
    assert err.value.message == "Name redefined"


def test_deadline_stops_a_runaway_loop() -> None:
    started = time.monotonic()
    with pytest.raises(ScriptAborted):
        run_script("While True\nWend", {}, ["y"], deadline=0.2)
    assert time.monotonic() - started < 2


def test_deadline_requests_the_abort_token() -> None:
    token = AbortToken()
    with pytest.raises(ScriptAborted):
        run_script("Do\n x = x + 1\nLoop", {}, ["y"], abort=token, deadline=0.1)
    assert token.requested


def test_abort_token_stops_the_run() -> None:
    token = AbortToken()
    token.request()
    with pytest.raises(ScriptAborted):
        run_script("For i = 1 To 100000\nNext", {}, ["y"], abort=token)


def test_number_format_of_the_culture() -> None:
    code = 'y = CStr(x / 4) & "|" & CDbl("1.250,5")'
    assert run_script(code, {"x": 5}, ["y"], culture=Culture(",", ".")) == {
        "y": "1,25|1250,5"
    }
    assert run_script(code, {"x": 5}, ["y"], culture=Culture(".", ",")) == {
        "y": "1.25|1.2505"
    }


def test_culture_for_locale() -> None:
    assert culture_for("de-DE") == Culture(",", ".", "€")
    assert culture_for("en-US") == Culture(".", ",", "$")
    assert culture_for("nl") == Culture(",", ".", "€")


def test_literal_types() -> None:
    values = [
        t.value for t in tokenize("32767 32768 2147483648 1.5 &hFFFF &h8000 &o17")
    ]
    assert [type_name(v) for v in values[:-1]] == [
        "Integer",
        "Long",
        "Double",
        "Double",
        "Integer",
        "Long",
        "Integer",
    ]
    assert values[4] == -1 and values[5] == -32768


def test_precedence() -> None:
    (stmt,) = parse("r = -2 ^ 2 + 3 * 4 = 16 And Not x").body
    expr = stmt.expr  # type: ignore[attr-defined]
    assert isinstance(expr, Binary) and expr.op == "and"
    assert isinstance(expr.right, Unary) and expr.right.op == "not"
    cmp = expr.left
    assert isinstance(cmp, Compare) and cmp.op == "="
    total = cmp.left
    assert isinstance(total, Binary) and total.op == "+"
    power = total.left
    assert isinstance(power, Binary) and power.op == "^"
    assert isinstance(power.left, Unary) and power.left.op == "-"


def test_parenthesized_function_name_is_called() -> None:
    (stmt,) = parse("r = (Int)(x)").body
    assert isinstance(stmt.expr, Call) and stmt.expr.key == "int"  # type: ignore[attr-defined]
    assert run_script("r = (Int)(x / 2)", {"x": 5}, ["r"]) == {"r": 2.0}


def test_hoisted_declarations() -> None:
    program = parse("x = 1\nDim x, Y\nConst c = 5")
    assert program.locals == frozenset({"x", "y"})
    assert program.consts == {"c": 5}
    assert isinstance(parse("r = (5)").body[0].expr, Literal)  # type: ignore[attr-defined]
    assert isinstance(parse("r = x").body[0].expr, Name)  # type: ignore[attr-defined]
