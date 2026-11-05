"""The recover service's project assembly, against the real Gira application."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from editor_gui.device import ComObjectFlags
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from editor_gui.plugins.recover.strings import S
from xknxeditor.prod import load
from xknxeditor.proj.core.medium import MediumError
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
        self.flags = ComObjectFlags()


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
        self.programs_for: list[str | None] = []
        self.path = Path("site.xknx")
        # The error the next ``add_device`` calls raise (None: they add the device).
        self.add_error: Exception | None = None
        self.add_calls = 0
        self.pretend_exists = False
        self.recalculated: tuple[int, list[str], bool] | None = None
        # The edits of each undo step: the number of edits made before it ended.
        self.undo_steps: list[int] = []

    @property
    def devices(self) -> list[_FakeDevice]:
        return [self._device]

    def find_device_by_address(self, address: str) -> _FakeDevice | None:
        return self._device if self.pretend_exists else None

    def recalculate_params(
        self, node_id: int, ref_ids: list[str], *, stored: bool = False
    ) -> dict[str, Any]:
        self.recalculated = (node_id, ref_ids, stored)
        return {}

    @contextlib.contextmanager
    def one_undo_step(self, label: str | None = None) -> Iterator[None]:
        yield
        self.undo_steps.append(len(self.links) + len(self.flags))

    def find_or_create_segment_for_address(
        self, address: str, hardware2program_ref_id: str | None = None
    ) -> int:
        self.segments_for.append(address)
        self.programs_for.append(hardware2program_ref_id)
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
        self.add_calls += 1
        if self.add_error is not None:
            raise self.add_error
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
    # The values were read from the device: calculations give them back.
    assert project.recalculated == (7, [ref_id], True)
    # Device placed on the segment for its address, with the device octet set.
    assert project.segments_for == ["1.1.5"]
    # The segment is chosen for the media of the device's hardware.
    assert project.programs_for == ["HP-1"]
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
    # Only the flags the group object does not have already are set.
    assert sorted(project.flags) == [
        (ref_id, "transmit", True),
        (ref_id, "write", True),
    ]
    # The device with its flags and links is one undo step.
    assert project.undo_steps == [3]


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


def _rf_entry(application: Any) -> RecoverEntry:
    recovered = RecoveredDevice(
        address="1.1.3",
        application_id=application.id,
        device_address=0x1103,
        group_addresses=[],
        links=[],
        group_objects={},
        parameters=RecoveredParameters(values={}, unknown=[]),
    )
    return RecoverEntry(
        address="1.1.3",
        mask_version=0x27B0,
        application=application,
        product_ref_id="P-1",
        hardware2program_ref_id="HP-rf",
        product_name="JU 1 S RF",
        recovered=recovered,
    )


def test_a_failed_add_is_shown_and_can_be_retried(application) -> None:  # type: ignore[no-untyped-def]
    project = _FakeProject(refs=[])
    project.add_error = MediumError("device_line_medium", device="JU 1 S RF")
    service = RecoverService(_FakeAPI(project))  # type: ignore[arg-type]
    entry = _rf_entry(application)
    service.entries = [entry]

    assert service.apply_to_open_project() == 0

    # The reason is kept and shown with the device, and the window says it failed.
    assert entry.error == "Device 'JU 1 S RF' is incompatible with line's medium type."
    assert entry.state == S.STATE_ERROR
    assert entry.error in service.entry_detail(entry)
    assert service.apply_error == S.APPLY_FAILED % {"count": 1}
    assert entry.applied is False

    # Once the project accepts it, the next add tries it again.
    project.add_error = None
    assert service.apply_to_open_project() == 1
    assert entry.applied is True
    assert entry.error is None
    assert service.apply_error == ""


def test_auto_apply_tries_a_device_once(application) -> None:  # type: ignore[no-untyped-def]
    project = _FakeProject(refs=[])
    project.add_error = RuntimeError("refused")
    service = RecoverService(_FakeAPI(project))  # type: ignore[arg-type]
    entry = _rf_entry(application)
    service.entries = [entry]

    # Live auto-apply runs every frame: a device it could not add is not tried again ...
    service.poll()
    service.poll()
    assert project.add_calls == 1
    assert entry.error == "refused"
    # ... but an explicit add is.
    project.add_error = None
    assert service.apply_to_project() == 1
    assert project.add_calls == 2


def test_a_new_project_tries_failed_devices_again(
    application,
    tmp_path: Path,  # type: ignore[no-untyped-def]
) -> None:
    project = _FakeProject(refs=[])
    project.add_error = RuntimeError("refused")
    created: list[Path] = []
    project.new = created.append  # type: ignore[attr-defined]
    service = RecoverService(_FakeAPI(project))  # type: ignore[arg-type]
    entry = _rf_entry(application)
    service.entries = [entry]
    service.apply_to_project()
    assert entry.error == "refused"

    project.add_error = None
    target = tmp_path / "recovered.xknx"
    assert service.create_project_and_apply(target) == 1
    assert created == [target]
    assert entry.applied is True and entry.error is None
    assert service.apply_error == ""
