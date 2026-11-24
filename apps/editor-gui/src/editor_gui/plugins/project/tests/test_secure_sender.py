"""The device that sends a secured group telegram for the editor's connection, and whether an
internal bus interface is secured."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.device import ComObjectFlags
from editor_gui.plugins.project.service import (
    ProjectService,
    _ProductSecurity,  # pyright: ignore[reportPrivateUsage]
    _secure_sender_score,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.proj.core.service import SecureSenderCandidate

_CT = ComObjectFlags(communication=True, transmit=True)
_CI = ComObjectFlags(communication=True, read_on_init=True)
_C = ComObjectFlags(communication=True)


@pytest.mark.parametrize(
    ("flags", "write", "scores"),
    [
        (_CT, True, (10, 5, 4)),
        (_C, True, (3, 2, 1)),
        (_CI, False, (10, 6, 4)),
        (_CT, False, (6, 5, 4)),
        (_C, False, (3, 2, 1)),
    ],
)
def test_the_scores(
    flags: ComObjectFlags, write: bool, scores: tuple[int, int, int]
) -> None:
    def score(transmit_only: bool, slow: bool) -> int:
        return _secure_sender_score(
            flags, write=write, transmit_only=transmit_only, slow=slow
        )

    # A device that only transmits counts before one that receives slowly.
    assert (score(False, False), score(False, True), score(True, True)) == scores


class _Project(ProjectService):
    """A project service with devices but no project file."""

    def __init__(self, candidates: list[SecureSenderCandidate], devices: list[Any]):
        self._pid = "p"
        self._io_lock = threading.RLock()
        self._svc = cast(
            Any, SimpleNamespace(secure_sender_candidates=lambda pid, ga: candidates)
        )
        self._stub_devices = devices

    @property
    def devices(self) -> list[Any]:  # type: ignore[override]
        return self._stub_devices

    def find_device_by_node_id(self, node_id: int) -> Any:
        return next(d for d in self._stub_devices if d.node_id == node_id)

    def _rf_capabilities(self, node_id: int) -> tuple[str | None, str | None]:
        return ("Ready", None) if node_id == 3 else (None, None)


def _candidate(device: int, com_object: int, **kwargs: Any) -> SecureSenderCandidate:
    values: dict[str, Any] = {
        "individual_address_loaded": True,
        "application_program_loaded": True,
        "rf": False,
    }
    return SecureSenderCandidate(
        device_id=device,
        com_object_id=com_object,
        address=0x1100 + device,
        **(values | kwargs),
    )


def _device(node_id: int, com_object: int, flags: ComObjectFlags) -> Any:
    return SimpleNamespace(
        node_id=node_id,
        com_objects=[SimpleNamespace(db_id=com_object, flags=flags)],
    )


def test_the_device_that_suits_best() -> None:
    devices = [
        _device(1, 11, _C),
        _device(2, 12, _CT),
        _device(3, 13, _CT),
        _device(4, 14, _CI),
        _device(5, 15, _CT),
    ]
    candidates = [
        _candidate(1, 11),
        _candidate(2, 12),
        _candidate(3, 13, rf=True),
        _candidate(4, 14),
        # Downloaded neither address nor application: never chosen.
        _candidate(5, 15, individual_address_loaded=False),
    ]
    project = _Project(candidates, devices)
    # A write from the one that transmits, a read from the one that reads on init.
    assert project.secure_group_sender(0x0801, write=True) is devices[1]
    assert project.secure_group_sender(0x0801, write=False) is devices[3]
    # Of equals the first, a device on RF that only transmits after the others.
    assert (
        _Project(candidates[2:3] + candidates[1:2], devices).secure_group_sender(
            0x0801, write=True
        )
        is devices[1]
    )
    assert (
        _Project([candidates[4]], devices).secure_group_sender(0x0801, write=True)
        is None
    )
    assert _Project([], devices).secure_group_sender(0x0801, write=True) is None


def _app(access: str, secure: bool) -> Any:
    interface = SimpleNamespace(
        id="M-1_A-1_BI-1", access_type=SimpleNamespace(value=access), address_index=0
    )
    return SimpleNamespace(
        program=SimpleNamespace(
            is_secure_enabled=secure,
            static=SimpleNamespace(
                bus_interfaces=SimpleNamespace(bus_interface=[interface])
            ),
        )
    )


@pytest.mark.parametrize(
    ("app", "stored", "secure"),
    [
        (_app("Internal", True), False, True),
        (_app("Internal", False), True, False),
        (_app("Tunneling", False), True, True),
        (None, False, False),
    ],
)
def test_an_internal_bus_interface_follows_the_application(
    app: Any, stored: bool, secure: bool
) -> None:
    service = SimpleNamespace(_resolve_app=lambda program, application: app)
    context = _ProductSecurity(cast(Any, service))
    device = cast(
        Any,
        SimpleNamespace(hardware2program_ref_id="H", application_program_ref_id="A"),
    )
    assert context.interface_secure(device, "M-1_A-1_BI-1", stored) is secure
