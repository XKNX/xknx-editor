"""Global `MasterData` (datapoint types, manufacturers, medium types...).

Built by `parse_master_xml` from a standalone master XML. Raw IR lives on `.raw`; id-to-name
convenience maps get added as consumers require them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from xknxeditor.namespaces import detect_version

from .parse_cache import cached_to_ir

if TYPE_CHECKING:
    from pathlib import Path

    from xknxeditor.namespaces import intermediate as ir


@dataclass(slots=True)
class MasterData:
    raw: ir.MasterData | None
    _medium_types: dict[str, str] | None = field(default=None, repr=False)
    _manufacturers: dict[str, str] | None = field(default=None, repr=False)

    @property
    def medium_types(self) -> dict[str, str]:
        """Medium type id to display name."""
        if self._medium_types is None:
            holder = self.raw.medium_types if self.raw is not None else None
            self._medium_types = (
                {m.id: (m.name or m.text or m.id) for m in holder.medium_type if m.id}
                if holder is not None
                else {}
            )
        return self._medium_types

    @property
    def manufacturers(self) -> dict[str, str]:
        """Manufacturer id to display name."""
        if self._manufacturers is None:
            holder = self.raw.manufacturers if self.raw is not None else None
            self._manufacturers = (
                {m.id: (m.name or m.id) for m in holder.manufacturer if m.id}
                if holder is not None
                else {}
            )
        return self._manufacturers


def parse_master_xml(xml_bytes: bytes, cache_dir: Path | None = None) -> MasterData:
    """Build `MasterData` from a master XML; with ``cache_dir``, through the parse cache (the
    same XML is then parsed only once)."""
    knx = cached_to_ir(xml_bytes, detect_version(xml_bytes), cache_dir)
    return MasterData(raw=knx.master_data)
