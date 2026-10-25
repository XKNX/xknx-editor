"""Finding the project device a failed device check points to."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast

from editor_gui.device import Device
from editor_gui.plugins.project.compare_match import compare_mismatch, matching_devices
from editor_gui.plugins.project.program_queue import (
    OperationState,
    ProgramQueue,
    QueueItem,
)
from xknxeditor.download.errors import CompareMismatch, PartialDownloadError
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_base_choose_t import (
    LdCtrlBaseChoose,
    LdCtrlBaseChooseWhen,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp

_READ = bytes.fromhex("000000140001")


def _mismatch() -> CompareMismatch:
    return CompareMismatch(
        "The device ID is incompatible",
        object_index=0,
        property_id=78,
        expected=bytes.fromhex("000000250000"),
        read=_READ,
        mask=bytes.fromhex("FFFFFFFF0000"),
    )


def _device(node_id: int, *controls: object) -> Device:
    procedures = SimpleNamespace(
        load_procedure=[SimpleNamespace(choice=list(controls))]
    )
    return cast(
        Device,
        SimpleNamespace(
            node_id=node_id,
            product_name=f"Product {node_id}",
            individual_address=f"1.0.{node_id}",
            app=SimpleNamespace(load_procedures=procedures, name="App"),
        ),
    )


def _hardware(value: str, mask: str | None = None, **index: int) -> LdCtrlCompareProp:
    return LdCtrlCompareProp(
        prop_id=78,
        inline_data=bytes.fromhex(value),
        mask=bytes.fromhex(mask) if mask else None,
        **(index or {"obj_idx": 0}),
    )


def test_device_whose_application_accepts_the_reported_value() -> None:
    interface = _device(3, _hardware("000000140000", "FFFFFFFF0000"))
    other = _device(4, _hardware("000000250000", "FFFFFFFF0000"))
    by_type = _device(5, _hardware("00000014", obj_type=0))
    unmasked = _device(6, _hardware("000000140000"))
    unloaded = cast(Device, SimpleNamespace(node_id=7))
    assert matching_devices(
        _mismatch(), [interface, other, by_type, unmasked, unloaded]
    ) == [interface, by_type]


def test_a_check_in_any_branch_of_a_choose_counts() -> None:
    # Which branch runs depends on the device's parameters, so each one may be the check.
    chosen = _device(
        8,
        LdCtrlBaseChoose(
            param_ref_id="P",
            when=[
                LdCtrlBaseChooseWhen(test="0", choice=[_hardware("00000025")]),
                LdCtrlBaseChooseWhen(
                    default=True, choice=[_hardware("000000140000", "FFFFFFFF0000")]
                ),
            ],
        ),
    )
    assert matching_devices(_mismatch(), [chosen]) == [chosen]


def test_mismatch_is_found_behind_a_wrapping_error() -> None:
    mismatch = _mismatch()
    try:
        try:
            raise mismatch
        except CompareMismatch as error:
            raise PartialDownloadError("download failed") from error
    except PartialDownloadError as wrapped:
        assert compare_mismatch(wrapped) is mismatch
    assert compare_mismatch(CompareMismatch("memory echo mismatch")) is None
    assert compare_mismatch(None) is None


def test_queue_appends_the_description_to_the_failure() -> None:
    futures: list[Future[Any]] = []

    def start(item: QueueItem) -> Future[Any]:
        futures.append(Future())
        return futures[-1]

    queue = ProgramQueue(
        is_busy=lambda: False,
        start=start,
        describe_error=lambda item, error: f"Matches node {item.node_id + 1}.",
    )
    queue.enqueue(
        QueueItem(node_id=3, address="1.0.4", name="", scope=DownloadScope.FULL)
    )
    futures[0].set_exception(_mismatch())
    item = queue.history[0]
    assert item.state is OperationState.FAILED
    assert item.message == "The device ID is incompatible Matches node 4."
