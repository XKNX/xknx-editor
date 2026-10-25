"""A ``choose`` in a load procedure selects its controls by a parameter of the device; a
property the procedure writes with different values is a step of it, not a setting."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from xknxeditor.download.image import (
    DownloadImage,
    _procedure_parameters,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.download.merge import (
    iter_load_controls,
    procedure_parameter_refs,
    resolve_download_controls,
)
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.namespaces.intermediate.ld_ctrl_base_choose_t import (
    LdCtrlBaseChoose,
    LdCtrlBaseChooseWhen,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_delay_t import LdCtrlDelay
from xknxeditor.namespaces.intermediate.ld_ctrl_merge_t import LdCtrlMerge
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)

from .conftest import FakeDevice
from .test_merge import _application as _styled_application
from .test_merge import _master_with_default
from .test_procedure import _application, _FakeManager


def _delay(ms: int) -> LdCtrlDelay:
    return LdCtrlDelay(milli_seconds=ms)


def _when(
    test: str | None, *controls: object, default: bool = False
) -> LdCtrlBaseChooseWhen:
    return LdCtrlBaseChooseWhen(test=test, default=default, choice=list(controls))  # type: ignore[arg-type]


def _choose(ref: str, *whens: LdCtrlBaseChooseWhen) -> LdCtrlBaseChoose:
    return LdCtrlBaseChoose(param_ref_id=ref, when=list(whens))


def _delays(controls: list[object]) -> list[int]:
    return [c.milli_seconds for c in controls if isinstance(c, LdCtrlDelay)]


def _resolve(values: dict[str, str], *controls: object) -> list[object]:
    return resolve_download_controls(_application(*controls), parameter_values=values)


# The MDT BE-GBZx.01 lays out its segments by "Switching times in the device" (0/1).
_SWITCHING_TIMES = _choose(
    "P-203", _when("0", _delay(5406)), _when("1", _delay(899), _delay(2966))
)


@pytest.mark.parametrize(("value", "delays"), [("0", [5406]), ("1", [899, 2966])])
def test_the_branch_the_parameter_selects_runs(value: str, delays: list[int]) -> None:
    controls = _resolve(
        {"P-203": value}, LdCtrlConnect(), _SWITCHING_TIMES, LdCtrlRestart()
    )
    assert _delays(controls) == delays
    assert not any(isinstance(c, LdCtrlBaseChoose) for c in controls)
    assert isinstance(controls[0], LdCtrlConnect)
    assert isinstance(controls[-1], LdCtrlRestart)


def test_every_matching_branch_runs_in_order_else_the_default() -> None:
    choose = _choose(
        "P",
        _when("<5", _delay(1)),
        _when(None, _delay(9), default=True),
        _when("2 3", _delay(2)),
    )
    assert _delays(_resolve({"P": "2"}, choose)) == [1, 2]
    assert _delays(_resolve({"P": "7"}, choose)) == [9]
    # No match and no default: nothing.
    assert _delays(_resolve({"P": "7"}, _choose("P", _when("1", _delay(1))))) == []


def test_an_inactive_parameter_selects_nothing_not_even_the_default() -> None:
    choose = _choose("P", _when("1", _delay(1)), _when(None, _delay(9), default=True))
    assert _delays(_resolve({}, choose)) == []


def test_a_nested_choose_is_resolved_in_turn() -> None:
    inner = _choose("Q", _when("1", _delay(11)), _when("2", _delay(12)))
    outer = _choose("P", _when("1", _delay(1), inner))
    assert _delays(_resolve({"P": "1", "Q": "2"}, outer)) == [1, 12]
    assert procedure_parameter_refs(_application(outer)) == {"P", "Q"}
    assert [type(c).__name__ for c in iter_load_controls([outer])] == [
        "LdCtrlDelay",
        "LdCtrlDelay",
        "LdCtrlDelay",
    ]


def test_a_choose_in_a_merged_procedure_and_its_fragments() -> None:
    master = _master_with_default(
        LdCtrlConnect(),
        _choose("P", _when("1", _delay(1))),
        LdCtrlMerge(merge_id=4),
    )
    fragment = LoadProceduresLoadProcedure(
        merge_id=4,
        choice=[_choose("P", _when("1", _delay(2)), _when("0", _delay(3)))],  # type: ignore[list-item]
    )
    application = _styled_application(LoadProcedureStyle.MERGED_PROCEDURE, fragment)
    controls = resolve_download_controls(
        cast(Any, application), master, parameter_values={"P": "1"}
    )
    assert _delays(controls) == [1, 2]


def test_the_image_holds_the_values_of_the_active_parameters_tested() -> None:
    application = _application(
        _choose("P", _when("1", _delay(1))), _choose("Q", _when("1", _delay(2)))
    )
    values = {"P": "1", "Q": "0", "R": "5"}
    ui = SimpleNamespace(
        active_parameter_ref_ids=lambda: frozenset({"P", "R"}),
        get_value=values.get,
    )
    # Q is inactive, R is tested by no choose.
    assert _procedure_parameters(application, cast(Any, ui)) == {"P": "1"}


async def test_the_runner_resolves_by_the_image_values() -> None:
    application = _application(LdCtrlConnect(), _SWITCHING_TIMES)
    image = DownloadImage((), (), procedure_parameters={"P-203": "1"})
    runner = LoadProcedureRunner(
        application, image, connection_manager=_FakeManager(FakeDevice())
    )
    assert _delays(runner._controls) == [899, 2966]  # pyright: ignore[reportPrivateUsage]


def _handshake(data: bytes) -> LdCtrlWriteProp:
    return LdCtrlWriteProp(
        obj_idx=5, prop_id=51, count=1, verify=False, inline_data=data
    )


async def test_a_property_written_with_different_values_is_a_procedure_step() -> None:
    # A Berker 8014xxx0 writes object 5 property 51 with 01 at the start and 02 later.
    device = FakeDevice(object_types={0: 0})
    for pid in (51, 52):
        device.properties[5, pid] = b"\x00"
        device.property_element_sizes[5, pid] = 1
    application = _application(
        LdCtrlConnect(),
        _handshake(b"\x01"),
        LdCtrlWriteProp(
            obj_idx=5, prop_id=52, count=1, verify=True, inline_data=b"\x01"
        ),
        _handshake(b"\x02"),
    )
    runner = LoadProcedureRunner(
        application, DownloadImage((), ()), connection_manager=_FakeManager(device)
    )
    report = await runner.preflight()
    steps = {(p.property_id, p.planned): p.procedure_step for p in report.properties}
    assert steps == {(51, b"\x01"): True, (51, b"\x02"): True, (52, b"\x01"): False}
