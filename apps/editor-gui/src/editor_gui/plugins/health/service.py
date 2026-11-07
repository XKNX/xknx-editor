"""Project health checks: derive actionable commissioning problems from the live project state.

Pure computation over the GUI :class:`ProjectService` — no imgui. Results are cached by the
project revision so the panel can call :meth:`findings` every frame cheaply."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from editor_gui.device import device_label
from editor_gui.plugins.health.strings import S
from editor_gui.strings import get_locale

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.proj.core.service import DeviceLinkInfo


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
        self._cache_key: tuple[int, int, str, frozenset[int]] | None = None
        # The group addresses (raw) another program sends to, e.g. Home Assistant: they have a
        # sender without a device of the project. Set by the app.
        self.external_senders: Callable[[], frozenset[int]] = frozenset

    def findings(self) -> list[Finding]:
        """Current health findings, recomputed only when the project or the language changed.

        While a background task holds the project (opening it, a language switch rebuilding the
        devices) its reads are empty placeholders, so the last findings stay until it is done."""
        if self._project.loading:
            return self._cache or []
        key = (
            self._project.revision,
            id(self._project.devices),
            get_locale(),
            self.external_senders(),
        )
        if self._cache is None or self._cache_key != key:
            self._cache = self._compute()
            self._cache_key = key
        return self._cache

    def _compute(self) -> list[Finding]:
        if not self._project.is_open:
            return []
        out: list[Finding] = []
        devices = self._project.devices
        # Every link of the project in one read: one per group object and group address would be
        # thousands of queries after each edit.
        links = self._project.device_links()
        out.extend(self._check_addresses(devices))
        out.extend(self._check_missing_apps())
        out.extend(
            self._check_com_objects(devices, {link.com_object_id for link in links})
        )
        out.extend(self._check_group_addresses(links))
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
                        S.HEALTH_NO_ADDRESS.format(device=device_label(d)),
                        d.node_id,
                    )
                )
                continue
            if ia in seen:
                out.append(
                    Finding(
                        Severity.ERROR,
                        "address",
                        S.HEALTH_DUPLICATE_ADDRESS.format(
                            address=ia, device=d.display_name
                        ),
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
                S.HEALTH_SKIPPED(len(refs)).format(count=len(refs)),
            )
        ]

    def _check_com_objects(
        self, devices: list[Device], linked: set[int]
    ) -> list[Finding]:
        """Devices with group objects that communicate but have no group address; ``linked`` are
        the group objects (their ``db_id``) that have one."""
        out: list[Finding] = []
        for d in devices:
            unlinked = 0
            for co in d.get_visible_com_objects():
                if not co.flags.communication or co.db_id is None:
                    continue
                if co.db_id not in linked:
                    unlinked += 1
            if unlinked:
                out.append(
                    Finding(
                        Severity.INFO,
                        "com-object",
                        S.HEALTH_UNLINKED(unlinked).format(
                            device=device_label(d), count=unlinked
                        ),
                        d.node_id,
                    )
                )
        return out

    def _check_group_addresses(self, links: list[DeviceLinkInfo]) -> list[Finding]:
        """Group addresses without a datapoint type, and linked ones without exactly one
        sender."""
        assigned = {link.group_address_id for link in links}
        senders_of = Counter(link.group_address_id for link in links if link.is_sending)
        external = self.external_senders()
        out: list[Finding] = []
        for ga in self._project.group_addresses:
            if not ga.datapoint_type:
                out.append(
                    Finding(
                        Severity.WARNING,
                        "group-address",
                        S.HEALTH_NO_DPT.format(address=ga.address, name=ga.name),
                        ga_id=ga.id,
                    )
                )
            if ga.id in assigned:
                senders = senders_of[ga.id]
                if senders == 0 and ga.raw not in external:
                    out.append(
                        Finding(
                            Severity.WARNING,
                            "group-address",
                            S.HEALTH_NO_SENDER.format(address=ga.address, name=ga.name),
                            ga_id=ga.id,
                        )
                    )
                elif senders > 1:
                    out.append(
                        Finding(
                            Severity.WARNING,
                            "group-address",
                            S.HEALTH_SENDERS(senders).format(
                                address=ga.address, name=ga.name, count=senders
                            ),
                            ga_id=ga.id,
                        )
                    )
        return out
