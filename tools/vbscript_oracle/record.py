"""Record the results of the VBScript engine for the VBScript conformance probes.

Windows only. Runs every probe in the 32-bit VBScript engine and writes the golden file the
conformance tests compare the interpreter with.

    uv run python tools/vbscript_oracle/record.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from oracle import CSCRIPT32, Case, run_cases

ROOT = Path(__file__).resolve().parents[2]
CONFORMANCE = ROOT / "packages" / "prod" / "tests" / "script" / "vbscript"


def _load_corpus() -> Any:
    spec = importlib.util.spec_from_file_location("corpus", CONFORMANCE / "corpus.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["corpus"] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="probe id prefix")
    args = parser.parse_args()
    corpus = _load_corpus()
    probes = [p for p in corpus.probes() if not args.only or p.id.startswith(args.only)]
    cases = [Case(p.id, p.code, p.inputs, p.outputs, p.locale) for p in probes]
    results = dict(
        zip((p.id for p in probes), run_cases(cases, timeout=20), strict=True)
    )
    out = CONFORMANCE / "golden" / "vbscript.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if args.only and out.exists():
        existing = json.loads(out.read_text(encoding="utf-8"))["results"]
    existing.update(results)
    version = run_cases(
        [
            Case(
                "v",
                'r = ScriptEngine() & " " & ScriptEngineMajorVersion() & "." & '
                'ScriptEngineMinorVersion() & "." & ScriptEngineBuildVersion()',
                {},
                ["r"],
            )
        ]
    )[0]
    payload = {
        "engine": version.split(":", 1)[-1] + f" ({CSCRIPT32.parent.name})",
        "results": dict(sorted(existing.items())),
    }
    out.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"{len(results)} probes recorded -> {out}")


if __name__ == "__main__":
    main()
