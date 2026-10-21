"""Tree-walking execution of a parsed VBScript procedure body."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from ..errors import ScriptAborted
from .builtins import BUILTINS, CONSTANTS
from .errors import (
    FOR_NOT_INITIALIZED,
    ILLEGAL_ASSIGNMENT,
    NO_SUCH_MEMBER,
    OBJECT_NOT_SET,
    OBJECT_REQUIRED,
    OUT_OF_STACK,
    RUNTIME_SOURCE,
    RUNTIME_TEXTS,
    TYPE_MISMATCH,
    WRONG_ARGUMENTS,
    VBRuntimeError,
)
from .syntax import (
    Apply,
    Assign,
    Binary,
    Call,
    CallStmt,
    Compare,
    DoLoop,
    Exit,
    Expr,
    For,
    If,
    IndexAssign,
    Literal,
    Member,
    MemberAssign,
    Name,
    ObjectRef,
    OnError,
    Program,
    Select,
    SetAssign,
    Stmt,
    Unary,
    While,
)
from .variant import (
    EMPTY,
    Culture,
    Integer,
    Value,
    add,
    common_number,
    compare,
    compare_op,
    concat,
    condition,
    describe,
    divide,
    int_divide,
    logical,
    logical_not,
    modulo,
    multiply,
    negate,
    power,
    subtract,
    to_double,
    to_long,
    to_text,
)

if TYPE_CHECKING:
    from ..sandbox import AbortToken

_BINARY: Final[dict[str, Callable[[Value, Value, Culture], Value]]] = {
    "+": add,
    "-": subtract,
    "*": multiply,
    "/": divide,
    "\\": int_divide,
    "mod": modulo,
    "^": power,
    "&": concat,
}
_LOGICAL: Final = frozenset({"and", "or", "xor", "eqv", "imp"})

# Statements between two abort and deadline checks.
_CHECK_EVERY: Final = 256


class _ExitBlock(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class _ErrObject:
    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.number = 0
        self.description = ""
        self.source = ""

    def set(self, exc: VBRuntimeError) -> None:
        self.number = exc.number
        self.description = exc.description
        self.source = exc.source


class Interpreter:
    """Runs one procedure body against the given global variables."""

    def __init__(
        self,
        program: Program,
        globals_: dict[str, Value],
        culture: Culture,
        *,
        abort: AbortToken | None = None,
        deadline: float | None = None,
    ) -> None:
        self.program = program
        self.globals = globals_
        self.culture = culture
        self.locals: dict[str, Value] = dict.fromkeys(program.locals, EMPTY)
        self.consts = program.consts
        self.resume_next = False
        self.err = _ErrObject()
        self._abort = abort
        self._deadline = deadline
        self._start = time.monotonic()
        self._steps = 0

    # -- control

    def run(self) -> None:
        try:
            with contextlib.suppress(_ExitBlock):
                self.block(self.program.body)
        except RecursionError:
            raise VBRuntimeError(OUT_OF_STACK) from None

    def _tick(self) -> None:
        self._steps += 1
        if self._steps % _CHECK_EVERY:
            return
        if self._abort is not None and self._abort.requested:
            raise ScriptAborted()
        if (
            self._deadline is not None
            and time.monotonic() - self._start > self._deadline
        ):
            if self._abort is not None:
                self._abort.request()
            raise ScriptAborted()

    def _swallow(self, exc: VBRuntimeError) -> None:
        """Record ``exc`` under ``On Error Resume Next``, or raise it."""
        if not self.resume_next:
            raise exc
        self.err.set(exc)

    def block(self, stmts: tuple[Stmt, ...]) -> None:
        for stmt in stmts:
            self._tick()
            try:
                self.execute(stmt)
            except VBRuntimeError as exc:
                self._swallow(exc)

    # -- statements

    def execute(self, stmt: Stmt) -> None:
        if isinstance(stmt, Assign):
            self.assign(stmt.key, stmt.name, self.evaluate(stmt.expr))
        elif isinstance(stmt, If):
            self._if(stmt)
        elif isinstance(stmt, While):
            self._while(stmt)
        elif isinstance(stmt, For):
            self._for(stmt)
        elif isinstance(stmt, DoLoop):
            self._do(stmt)
        elif isinstance(stmt, Select):
            self._select(stmt)
        elif isinstance(stmt, CallStmt):
            self._call_statement(stmt.call)
        elif isinstance(stmt, OnError):
            self.resume_next = stmt.resume_next
            self.err.clear()
        elif isinstance(stmt, Exit):
            raise _ExitBlock(stmt.kind)
        elif isinstance(stmt, IndexAssign):
            for arg in stmt.args:
                self.evaluate(arg)
            self.evaluate(stmt.expr)
            raise VBRuntimeError(TYPE_MISMATCH, stmt.name)
        elif isinstance(stmt, MemberAssign):
            self._member_assign(stmt)
        elif isinstance(stmt, SetAssign):
            value = self.evaluate(stmt.expr)
            raise VBRuntimeError(OBJECT_REQUIRED, describe(value, self.culture))

    def _condition(self, expr: Expr) -> bool:
        value = self.evaluate(expr)
        detail = expr.name if isinstance(expr, Name) else None
        return condition(value, self.culture, detail)

    def _guarded_condition(self, expr: Expr, on_error: bool) -> bool:
        """The condition; under ``On Error Resume Next`` an error gives ``on_error``."""
        try:
            return self._condition(expr)
        except VBRuntimeError as exc:
            self._swallow(exc)
            return on_error

    def _if(self, stmt: If) -> None:
        for cond, body in stmt.branches:
            if self._guarded_condition(cond, True):
                self.block(body)
                return
        self.block(stmt.orelse)

    def _while(self, stmt: While) -> None:
        while True:
            self._tick()
            if not self._guarded_condition(stmt.cond, True):
                return
            self.block(stmt.body)

    def _do(self, stmt: DoLoop) -> None:
        while True:
            self._tick()
            if stmt.pre is not None:
                until, cond = stmt.pre
                try:
                    ok = self._condition(cond) != until
                except VBRuntimeError as exc:
                    self._swallow(exc)
                    ok = True
                if not ok:
                    return
            try:
                self.block(stmt.body)
            except _ExitBlock as exit_:
                if exit_.kind == "do":
                    return
                raise
            if stmt.post is not None:
                until, cond = stmt.post
                try:
                    again = self._condition(cond) != until
                except VBRuntimeError as exc:
                    self._swallow(exc)
                    return
                if not again:
                    return

    def _for(self, stmt: For) -> None:
        try:
            start = self.evaluate(stmt.start)
            self.assign(stmt.key, stmt.name, start)
            end = self.evaluate(stmt.end)
            step = self.evaluate(stmt.step) if stmt.step is not None else Integer(1)
            start, end, step = common_number((start, end, step), self.culture)
            self.assign(stmt.key, stmt.name, start)
        except VBRuntimeError as exc:
            self._swallow(exc)
            try:
                self.block(stmt.body)
            except _ExitBlock as exit_:
                if exit_.kind == "for":
                    return
                raise
            raise VBRuntimeError(FOR_NOT_INITIALIZED) from None
        down = to_double(step, self.culture) < 0
        while True:
            self._tick()
            c = compare(self.lookup(stmt.key, stmt.name), end, self.culture)
            if c is None or (c < 0 if down else c > 0):
                return
            try:
                self.block(stmt.body)
            except _ExitBlock as exit_:
                if exit_.kind == "for":
                    return
                raise
            self.assign(
                stmt.key,
                stmt.name,
                add(self.lookup(stmt.key, stmt.name), step, self.culture),
            )

    def _select(self, stmt: Select) -> None:
        try:
            test = self.evaluate(stmt.test)
        except VBRuntimeError as exc:
            self._swallow(exc)
            return
        test_literal = isinstance(stmt.test, Literal) and stmt.test.hard
        for values, body in stmt.cases:
            for expr in values:
                try:
                    value = self.evaluate(expr)
                    hard = isinstance(expr, Literal) and expr.hard
                    matched = (
                        compare(test, value, self.culture, test_literal, hard) == 0
                    )
                except VBRuntimeError as exc:
                    self._swallow(exc)
                    matched = True
                if matched:
                    self.block(body)
                    return
        if stmt.orelse is not None:
            self.block(stmt.orelse)

    def _call_statement(self, call: Expr) -> None:
        if isinstance(call, Member):
            self._member(call, statement=True)
            return
        if isinstance(call, Call):
            if not call.args and self._is_variable(call.key):
                raise VBRuntimeError(TYPE_MISMATCH, call.name)
            self.evaluate(call)
            return
        self.evaluate(call)

    # -- names

    def _is_variable(self, key: str) -> bool:
        return key in self.locals or key in self.globals or key in self.consts

    def lookup(self, key: str, name: str) -> Value:
        if key in self.locals:
            return self.locals[key]
        if key in self.consts:
            return self.consts[key]
        if key in self.globals:
            return self.globals[key]
        if key in CONSTANTS:
            return CONSTANTS[key]
        builtin = BUILTINS.get(key)
        if builtin is not None:
            return self._call_builtin(key, name, [])
        if key == "err":
            return self.err.number
        return EMPTY

    def assign(self, key: str, name: str, value: Value) -> None:
        if key in self.locals:
            self.locals[key] = value
        elif key in self.consts or key in CONSTANTS:
            raise VBRuntimeError(ILLEGAL_ASSIGNMENT, name)
        elif key in self.globals:
            self.globals[key] = value
        elif key in BUILTINS or key == "err":
            raise VBRuntimeError(ILLEGAL_ASSIGNMENT, name)
        else:
            self.locals[key] = value

    # -- expressions

    def evaluate(self, expr: Expr) -> Value:
        if isinstance(expr, Name):
            return self.lookup(expr.key, expr.name)
        if isinstance(expr, Literal):
            return expr.value
        if isinstance(expr, Binary):
            left = self.evaluate(expr.left)
            right = self.evaluate(expr.right)
            if expr.op in _LOGICAL:
                return logical(expr.op, left, right, self.culture)
            return _BINARY[expr.op](left, right, self.culture)
        if isinstance(expr, Compare):
            left = self.evaluate(expr.left)
            right = self.evaluate(expr.right)
            return compare_op(
                expr.op,
                left,
                right,
                self.culture,
                isinstance(expr.left, Literal) and expr.left.hard,
                isinstance(expr.right, Literal) and expr.right.hard,
            )
        if isinstance(expr, Call):
            return self._call(expr)
        if isinstance(expr, Unary):
            operand = self.evaluate(expr.operand)
            if expr.op == "-":
                return negate(operand, self.culture)
            if expr.op == "not":
                return logical_not(operand, self.culture)
            return operand
        if isinstance(expr, Member):
            return self._member(expr, statement=False)
        if isinstance(expr, Apply):
            target = self.evaluate(expr.target)
            for arg in expr.args:
                self.evaluate(arg)
            raise VBRuntimeError(TYPE_MISMATCH, describe(target, self.culture))
        if isinstance(expr, ObjectRef):
            if expr.word == "me":
                raise VBRuntimeError(NO_SUCH_MEMBER)
            raise VBRuntimeError(OBJECT_NOT_SET)
        raise AssertionError(f"unknown expression {expr!r}")

    def _call(self, call: Call) -> Value:
        if self._is_variable(call.key):
            for arg in call.args:
                self.evaluate(arg)
            raise VBRuntimeError(TYPE_MISMATCH, call.name)
        if call.key in BUILTINS:
            args = [self.evaluate(arg) for arg in call.args]
            return self._call_builtin(call.key, call.name, args)
        if call.key in CONSTANTS:
            raise VBRuntimeError(TYPE_MISMATCH, call.name)
        if call.key == "err" and not call.args:
            return self.err.number
        for arg in call.args:
            self.evaluate(arg)
        raise VBRuntimeError(TYPE_MISMATCH, call.name)

    def _call_builtin(self, key: str, name: str, args: list[Value]) -> Value:
        builtin = BUILTINS[key]
        if not builtin.min_args <= len(args) <= builtin.max_args:
            raise VBRuntimeError(WRONG_ARGUMENTS, name)
        return builtin.func(args, self.culture)

    def _member_assign(self, stmt: MemberAssign) -> None:
        value = self.evaluate(stmt.expr)
        if stmt.obj != "err" or self._is_variable(stmt.obj):
            raise VBRuntimeError(OBJECT_REQUIRED, stmt.obj_name)
        if stmt.key == "number":
            self.err.number = to_long(value, self.culture)
        elif stmt.key == "description":
            self.err.description = to_text(value, self.culture)
        elif stmt.key == "source":
            self.err.source = to_text(value, self.culture)
        elif stmt.key not in ("helpfile", "helpcontext"):
            raise VBRuntimeError(NO_SUCH_MEMBER, stmt.name)

    def _member(self, member: Member, *, statement: bool) -> Value:
        if member.obj != "err" or self._is_variable(member.obj):
            raise VBRuntimeError(OBJECT_REQUIRED, member.obj_name)
        args = [self.evaluate(arg) for arg in member.args]
        key = member.key
        if key in ("number", "description", "source", "helpfile", "helpcontext"):
            if args:
                raise VBRuntimeError(WRONG_ARGUMENTS, member.name)
            if key == "number":
                return self.err.number
            if key == "description":
                return self.err.description
            if key == "source":
                return self.err.source
            return "" if key == "helpfile" else 0
        if key == "clear":
            if args:
                raise VBRuntimeError(WRONG_ARGUMENTS, member.name)
            self.err.clear()
            return EMPTY
        if key == "raise":
            if not 1 <= len(args) <= 5:
                raise VBRuntimeError(WRONG_ARGUMENTS, member.name)
            number = to_long(args[0], self.culture)
            source = (
                to_text(args[1], self.culture)
                if len(args) > 1 and args[1] is not EMPTY
                else RUNTIME_SOURCE
            )
            description = (
                to_text(args[2], self.culture)
                if len(args) > 2 and args[2] is not EMPTY
                else RUNTIME_TEXTS.get(number, "Unknown runtime error")
            )
            raise VBRuntimeError(number, description=description, source=source)
        raise VBRuntimeError(NO_SUCH_MEMBER, member.name)


__all__ = ["Interpreter"]
