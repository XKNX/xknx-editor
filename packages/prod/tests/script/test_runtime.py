from __future__ import annotations

import threading
import time

import pytest

from xknxeditor.prod.script import AbortToken, ScriptAborted, ScriptContext
from xknxeditor.prod.script.compat import numfmt
from xknxeditor.prod.script.compat.runtime import (
    JScriptEnv,
    locale_compare,
    locale_date,
    locale_number,
    parse_date,
    to_lower,
    to_upper,
    tz_rule,
)

ENV = JScriptEnv(tz="Europe/Berlin", locale="de-DE")


def test_tz_rule_is_current_eu_rule() -> None:
    rule = tz_rule("Europe/Berlin", 2026)
    assert rule == {
        "std": 60,
        "dst": {"delta": 60, "start": [2, 5, 0, 120], "end": [9, 5, 0, 180]},
    }
    assert tz_rule("UTC", 2026) == {"std": 0, "dst": None}
    south = tz_rule("Australia/Sydney", 2026)["dst"]
    assert south is not None and south["start"][0] == 9


def test_current_rule_applies_to_every_year() -> None:
    ctx = ScriptContext(
        jscript=ENV,
        script="function f() { return [new Date(1970, 6, 2).getTimezoneOffset(), new Date(2026, 0, 2).getTimezoneOffset()].join(); }",
    )
    assert ctx.invoke("f", []).value == "-120,-60"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026/01/02", [2026, 0, 2, 0, 0, 0, None]),
        ("1/2/2026", [2026, 0, 2, 0, 0, 0, None]),
        ("Jan 2, 2026 10:00 PM", [2026, 0, 2, 22, 0, 0, None]),
        ("Fri Jan 2 03:04:05 UTC+0100 2026", [2026, 0, 2, 3, 4, 5, 60]),
        ("Jan 2, 2026 EST", [2026, 0, 2, 0, 0, 0, -300]),
        ("12/31/99", [1999, 11, 31, 0, 0, 0, None]),
        ("2026-01-02", None),
        ("2026-01-02T10:00:00Z", None),
        ("garbage", None),
    ],
)
def test_parse_date(text: str, expected: list[int | None] | None) -> None:
    assert parse_date(text) == expected


def test_locale_formatting() -> None:
    assert (
        locale_date("de-DE", [2026, 0, 2, 5, 3, 4, 5], "full")
        == "Freitag, 2. Januar 2026 03:04:05"
    )
    assert (
        locale_date("en-US", [2026, 0, 2, 5, 15, 4, 5], "full")
        == "Friday, January 2, 2026 3:04:05 PM"
    )
    assert locale_number("de-DE", 1234567.891) == "1.234.567,89"
    assert locale_number("de-DE", -0.005) == "-0,01"
    assert locale_number("en-US", 1234.5) == "1,234.50"


def test_collation_and_case() -> None:
    assert locale_compare("a", "B") == -1
    assert locale_compare("a", "A") == -1
    assert locale_compare("10", "9") == -1
    assert to_upper("ßı") == "ßI"
    assert to_lower("İΩ") == "iΩ"


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (1.1 + 2.2, "3.3000000000000002"),
        (3.8043093288962915, "3.8043093288962914"),
        (1e21, "1e+21"),
        (1e-7, "1e-7"),
    ],
)
def test_number_to_string(x: float, expected: str) -> None:
    assert numfmt.to_string(x) == expected


def test_number_formats() -> None:
    assert numfmt.to_fixed(1.005, 2) == "1.01"
    assert numfmt.to_fixed(1e21, 2) == "1000000000000000000000.00"
    assert numfmt.to_precision(123456, 2) == "1.2e+5"
    assert numfmt.to_exponential(1.005, 2) == "1.01e+0"
    assert numfmt.to_radix(0.1, 3) == "0.002200220022002200220022002200220100"
    assert numfmt.to_radix(1e21, 36) == "5.v1j4f4ds7at(e+13)"


def test_abort_cannot_be_caught() -> None:
    token = AbortToken()
    ctx = ScriptContext(
        abort=token,
        jscript=ENV,
        script="function h() { while (true) { try { while (true) {} } catch (e) { } } }",
    )
    threading.Timer(0.2, token.request).start()
    started = time.monotonic()
    with pytest.raises(ScriptAborted):
        ctx.invoke("h", [])
    assert time.monotonic() - started < 10


def test_busy_wait_keeps_python_threads_running() -> None:
    ticks = [0]
    stop = threading.Event()

    def counter() -> None:
        while not stop.is_set():
            ticks[0] += 1
            time.sleep(0.001)

    ctx = ScriptContext(
        jscript=ENV,
        script="function sleep(ms) { var s = new Date().getTime(); while (new Date().getTime() - s < ms) {} }",
    )
    thread = threading.Thread(target=counter)
    thread.start()
    time.sleep(0.05)
    before = ticks[0]
    ctx.invoke("sleep", [400])
    after = ticks[0]
    stop.set()
    thread.join()
    # A script holding the GIL throughout lets the thread tick about once. Running freely it ticks
    # at most 25 times on Windows (a sleep lasts a 15.6 ms timer tick there), fewer under load.
    assert after - before > 5
