from __future__ import annotations

import pytest

from xknxeditor.prod.script import ScriptCompileError
from xknxeditor.prod.script.compat.frontend import preprocess_cc, transform


def test_rewrites_keep_line_numbers() -> None:
    src = "var a = 'x' + 1.5;\nfunction f(o) {\n  return o.m(1) + o[k];\n}\n"
    out = transform(src).code
    assert out.split("\n")[0].startswith("__xk.dn(f);")
    assert out.count("\n") >= src.count("\n")
    assert "__xk.add(" in out
    assert '__xk.call(o,__xk.mth(o,"m"),"m",1)' in out
    assert "__xk.get(o,k)" in out


def test_long_condition_chains() -> None:
    # A product's condition can chain well over a hundred terms; each nests one level to the left.
    terms = " || ".join(f"(p{i} == 1 && q{i} == 0)" for i in range(170))
    out = transform(f"if ({terms}) {{ x = 1; }}").code
    assert out.count("&&") == 170
    assert out.count("||") == 169


def test_catch_parameter_is_function_scoped() -> None:
    out = transform("function f() { try { x(); } catch (e) { return e; } }").code
    assert "catch (__xk_e" in out
    assert "var e;" in out


def test_block_functions_and_named_expressions_are_hoisted() -> None:
    out = transform(
        "function f() { if (a) { function g() {} } var h = function k() {}; }"
    ).code
    assert out.count("function g() {}") == 1
    assert "function k() {}" in out.split("var h =")[1]
    assert "__xk.dn(g,k);" in out


def test_function_text_is_mapped_to_original() -> None:
    result = transform("function f(a) { return a + 1; }")
    assert "function f(a) { return a + 1; }" in result.fnmap.values()


@pytest.mark.parametrize(
    "src",
    [
        "let a = 1;",
        "var o = {class: 1};",
        "var o = {a: 1,};",
        "var o = {get x() { return 1; }};",
        "<!-- x\nvar a;",
        "var s = `a`;",
        "var f = x => x;",
    ],
)
def test_rejects_what_jscript_rejects(src: str) -> None:
    with pytest.raises(ScriptCompileError):
        transform(src)


@pytest.mark.parametrize(
    "src",
    [
        "var int = 1;",
        "var r = /a/gg;",
        "var a = [1,];",
        "debugger;",
        "var s = 'a\\u2028b';",
        "'use strict';",
    ],
)
def test_accepts_what_jscript_accepts(src: str) -> None:
    transform(src)


def test_compile_error_messages() -> None:
    with pytest.raises(ScriptCompileError) as err:
        transform("var =")
    assert (err.value.message, err.value.number) == ("Expected identifier", -2146827278)
    with pytest.raises(ScriptCompileError) as err:
        transform("function f() {")
    assert err.value.message == "Expected '}'"


def test_conditional_compilation() -> None:
    assert preprocess_cc("var r = 0; /*@ r = 1; @*/") == "var r = 0; /*@ r = 1; @*/"
    out = preprocess_cc(
        "/*@cc_on @*/ /*@if (@_jscript_version >= 5) r = 'new'; @else r = 'old'; @end @*/"
    )
    assert "r = 'new'" in out
    assert "old" not in out
    assert "r = 3" in preprocess_cc("/*@cc_on @set @v = 1 + 2 r = @v; @*/")
    assert "true, true" in preprocess_cc("/*@cc_on f(@_win32, @_x86); @*/")
