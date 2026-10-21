"""VBScript conformance probes; consumed by the oracle recorder and the tests.

Each probe is a procedure body with input and output variables, run like a parameter calculation.
The result format matches ``tools/vbscript_oracle/oracle.py``: ``ok|<outputs>`` with each output
as ``<TypeName>:<CStr>`` (or ``Null``, other than printable ASCII written as ``\\uXXXX``),
``compile|<number>|<text>``, ``error|<number>|<text>`` or ``timeout``.
"""

from __future__ import annotations

import math
from typing import NamedTuple

from xknxeditor.prod.script import ScriptAborted
from xknxeditor.prod.script.vbscript import (
    Culture,
    HostValue,
    VBCompileError,
    VBRuntimeError,
    run_variants,
)
from xknxeditor.prod.script.vbscript.variant import NULL, Value, to_text, type_name

CULTURES = {
    "en-US": Culture(".", ",", "$"),
    "de-DE": Culture(",", ".", "\u20ac"),
}


class Probe(NamedTuple):
    id: str
    code: str
    inputs: dict[str, HostValue]
    outputs: list[str]
    locale: str = "en-US"


def show(value: Value, culture: Culture, exact: bool = False) -> str:
    if value is NULL:
        return "Null"
    text = "".join(
        ch if 32 <= ord(ch) <= 126 and ch != "\\" else f"\\u{ord(ch):04X}"
        for ch in to_text(value, culture)
    )
    out = f"{type_name(value)}:{text}"
    if exact and isinstance(value, float):
        out += "#" + _bits(float(value))
    return out


def _bits(x: float) -> str:
    """Sign, binary exponent and the two 26-bit halves of the mantissa of a normal number."""
    if x == 0:
        return "0"
    mantissa, exponent = math.frexp(abs(x))
    m = int(mantissa * 2**53)
    sign = -1 if x < 0 else 1
    return f"{sign}:{exponent - 1}:{m >> 26}:{m & (2**26 - 1)}"


def run_probe(
    code: str,
    inputs: dict[str, HostValue],
    outputs: list[str],
    locale: str = "en-US",
    deadline: float = 2.0,
    exact: bool = False,
) -> str:
    """The result of one probe in the oracle's format."""
    culture = CULTURES[locale]
    try:
        values = run_variants(code, inputs, outputs, culture=culture, deadline=deadline)
    except VBCompileError as exc:
        return f"compile|{exc.number}|{exc.message}"
    except VBRuntimeError as exc:
        return f"error|{exc.number}|{exc.description}"
    except ScriptAborted:
        return "timeout"
    return "ok|" + "|".join(show(values[name], culture, exact) for name in outputs)


# ---------------------------------------------------------------------------------------------
# Probes

_VALUES = {
    "E": "Empty",
    "N": "Null",
    "T": "True",
    "F": "False",
    "By": "CByte(3)",
    "I": "CInt(-3)",
    "L": "CLng(70000)",
    "S": "CSng(1.5)",
    "D": "CDbl(-2.5)",
    "s4": '"4"',
    "sx": '"x"',
    "sT": '"True"',
    "s0": '""',
    "sD": '"2.5"',
}

_BINARY = ["+", "-", "*", "/", "\\", "Mod", "^", "&", "And", "Or", "Xor", "Eqv", "Imp"]
_COMPARE = ["=", "<>", "<", ">", "<=", ">="]


def _expr(pid: str, expr: str, setup: str = "", locale: str = "en-US") -> Probe:
    code = (setup + "\n" if setup else "") + f"r = {expr}"
    return Probe(pid, code, {}, ["r"], locale)


def _operator_probes() -> list[Probe]:
    out: list[Probe] = []
    names = list(_VALUES)
    for op in _BINARY + _COMPARE:
        for a in names:
            for b in names:
                setup = f"a = {_VALUES[a]}: b = {_VALUES[b]}"
                out.append(_expr(f"op:{a} {op} {b}", f"a {op} b", setup))
    for op in ["-", "Not ", "+"]:
        for a in names:
            out.append(_expr(f"unary:{op}{a}", f"{op}a", f"a = {_VALUES[a]}"))
    return out


_LITERALS = [
    "4",
    "4.5",
    '"4"',
    '"True"',
    '"x"',
    '""',
    "&h4",
    "40000",
    "(4)",
    "-4",
    "True",
]


def _literal_probes() -> list[Probe]:
    out: list[Probe] = []
    for a, value in _VALUES.items():
        for lit in _LITERALS:
            for op in ["=", "<"]:
                setup = f"a = {value}"
                out.append(_expr(f"lit:{a} {op} {lit}", f"a {op} {lit}", setup))
                out.append(_expr(f"lit:{lit} {op} {a}", f"{lit} {op} a", setup))
    pairs = [
        ('"10"', "10"),
        ('"abc"', "10"),
        ("10", '"9"'),
        ('"10"', '"9"'),
        ("10", "9"),
        ('"1e1"', "10"),
        ('" 10 "', "10"),
    ]
    for x, y in pairs:
        for op in _COMPARE:
            out.append(_expr(f"lit2:{x} {op} {y}", f"{x} {op} {y}"))
    return out


_FUNCTIONS_1 = [
    "CBool",
    "CByte",
    "CInt",
    "CLng",
    "CSng",
    "CDbl",
    "CStr",
    "Int",
    "Fix",
    "Abs",
    "Sgn",
    "Round",
    "Sqr",
    "Hex",
    "Oct",
    "IsNumeric",
    "IsEmpty",
    "IsNull",
    "Len",
    "UCase",
    "LCase",
    "Trim",
    "TypeName",
    "VarType",
    "Exp",
    "Log",
    "Atn",
    "Cos",
    "Sin",
    "Tan",
    "Space",
]


def _function_probes() -> list[Probe]:
    out: list[Probe] = []
    values = dict(_VALUES)
    values.update(
        {
            "big": "2147483647.5",
            "neg": "-32768.5",
            "h": '"&hFF"',
            "half": "2.5",
            "half3": "3.5",
            "tiny": "1E-300",
            "sp": '" 12 "',
        }
    )
    for func in _FUNCTIONS_1:
        for a, value in values.items():
            out.append(_expr(f"fn:{func}({a})", f"{func}(a)", f"a = {value}"))
    calls = [
        "Round(2.675, 2)",
        "Round(2.345, 2)",
        "Round(1.005, 2)",
        "Round(0.125, 2)",
        "Round(-2.5)",
        "Round(1234.5678, 3)",
        "Round(1.5, -1)",
        "Round(1.5, 16)",
        "Round(1.25, 15)",
        "Round(CSng(2.5))",
        "Round(True, 1)",
        "Round(7, 2)",
        'Round("2.55", 1)',
        'Left("abcdef", 3)',
        'Left("abc", 10)',
        'Left("abc", -1)',
        "Left(Null, 1)",
        "Left(12345, 2)",
        'Right("abcdef", 2)',
        'Right("abc", 0)',
        'Mid("abcdef", 2)',
        'Mid("abcdef", 2, 3)',
        'Mid("abcdef", 10, 3)',
        'Mid("abcdef", 0)',
        'Mid("abc", 1, -1)',
        'InStr("abcabc", "c")',
        'InStr(4, "abcabc", "c")',
        'InStr(1, "ABC", "b", 1)',
        'InStr(1, "ABC", "b", 0)',
        'InStr("abc", "")',
        'InStr(5, "abc", "")',
        'InStr(0, "abc", "a")',
        'InStr(Null, "a")',
        'InStrRev("abcabc", "b")',
        'InStrRev("abcabc", "b", 4)',
        'Replace("aaaa", "a", "x", 2, 2)',
        'Replace("aAa", "a", "x", 1, -1, 1)',
        'Replace("abc", "", "x")',
        'Replace("abc", "b", "")',
        'String(3, "xy")',
        "String(3, 65)",
        "String(2, 321)",
        'Space(0) & "|"',
        "Chr(65) & Chr(97)",
        "Chr(-1)",
        "Chr(256)",
        "ChrW(8364)",
        'Asc("A")',
        'Asc("")',
        'AscW("€")',
        'StrComp("a", "B")',
        'StrComp("a", "B", 1)',
        'StrComp("a", Null)',
        'StrReverse("abc")',
        "Hex(-1)",
        "Hex(-32769)",
        "Hex(65536)",
        "Hex(4294967295)",
        "Hex(&hFFFF)",
        "Hex(CByte(255))",
        "Oct(-1)",
        "Oct(8)",
        "Len(1.5)",
        "Len(True)",
        'LTrim("  a  ") & "|"',
        'RTrim("  a  ") & "|"',
        'UCase("straße")',
        'LCase("ÄBC")',
        "Sqr(-1)",
        "Log(0)",
        "Exp(1000)",
        "Int(-2.5)",
        "Fix(-2.5)",
        'Int("-2.5")',
        "Abs(-32768)",
        "Abs(CInt(-32768))",
        "Abs(-2147483648)",
        "CInt(32767.5)",
        "CInt(-32768.5)",
        "CLng(2147483647.5)",
        "CLng(-2147483648.5)",
        "CByte(255.5)",
        "CByte(-0.5)",
        "CByte(True)",
        'CBool("yes")',
        'CBool(" true ")',
        'CBool("FALSE")',
        'CBool("0")',
        "CSng(1E+39)",
        'CDbl("1e400")',
        'IsNumeric("&h10")',
        'IsNumeric("1e")',
        'IsNumeric("(5)")',
        'IsNumeric("5-")',
        'IsNumeric("-")',
        'IsNumeric(".")',
        'IsNumeric("1,2")',
        'IsNumeric("1 2")',
        "TypeName(vbTrue)",
        "vbCrLf",
        "vbLong + 1",
        "Len(vbTab)",
        "Int",
        "Len()",
        'Left("a")',
        "Foo(1)",
        "Err.Number",
        "Round(1.25, 17)",
        "Round(1.25, 18)",
        "Round(1.25, 19)",
        "Round(1.25, 20)",
        "Round(1.25, 21)",
        "Round(1E300, 16)",
        "Round(1E300, 17)",
        "Round(123456.789, 18)",
        "Round(0.000123456789, 19)",
        "Round(1.5, 22)",
        'Round(2.5, "1")',
        "Round(2.5, Null)",
        'CBool("TRUE")',
        'CBool(" true")',
        'CBool("true ")',
        'IsNumeric("1e400")',
        'CDbl("-1e400")',
        'CDbl("1e-400")',
    ]
    for i, call in enumerate(calls):
        out.append(_expr(f"call:{i}:{call}", call))
    return out


_NUMBERS = [
    "1.5",
    "-0.5",
    "1/3",
    "2/3",
    "0.1+0.2",
    "1E15",
    "1E14",
    "999999999999999.4",
    "999999999999999.6",
    "123456789012345.5",
    "1E-5",
    "1E-15",
    "1.2E-15",
    "1E-16",
    "1.23456789012345E-7",
    "0.000123456789012345678",
    "-1.5E-20",
    "12345678901234567890",
    "1.7976931348623157E+308",
    "4.94065645841247E-324",
    "CSng(1)/3",
    "CSng(16777217)",
    "CSng(1E7)",
    "CSng(1234567)",
    "CSng(1.2E-7)",
    "CSng(1E-7)",
    "CSng(0.1)*3",
    "2^31",
    "2^0.5",
    "100",
    "0",
    "-0",
    "0*-1",
    "1E308*10",
    "&h7FFF",
    "&h8000",
    "&h8001",
    "&hFFFF",
    "&h10000",
    "&hFFFF8000",
    "&hFFFF8001",
    "&h7FFFFFFF",
    "&h80000000",
    "&hFFFFFFFF",
    "&o777",
    "&o100000",
    "&o177777",
    "&17",
    "&hFF&",
    "32767",
    "32768",
    "-32768",
    "2147483647",
    "2147483648",
    "08",
    "1.",
    ".5",
    "1e+2",
]

_TEXTS = [
    "1.5",
    "1,5",
    "1.234,5",
    "1,234.5",
    " 12 ",
    "$1",
    "+5",
    "-5",
    "5-",
    "(5)",
    "1e3",
    "1E+3",
    "1d3",
    "&o17",
    "&hFFFF",
    "&hFFFFFFFF",
    "&h1FFFFFFFF",
    ".5",
    "5.",
    "",
    "Infinity",
    "1,,2",
    "12,34",
    "1e",
    "abc",
    "0x10",
    "\t7",
    "1$",
    "-$1",
    "$-1",
    "$ 1",
    "1 $",
    "\u20ac1",
    "1 \u20ac",
    "1\u20ac",
    "\u00a31",
    "1,5,",
    ",5",
    "1.5.5",
    "1.2,3,4",
    "- 5",
    "5 -",
    "( 5 )",
    "-(5)",
    "(-5)",
    "1e+",
    "1e 5",
    "1 e5",
    "&H",
    " &h10 ",
    "&h-1",
    "+&h10",
    "&o8",
    "&8",
    "&hFFFFFFFFFFFFFFFF",
    "&h10000000000000000",
    "&h8000000000000000",
    "&o1777777777777777777777",
    "1\u00a0",
    "\u00001",
    "1\u0000",
    "\u00a01",
    "0001",
    "1e0010",
    "%5",
    "5%",
    "1,2.3",
    "1.",
    "-.5",
    "+-5",
    "--5",
    "5+",
    "(5",
    "5)",
    "$$1",
    "1e-400",
    "1E309",
    "TRUE",
    "true ",
]


def _number_probes() -> list[Probe]:
    out: list[Probe] = []
    for locale in CULTURES:
        for i, number in enumerate(_NUMBERS):
            out.append(
                Probe(
                    f"num:{locale}:{i}:{number}",
                    f"r = {number}\ns = CStr(r)",
                    {},
                    ["r", "s"],
                    locale,
                )
            )
        for i, text in enumerate(_TEXTS):
            out.append(
                Probe(
                    f"text:{locale}:{i}:{text}",
                    "r = CDbl(t)\ns = IsNumeric(t)",
                    {"t": text},
                    ["r", "s"],
                    locale,
                )
            )
            out.append(
                Probe(
                    f"textop:{locale}:{i}:{text}",
                    "r = t + 1\ns = t And 1\nu = (t = 5)\nv = Int(t)",
                    {"t": text},
                    ["r", "s", "u", "v"],
                    locale,
                )
            )
        for value in [1.5, -0.25, 1e20, 2147483648, 3, 0.1]:
            out.append(
                Probe(
                    f"input:{locale}:{value}",
                    'r = x & ""\ns = x * 2\nt = TypeName(x)\nu = (x = "1.5")',
                    {"x": value},
                    ["r", "s", "t", "u"],
                    locale,
                )
            )
    return out


_STATEMENTS: list[tuple[str, str]] = [
    ("if line", "If a > 1 Then r = 1 Else r = 2"),
    ("if line end if", "If a > 1 Then r = 1 End If"),
    ("if line else end if", "If a < 1 Then r = 1 Else r = 2 End If"),
    ("if line multi", "If a > 1 Then r = 1: s = 2 Else r = 3: s = 4"),
    ("if line colon end", "If a > 1 Then r = 1 : End If : s = 5"),
    ("if line colon first", "If a > 1 Then : r = 2"),
    ("if line nested", "If a < 1 Then r = 1 Else If a = 5 Then r = 2 Else r = 3"),
    ("if line empty else", "If a > 1 Then r = 1 Else"),
    ("if line colon else", "If a < 1 Then r = 1: Else r = 2"),
    (
        "if block",
        "If a > 1 Then\n r = 1\nElseIf a > 0 Then\n r = 2\nElse\n r = 3\nEnd If",
    ),
    (
        "if block elseif",
        "If a > 10 Then\n r = 1\nElseIf a > 4 Then r = 2\nElse r = 3\nEnd If",
    ),
    ("if block else colon", "If a > 10 Then\n r = 1\nElse : r = 4\nEnd If"),
    ("if block else end colon", "If a > 10 Then\n r = 1\nElse r = 5 : End If"),
    ("if block comment", "If a > 1 Then ' note\n r = 1\nEnd If"),
    ("if block empty", "If a > 1 Then\nEnd If\nr = 7"),
    ("if then newline colon", "If a > 10 Then :\n r = 2\nEnd If"),
    ("if block same line else", "If a > 1 Then\n r = 1 Else r = 2\nEnd If"),
    ("if block colon elseif", "If a > 1 Then\n r = 1 : ElseIf a Then\n r = 2\nEnd If"),
    ("if endif", "If a > 1 Then\n r = 1\nEndIf"),
    ("if missing end", "If a > 1 Then\n r = 1"),
    ("if then missing", "If a > 1\n r = 1\nEnd If"),
    ("if null", "If Null Then r = 1 Else r = 2"),
    ("if string", 'If "True" Then r = 1 Else r = 2'),
    ("if string bad", 'If "abc" Then r = 1 Else r = 2'),
    ("if empty", "If x Then r = 1 Else r = 2"),
    ("if double", "If 0.4 Then r = 1 Else r = 2"),
    ("while", "While a < 10\n a = a + 3\nWend\nr = a"),
    ("while colon", "While a < 10 : a = a + 1 : Wend : r = a"),
    (
        "while nested",
        "i = 0\nWhile i < 3\n j = 0\n While j < 2\n  r = r & i & j\n  j = j + 1\n Wend\n i = i + 1\nWend",
    ),
    ("do while", "Do While a < 20\n a = a * 2\nLoop\nr = a"),
    ("do until", "Do Until a > 20\n a = a * 2\nLoop\nr = a"),
    ("do loop while", "Do\n a = a + 1\nLoop While a < 3\nr = a"),
    ("do loop until", "Do\n a = a + 1\nLoop Until a > 7\nr = a"),
    ("do exit", "Do\n a = a + 1\n If a > 12 Then Exit Do\nLoop\nr = a"),
    ("do both", "Do While a < 3\n a = a + 1\nLoop Until a > 4"),
    ("do no cond", "Do\n a = a + 1\n If a = 9 Then Exit Do\nLoop\nr = a"),
    ("for", "For i = 1 To a\n r = r & i\nNext\ns = i"),
    ("for step", 'For i = 10 To 1 Step -3\n r = r & i & ","\nNext\ns = i'),
    ("for frac", 'For i = 0 To 1 Step 0.25\n r = r & i & ";"\nNext\ns = TypeName(i)'),
    ("for none", "For i = 5 To 1\n r = 1\nNext\ns = i"),
    ("for exit", "For i = 1 To 10\n If i = a Then Exit For\nNext\nr = i"),
    ("for modify", "For i = 1 To 10\n i = i + 2\n r = r & i\nNext"),
    (
        "for types",
        "For i = 1 To 3\nNext\nr = TypeName(i)\nFor j = CLng(1) To 2\nNext\ns = TypeName(j)",
    ),
    (
        "for nested exit",
        "For i = 1 To 3\n For j = 1 To 3\n  If j = 2 Then Exit For\n  r = r & i & j\n Next\nNext",
    ),
    ("for next name", "For i = 1 To 3\nNext i"),
    ("for string bounds", 'For i = "1" To "3"\n r = r & i\nNext\ns = TypeName(i)'),
    (
        "select",
        "Select Case a\nCase 1, 2\n r = 1\nCase 5\n r = 5\nCase Else\n r = 9\nEnd Select",
    ),
    ("select else", "Select Case a + 1\nCase 1\n r = 1\nCase Else\n r = 9\nEnd Select"),
    (
        "select string",
        'Select Case "5"\nCase 5\n r = "num"\nCase Else\n r = "else"\nEnd Select',
    ),
    (
        "select var string",
        'x = "5"\nSelect Case x\nCase 5\n r = "num"\nCase Else\n r = "else"\nEnd Select',
    ),
    (
        "select case string",
        'Select Case a\nCase "5"\n r = "str"\nCase Else\n r = "else"\nEnd Select',
    ),
    ("select inline", "Select Case a\nCase 1 : r = 1\nCase 5 : r = 5\nEnd Select"),
    ("select same line", "Select Case a\nCase 5 r = 6\nEnd Select"),
    ("select none", "Select Case a\nEnd Select\nr = 1"),
    ("select is", "Select Case a\nCase Is > 2\n r = 1\nEnd Select"),
    ("select range", "Select Case a\nCase 1 To 9\n r = 1\nEnd Select"),
    ("select stmt before case", "Select Case a\n r = 1\nCase 5\nEnd Select"),
    ("select case after else", "Select Case a\nCase Else\n r = 1\nCase 5\nEnd Select"),
    (
        "select null",
        "Select Case Null\nCase Null\n r = 1\nCase Else\n r = 2\nEnd Select",
    ),
    (
        "select float",
        "Select Case a / 2\nCase 2.5\n r = 1\nCase Else\n r = 2\nEnd Select",
    ),
    ("dim list", "Dim x, y, z\nx = 1: y = 2: z = 3\nr = x + y + z"),
    ("dim twice", "Dim x\nDim x"),
    ("dim trailing comma", "Dim x,"),
    ("dim alias", "Dim r\nr = 5"),
    ("dim after use", "r = 1\nx = 2\nDim x\ns = x"),
    ("const", 'Const c = 5, d = "x"\nr = c & d'),
    ("const neg", "Const c = -5\nr = c * 2"),
    ("const hex", "Const c = &hFF\nr = c"),
    ("const expr", "Const c = 1 + 2"),
    ("const assign", "Const c = 1\nc = 2"),
    ("const redefined", "x = 1\nDim x\nConst x = 2"),
    ("const before use", "r = c\nConst c = 7"),
    ("implicit", "x = a * 2\nr = x + y"),
    ("case insensitive", "R = A + 1\nIF r > 0 THEN s = 1 END IF"),
    ("colons", "r = 1: s = 2::r = r + s"),
    ("continuation", "r = 1 + _\n  2 + _\n  3"),
    ("continuation no space", "r = 1 +_\n 2"),
    ("rem", "Rem comment\nr = 1 Rem another\ns = 2: Rem third"),
    ("comment", "r = 1 ' comment\n' line\ns = 2"),
    ("brackets", "[my var] = 5\nr = [my var] + 1"),
    ("string escape", 'r = "a""b"'),
    ("string unterminated", 'r = "abc'),
    ("invalid char", "r = 1 @ 2"),
    ("invalid number", "r = 1.5e"),
    ("hex too long", "r = &hFFFFFFFFF"),
    ("syntax", "r = 1 +"),
    ("paren", "r = (1"),
    ("paren extra", "r = 1)"),
    ("operators alt", "r = (1 >< 2) & (1 => 2) & (1 =< 2)"),
    ("operator spaced", "r = 1 < = 2"),
    ("unary chain", "r = 5 + + - - 1"),
    ("precedence", "r = 2 + 3 * 4 ^ 2 / 8 \\ 2 Mod 3 & 1"),
    ("neg power", "r = -2 ^ 2\ns = 2 ^ -2 ^ 2"),
    ("not operand", "r = 1 + Not 0 + 5\ns = Not 1 = 2\nt = 1 & Not 0 & 2"),
    (
        "logic precedence",
        "r = 3 And 5 Or 8\ns = 3 Or 5 And 8\nt = 3 Xor 5 Or 1\nu = 1 Eqv 2 Imp 3",
    ),
    ("compare chain", "r = 1 < 2 < 3\ns = 3 > 2 > 1"),
    (
        "int overflow promote",
        "a = 32767: a = a + 1: r = TypeName(a)\nb = 2147483647: b = b + 1: s = TypeName(b)",
    ),
    ("int literal mult", "r = 200 * 200\ns = 256 * 256 * 256 * 256"),
    (
        "byte promote",
        "r = CByte(200) + CByte(100)\ns = CByte(5) - CByte(10)\nt = CByte(16) * CByte(16)",
    ),
    ("division", "r = 0 / 0"),
    ("division by zero", "r = 1 / 0"),
    ("int division by zero", "r = 1 \\ 0"),
    ("mod by zero", "r = 1 Mod 0"),
    ("overflow double", "r = 1E308 * 10"),
    ("power invalid", "r = (-8) ^ (1 / 3)"),
    ("power zero", "r = 0 ^ -1"),
    ("int div overflow", "r = 2147483648 \\ 2"),
    (
        "on error",
        'On Error Resume Next\nr = 1 / 0\ns = Err.Number & " " & Err.Description\nt = 5',
    ),
    ("on error if", 'On Error Resume Next\nIf "a" Then r = 1 Else r = 2'),
    (
        "on error elseif",
        'On Error Resume Next\nIf 0 Then\n r = 1\nElseIf "a" Then\n r = 2\nElse\n r = 3\nEnd If',
    ),
    ("on error assign", 'On Error Resume Next\nr = 7\nr = CInt("x")\ns = Err.Number'),
    (
        "on error loop until",
        'On Error Resume Next\nDo\n a = a + 1\nLoop Until "a"\nr = a',
    ),
    (
        "on error do until",
        'On Error Resume Next\nDo Until "a"\n a = a + 1\n If a > 8 Then Exit Do\nLoop\nr = a',
    ),
    (
        "on error for",
        'On Error Resume Next\nFor i = 1 To "a"\n r = r & i\nNext\ns = Err.Number',
    ),
    (
        "on error select",
        'On Error Resume Next\nSelect Case "a" + 1\nCase 1\n r = 1\nCase Else\n r = 3\nEnd Select\ns = Err.Number',
    ),
    (
        "on error case",
        'On Error Resume Next\nSelect Case 2\nCase "a" + 1\n r = 1\nCase 2\n r = 2\nEnd Select',
    ),
    (
        "on error goto 0",
        "On Error Resume Next\nr = 1 / 0\nOn Error GoTo 0\ns = Err.Number\nt = 1 / 0",
    ),
    ("on error clear", "On Error Resume Next\nr = 1 / 0\nErr.Clear\ns = Err.Number"),
    (
        "on error again",
        "On Error Resume Next\nr = 1 / 0\nOn Error Resume Next\ns = Err.Number",
    ),
    ("on error line if", 'On Error Resume Next\nIf 1 Then r = 1: s = "a" + 1: r = 2'),
    (
        "err raise",
        'On Error Resume Next\nErr.Raise 1000, "src", "my error"\nr = Err.Description\ns = Err.Number',
    ),
    ("err raise known", "On Error Resume Next\nErr.Raise 6\nr = Err.Description"),
    ("err raise uncaught", "Err.Raise 11"),
    ("err default", "On Error Resume Next\nr = 1 / 0\ns = Err"),
    ("exit sub", "r = 1\nExit Sub\nr = 2"),
    ("exit for outside", "Exit For"),
    ("exit do outside", "Exit Do"),
    ("exit function", "Exit Function"),
    ("unexpected next", "Next"),
    ("unexpected loop", "Loop"),
    ("unexpected wend", "Wend"),
    ("unexpected end if", "End If"),
    ("option explicit", "Option Explicit"),
    ("call undefined", "Foo"),
    ("call undefined args", "r = Foo(1, 2)"),
    ("call builtin statement", 'Call Len("x")\nr = 1'),
    ("index scalar", "x = 1\nr = x(0)"),
    ("index assign", "x = 1\nx(0) = 2"),
    ("builtin no parens", "r = Int"),
    ("builtin wrong args", 'r = Left("a")'),
    ("assign builtin", "Len = 5"),
    ("assign constant", "vbCrLf = 5"),
    ("set", "Set x = 1"),
    ("member", "x.y = 1"),
    ("member read", "r = x.y"),
    ("not literal", 'x = "10"\nr = (x = -10)\ns = (x = (10))\nt = (x = 5 + 5)'),
    ("empty compare", 'r = (x = 0) & (x = "") & (x = "0") & (x < 1) & (x = Empty)'),
    ("output read before write", 'r = r + 1\ns = s & "x"'),
    ("output types", 'r = True\ns = 1.5\nt = "x"\nu = Null\nv = Empty'),
    ("long line", "r = " + " + ".join(str(i) for i in range(200))),
    ("expr then", "r = Then"),
    ("expr paren", "r = )"),
    ("expr star", "r = *2"),
    ("expr comma", "r = ,"),
    ("expr is", "r = Is"),
    ("expr end", "r = End"),
    ("expr dim", "r = Dim"),
    ("expr not", "r = Not"),
    ("expr nothing", "r ="),
    ("expr open", "r = ("),
    ("expr minus", "r = -"),
    ("expr equals", "r = = 1"),
    ("expr keyword operand", "r = 1 + If"),
    ("expr nothing kw", "r = Nothing"),
    ("expr me", "r = Me"),
    ("unterminated while", "While a < 3\n a = a + 1"),
    ("unterminated for", "For i = 1 To 3\n r = i"),
    ("unterminated do", "Do\n a = a + 1"),
    ("unterminated select", "Select Case a\nCase 1\n r = 1"),
    ("unterminated else", "If a Then\n r = 1\nElse\n r = 2"),
    ("end sub", "r = 1\nEnd Sub"),
    ("end function", "End Function"),
    ("const missing value", "Const c ="),
    ("const string op", 'Const c = "a" & "b"'),
    ("const paren", "Const c = (5)\nr = c"),
    ("const true", "Const c = True\nr = c"),
    ("const empty", "Const c = Empty\nr = TypeName(c)"),
    ("const neg string", 'Const c = -"5"'),
    ("const double neg", "Const c = - - 5\nr = c"),
    (
        "err assign",
        "On Error Resume Next\nErr.Number = 5\nr = Err.Number\ns = Err.Description",
    ),
    ("member assign err", "Err.Foo = 1"),
    ("member call", "x.Foo"),
    ("for each", "For Each x In y\nNext"),
    ("dim array", "Dim x(3)\nx(1) = 5\nr = x(1)"),
    ("redim", "ReDim x(3)"),
    ("with", "With x\nEnd With"),
    ("function def", "Function F()\nEnd Function"),
    ("sub def", "Sub S()\nEnd Sub"),
    ("randomize", "Randomize"),
    ("goto", "GoTo 10"),
    ("on error goto label", "On Error GoTo foo"),
    ("stop", "Stop\nr = 1"),
    ("next count", "For i = 1 To 2\nNext i, j"),
    ("cast int", "r = (Int)(a * 2.5)\ns = (CInt) (a / 2)\nt = ((Int))(a - 0.5) + 1"),
    ("cast len", 'r = (Len)("abc")'),
    ("cast undefined", "r = (Foo)(1)"),
    ("cast variable", "x = 5\nr = (x)(1)"),
    ("cast literal", "r = (1)(2)"),
    ("paren name", "r = (Int)"),
    ("paren variable", "x = 4\nr = (x)"),
    ("then end if inline", "If a Then\n r = 1 End If"),
    ("else end if inline", "If a > 9 Then\n r = 1\nElse\n r = 2 End If"),
    ("elseif end if inline", "If a > 9 Then\n r = 1\nElseIf a Then\n r = 3 End If"),
    (
        "else end if inline nested",
        "If a > 9 Then\n r = 1\nElse\n If a Then s = 1 End If\n r = 2 End If",
    ),
    ("wend inline", "While a < 9\n a = a + 1 Wend"),
    ("next inline", "For i = 1 To 2\n r = i Next"),
    ("loop inline", "Do\n a = a + 1 Loop Until a > 9"),
    ("end select inline", "Select Case a\nCase 5\n r = 1 End Select"),
    ("double end if", "If a Then r = 1 Else s = 2 End If End If"),
    ("top end if", "r = 1 End If"),
    ("blank nbsp", "r = 1\u00a0+ 1"),
    ("blank vtab", "r = 1\v+ 1"),
    ("blank formfeed", "r = 1\f+ 1"),
    ("blank nul", "r = 1\u0000+ 1"),
    ("digit arabic", "r = \u0661"),
    ("letter umlaut", "\u00e4 = 1"),
    ("cr line end", "r = 1\rs = 2"),
    ("crlf line end", "r = 1\r\ns = 2"),
    ("unicode string", 'r = "\u20ac\u00e4"\ns = Len(r)'),
]


def _statement_probes() -> list[Probe]:
    out: list[Probe] = []
    for name, code in _STATEMENTS:
        for a in (5, 0):
            out.append(
                Probe(f"stmt:{name}:{a}", code, {"a": a}, ["r", "s", "t", "u", "v"])
            )
    out.append(Probe("stmt:runaway", "While True\nWend", {}, ["r"]))
    out.append(
        Probe("stmt:runaway error", 'On Error Resume Next\nWhile "a"\nWend', {}, ["r"])
    )
    return out


# Calculations of the kind products use: encoded times, bit fields and 2-byte floats.
_CALCULATIONS: list[
    tuple[str, str, list[str], list[str], list[dict[str, HostValue]]]
] = [
    (
        "time base encode",
        "Dim Base, Shift, Mant\nBase = 10\nShift = 0\nIf ms < 100 Then ms = 100\n"
        "Mant = ms / Base\nWhile Mant > 255 And Shift < 15\n Shift = Shift + 1\n"
        " Mant = ms / (Base * 2 ^ Shift)\nWend\nMant = CInt(Mant)\nraw = Shift * 2 ^ 8 + Mant\n",
        ["ms"],
        ["raw"],
        [{"ms": v} for v in (0, 99, 100, 2550, 2561, 65535, 123456, 2147483647)],
    ),
    (
        "time base decode",
        "Dim Base, Shift, Mant\nBase = 10\nShift = raw And &hFF00\nShift = Int(Shift / 2 ^ 8)\n"
        "Mant = raw And 255\nms = Mant * (Base * 2 ^ Shift)\n",
        ["raw"],
        ["ms"],
        [{"raw": v} for v in (0, 255, 256, 3840, 65535)],
    ),
    (
        "bit fields",
        "flags = 0\nIf a Then flags = flags Or 1\nIf b Then flags = flags Or 2\n"
        "flags = flags Or (c * 16)\nflags = flags And &hFF",
        ["a", "b", "c"],
        ["flags"],
        [
            {"a": 0, "b": 0, "c": 0},
            {"a": 1, "b": 0, "c": 3},
            {"a": 1, "b": 1, "c": 15},
            {"a": 0, "b": 1, "c": 16},
        ],
    ),
    (
        "bit split",
        "a = flags And 1\nb = (flags And 2) / 2\nc = (flags And &hF0) \\ 16\nd = Not flags And 255",
        ["flags"],
        ["a", "b", "c", "d"],
        [{"flags": v} for v in (0, 1, 2, 3, 255, 170)],
    ),
    (
        "dpt9 encode",
        "On Error Resume Next\nv = temp * 0.01\nIf v > 670760.96 Then\n hi = &h7F: lo = &hFF\n"
        "ElseIf v < -671088.64 Then\n hi = &hF8: lo = 0\nElse\n e = 0\n"
        " If v >= 0 Then\n  While 2 ^ e * 20.47 < v And e < 15\n   e = e + 1\n  Wend\n"
        " Else\n  While 2 ^ e * 20.48 < -v And e < 15\n   e = e + 1\n  Wend\n End If\n"
        " m = CLng(v / (2 ^ e * 0.01))\n hi = (e * 8) Or ((m And &h700) / 256)\n"
        " If v < 0 And m <> 0 Then hi = hi Or &h80\n lo = m And &hFF\nEnd If\nraw = hi * 256 + lo",
        ["temp"],
        ["raw"],
        [
            {"temp": v}
            for v in (0, 2150, -2150, 2047, 100000, -27300, 67076096, 2147483647)
        ],
    ),
    (
        "dpt9 decode text",
        "On Error Resume Next\nv = 0.01\nIf raw And &h8000 Then\n t = &hF800\n"
        " t = t Or (raw And &h7FF)\n t = t - 1\n t = Not t\n v = v * t\n"
        " v = v * 2 ^ ((raw And &h7800) / 2048)\n v = v * -1\nElse\n"
        " v = v * (raw And &h7FF)\n v = v * 2 ^ ((raw And &h7800) / 2048)\nEnd If\ntext = CStr(v)",
        ["raw"],
        ["text"],
        [{"raw": v} for v in (0, 0x0C34, 0x8C34, 0x7FFF, 0xF800, 1)],
    ),
    (
        "percent scaling",
        "If pct > 100 Then pct = 100\nraw = Round(pct * 255 / 100)\nback = Int(raw * 100 / 255 + 0.5)",
        ["pct"],
        ["raw", "back"],
        [{"pct": v} for v in (0, 1, 50, 99, 100, 150)],
    ),
    (
        "select scene",
        "Select Case mode\nCase 0\n out = 0\nCase 1, 2\n out = mode * 10 + scene\n"
        "Case 3\n out = &h80 Or scene\nCase Else\n out = 255\nEnd Select",
        ["mode", "scene"],
        ["out"],
        [{"mode": m, "scene": s} for m in (0, 1, 2, 3, 7) for s in (0, 5)],
    ),
    (
        "text number",
        "If IsNumeric(text) Then\n v = CDbl(text)\nElse\n v = 0\nEnd If\nraw = CLng(v * 10)\nback = CStr(raw / 10)",
        ["text"],
        ["raw", "back"],
        [{"text": t} for t in ("21.5", "21,5", "abc", "", "-3", "1e2", "999999999")],
    ),
    (
        "hysteresis pair",
        "lower = setpoint - hyst / 2\nupper = setpoint + hyst / 2\nIf lower < 0 Then lower = 0\n"
        "lo_raw = CInt(lower * 10)\nhi_raw = CInt(upper * 10)",
        ["setpoint", "hyst"],
        ["lo_raw", "hi_raw"],
        [{"setpoint": s, "hyst": h} for s in (0, 21, 3276) for h in (0, 1, 3)],
    ),
]


def _calculation_probes() -> list[Probe]:
    out: list[Probe] = []
    for name, code, _inputs, outputs, sets in _CALCULATIONS:
        for i, values in enumerate(sets):
            for locale in CULTURES:
                out.append(
                    Probe(f"calc:{name}:{i}:{locale}", code, values, outputs, locale)
                )
    return out


def probes() -> list[Probe]:
    return [
        *_operator_probes(),
        *_literal_probes(),
        *_function_probes(),
        *_number_probes(),
        *_statement_probes(),
        *_calculation_probes(),
    ]
