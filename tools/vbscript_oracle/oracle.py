"""Run VBScript procedure bodies in the Windows VBScript engine (32-bit cscript).

Each case is compiled with ``ExecuteGlobal`` as the body of its own ``Sub``, the way the
calculation code runs: every alias is a global variable, the inputs are set before the call and
the outputs are read afterwards. A second ``Sub`` calls it and marks a normal return (an error
the body handled with ``On Error Resume Next`` stays in ``Err`` but does not reach the caller).
All globals of the batch are reset to Empty before each case, so a name another case declared
behaves like an undeclared local.

Results are ``ok|<outputs>``, ``compile|<number>|<text>``, ``error|<number>|<text>`` or
``timeout``; an output is ``<TypeName>:<CStr>`` or ``Null``, with characters outside printable
ASCII (and the backslash) written as ``\\uXXXX``.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CSCRIPT32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "SysWOW64" / "cscript.exe"

HostValue = bool | int | float | str | None

LCIDS = {"en-US": 1033, "de-DE": 1031, "en-GB": 2057, "nl-NL": 1043, "de-CH": 2055}


@dataclass
class Case:
    id: str
    code: str
    inputs: dict[str, HostValue]
    outputs: list[str]
    locale: str = "en-US"
    extra: dict[str, object] = field(default_factory=dict[str, object])
    exact: bool = False  # append the bits of Single and Double outputs


_HARNESS = r"""
Dim xk_out: Set xk_out = WScript.StdOut
Dim xk_known: Set xk_known = CreateObject("Scripting.Dictionary")
xk_known.CompareMode = 1
Dim xk_reset: xk_reset = ""
Dim xk_done
Function xk_f(v)
  If IsNull(v) Then
    xk_f = "Null"
  Else
    xk_f = TypeName(v) & ":" & xk_escape(CStr(v))
  End If
End Function
Function xk_escape(s)
  Dim i, c, out
  out = ""
  For i = 1 To Len(s)
    c = AscW(Mid(s, i, 1))
    If c < 0 Then c = c + 65536
    If c < 32 Or c > 126 Or c = 92 Then
      out = out & "\u" & Right("000" & Hex(c), 4)
    Else
      out = out & ChrW(c)
    End If
  Next
  xk_escape = out
End Function
Function xk_bits(v)
  Dim a, e, m, hi
  xk_bits = xk_f(v)
  If VarType(v) <> 4 And VarType(v) <> 5 Then Exit Function
  a = Abs(CDbl(v))
  If a = 0 Then
    xk_bits = xk_bits & "#0"
    Exit Function
  End If
  e = Int(Log(a) / Log(2))
  If a / 2 ^ e >= 2 Then e = e + 1
  If a / 2 ^ e < 1 Then e = e - 1
  m = a / 2 ^ e * 2 ^ 52
  hi = Fix(m / 2 ^ 26)
  xk_bits = xk_bits & "#" & Sgn(v) & ":" & e & ":" & hi & ":" & (m - hi * 2 ^ 26)
End Function
Sub xk_run(id, lcid, names, body, setup, outs)
  Dim n, decl
  On Error Resume Next
  SetLocale lcid
  decl = ""
  For Each n In Split(names, ",")
    If n <> "" Then
      If Not xk_known.Exists(n) Then
        xk_known.Add n, 1
        decl = decl & "Dim " & n & vbLf
        xk_reset = xk_reset & n & " = Empty" & vbLf
      End If
    End If
  Next
  Err.Clear
  ExecuteGlobal decl
  If Err.Number <> 0 Then
    xk_out.WriteLine id & "|compile|" & Err.Number & "|" & Err.Description
    Exit Sub
  End If
  ExecuteGlobal "Sub xk_b" & id & "()" & vbLf & body & vbLf & "End Sub"
  If Err.Number <> 0 Then
    xk_out.WriteLine id & "|compile|" & Err.Number & "|" & Err.Description
    Exit Sub
  End If
  ExecuteGlobal "Sub xk_c" & id & "()" & vbLf & "xk_b" & id & vbLf & "xk_done = True" & vbLf & "End Sub"
  ExecuteGlobal xk_reset
  ExecuteGlobal setup
  xk_done = False
  Err.Clear
  Execute "xk_c" & id
  If Not xk_done Then
    xk_out.WriteLine id & "|error|" & Err.Number & "|" & Err.Description
    Exit Sub
  End If
  xk_out.WriteLine id & "|ok|" & Eval(outs)
End Sub
"""


def vb_string(text: str) -> str:
    """A VBScript string expression for ``text``."""
    parts: list[str] = []
    chunk: list[str] = []
    for ch in text:
        if ch in "\r\n\t\0" or ord(ch) < 32:
            if chunk:
                parts.append('"' + "".join(chunk).replace('"', '""') + '"')
                chunk = []
            parts.append(f"ChrW({ord(ch)})")
        else:
            chunk.append(ch)
    if chunk or not parts:
        parts.append('"' + "".join(chunk).replace('"', '""') + '"')
    return " & ".join(parts)


def vb_value(value: HostValue) -> str:
    """A VBScript expression giving the variant a host value becomes."""
    if value is None:
        return "Empty"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        if -(2**31) <= value < 2**31:
            return f"CLng({value})" if value >= 0 else f"CLng(-{-value})"
        return f"CDbl({value}.0)" if value >= 0 else f"CDbl(-{-value}.0)"
    if isinstance(value, float):
        text = repr(value)
        if "e" not in text and "." not in text:
            text += ".0"
        return f"CDbl({text})" if value >= 0 else f"CDbl(-{text[1:]})"
    return vb_string(value)


def _case_call(index: int, case: Case) -> str:
    names = [*case.inputs, *case.outputs]
    setup = ": ".join(f"{name} = {vb_value(case.inputs.get(name))}" for name in names)
    show = "xk_bits" if case.exact else "xk_f"
    outs = ' & "|" & '.join(f"{show}({name})" for name in case.outputs) or '""'
    return (
        f"xk_run {index}, {LCIDS[case.locale]}, {vb_string(','.join(names))}, "
        f"{vb_string(case.code)}, {vb_string(setup)}, {vb_string(outs)}"
    )


def _run_batch(cases: list[Case], timeout: float, workdir: Path) -> dict[int, str]:
    script = workdir / "batch.vbs"
    lines = [_HARNESS]
    lines.extend(_case_call(i, case) for i, case in enumerate(cases))
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-16")
    try:
        proc = subprocess.run(
            [str(CSCRIPT32), "//nologo", "//U", str(script)],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        out = proc.stdout
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
    results: dict[int, str] = {}
    for line in out.decode("utf-16-le", errors="replace").splitlines():
        index, _, rest = line.partition("|")
        if index.isdigit():
            results[int(index)] = rest
    return results


def run_cases(cases: list[Case], *, batch: int = 200, timeout: float = 60) -> list[str]:
    """The engine's result for each case."""
    results: list[str] = [""] * len(cases)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        pending = list(range(len(cases)))
        while pending:
            chunk = pending[:batch]
            got = _run_batch([cases[i] for i in chunk], timeout, workdir)
            done = 0
            for pos, i in enumerate(chunk):
                if pos in got:
                    results[i] = got[pos]
                    done = pos + 1
                else:
                    break
            if done < len(chunk):
                # The case after the last result did not finish (or crashed the host).
                results[chunk[done]] = "timeout"
                done += 1
            pending = pending[done:]
    return results
