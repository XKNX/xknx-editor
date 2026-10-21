"""The VBScript interpreter must reproduce the recorded results of the VBScript engine."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest

from .corpus import Probe, probes, run_probe

GOLDEN = Path(__file__).parent / "golden" / "vbscript.json"

_TRIG = "the 32-bit engine reduces a large argument of a trigonometric function less precisely"
_UNSUPPORTED = (
    "arrays, For Each and With are not supported (no parameter calculation uses them)"
)

# Behaviour of the VBScript engine that is not reproduced; each entry is a known difference.
KNOWN_DIFFERENCES: dict[str, str] = {
    "fn:Cos(big)": _TRIG,
    "fn:Sin(big)": _TRIG,
    "fn:Tan(big)": _TRIG,
    **{
        f"stmt:{name}:{a}": _UNSUPPORTED
        for name in ("for each", "dim array", "redim", "with")
        for a in (5, 0)
    },
}


@cache
def _golden() -> dict[str, str]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["results"]


def _ids() -> list[Probe]:
    return probes()


@pytest.mark.parametrize("probe", _ids(), ids=lambda p: p.id)
def test_probe(probe: Probe) -> None:
    if probe.id in KNOWN_DIFFERENCES:
        pytest.skip(KNOWN_DIFFERENCES[probe.id])
    expected = _golden()[probe.id]
    deadline = 0.5 if expected == "timeout" else 5.0
    assert (
        run_probe(probe.code, probe.inputs, probe.outputs, probe.locale, deadline)
        == expected
    )


def test_every_probe_is_recorded() -> None:
    assert {p.id for p in probes()} <= set(_golden())
