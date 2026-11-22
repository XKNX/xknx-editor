"""What starting the editor imports: the modules only some sessions use are imported when first
used, and the garbage collector runs again once the imports are done."""

from __future__ import annotations

import json
import subprocess
import sys

# Loaded when used: converting a .vd* product database, a VBScript calculation, the alternative
# catalog's connection.
_LATER = ("xknxeditor.prod.vd", "xknxeditor.prod.script.vbscript", "httpx")


def _imported(prelude: str = "") -> dict[str, object]:
    code = (
        "import gc, json, sys\n"
        f"{prelude}\n"
        "import editor_gui.main\n"
        f"print(json.dumps({{'collecting': gc.isenabled(), 'loaded': [m for m in {_LATER!r} if m in sys.modules]}}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_rarely_used_modules_wait_and_the_collector_runs_again() -> None:
    imported = _imported()
    assert imported == {"collecting": True, "loaded": []}


def test_a_collector_turned_off_stays_off() -> None:
    assert _imported("gc.disable()")["collecting"] is False
