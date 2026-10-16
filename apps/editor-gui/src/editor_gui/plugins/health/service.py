"""Project health checks: derive actionable commissioning problems from the live project state.

Pure computation over the GUI :class:`ProjectService` — no imgui. Results are cached by the
project revision so the panel can call :meth:`findings` every frame cheaply."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    category: str
    message: str
    device_node_id: int | None = None  # navigation target (select this device) when set
    ga_id: int | None = None  # navigation target (select this group address) when set


class HealthService:
    def __init__(self, project: ProjectService) -> None:
        self._project = project
        self._cache: list[Finding] | None = None
        self._cache_revision = -1

    def findings(self) -> list[Finding]:
        """Current health findings, recomputed only when the project changed."""
        rev = self._project.revision
        if self._cache is None or self._cache_revision != rev:
            self._cache = self._compute()
            self._cache_revision = rev
        return self._cache

    def _compute(self) -> list[Finding]:
        if not self._project.is_open:
            return []
        out: list[Finding] = []
        devices = self._project.devices
        out.extend(self._check_addresses(devices))
        out.extend(self._check_missing_apps())
        out.extend(self._check_com_objects(devices))
        out.extend(self._check_group_addresses())
        return out

    def _check_addresses(self, devices: list[Device]) -> list[Finding]:
        out: list[Finding] = []
        seen: set[str] = set()
        for d in devices:
            ia = d.individual_address or ""
            if not ia:
                out.append(
                    Finding(
                        Severity.WARNING,
                        "address",
                        f"{d.name}: no individual address",
                        d.node_id,
                    )
                )
                continue
            if ia in seen:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "address",
                        f"Duplicate individual address {ia} ({d.name})",
                        d.node_id,
                    )
                )
            seen.add(ia)
        return out

    def _check_missing_apps(self) -> list[Finding]:
        refs = self._project.missing_program_refs()
        if not refs:
            return []
        return [
            Finding(
                Severity.WARNING,
                "catalog",
                f"{len(refs)} device(s) skipped: application not in catalog",
            )
        ]

    def _check_com_objects(self, devices: list[Device]) -> list[Finding]:
        out: list[Finding] = []
        for d in devices:
            unlinked = 0
            for co in d.get_visible_com_objects():
                if not co.flags.communication or co.db_id is None:
                    continue
                if not self._project.get_links_for_com_object(co.db_id):
                    unlinked += 1
            if unlinked:
                out.append(
                    Finding(
                        Severity.INFO,
                        "com-object",
                        f"{d.name}: {unlinked} unlinked communication object(s)",
                        d.node_id,
                    )
                )
        return out

    def _check_group_addresses(self) -> list[Finding]:
        out: list[Finding] = []
        for ga in self._project.group_addresses:
            if not ga.datapoint_type:
                out.append(
                    Finding(
                        Severity.WARNING,
                        "group-address",
                        f"{ga.address} {ga.name}: no datapoint type",
                        ga_id=ga.id,
                    )
                )
            assignments = self._project.get_assignments_for_ga(ga.id)
            if assignments:
                senders = sum(1 for a in assignments if a.is_sending)
                if senders == 0:
                    out.append(
                        Finding(
                            Severity.WARNING,
                            "group-address",
                            f"{ga.address} {ga.name}: no sending object",
                            ga_id=ga.id,
                        )
                    )
                elif senders > 1:
                    out.append(
                        Finding(
                            Severity.WARNING,
                            "group-address",
                            f"{ga.address} {ga.name}: {senders} sending objects",
                            ga_id=ga.id,
                        )
                    )
        return out
