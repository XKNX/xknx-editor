"""Health findings: kept while a background task holds the project, and the link checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from editor_gui.device import ComObjectFlags
from editor_gui.plugins.health.service import HealthService, Severity
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

    @property
    def display_name(self) -> str:
        return self.name

    def get_visible_com_objects(self) -> list[_ComObject]:
        return self.com_objects


@dataclass
class _Project:
    revision: int = 1
    loading: bool = False
    is_open: bool = True
    devices: list[Any] = field(default_factory=list)
    group_addresses: list[_Ga] = field(default_factory=list)
    links: list[DeviceLinkInfo] = field(default_factory=list)
    link_reads: int = 0

    def missing_program_refs(self) -> list[str]:
        return []

    def device_links(self) -> list[DeviceLinkInfo]:
        self.link_reads += 1
        return self.links


def _link(com_object_id: int, ga_id: int, *, sending: bool) -> DeviceLinkInfo:
    return DeviceLinkInfo(
        device_id=1,
        group_address_id=ga_id,
        is_sending=sending,
        communication=True,
        read=False,
        write=True,
        transmit=False,
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
        group_addresses=[
            _Ga(1, "1/1/1", "One sender", "1.001"),
            _Ga(2, "1/1/2", "No sender", "1.001"),
            _Ga(3, "1/1/3", "Two senders", "1.001"),
            _Ga(4, "1/1/4", "Not linked", "1.001"),
        ],
        links=[
            _link(10, 1, sending=True),
            _link(11, 1, sending=False),
            _link(12, 2, sending=False),
            _link(13, 3, sending=True),
            _link(14, 3, sending=True),
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
