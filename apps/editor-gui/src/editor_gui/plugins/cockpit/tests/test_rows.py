"""The Device Overview rows follow the project, also when it was busy while they were first asked for."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.cockpit.service import CockpitRow, CockpitService
from editor_gui.plugins.health.service import Severity


class _Project:
    """The parts of the project service the rows read; ``loading`` like a background task."""

    is_open = True
    revision = 7

    def __init__(self) -> None:
        self.loading = False
        self._devices = [
            SimpleNamespace(
                node_id=1,
                individual_address="1.1.2",
                display_name="Switch Actuator",
                description="",
            )
        ]

    @property
    def devices(self) -> list[Any]:
        # While a background task holds the project, its reads are empty placeholders.
        return [] if self.loading else self._devices

    def get_device_info(self, node_id: int) -> None:
        return None


def _service(project: _Project) -> CockpitService:
    service = CockpitService(cast(Any, project))
    service._health = cast(Any, SimpleNamespace(findings=list))  # pyright: ignore[reportPrivateUsage]
    return service


def test_rows_asked_for_while_the_project_is_busy_appear_when_it_is_done() -> None:
    project = _Project()
    project.loading = True
    service = _service(project)
    assert service.rows() == []
    # Done, with the same revision: the rows are there without another edit.
    project.loading = False
    assert [r.individual_address for r in service.rows()] == ["1.1.2"]


def test_the_last_rows_stay_while_the_project_is_busy() -> None:
    project = _Project()
    service = _service(project)
    rows = service.rows()
    project.loading = True
    assert service.rows() is rows


def _finding_row(address: str, name: str, *messages: str) -> CockpitRow:
    return CockpitRow(
        node_id=1,
        individual_address=address,
        name=name,
        product_name="",
        order_number="",
        issues=[(Severity.INFO, m) for m in messages],
    )


def test_the_status_shows_the_finding_without_the_device() -> None:
    row = _finding_row(
        "1.1.2",
        "Switch Actuator",
        "1.1.2  Switch Actuator: 10 unlinked group object(s)",
    )
    assert row.issue_summary == "10 unlinked group object(s)"
    # A device without an address is named alone; a space before the colon (French).
    row = _finding_row("", "Sensor", "Sensor : pas d'adresse individuelle")
    assert row.issue_summary == "pas d'adresse individuelle"


def test_a_finding_about_something_else_keeps_its_text() -> None:
    row = _finding_row(
        "1.1.2",
        "Switch Actuator",
        "Duplicate individual address 1.1.2 (1.1.2  Switch Actuator)",
        "1.1.2  Switch Actuator: 10 unlinked group object(s)",
    )
    assert row.issue_summary == (
        "Duplicate individual address 1.1.2 (1.1.2  Switch Actuator)  (+1)"
    )
    # The device's name followed by more of a name is not the device.
    row = _finding_row("", "Switch", "Switch Actuator: 3 unlinked group object(s)")
    assert row.issue_summary == "Switch Actuator: 3 unlinked group object(s)"


def test_a_full_width_colon_ends_the_device_too() -> None:
    row = _finding_row("1.1.2", "Aktor", "1.1.2  Aktor\uff1a10 个未链接的组对象")
    assert row.issue_summary == "10 个未链接的组对象"
