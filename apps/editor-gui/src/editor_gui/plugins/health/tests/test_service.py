"""Health findings stay while a background task holds the project."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from editor_gui.plugins.health.service import HealthService


@dataclass
class _Ga:
    id: int
    address: str
    name: str
    datapoint_type: str | None = None


@dataclass
class _Project:
    revision: int = 1
    loading: bool = False
    is_open: bool = True
    devices: list[Any] = field(default_factory=list)
    group_addresses: list[_Ga] = field(default_factory=list)

    def missing_program_refs(self) -> list[str]:
        return []

    def get_assignments_for_ga(self, ga_id: int) -> list[Any]:
        return []


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
