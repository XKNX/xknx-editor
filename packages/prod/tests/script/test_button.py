"""Offline button handlers on a live DynamicUI."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiButton
from xknxeditor.prod.script import AbortToken, ScriptAborted, ScriptError
from xknxeditor.prod.script.button import (
    ParameterAccess,
    run_button,
    run_offline_button,
)
from xknxeditor.prod.script.compat.runtime import JScriptEnv

APP = "M-00FA_A-0001-01-0000"
MI2 = f"{APP}_MD-1_M-2_MI-1"
XML = (
    Path(__file__).parents[1] / "fixtures" / "calculations_application.xml"
).read_bytes()


def _ref(n: int) -> str:
    return f"{APP}_P-{n}_R-{n}"


def _button(handler: str, params: str | None = None, mi: str | None = None) -> UiButton:
    return UiButton(
        id=f"{APP}_B-9",
        text="B",
        handler=handler,
        handler_parameters=params,
        module_instance_id=mi,
    )


@pytest.fixture
def ui() -> DynamicUI:
    dui = DynamicUI(parse_application_xml(XML, "M-00FA")[0].program)
    dui.script_env = JScriptEnv(tz="Europe/Berlin", locale="en-US")
    dui.ui()
    return dui


def test_offline_button_writes_with_calculations(ui: DynamicUI) -> None:
    changes = run_offline_button(ui, _button("offlineButton", '{"n": 2}'))
    assert changes[_ref(1)] == ("1", "3")
    assert changes[_ref(2)] == ("2", "7")
    assert ui.get_value(_ref(4)) == "11"


def test_host_members_ignore_case(ui: DynamicUI) -> None:
    # The host objects are .NET objects in the reference tool, reached case-insensitively.
    changes = run_offline_button(ui, _button("caselessButton", '{"n": 2}'))
    assert changes[_ref(1)] == ("1", "3")


def test_failing_button_rolls_back(ui: DynamicUI) -> None:
    with pytest.raises(ScriptError) as err:
        run_offline_button(ui, _button("failingButton"))
    assert err.value.message == "stop"
    assert ui.get_value(_ref(3)) == "3"
    assert ui.get_value(_ref(4)) == "4"


def test_with_undo_rolls_back_its_group(ui: DynamicUI) -> None:
    changes = run_offline_button(ui, _button("undoButton"))
    assert ui.get_value(_ref(3)) == "3"
    assert changes == {_ref(4): ("4", "9")}


def test_rejected_write_throws_into_the_script(ui: DynamicUI) -> None:
    with pytest.raises(ScriptError) as err:
        run_offline_button(ui, _button("rejectButton"))
    assert err.value.message == (
        "Parameter value cannot be set, because validation failed."
    )
    logs: list[str] = []
    run_offline_button(
        ui, _button("catchButton"), on_log=lambda _lvl, msg: logs.append(msg)
    )
    assert logs == [
        "Parameter value cannot be set, because validation failed.",
        "The given key was not present in the dictionary.",
    ]


def test_module_scope_lookup(ui: DynamicUI) -> None:
    changes = run_offline_button(ui, _button("moduleButton", '{"ch": 2}', MI2))
    assert changes == {
        f"{MI2}_P-1_R-1": ("0", "20"),
        f"{MI2}_P-2_R-2": ("0", "120"),
    }


def test_module_lookup_by_application_relative_id(ui: DynamicUI) -> None:
    # "MD-1_P-1_R-1" gets the application prefix and resolves in the button's module instance.
    changes = run_offline_button(ui, _button("moduleIdButton", mi=MI2))
    assert changes[f"{MI2}_P-1_R-1"] == ("0", "33")


def test_lookups_and_device_members(ui: DynamicUI) -> None:
    logs: list[str] = []
    run_offline_button(
        ui, _button("infoButton"), on_log=lambda _lvl, msg: logs.append(msg)
    )
    assert logs == [f"A|{_ref(3)}|true|Code must look like K123.|Calc"]


def test_online_writes_are_committed_one_by_one(ui: DynamicUI) -> None:
    commits: list[tuple[dict[str, tuple[str | None, str | None]], str | None]] = []
    access = ParameterAccess(ui, on_commit=lambda c, d: commits.append((c, d)))
    with pytest.raises(ScriptError):
        run_button(ui, _button("failingButton"), access)
    assert commits == [({_ref(3): ("3", "7"), _ref(4): ("4", "8")}, None)]
    assert ui.get_value(_ref(3)) == "7"


def test_force_stop_aborts_a_loop(ui: DynamicUI) -> None:
    abort = AbortToken()
    threading.Timer(0.3, abort.request).start()
    with pytest.raises(ScriptAborted):
        run_offline_button(ui, _button("loopButton"), abort=abort)
