"""Lexer, syntax tree and parser for the VBScript statements of a procedure body."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from .errors import VBCompileError
from .variant import EMPTY, NULL, Culture, Integer, Value, negate

KEYWORDS: Final = frozenset(
    {
        "and",
        "boolean",
        "byref",
        "byval",
        "call",
        "case",
        "class",
        "const",
        "currency",
        "dim",
        "do",
        "double",
        "each",
        "else",
        "elseif",
        "empty",
        "end",
        "endif",
        "enum",
        "eqv",
        "event",
        "exit",
        "false",
        "for",
        "function",
        "get",
        "goto",
        "if",
        "imp",
        "implements",
        "in",
        "integer",
        "is",
        "let",
        "like",
        "long",
        "loop",
        "lset",
        "me",
        "mod",
        "new",
        "next",
        "not",
        "nothing",
        "null",
        "on",
        "option",
        "optional",
        "or",
        "paramarray",
        "preserve",
        "private",
        "public",
        "raiseevent",
        "redim",
        "rem",
        "resume",
        "rset",
        "select",
        "set",
        "shared",
        "single",
        "static",
        "stop",
        "sub",
        "then",
        "to",
        "true",
        "type",
        "typeof",
        "until",
        "variant",
        "wend",
        "while",
        "with",
        "xor",
    }
)

_COMPARE_OPS: Final = {
    "=": "=",
    "<>": "<>",
    "><": "<>",
    "<": "<",
    ">": ">",
    "<=": "<=",
    "=<": "<=",
    ">=": ">=",
    "=>": ">=",
}

# ---------------------------------------------------------------------------------------------
# Tokens


@dataclass(frozen=True, slots=True)
class Token:
    kind: str  # num, str, id, kw, op, nl, colon, eof
    value: object
    text: str
    line: int
    column: int


def _is_ident_start(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _is_ident_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _decimal_literal(digits: str) -> Value:
    n = int(digits)
    if n <= 32767:
        return Integer(n)
    if n <= 2147483647:
        return n
    return float(n)


def _radix_literal(n: int) -> Value:
    if n <= 0xFFFF:
        v = n - 0x10000 if n >= 0x8000 else n
    else:
        v = n - 0x100000000 if n >= 0x80000000 else n
    return Integer(v) if -32768 < v <= 32767 else v


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(text)
    line = 1
    line_start = 0

    def fail(message: str, at: int) -> VBCompileError:
        return VBCompileError(message, line, at - line_start + 1)

    def add(kind: str, value: object, start: int) -> None:
        tokens.append(Token(kind, value, text[start:i], line, start - line_start + 1))

    while i < n:
        ch = text[i]
        start = i
        if ch in " \t\v\f":
            i += 1
        elif ch in "\r\n":
            i += 2 if text.startswith("\r\n", i) else 1
            tokens.append(Token("nl", None, "", line, start - line_start + 1))
            line += 1
            line_start = i
        elif ch == "'":
            while i < n and text[i] not in "\r\n":
                i += 1
        elif ch == ":":
            i += 1
            add("colon", None, start)
        elif ch == "_" and _continuation(text, i + 1):
            i += 1
            while i < n and text[i] in " \t":
                i += 1
            i += 2 if text.startswith("\r\n", i) else 1
            line += 1
            line_start = i
        elif _is_ident_start(ch):
            while i < n and _is_ident_char(text[i]):
                i += 1
            word = text[start:i]
            low = word.lower()
            if low == "rem":
                while i < n and text[i] not in "\r\n":
                    i += 1
            elif low in KEYWORDS:
                add("kw", low, start)
            else:
                add("id", word, start)
        elif ch == "[":
            j = text.find("]", i + 1)
            end_of_line = min(
                (k for k in (text.find("\r", i), text.find("\n", i)) if k >= 0),
                default=n,
            )
            if j < 0 or j > end_of_line:
                raise fail("Expected ']'", i)
            i = j + 1
            add("id", text[start + 1 : j], start)
        elif "0" <= ch <= "9" or (
            ch == "." and i + 1 < n and "0" <= text[i + 1] <= "9"
        ):
            i = _number(text, i, tokens, line, line_start)
        elif ch == '"':
            parts: list[str] = []
            i += 1
            while True:
                if i >= n or text[i] in "\r\n":
                    raise fail("Unterminated string constant", i)
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        parts.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                parts.append(text[i])
                i += 1
            add("str", "".join(parts), start)
        elif (
            ch == "&"
            and i + 1 < n
            and (text[i + 1] in "hHoO" or text[i + 1] in "01234567")
        ):
            i = _radix(text, i, tokens, line, line_start)
        elif ch in "<>=":
            two = text[i : i + 2]
            if two in _COMPARE_OPS:
                i += 2
                add("op", _COMPARE_OPS[two], start)
            else:
                i += 1
                add("op", ch, start)
        elif ch in "+-*/\\^&(),.":
            i += 1
            add("op", ch, start)
        else:
            raise fail("Invalid character", i)
    tokens.append(Token("eof", None, "", line, i - line_start + 1))
    return tokens


def _continuation(text: str, i: int) -> bool:
    n = len(text)
    while i < n and text[i] in " \t\v\f":
        i += 1
    return i >= n or text[i] in "\r\n"


def _number(text: str, i: int, tokens: list[Token], line: int, line_start: int) -> int:
    start = i
    n = len(text)
    while i < n and text[i].isascii() and text[i].isdigit():
        i += 1
    is_float = False
    if i < n and text[i] == ".":
        is_float = True
        i += 1
        while i < n and text[i].isascii() and text[i].isdigit():
            i += 1
    if i < n and text[i] in "eE":
        is_float = True
        j = i + 1
        if j < n and text[j] in "+-":
            j += 1
        k = j
        while k < n and text[k].isascii() and text[k].isdigit():
            k += 1
        if k == j:
            raise VBCompileError("Invalid number", line, start - line_start + 1)
        i = k
    literal = text[start:i]
    if is_float:
        value: Value = float(literal)
        if value == float("inf"):
            raise VBCompileError("Invalid number", line, start - line_start + 1)
    else:
        value = _decimal_literal(literal)
    tokens.append(Token("num", value, literal, line, start - line_start + 1))
    return i


def _radix(text: str, i: int, tokens: list[Token], line: int, line_start: int) -> int:
    start = i
    n = len(text)
    i += 1
    if text[i] in "hH":
        i += 1
        digits_start = i
        while i < n and text[i] in "0123456789abcdefABCDEF":
            i += 1
        base = 16
    else:
        if text[i] in "oO":
            i += 1
        digits_start = i
        while i < n and text[i] in "01234567":
            i += 1
        base = 8
    digits = text[digits_start:i]
    if not digits or (i < n and _is_ident_char(text[i])):
        raise VBCompileError("Syntax error", line, start - line_start + 1)
    value = int(digits, base)
    if value > 0xFFFFFFFF:
        raise VBCompileError("Syntax error", line, start - line_start + 1)
    if i < n and text[i] == "&":
        i += 1
    tokens.append(
        Token("num", _radix_literal(value), text[start:i], line, start - line_start + 1)
    )
    return i


# ---------------------------------------------------------------------------------------------
# Syntax tree


class Expr:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Literal(Expr):
    value: Value
    hard: bool  # number and string literals; the comparison rules treat them specially


@dataclass(frozen=True, slots=True)
class ObjectRef(Expr):
    """``Nothing`` or ``Me``, which have no value outside object code."""

    word: str


@dataclass(frozen=True, slots=True)
class Name(Expr):
    key: str
    name: str


@dataclass(frozen=True, slots=True)
class Call(Expr):
    key: str
    name: str
    args: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Apply(Expr):
    """Arguments applied to a value that is no function, such as ``(1)(2)``."""

    target: Expr
    args: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Member(Expr):
    obj: str
    obj_name: str
    key: str
    name: str
    args: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Unary(Expr):
    op: str  # "-", "+", "not"
    operand: Expr


@dataclass(frozen=True, slots=True)
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Compare(Expr):
    op: str
    left: Expr
    right: Expr


class Stmt:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Assign(Stmt):
    key: str
    name: str
    expr: Expr
    line: int


@dataclass(frozen=True, slots=True)
class IndexAssign(Stmt):
    key: str
    name: str
    args: tuple[Expr, ...]
    expr: Expr
    line: int


@dataclass(frozen=True, slots=True)
class MemberAssign(Stmt):
    obj: str
    obj_name: str
    key: str
    name: str
    expr: Expr
    line: int


@dataclass(frozen=True, slots=True)
class SetAssign(Stmt):
    key: str
    name: str
    expr: Expr
    line: int


@dataclass(frozen=True, slots=True)
class CallStmt(Stmt):
    call: Expr
    line: int


@dataclass(frozen=True, slots=True)
class If(Stmt):
    branches: tuple[tuple[Expr, tuple[Stmt, ...]], ...]
    orelse: tuple[Stmt, ...]
    line: int


@dataclass(frozen=True, slots=True)
class While(Stmt):
    cond: Expr
    body: tuple[Stmt, ...]
    line: int


@dataclass(frozen=True, slots=True)
class DoLoop(Stmt):
    pre: tuple[bool, Expr] | None  # (until, condition)
    post: tuple[bool, Expr] | None
    body: tuple[Stmt, ...]
    line: int


@dataclass(frozen=True, slots=True)
class For(Stmt):
    key: str
    name: str
    start: Expr
    end: Expr
    step: Expr | None
    body: tuple[Stmt, ...]
    line: int


@dataclass(frozen=True, slots=True)
class Select(Stmt):
    test: Expr
    cases: tuple[tuple[tuple[Expr, ...], tuple[Stmt, ...]], ...]
    orelse: tuple[Stmt, ...] | None
    line: int


@dataclass(frozen=True, slots=True)
class OnError(Stmt):
    resume_next: bool
    line: int


@dataclass(frozen=True, slots=True)
class Exit(Stmt):
    kind: str  # "for", "do", "sub"
    line: int


@dataclass(frozen=True, slots=True)
class Program:
    body: tuple[Stmt, ...]
    locals: frozenset[str]
    consts: dict[str, Value] = field(default_factory=dict[str, Value])


# ---------------------------------------------------------------------------------------------
# Parser

_WORD_VALUES: Final[dict[str, Value]] = {
    "true": True,
    "false": False,
    "empty": EMPTY,
    "null": NULL,
}


def _token_value(tok: Token) -> Value:
    value = tok.value
    assert isinstance(value, (bool, int, float, str))
    return value


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.locals: set[str] = set()
        self.consts: dict[str, Value] = {}
        self.loops: list[str] = []
        self.line_start = True
        self.depth = 0

    # -- token helpers

    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind != "eof":
            self.pos += 1
        return tok

    def is_kw(self, *words: str) -> bool:
        tok = self.tok
        return tok.kind == "kw" and tok.value in words

    def is_op(self, *ops: str) -> bool:
        tok = self.tok
        return tok.kind == "op" and tok.value in ops

    def peek_kw(self, offset: int, word: str) -> bool:
        tok = self.tokens[min(self.pos + offset, len(self.tokens) - 1)]
        return tok.kind == "kw" and tok.value == word

    def fail(self, message: str, tok: Token | None = None) -> VBCompileError:
        tok = tok or self.tok
        return VBCompileError(message, tok.line, tok.column)

    def expect_kw(self, word: str, message: str) -> Token:
        if not self.is_kw(word):
            raise self.fail(message)
        return self.advance()

    def expect_op(self, op: str, message: str) -> Token:
        if not self.is_op(op):
            raise self.fail(message)
        return self.advance()

    def at_end_of_statement(self) -> bool:
        return self.tok.kind in ("nl", "colon", "eof")

    def skip_separators(self) -> None:
        while self.tok.kind in ("nl", "colon"):
            self.line_start = self.tok.kind == "nl"
            self.advance()

    def end_statement(self) -> None:
        if self.at_end_of_statement():
            return
        if self.is_kw("else", "elseif"):
            raise self.fail("Must be first statement on the line")
        raise self.fail("Expected end of statement")

    def identifier(self) -> Token:
        tok = self.tok
        if tok.kind != "id":
            raise self.fail("Expected identifier")
        return self.advance()

    # -- program and blocks

    def program(self) -> Program:
        body = self.block(lambda: self.is_kw("end") and self.peek_kw(1, "sub"))
        if self.tok.kind == "eof":
            raise self.fail("Expected 'End'")
        self.advance()
        self.advance()
        self.skip_separators()
        if self.tok.kind != "eof":
            raise self.fail("Expected statement")
        return Program(body, frozenset(self.locals), dict(self.consts))

    def block(
        self, stop: Callable[[], bool], *, end_if_follows: bool = False
    ) -> tuple[Stmt, ...]:
        """Statements up to ``stop``; ``end_if_follows`` lets ``End If`` follow a statement
        directly, as in the ``Else`` part of a block ``If``."""
        stmts: list[Stmt] = []
        self.depth += 1
        while True:
            self.skip_separators()
            if self.tok.kind == "eof" or stop():
                self.depth -= 1
                return tuple(stmts)
            first = self.line_start
            stmt = self.statement(first)
            if stmt is not None:
                stmts.append(stmt)
            self.line_start = False
            if self.is_kw("end") and not self.at_end_of_statement():
                if end_if_follows and self.peek_kw(1, "if"):
                    continue
                if self.depth == 1:
                    raise self.fail("Expected 'Sub'", self.tokens[self.pos + 1])
                raise self.fail("Must be first statement on the line")
            self.end_statement()

    def statement(self, first: bool = True) -> Stmt | None:
        tok = self.tok
        if tok.kind == "kw":
            word = tok.value
            if word == "dim":
                return self.dim()
            if word == "const":
                return self.const()
            if word == "if":
                return self.if_statement()
            if word == "while":
                return self.while_statement()
            if word == "do":
                return self.do_statement()
            if word == "for":
                return self.for_statement()
            if word == "select":
                return self.select_statement()
            if word == "exit":
                return self.exit_statement()
            if word == "on":
                return self.on_error()
            if word == "call":
                self.advance()
                call = self.call_target()
                return CallStmt(call, tok.line)
            if word == "set":
                self.advance()
                name = self.identifier()
                self.expect_op("=", "Expected '='")
                return SetAssign(
                    name.text.lower(), name.text, self.expression(), tok.line
                )
            if word == "next":
                raise self.fail("Unexpected 'Next'")
            if word == "loop":
                raise self.fail("'loop' without 'do'")
            if word == "end":
                if self.loops:
                    raise self.fail("Expected statement")
                raise self.fail("Expected 'Sub'", self.tokens[self.pos + 1])
            if word == "stop":
                self.advance()
                return None
            if word in ("function", "sub", "class"):
                raise self.fail("Syntax error")
            raise self.fail("Expected statement")
        if tok.kind == "id":
            return self.simple_statement()
        raise self.fail("Expected statement")

    def simple_statement(self) -> Stmt | None:
        tok = self.advance()
        key = tok.text.lower()
        if key == "randomize" and not self.is_op("=", "(", "."):
            self.call_args_statement()
            return None
        if self.is_op("."):
            self.advance()
            member = self.identifier()
            if self.is_op("="):
                self.advance()
                return MemberAssign(
                    key,
                    tok.text,
                    member.text.lower(),
                    member.text,
                    self.expression(),
                    tok.line,
                )
            args = self.call_args_statement()
            return CallStmt(
                Member(key, tok.text, member.text.lower(), member.text, args), tok.line
            )
        if self.is_op("="):
            self.advance()
            return Assign(key, tok.text, self.expression(), tok.line)
        if self.is_op("("):
            self.advance()
            args = self.arguments()
            if self.is_op("="):
                self.advance()
                return IndexAssign(key, tok.text, args, self.expression(), tok.line)
            if not self.at_end_of_statement() and not self.is_kw("else"):
                raise self.fail("Expected end of statement")
            return CallStmt(Call(key, tok.text, args), tok.line)
        return CallStmt(Call(key, tok.text, self.call_args_statement()), tok.line)

    def call_args_statement(self) -> tuple[Expr, ...]:
        if self.at_end_of_statement() or self.is_kw("else", "end"):
            return ()
        args = [self.expression()]
        while self.is_op(","):
            self.advance()
            args.append(self.expression())
        return tuple(args)

    def call_target(self) -> Expr:
        tok = self.identifier()
        key = tok.text.lower()
        if self.is_op("."):
            self.advance()
            member = self.identifier()
            args: tuple[Expr, ...] = ()
            if self.is_op("("):
                self.advance()
                args = self.arguments()
            return Member(key, tok.text, member.text.lower(), member.text, args)
        args = ()
        if self.is_op("("):
            self.advance()
            args = self.arguments()
        return Call(key, tok.text, args)

    def dim(self) -> Stmt | None:
        self.advance()
        while True:
            name = self.identifier()
            key = name.text.lower()
            if key in self.locals or key in self.consts:
                raise self.fail("Name redefined", name)
            if self.is_op("("):
                raise self.fail("Arrays are not supported")
            self.locals.add(key)
            if not self.is_op(","):
                return None
            self.advance()

    def const(self) -> Stmt | None:
        self.advance()
        while True:
            name = self.identifier()
            key = name.text.lower()
            self.expect_op("=", "Expected '='")
            value = self.const_value()
            if key in self.locals or key in self.consts:
                raise self.fail("Name redefined", name)
            self.consts[key] = value
            if not self.is_op(","):
                if not self.at_end_of_statement():
                    raise self.fail("Expected literal constant")
                return None
            self.advance()

    def const_value(self) -> Value:
        sign = 1
        while self.is_op("-", "+"):
            if self.advance().value == "-":
                sign = -sign
        if self.is_op("("):
            self.advance()
            value = self.const_value()
            self.expect_op(")", "Expected ')'")
        else:
            tok = self.tok
            if tok.kind in ("num", "str"):
                self.advance()
                value = _token_value(tok)
            elif tok.kind == "kw" and tok.value in _WORD_VALUES:
                self.advance()
                value = _WORD_VALUES[str(tok.value)]
            elif self.at_end_of_statement():
                raise self.fail("Syntax error")
            else:
                raise self.fail("Expected literal constant")
        if sign < 0:
            if isinstance(value, str):
                raise self.fail("Expected literal constant")
            value = negate(value, Culture())
        return value

    # -- If

    def if_statement(self) -> Stmt:
        tok = self.advance()
        cond = self.expression()
        self.expect_kw("then", "Expected 'Then'")
        if self.tok.kind in ("nl", "eof"):
            return self.block_if(tok, cond)
        return self.line_if(tok, cond)

    def line_if(self, tok: Token, cond: Expr) -> Stmt:
        body = self.line_statements(first=True)
        orelse: tuple[Stmt, ...] = ()
        if self.is_kw("else"):
            self.advance()
            orelse = self.line_statements(first=False)
        if self.is_kw("end") and self.peek_kw(1, "if"):
            self.advance()
            self.advance()
        return If(((cond, body),), orelse, tok.line)

    def line_statements(self, *, first: bool) -> tuple[Stmt, ...]:
        stmts: list[Stmt] = []
        if (
            first
            and self.tok.kind != "colon"
            and (self.is_kw("else") or self.at_end_of_statement())
        ):
            raise self.fail("Expected statement")
        while True:
            if self.tok.kind == "colon":
                self.advance()
                continue
            if self.tok.kind in ("nl", "eof") or self.is_kw("else"):
                return tuple(stmts)
            if self.is_kw("end") and self.peek_kw(1, "if"):
                return tuple(stmts)
            stmt = self.inline_statement()
            if stmt is not None:
                stmts.append(stmt)
            if not (self.at_end_of_statement() or self.is_kw("else", "end")):
                raise self.fail("Expected end of statement")

    def inline_statement(self) -> Stmt | None:
        tok = self.tok
        if tok.kind == "kw" and tok.value in ("while", "do", "for", "select", "dim"):
            if tok.value == "dim":
                return self.dim()
            raise self.fail("Expected statement")
        if tok.kind == "kw" and tok.value == "if":
            self.advance()
            cond = self.expression()
            self.expect_kw("then", "Expected 'Then'")
            if self.tok.kind in ("nl", "eof"):
                raise self.fail("Expected statement")
            return self.line_if(tok, cond)
        return self.statement()

    def block_if(self, tok: Token, cond: Expr) -> Stmt:
        branches: list[tuple[Expr, tuple[Stmt, ...]]] = []
        body = self.block(lambda: self.is_kw("elseif", "else", "end"))
        branches.append((cond, body))
        orelse: tuple[Stmt, ...] = ()
        while True:
            if self.tok.kind == "eof":
                raise self.fail("Expected 'End'")
            if self.is_kw("elseif"):
                if not self.line_start:
                    raise self.fail("Must be first statement on the line")
                self.advance()
                c = self.expression()
                self.expect_kw("then", "Expected 'Then'")
                b = self.block(lambda: self.is_kw("elseif", "else", "end"))
                branches.append((c, b))
            elif self.is_kw("else"):
                self.advance()
                orelse = self.block(
                    lambda: self.is_kw("end", "elseif", "else"), end_if_follows=True
                )
                if not self.is_kw("end"):
                    raise self.fail("Expected 'End'")
            else:
                self.advance()
                self.expect_kw("if", "Expected 'If'")
                return If(tuple(branches), orelse, tok.line)

    # -- loops

    def while_statement(self) -> Stmt:
        tok = self.advance()
        cond = self.expression()
        self.loops.append("while")
        body = self.block(lambda: self.is_kw("wend"))
        self.loops.pop()
        self.expect_kw("wend", "Expected 'Wend'")
        return While(cond, body, tok.line)

    def do_statement(self) -> Stmt:
        tok = self.advance()
        pre: tuple[bool, Expr] | None = None
        if self.is_kw("while", "until"):
            until = self.advance().value == "until"
            pre = (until, self.expression())
        elif not self.at_end_of_statement():
            raise self.fail("Expected 'While', 'Until' or end of statement")
        self.loops.append("do")
        body = self.block(lambda: self.is_kw("loop"))
        self.loops.pop()
        self.expect_kw("loop", "Expected 'Loop'")
        post: tuple[bool, Expr] | None = None
        if self.is_kw("while", "until"):
            if pre is not None:
                raise self.fail("Expected end of statement")
            until = self.advance().value == "until"
            post = (until, self.expression())
        return DoLoop(pre, post, body, tok.line)

    def for_statement(self) -> Stmt:
        tok = self.advance()
        if self.is_kw("each"):
            raise self.fail("For Each is not supported")
        name = self.identifier()
        self.expect_op("=", "Expected '='")
        start = self.expression()
        self.expect_kw("to", "Expected 'To'")
        end = self.expression()
        step: Expr | None = None
        if self.tok.kind == "id" and str(self.tok.text).lower() == "step":
            self.advance()
            step = self.expression()
        self.loops.append("for")
        body = self.block(lambda: self.is_kw("next"))
        self.loops.pop()
        self.expect_kw("next", "Expected 'Next'")
        return For(name.text.lower(), name.text, start, end, step, body, tok.line)

    def exit_statement(self) -> Stmt:
        tok = self.advance()
        target = self.tok
        if target.kind == "kw" and target.value in ("for", "do"):
            self.advance()
            if target.value not in self.loops:
                raise self.fail("Invalid 'exit' statement", target)
            return Exit(str(target.value), tok.line)
        if target.kind == "kw" and target.value == "sub":
            self.advance()
            return Exit("sub", tok.line)
        if target.kind == "kw" and target.value in ("function", "property"):
            raise self.fail("Expected 'Sub'", target)
        raise self.fail(
            "Expected 'Do', 'For', 'Sub', 'Function', or 'Property'", target
        )

    def select_statement(self) -> Stmt:
        tok = self.advance()
        self.expect_kw("case", "Expected 'Case'")
        test = self.expression()
        self.end_statement()
        self.skip_separators()
        cases: list[tuple[tuple[Expr, ...], tuple[Stmt, ...]]] = []
        orelse: tuple[Stmt, ...] | None = None
        while True:
            if self.is_kw("end"):
                self.advance()
                self.expect_kw("select", "Expected 'Select'")
                return Select(test, tuple(cases), orelse, tok.line)
            if not self.is_kw("case"):
                raise self.fail(
                    "Expected 'End'" if orelse is not None else "Syntax error"
                )
            if orelse is not None:
                raise self.fail("Expected 'End'")
            self.advance()
            if self.is_kw("else"):
                self.advance()
                orelse = self.block(lambda: self.is_kw("case", "end"))
                continue
            values = [self.expression()]
            while self.is_op(","):
                self.advance()
                values.append(self.expression())
            body = self.block(lambda: self.is_kw("case", "end"))
            cases.append((tuple(values), body))
            if self.tok.kind == "eof":
                raise self.fail("Expected 'End'")

    def on_error(self) -> Stmt:
        tok = self.advance()
        if not (self.tok.kind == "id" and self.tok.text.lower() == "error"):
            raise self.fail("Syntax error")
        self.advance()
        if self.is_kw("resume"):
            self.advance()
            if not self.is_kw("next"):
                raise self.fail("Expected 'Next'")
            self.advance()
            return OnError(True, tok.line)
        if self.is_kw("goto"):
            self.advance()
            target = self.tok
            if target.kind == "num" and target.value == 0:
                self.advance()
                return OnError(False, tok.line)
            raise self.fail("Syntax error")
        raise self.fail("Syntax error")

    # -- expressions

    def expression(self) -> Expr:
        return self.imp()

    def imp(self) -> Expr:
        left = self.eqv()
        while self.is_kw("imp"):
            self.advance()
            left = Binary("imp", left, self.eqv())
        return left

    def eqv(self) -> Expr:
        left = self.xor()
        while self.is_kw("eqv"):
            self.advance()
            left = Binary("eqv", left, self.xor())
        return left

    def xor(self) -> Expr:
        left = self.or_()
        while self.is_kw("xor"):
            self.advance()
            left = Binary("xor", left, self.or_())
        return left

    def or_(self) -> Expr:
        left = self.and_()
        while self.is_kw("or"):
            self.advance()
            left = Binary("or", left, self.and_())
        return left

    def and_(self) -> Expr:
        left = self.not_()
        while self.is_kw("and"):
            self.advance()
            left = Binary("and", left, self.not_())
        return left

    def not_(self) -> Expr:
        if self.is_kw("not"):
            self.advance()
            return Unary("not", self.not_())
        return self.comparison()

    def comparison(self) -> Expr:
        left = self.concat()
        while self.tok.kind == "op" and self.tok.value in (
            "=",
            "<>",
            "<",
            ">",
            "<=",
            ">=",
        ):
            op = str(self.advance().value)
            left = Compare(op, left, self.concat())
        return left

    def concat(self) -> Expr:
        left = self.additive()
        while self.is_op("&"):
            self.advance()
            left = Binary("&", left, self.additive())
        return left

    def additive(self) -> Expr:
        left = self.modulo()
        while self.is_op("+", "-"):
            op = str(self.advance().value)
            left = Binary(op, left, self.modulo())
        return left

    def modulo(self) -> Expr:
        left = self.int_div()
        while self.is_kw("mod"):
            self.advance()
            left = Binary("mod", left, self.int_div())
        return left

    def int_div(self) -> Expr:
        left = self.multiplicative()
        while self.is_op("\\"):
            self.advance()
            left = Binary("\\", left, self.multiplicative())
        return left

    def multiplicative(self) -> Expr:
        left = self.power()
        while self.is_op("*", "/"):
            op = str(self.advance().value)
            left = Binary(op, left, self.power())
        return left

    def power(self) -> Expr:
        left = self.unary()
        while self.is_op("^"):
            self.advance()
            left = Binary("^", left, self.unary())
        return left

    def unary(self) -> Expr:
        if self.is_op("-", "+"):
            op = str(self.advance().value)
            return Unary(op, self.unary())
        if self.is_kw("not"):
            self.advance()
            return Unary("not", self.not_())
        return self.primary()

    def primary(self) -> Expr:
        tok = self.tok
        if tok.kind in ("num", "str"):
            self.advance()
            return Literal(_token_value(tok), True)
        if tok.kind == "kw":
            if tok.value in _WORD_VALUES:
                self.advance()
                return Literal(_WORD_VALUES[str(tok.value)], False)
            if tok.value in ("nothing", "me"):
                self.advance()
                return ObjectRef(str(tok.value))
            raise self.fail("Syntax error")
        if tok.kind == "op" and tok.value == "(":
            self.advance()
            inner = self.expression()
            self.expect_op(")", "Expected ')'")
            while self.is_op("("):
                # "(Int)(x)" calls Int; arguments after any other value are an error at run time.
                self.advance()
                args = self.arguments()
                if isinstance(inner, Name):
                    inner = Call(inner.key, inner.name, args)
                else:
                    inner = Apply(inner, args)
            return inner
        if tok.kind == "id":
            self.advance()
            key = tok.text.lower()
            if self.is_op("."):
                self.advance()
                member = self.identifier()
                args: tuple[Expr, ...] = ()
                if self.is_op("("):
                    self.advance()
                    args = self.arguments()
                return Member(key, tok.text, member.text.lower(), member.text, args)
            if self.is_op("("):
                self.advance()
                return Call(key, tok.text, self.arguments())
            return Name(key, tok.text)
        raise self.fail("Syntax error")

    def arguments(self) -> tuple[Expr, ...]:
        if self.is_op(")"):
            self.advance()
            return ()
        args = [self.expression()]
        while self.is_op(","):
            self.advance()
            args.append(self.expression())
        self.expect_op(")", "Expected ')'")
        return tuple(args)


def parse(text: str) -> Program:
    """The syntax tree of a procedure body; raises :class:`VBCompileError`.

    The body is parsed as if the ``End Sub`` of its procedure followed it, so a block left open
    reports the error the engine gives for that line. The text ends at a NUL character, which
    cuts off that ``End Sub`` as well."""
    cut = text.find("\0")
    tokens = tokenize(text if cut < 0 else text[:cut])
    if cut < 0:
        eof = tokens.pop()
        line = eof.line + 1
        tokens.extend(
            [
                Token("nl", None, "", eof.line, eof.column),
                Token("kw", "end", "End", line, 1),
                Token("kw", "sub", "Sub", line, 5),
                Token("eof", None, "", line, 8),
            ]
        )
    try:
        return _Parser(tokens).program()
    except RecursionError:
        raise VBCompileError("Out of memory", 1, 1) from None
