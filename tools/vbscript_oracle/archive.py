"""Compare the VBScript interpreter with the VBScript engine on real calculations.

Reads the calculations ``extract.py`` collected, runs each transformation (LR and RL) with several
sets of input values (defaults, minimum, maximum, zero and random values in range) in the 32-bit
VBScript engine and in the interpreter, and reports every difference. Windows only.

    uv run python tools/vbscript_oracle/archive.py out/calculations.jsonl --report out/diff.txt
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from oracle import Case, HostValue, run_cases

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "packages" / "prod" / "tests" / "script" / "vbscript" / "corpus.py"


def _load_corpus() -> Any:
    spec = importlib.util.spec_from_file_location("corpus", CORPUS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["corpus"] = module
    spec.loader.exec_module(module)
    return module


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    t = text.strip()
    try:
        return int(t, 16) if t[:2].lower() == "0x" else int(t)
    except ValueError:
        return None


def _default(param: dict[str, Any]) -> HostValue:
    t = param["type"]
    raw = param["default"]
    if t is None or t["kind"] == "none":
        return None
    if t["kind"] in ("number", "time", "restriction"):
        return _int(raw)
    if t["kind"] == "float":
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None
    return raw if raw is not None else ""


def _candidates(param: dict[str, Any], rng: random.Random) -> list[HostValue]:
    """Default, minimum, maximum, zero, then random values for one parameter."""
    t = param["type"]
    default = _default(param)
    if t is None or t["kind"] == "none":
        return [None] * 5
    kind = t["kind"]
    if kind in ("number", "time"):
        lo, hi = t["min"], t["max"]
        if lo > hi:
            lo, hi = hi, lo
        zero = 0 if lo <= 0 <= hi else lo
        return [default, lo, hi, zero, *(rng.randint(lo, hi) for _ in range(4))]
    if kind == "restriction":
        values = t["values"] or [0]
        return [default, values[0], values[-1], min(values, key=abs)] + [
            rng.choice(values) for _ in range(4)
        ]
    if kind == "float":
        lo, hi = t["min"], t["max"]
        if lo > hi:
            lo, hi = hi, lo
        zero = 0.0 if lo <= 0 <= hi else lo
        return [
            default,
            lo,
            hi,
            zero,
            *(round(rng.uniform(lo, hi), 2) for _ in range(4)),
        ]
    texts = ["", "21.5", "abc", "-3", "1,5"]
    return [default, *texts]


def _cases(
    records: list[dict[str, Any]], locale: str, seed: int
) -> tuple[list[Case], int]:
    cases: list[Case] = []
    seen: set[str] = set()
    skipped = 0
    for record in records:
        for direction, ins, outs in (("LR", "L", "R"), ("RL", "R", "L")):
            code = record["code"][direction]
            if not code.strip():
                continue
            inputs = record[ins]
            outputs = [p["alias"] for p in record[outs]]
            names = [p["alias"].lower() for p in inputs] + [o.lower() for o in outputs]
            if len(set(names)) != len(names):
                skipped += 1
                continue
            rng = random.Random(f"{seed}:{record['id']}:{direction}")
            columns = [_candidates(p, rng) for p in inputs]
            count = max((len(c) for c in columns), default=1)
            for i in range(count):
                values = {
                    p["alias"]: column[min(i, len(column) - 1)]
                    for p, column in zip(inputs, columns, strict=True)
                }
                key = json.dumps([code, values, outputs], sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                cases.append(
                    Case(
                        f"{record['id']}:{direction}:{i}",
                        code,
                        values,
                        outputs,
                        locale,
                        {"app": record["app"]},
                        exact=True,
                    )
                )
    return cases, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("calculations", type=Path)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    corpus = _load_corpus()
    records = [
        json.loads(line)
        for line in args.calculations.read_text(encoding="utf-8").splitlines()
    ]
    cases, skipped = _cases(records, args.locale, args.seed)
    if args.limit:
        cases = cases[: args.limit]
    bodies = {c.code for c in cases}
    print(
        f"{len(records)} calculations, {len(bodies)} distinct transformation bodies, "
        f"{len(cases)} cases ({skipped} transformations skipped: duplicate alias)"
    )
    started = time.monotonic()
    expected = run_cases(cases, batch=300, timeout=60)
    print(f"engine: {time.monotonic() - started:.0f} s")
    started = time.monotonic()
    mismatches: list[tuple[Case, str, str]] = []
    outcome: Counter[str] = Counter()
    for case, want in zip(cases, expected, strict=True):
        deadline = 1.0 if want == "timeout" else 10.0
        got = corpus.run_probe(
            case.code, case.inputs, case.outputs, case.locale, deadline, exact=True
        )
        outcome[want.split("|", 1)[0]] += 1
        if got != want:
            mismatches.append((case, want, got))
    print(f"interpreter: {time.monotonic() - started:.0f} s")
    print("engine outcomes:", dict(outcome))
    print(
        f"mismatches: {len(mismatches)} cases, {len({c.code for c, _, _ in mismatches})} bodies"
    )
    if args.report is not None:
        with args.report.open("w", encoding="utf-8") as out:
            for case, want, got in mismatches:
                out.write(f"=== {case.id} ({case.extra['app']}) inputs={case.inputs}\n")
                out.write(case.code.rstrip() + "\n")
                out.write(f"--- engine:      {want}\n--- interpreter: {got}\n\n")


if __name__ == "__main__":
    main()
