"""Plugins of the old tool the converter replaces by options: the byte swapper download plugin
and the comfort UI plugin of ABB, the optimized download plugin of Hager (see
:mod:`.optimized`). Their baggages are removed from the programs, and from the package where no
program of the manufacturer uses them anymore; the hardware of programs whose ABB plugin was
replaced no longer needs the plugin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .baggage import BaggageDoc
    from .hardware import HardwareDoc

# program numbers whose plugin stays (programs of M-0002, or of M-0002 as original manufacturer)
_PLUGIN_MUST_REMAIN = frozenset(
    {41048, 41073, 41115, 41116, 41119, 41120, 41121, 41122}
)

_GUID = re.compile(
    r"\s*(?:\{(?P<b>[0-9a-fA-F-]{36})\}|\((?P<p>[0-9a-fA-F-]{36})\)|(?P<d>[0-9a-fA-F-]{36})"
    r"|(?P<n>[0-9a-fA-F]{32}))\s*"
)
_HYPHENS = (8, 13, 18, 23)


def guid(text: str) -> str:
    """The class id ``text`` as 32 upper case hex digits (raises ValueError as an invalid one
    fails the conversion of the program)."""
    m = _GUID.fullmatch(text)
    if m is None:
        raise ValueError(f"Unrecognized Guid format: {text}")
    if m["n"] is not None:
        return m["n"].upper()
    value = m["b"] or m["p"] or m["d"] or ""
    if any(value[i] != "-" for i in _HYPHENS) or value.count("-") != 4:
        raise ValueError(f"Unrecognized Guid format: {text}")
    return value.replace("-", "").upper()


def plugin_must_remain(
    application_id: str, manufacturer: str, original: str | None
) -> bool:
    """Whether the plugin of this ABB program is kept (no replacement)."""
    number = int(application_id.split("_A-", 1)[1][:4], 16)
    if number not in _PLUGIN_MUST_REMAIN:
        return False
    return manufacturer == "M-0002" or original == "M-0002"


@dataclass
class Replacer:
    guids: frozenset[str]
    baggages: tuple[str, ...]
    # the programs whose plugin was replaced
    applications: list[str] = field(default_factory=list[str])
    # the baggages each program still refers to, and those removed from it
    uses: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    removed: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def replaces(self, clsid: str) -> bool:
        return guid(clsid) in self.guids

    def add_application(self, application_id: str) -> None:
        if application_id not in self.applications:
            self.applications.append(application_id)

    def add_ref(self, application_id: str, ref: str) -> None:
        self.uses.setdefault(application_id, []).append(ref)

    def is_baggage(self, ref: str) -> bool:
        return any(b in ref for b in self.baggages)

    def remove_ref(self, application_id: str, ref: str) -> None:
        self.removed.setdefault(application_id, []).append(ref)
        used = self.uses.get(application_id)
        if used is None:
            return
        if ref in used:
            used.remove(ref)
        if not used:
            del self.uses[application_id]

    def remove_unused_baggages(self, docs: list[BaggageDoc]) -> list[BaggageDoc]:
        """Baggages removed from replaced programs that no program uses anymore leave the
        package; a ``Baggages.xml`` left without baggages too (the docs that stay)."""
        if not self.applications:
            return docs
        for manufacturer in dict.fromkeys(a[:6] for a in self.applications):
            doc = next(
                (d for d in docs if d.manufacturer.lower() == manufacturer.lower()),
                None,
            )
            if doc is None:
                continue
            done: list[str] = []
            for application_id in self.applications:
                for ref in self.removed.get(application_id, []):
                    if ref in done:
                        continue
                    done.append(ref)
                    if any(
                        ref in used
                        for other, used in self.uses.items()
                        if other.startswith(manufacturer)
                    ):
                        continue
                    for baggage in doc.baggages:
                        if baggage.get("Id") == ref:
                            doc.baggages.remove(baggage)
                            doc.remove(baggage)
                            break
            if not doc.baggages:
                docs = [d for d in docs if d is not doc]
        return docs

    def reset_no_download_without_plugin(self, docs: list[HardwareDoc]) -> None:
        """The hardware of replaced programs can be downloaded without plugin."""
        for manufacturer in dict.fromkeys(a[:6].lower() for a in self.applications):
            doc = next(
                (d for d in docs if d.manufacturer.lower() == manufacturer), None
            )
            if doc is None:
                continue
            for application_id in self.applications:
                if not application_id.lower().startswith(manufacturer):
                    continue
                for hardware in doc.hardware:
                    if hardware.get("NoDownloadWithoutPlugin") != "true":
                        continue
                    container = hardware.find("Hardware2Programs")
                    h2ps = container.children if container is not None else []
                    for h2p in h2ps:
                        refs = [
                            c for c in h2p.children if c.tag == "ApplicationProgramRef"
                        ]
                        if refs and refs[0].get("RefId") == application_id:
                            del hardware.attrs["NoDownloadWithoutPlugin"]
                            break


def byte_swapper() -> Replacer:
    return Replacer(
        frozenset(
            {"15053D1A12DE462AAEE9E967A27EFF5B", "A81BCB7611334316929D1409CEA0EC4D"}
        ),
        (
            "M-0002_BG-AbbUsU",
            "M-0002_BG-ABB.5FDNLD",
            "M-0007_BG-AbbUsU",
            "M-0007_BG-ABB.5FDNLD",
            "M-0007_BG-USU",
        ),
    )


def comfort_plugin() -> Replacer:
    return Replacer(
        frozenset(
            {"728509510DFE400B807EF875C89E4365", "1F9E15379A424E879D75CCA0065EF9A1"}
        ),
        (
            "M-0002_BG--ABB.5FMC10",
            "M-0002_BG--DeviceConfigurationConverter",
            "M-0002_BG-StdParamDialogMitKonv",
            "M-0007_BG--ABB.5FMC10",
            "M-0007_BG--DeviceConfigurationConverter",
            "M-0007_BG-StdParamDialogMitKonv",
        ),
    )


def optimized_download() -> Replacer:
    return Replacer(
        frozenset(
            {
                "000B8A16FD82431988A929EBBAF186DE",
                "8EF0B7D0DF4549A49E92001EE739B968",
                "B385ADFEA7D245039657D49E97FF9FF4",
                "EE07B2F083074D7EB51C7FD982981BA3",
            }
        ),
        (
            "OptimizeDownload.2EInstaller",
            "OptimizeDownload2.2EInstaller",
            "OptimizeDownload.2EPlugin",
            "OptimizeDownload2.2EPlugin",
            "OptimizeDL-Data",
            "OptimizeDL2-Data",
        ),
    )
