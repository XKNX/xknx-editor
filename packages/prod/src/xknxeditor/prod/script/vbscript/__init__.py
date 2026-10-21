"""A VBScript interpreter for the inline code of parameter calculations.

The code runs as the body of a procedure. Every parameter alias is a global variable of the
script holding the value it is given (numbers as Long, or Double outside the Long range, texts as
String, no value as Empty); the outputs' values are read back afterwards. Variables the code
declares, or assigns without declaring, are local to the procedure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, cast

from ..errors import ScriptCompileError, ScriptError
from .errors import COMPILE_SOURCE, VBCompileError, VBRuntimeError
from .interpreter import Interpreter
from .syntax import KEYWORDS, Program, parse
from .variant import (
    EMPTY,
    INT32_MAX,
    INT32_MIN,
    NULL,
    Culture,
    Value,
    type_name,
)

if TYPE_CHECKING:
    from ..sandbox import AbortToken

HostValue = bool | int | float | str | None


@lru_cache(maxsize=512)
def compile_code(code: str) -> Program:
    """The parsed procedure body of ``code``; raises :class:`VBCompileError`."""
    return parse(code)


def culture_for(locale: str | None) -> Culture:
    """The number format of ``locale`` (``None``: the system locale)."""
    from ..compat.runtime import currency_symbol, number_separators

    decimal, group = number_separators(locale)
    return Culture(decimal, group, currency_symbol(locale))


def to_variant(value: HostValue) -> Value:
    """A host value as the variant the script sees."""
    if value is None:
        return EMPTY
    if isinstance(value, int) and not isinstance(value, bool):
        return value if INT32_MIN <= value <= INT32_MAX else float(value)
    return value


def from_variant(value: Value) -> HostValue:
    """A variant as a host value; Empty and Null are ``None``."""
    if value is EMPTY or value is NULL:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    assert isinstance(value, str)
    return value


def _check_name(name: str) -> None:
    valid = (
        bool(name)
        and name[0].isascii()
        and name[0].isalpha()
        and all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in name)
    )
    if not valid or name.lower() in KEYWORDS:
        raise ScriptCompileError(
            "Expected identifier", name=COMPILE_SOURCE, number=1010, line=1
        )


def run_variants(
    code: str,
    inputs: Mapping[str, HostValue],
    outputs: Mapping[str, HostValue] | Iterable[str],
    *,
    culture: Culture | None = None,
    abort: AbortToken | None = None,
    deadline: float | None = None,
) -> dict[str, Value]:
    """Run ``code`` and return the variants of ``outputs``.

    ``outputs`` maps each output to its value before the run, or names outputs that start Empty.
    Raises :class:`VBCompileError` or :class:`VBRuntimeError` (with the engine's numbers and
    texts), :class:`ScriptCompileError` for an alias that cannot be a variable name, and
    :class:`ScriptAborted` when ``abort`` is requested or ``deadline`` seconds have passed.
    """
    if isinstance(outputs, Mapping):
        initial = {name: cast("HostValue", outputs[name]) for name in outputs}
    else:
        initial = dict[str, HostValue].fromkeys(outputs)
    names: dict[str, str] = {}
    for name in [*inputs, *initial]:
        _check_name(name)
        key = name.lower()
        if key in names:
            raise ScriptCompileError(
                "Name redefined", name=COMPILE_SOURCE, number=1041, line=1
            )
        names[key] = name
    program = compile_code(code)
    variables: dict[str, Value] = {}
    for name, value in [*inputs.items(), *initial.items()]:
        variables[name.lower()] = to_variant(value)
    Interpreter(
        program, variables, culture or Culture(), abort=abort, deadline=deadline
    ).run()
    return {name: variables[name.lower()] for name in initial}


def run_script(
    code: str,
    inputs: Mapping[str, HostValue],
    outputs: Mapping[str, HostValue] | Iterable[str],
    *,
    culture: Culture | None = None,
    abort: AbortToken | None = None,
    deadline: float | None = None,
) -> dict[str, HostValue]:
    """Run ``code`` and return the values of ``outputs`` (``None`` for Empty or Null).

    Raises :class:`ScriptCompileError` or :class:`ScriptError` with the engine's error text, and
    :class:`ScriptAborted` when ``abort`` is requested or ``deadline`` seconds have passed.
    """
    try:
        values = run_variants(
            code, inputs, outputs, culture=culture, abort=abort, deadline=deadline
        )
    except VBCompileError as exc:
        raise ScriptCompileError(
            exc.message,
            name=COMPILE_SOURCE,
            number=exc.number,
            line=exc.line,
            column=exc.column,
        ) from None
    except VBRuntimeError as exc:
        raise ScriptError(exc.message, name=exc.source, number=exc.number) from None
    return {name: from_variant(value) for name, value in values.items()}


__all__ = [
    "Culture",
    "HostValue",
    "VBCompileError",
    "VBRuntimeError",
    "compile_code",
    "culture_for",
    "from_variant",
    "run_script",
    "run_variants",
    "to_variant",
    "type_name",
]
