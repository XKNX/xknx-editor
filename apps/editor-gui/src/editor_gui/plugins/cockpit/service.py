"""Commissioning cockpit data: one row per project device with catalog + health status.

Rows are built from the live project and cached by the project revision, its device list and the
language, so the per-frame table render never hits the database. "Attention" is derived from the
shared health checks."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from editor_gui.device import address_order
from editor_gui.plugins.cockpit.strings import S
from editor_gui.plugins.health.service import HealthService, Severity
from editor_gui.strings import get_locale

_SEVERITY_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
# The colon that ends the device a finding starts with ("1.1.2  Name: 10 unlinked ..."): with a
# space before it in French, full width (U+FF1A) in Chinese and Japanese.
_LABEL_COLON = re.compile("^\\s*[:\uff1a]\\s*")

if TYPE_CHECKING:
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.proj.core.service import DeviceInfo


@dataclass
class CockpitRow:
    node_id: int
    individual_address: str
    name: str
    product_name: str
    order_number: str
    commissioning: str = ""  # short "loaded" label for the Loaded column
    commissioning_tooltip: str = ""  # per-flag detail + serial / last download
    loaded_count: int = -1  # loaded commissioning flags, -1 without device info
    issues: list[tuple[Severity, str]] = field(
        default_factory=list[tuple[Severity, str]]
    )
    description: str = ""

    @property
    def needs_attention(self) -> bool:
        return bool(self.issues)

    @property
    def product(self) -> str:
        return self.product_name or self.order_number

    @property
    def top_severity(self) -> Severity | None:
        """Most severe finding on this device, or None when there are no findings."""
        if not self.issues:
            return None
        return min((sev for sev, _ in self.issues), key=lambda s: _SEVERITY_RANK[s])

    @property
    def issue_summary(self) -> str:
        """Short inline text for the status column: the most severe finding's message without the
        device it starts with (the row shows it already), plus ``(+N)`` when further findings
        exist."""
        if not self.issues:
            return ""
        _, message = min(self.issues, key=lambda i: _SEVERITY_RANK[i[0]])
        # The health checks name the device as the device lists do: "address  name".
        label = (
            f"{self.individual_address}  {self.name}"
            if self.individual_address
            else self.name
        )
        if label and message.startswith(label):
            # The colon after it may have a space before it in some languages.
            rest = _LABEL_COLON.sub("", message[len(label) :], count=1)
            if rest != message[len(label) :]:
                message = rest
        extra = len(self.issues) - 1
        return f"{message}  (+{extra})" if extra else message

    @property
    def issue_tooltip(self) -> str:
        return "\n".join(message for _, message in self.issues)


_SORT_KEYS: tuple[Callable[[CockpitRow], Any], ...] = (
    lambda r: address_order(r.individual_address),
    lambda r: r.name.casefold(),
    lambda r: r.product.casefold(),
    lambda r: r.loaded_count,
    lambda r: len(r.issues),
)


def sort_rows(
    rows: list[CockpitRow], column: int, descending: bool = False
) -> list[CockpitRow]:
    """``rows`` ordered by the given table column, ties by individual address."""
    key = _SORT_KEYS[column]
    return sorted(
        rows,
        key=lambda r: (key(r), address_order(r.individual_address), r.node_id),
        reverse=descending,
    )


def _commissioning(info: DeviceInfo | None) -> tuple[str, str, int]:
    """Return a short "loaded" label, a detailed tooltip and the number of loaded flags."""
    if info is None:
        return "", "", -1
    flags = [
        (S.COCKPIT_LOADED_TOOLTIP_IA, info.individual_address_loaded),
        (S.COCKPIT_LOADED_TOOLTIP_APP, info.application_program_loaded),
        (S.COCKPIT_LOADED_TOOLTIP_COMM, info.communication_part_loaded),
        (S.COCKPIT_LOADED_TOOLTIP_MEDIUM, info.medium_config_loaded),
        (S.COCKPIT_LOADED_TOOLTIP_PARAMS, info.parameters_loaded),
    ]
    done = sum(1 for _, v in flags if v)
    if done == 0:
        label = S.COCKPIT_LOADED_NONE
    elif done == len(flags):
        label = S.COCKPIT_LOADED_FULL
    else:
        label = S.COCKPIT_LOADED_PARTIAL.format(done=done, total=len(flags))
    lines = [f"{'✓' if v else '·'} {name}" for name, v in flags]
    if info.serial_number:
        lines.append(S.COCKPIT_LOADED_TOOLTIP_SERIAL.format(serial=info.serial_number))
    if info.last_download:
        lines.append(
            S.COCKPIT_LOADED_TOOLTIP_LAST_DOWNLOAD.format(when=info.last_download)
        )
    return label, "\n".join(lines), done


class CockpitService:
    def __init__(self, project: ProjectService) -> None:
        self._project = project
        self._health = HealthService(project)
        self._cache: list[CockpitRow] | None = None
        self._cache_key: tuple[int, int, str] | None = None

    def skipped_count(self) -> int:
        """Devices dropped from the view because their application is not in the catalog."""
        return len(self._project.missing_program_refs())

    def rows(self) -> list[CockpitRow]:
        """One row per device, built again only when the project or the language changed.

        While a background task holds the project (opening it, rebuilding the devices) its reads
        are empty placeholders, so the last rows stay until it is done: built from them, the
        table stayed empty until the next edit."""
        if self._project.loading:
            return self._cache or []
        key = (self._project.revision, id(self._project.devices), get_locale())
        if self._cache is None or self._cache_key != key:
            self._cache = self._build()
            self._cache_key = key
        return self._cache

    def _build(self) -> list[CockpitRow]:
        if not self._project.is_open:
            return []
        by_device: dict[int, list[tuple[Severity, str]]] = {}
        for f in self._health.findings():
            if f.device_node_id is not None:
                by_device.setdefault(f.device_node_id, []).append(
                    (f.severity, f.message)
                )
        rows: list[CockpitRow] = []
        for d in self._project.devices:
            info = self._project.get_device_info(d.node_id)
            label, tooltip, loaded_count = _commissioning(info)
            rows.append(
                CockpitRow(
                    node_id=d.node_id,
                    individual_address=d.individual_address or "",
                    name=d.display_name,
                    product_name=info.product_name if info else "",
                    order_number=info.order_number if info else "",
                    commissioning=label,
                    commissioning_tooltip=tooltip,
                    loaded_count=loaded_count,
                    issues=by_device.get(d.node_id, []),
                    description=d.description,
                )
            )
        return rows
