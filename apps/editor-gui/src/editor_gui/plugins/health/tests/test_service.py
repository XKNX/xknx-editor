"""Health findings: kept while a background task holds the project, and the link checks."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, cast

import pytest

from editor_gui.device import ComObjectFlags
from editor_gui.plugins.health.service import Finding, HealthService, Severity
from editor_gui.plugins.health.strings import S
from xknxeditor.proj.core.service import DeviceLinkInfo


@dataclass
class _Ga:
    id: int
    address: str
    name: str
    datapoint_type: str | None = None
    raw: int = 0


@dataclass
class _ComObject:
    db_id: int | None
    flags: ComObjectFlags = field(default_factory=ComObjectFlags)


@dataclass
class _Device:
    node_id: int
    name: str
    com_objects: list[_ComObject]
    individual_address: str = ""
    inactive: set[int] = field(default_factory=set)

    @property
    def display_name(self) -> str:
        return self.name

    def get_visible_com_objects(self) -> list[_ComObject]:
        return [co for co in self.com_objects if co.db_id not in self.inactive]


@dataclass
class _Project:
    revision: int = 1
    loading: bool = False
    is_open: bool = True
    devices: list[Any] = field(default_factory=list)
    group_addresses: list[_Ga] = field(default_factory=list)
    links: list[DeviceLinkInfo] = field(default_factory=list)
    link_reads: int = 0
    unloaded_devices: list[Any] = field(default_factory=list)
    pending: dict[int, tuple[str, ...]] = field(default_factory=dict)

    def pending_downloads(self) -> dict[int, tuple[str, ...]]:
        return self.pending

    def missing_program_refs(self) -> list[str]:
        return []

    def device_links(self) -> list[DeviceLinkInfo]:
        self.link_reads += 1
        return self.links


def _link(
    com_object_id: int, ga_id: int, *, sending: bool, transmit: bool = False
) -> DeviceLinkInfo:
    return DeviceLinkInfo(
        device_id=1,
        group_address_id=ga_id,
        is_sending=sending,
        communication=True,
        read=False,
        write=True,
        transmit=transmit,
        update=False,
        com_object_id=com_object_id,
    )


def test_findings_stay_while_the_project_is_busy() -> None:
    project = _Project(group_addresses=[_Ga(1, "1/1/1", "Light")])
    service = HealthService(cast(Any, project))
    assert len(service.findings()) == 1
    # A language switch: the revision moves and the reads are placeholders meanwhile.
    project.loading, project.revision, project.group_addresses = True, 2, []
    assert len(service.findings()) == 1
    project.loading, project.group_addresses = False, [_Ga(1, "1/1/1", "Light")]
    assert len(service.findings()) == 1
    project.revision, project.group_addresses = 3, []
    assert service.findings() == []


def test_group_objects_without_a_group_address_are_counted() -> None:
    silent = ComObjectFlags(communication=False)
    device = _Device(
        1,
        "Actuator",
        [_ComObject(10), _ComObject(11), _ComObject(12), _ComObject(13, silent)],
        individual_address="1.1.1",
    )
    project = _Project(
        devices=[device],
        links=[_link(10, 1, sending=True)],
        group_addresses=[_Ga(1, "1/1/1", "Light", "1.001")],
    )
    findings = HealthService(cast(Any, project)).findings()
    unlinked = [f for f in findings if f.category == "com-object"]
    # 11 and 12 communicate without a link; 13 does not communicate.
    assert len(unlinked) == 1
    assert unlinked[0].device_node_id == 1
    assert unlinked[0].severity is Severity.INFO
    assert "2" in unlinked[0].message


def test_senders_of_each_linked_group_address() -> None:
    project = _Project(
        devices=[_Device(1, "Device", [_ComObject(i) for i in range(10, 15)], "1.1.1")],
        group_addresses=[
            _Ga(1, "1/1/1", "One sender", "1.001"),
            _Ga(2, "1/1/2", "No sender", "1.001"),
            _Ga(3, "1/1/3", "Two senders", "1.001"),
            _Ga(4, "1/1/4", "Not linked", "1.001"),
        ],
        links=[
            _link(10, 1, sending=True, transmit=True),
            _link(11, 1, sending=False),
            _link(12, 2, sending=False),
            _link(13, 3, sending=True, transmit=True),
            _link(14, 3, sending=True, transmit=True),
        ],
    )
    findings = HealthService(cast(Any, project)).findings()
    assert [(f.ga_id, f.message) for f in findings] == [
        (2, S.HEALTH_NO_SENDER.format(address="1/1/2", name="No sender")),
        (3, S.HEALTH_SENDERS(2).format(address="1/1/3", name="Two senders", count=2)),
    ]
    # All links in one read.
    assert project.link_reads == 1


def test_a_group_address_another_program_sends_to_has_a_sender() -> None:
    project = _Project(
        devices=[_Device(1, "Device", [_ComObject(12), _ComObject(13)], "1.1.1")],
        group_addresses=[
            _Ga(2, "1/1/2", "Sent by Home Assistant", "1.001", raw=0x0902),
            _Ga(3, "1/1/3", "No sender", "1.001", raw=0x0903),
        ],
        links=[_link(12, 2, sending=False), _link(13, 3, sending=False)],
    )
    health = HealthService(cast(Any, project))
    senders = {0x0902}
    health.external_senders = lambda: frozenset(senders)
    assert [f.ga_id for f in health.findings()] == [3]
    # A change of what the other program sends is taken at once.
    senders.add(0x0903)
    assert health.findings() == []


def test_pending_downloads_include_missing_product_data_and_combine_parts() -> None:
    project = _Project(
        devices=[_Device(1, "Actuator", [], "1.1.1")],
        unloaded_devices=[_Device(2, "Missing product", [], "1.1.2")],
        pending={
            1: ("parameters", "group_communication"),
            2: ("application_program",),
        },
    )
    findings = HealthService(cast(Any, project)).findings()
    assert [(f.category, f.device_node_id, f.severity) for f in findings] == [
        ("download-pending", 1, Severity.WARNING),
        ("download-pending", 2, Severity.WARNING),
    ]
    assert "Download pending: Parameters, Group Addresses" in findings[0].message


def _sender_project(*links: DeviceLinkInfo) -> _Project:
    return _Project(
        devices=[
            _Device(
                1, "Device", [_ComObject(link.com_object_id) for link in links], "1.1.1"
            )
        ],
        links=list(links),
        group_addresses=[_Ga(1, "1/1/1", "Light", "1.001")],
    )


def _group_findings(project: _Project) -> list[Finding]:
    return [
        finding
        for finding in HealthService(cast(Any, project)).findings()
        if finding.category == "group-address"
    ]


def test_one_transmitter_and_a_receiver_do_not_compete() -> None:
    project = _sender_project(
        _link(10, 1, sending=True, transmit=True),
        _link(11, 1, sending=True),
    )
    assert _group_findings(project) == []


@pytest.mark.parametrize(
    "write,update", [(False, False), (True, False), (False, True), (True, True)]
)
def test_receiving_flags_do_not_disqualify_a_transmitter(
    write: bool, update: bool
) -> None:
    project = _sender_project(
        _link(10, 1, sending=True, transmit=True),
        replace(_link(11, 1, sending=True, transmit=True), write=write, update=update),
    )
    (finding,) = _group_findings(project)
    assert finding.severity is Severity.WARNING
    assert finding.message == S.HEALTH_SENDERS(2).format(
        address="1/1/1", name="Light", count=2
    )


@pytest.mark.parametrize("reason", ["communication", "association", "inactive"])
def test_disabled_or_inactive_objects_and_other_associations_do_not_count(
    reason: str,
) -> None:
    links = [
        _link(10, 1, sending=True, transmit=True),
        _link(11, 1, sending=True, transmit=True),
    ]
    if reason == "communication":
        links[1] = replace(links[1], communication=False)
    elif reason == "association":
        links[1] = replace(links[1], is_sending=False)
    project = _sender_project(*links)
    if reason == "inactive":
        project.devices[0].inactive.add(11)
    assert _group_findings(project) == []


def test_read_responder_supplies_values_without_competing_with_a_transmitter() -> None:
    responder = replace(_link(11, 1, sending=True), read=True)
    assert _group_findings(_sender_project(responder)) == []
    assert (
        _group_findings(
            _sender_project(_link(10, 1, sending=True, transmit=True), responder)
        )
        == []
    )
    assert (
        len(_group_findings(_sender_project(replace(responder, is_sending=False)))) == 1
    )


def test_a_receiver_or_startup_read_does_not_supply_values() -> None:
    project = _sender_project(_link(10, 1, sending=True))
    project.devices[0].com_objects[0].flags.read_on_init = True
    (finding,) = _group_findings(project)
    assert finding.message == S.HEALTH_NO_SENDER.format(address="1/1/1", name="Light")


def test_only_inactive_links_do_not_report_a_missing_source() -> None:
    project = _sender_project(_link(10, 1, sending=True))
    project.devices[0].inactive.add(10)
    assert _group_findings(project) == []


def test_unknown_objects_do_not_prove_a_missing_source_or_add_a_transmitter() -> None:
    unknown = _link(12, 1, sending=True, transmit=True)
    project = _sender_project(unknown)
    project.devices = []
    assert _group_findings(project) == []
    project = _sender_project(_link(10, 1, sending=True, transmit=True))
    project.links.append(unknown)
    assert _group_findings(project) == []
    project = _sender_project(
        _link(10, 1, sending=True, transmit=True),
        _link(11, 1, sending=True, transmit=True),
    )
    project.links.append(unknown)
    assert len(_group_findings(project)) == 1


def test_defensive_unknown_flags_do_not_prove_a_missing_source() -> None:
    project = _sender_project(replace(_link(10, 1, sending=True), transmit=None))
    assert _group_findings(project) == []


def test_an_object_is_counted_once_even_with_duplicate_link_rows() -> None:
    link = _link(10, 1, sending=True, transmit=True)
    assert _group_findings(_sender_project(link, link)) == []
