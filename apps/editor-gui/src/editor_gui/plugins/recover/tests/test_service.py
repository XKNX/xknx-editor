"""The recover service's project assembly, against the real Gira application."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from xknxeditor.prod import load
from xknxeditor.recover import RecoveredDevice, RecoveredParameters
from xknxeditor.recover.recover import com_object_ref_by_number
from xknxeditor.recover.tables_decode import DecodedGroupObject, DecodedLink

_FIXTURE = (
    Path(__file__).resolve().parents[7]
    / "packages"
    / "prod"
    / "tests"
    / "fixtures"
    / "gira_2gang_button_interface.knxprod"
)


class _FakeComObject:
    def __init__(self, ref_id: str, db_id: int) -> None:
        self.id = ref_id
        self.db_id = db_id


class _FakeDevice:
    def __init__(self, node_id: int, refs: list[str]) -> None:
        self.node_id = node_id
        self._by_ref = {ref: _FakeComObject(ref, i + 1) for i, ref in enumerate(refs)}

    def find_com_object(self, ref_id: str) -> _FakeComObject | None:
        return self._by_ref.get(ref_id)


class _FakeProject:
    def __init__(self, refs: list[str]) -> None:
        self.is_open = True
        self._device = _FakeDevice(node_id=7, refs=refs)
        self._next_ga = 100
        self.links: list[tuple[int, int, bool]] = []
        self.flags: list[tuple[str, str, bool]] = []
        self.params: list[tuple[str, str]] = []
        self.gas: list[int] = []
        self.addresses: list[tuple[int | None, int | None]] = []
        self.names: list[str] = []
        self.descriptions: list[tuple[int, str]] = []
        self.segments_for: list[str] = []
        self.pretend_exists = False
        self.recalculated: tuple[int, list[str]] | None = None

    @property
    def devices(self) -> list[_FakeDevice]:
        return [self._device]

    def find_device_by_address(self, address: str) -> _FakeDevice | None:
        return self._device if self.pretend_exists else None

    def recalculate_params(self, node_id: int, ref_ids: list[str]) -> dict[str, Any]:
        self.recalculated = (node_id, ref_ids)
        return {}

    def find_or_create_segment_for_address(self, address: str) -> int:
        self.segments_for.append(address)
        return 1

    def add_device(
        self,
        product_ref_id: str,
        h2p: str | None,
        name: str,
        app: Any,
        *,
        segment_id: int | None = None,
        address: int | None = None,
        parameters: list[tuple[str, str]] | None = None,
        with_additional_addresses: bool = True,
    ) -> int:
        assert not with_additional_addresses
        self.addresses.append((segment_id, address))
        self.names.append(name)
        self.params.extend(parameters or [])
        return 7

    def create_group_address_value(self, value: int, name: str = "") -> int:
        self._next_ga += 1
        self.gas.append(value)
        return self._next_ga

    def link_com_object_to_ga(
        self, com_object_id: int, ga_id: int, is_sending: bool = False
    ) -> int:
        self.links.append((com_object_id, ga_id, is_sending))
        return len(self.links)

    def set_flag(self, device: Any, ref_id: str, flag: str, value: bool) -> None:
        self.flags.append((ref_id, flag, value))

    def set_device_description(
        self, node_id: int, old_description: str, new_description: str
    ) -> None:
        self.descriptions.append((node_id, new_description))

    def set_param(self, device: Any, ref_id: str, value: str) -> None:
        self.params.append((ref_id, value))


class _FakeAPI:
    def __init__(self, project: _FakeProject) -> None:
        self.project = project


@pytest.fixture(scope="module")
def application():  # type: ignore[no-untyped-def]
    if not _FIXTURE.exists():
        pytest.skip(f"fixture missing: {_FIXTURE}")
    return next(iter(load(str(_FIXTURE)).applications.values()))


def test_apply_to_project_writes_links_flags_and_params(application) -> None:  # type: ignore[no-untyped-def]
    number_to_ref = com_object_ref_by_number(application)
    number = next(iter(number_to_ref))
    ref_id = number_to_ref[number]

    recovered = RecoveredDevice(
        address="1.1.5",
        application_id=application.id,
        device_address=0x1105,
        group_addresses=[0x0B00],
        links=[
            DecodedLink(group_address=0x0B00, group_object_number=number, sending=True)
        ],
        group_objects={
            number: DecodedGroupObject(
                number=number,
                priority="Low",
                communication=True,
                read=False,
                write=True,
                transmit=True,
                update=False,
                read_on_init=False,
                size_code=0,
                object_size="1 Bit",
            )
        },
        parameters=RecoveredParameters(values={ref_id: "1"}, unknown=[]),
    )

    project = _FakeProject(refs=list(number_to_ref.values()))
    service = RecoverService(_FakeAPI(project))  # type: ignore[arg-type]
    service.entries = [
        RecoverEntry(
            address="1.1.5",
            mask_version=0x07B0,
            application=application,
            product_ref_id="P-1",
            hardware2program_ref_id="HP-1",
            product_name="AKH-0400.03",
            recovered=recovered,
        )
    ]

    added = service.apply_to_project()

    assert added == 1
    assert project.recalculated == (7, [ref_id])
    # Device placed on the segment for its address, with the device octet set.
    assert project.segments_for == ["1.1.5"]
    assert project.addresses == [(1, 5)]
    # The bus has no clear name, so the device is named after the matched product.
    assert project.names == ["AKH-0400.03"]
    # ...and its individual address is recorded as the description.
    assert project.descriptions == [(7, "1.1.5")]
    # Recovered parameters are passed as overrides at creation.
    assert (ref_id, "1") in project.params
    assert project.gas == [0x0B00]  # created once
    assert len(project.links) == 1
    assert project.links[0][2] is True  # sending
    assert (ref_id, "communication", True) in project.flags
    assert (ref_id, "write", True) in project.flags


def test_apply_keeps_read_on_init_a_table_does_not_hold() -> None:
    project = _FakeProject(refs=["O-1_R"])
    service = RecoverService(_FakeAPI(project))  # type: ignore[arg-type]
    group_object = DecodedGroupObject(
        number=1,
        priority="Low",
        communication=True,
        read=False,
        write=True,
        transmit=True,
        update=True,
        read_on_init=None,  # a memory-mapped record
        size_code=0,
        object_size="1 Bit",
    )
    recovered = RecoveredDevice(
        address="1.1.28",
        application_id="A-1",
        device_address=0x111C,
        group_addresses=[],
        links=[],
        group_objects={1: group_object},
        parameters=RecoveredParameters(values={}, unknown=[]),
    )

    service._apply_flags(project.devices[0], recovered, {1: "O-1_R"})  # pyright: ignore[reportPrivateUsage]

    assert ("O-1_R", "update", True) in project.flags
    assert all(flag != "read_on_init" for _, flag, _ in project.flags)


def test_apply_skips_device_already_in_project(application) -> None:  # type: ignore[no-untyped-def]
    recovered = RecoveredDevice(
        address="1.1.5",
        application_id=application.id,
        device_address=0x1105,
        group_addresses=[],
        links=[],
        group_objects={},
        parameters=RecoveredParameters(values={}, unknown=[]),
    )
    project = _FakeProject(refs=[])
    project.pretend_exists = True  # a device already sits at 1.1.5
    service = RecoverService(_FakeAPI(project))  # type: ignore[arg-type]
    entry = RecoverEntry(
        address="1.1.5",
        mask_version=0x0705,
        application=application,
        product_ref_id="P-1",
        recovered=recovered,
    )
    service.entries = [entry]

    added = service.apply_to_project()

    assert added == 0  # skipped, not crashed
    assert project.addresses == []  # add_device never called
    assert entry.applied is True  # not retried each frame
