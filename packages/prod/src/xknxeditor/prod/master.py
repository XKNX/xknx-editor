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

    def mask(self, mask_id: str) -> ir.MaskVersion | None:
        """The declaration for a device mask, if available."""
        holder = self.raw.mask_versions if self.raw is not None else None
        if holder is None:
            return None
        return next((mask for mask in holder.mask_version if mask.id == mask_id), None)

    def compatible_masks(self, mask_id: str) -> tuple[str, ...]:
        """Older application masks explicitly supported by this device mask."""
        mask = self.mask(mask_id)
        holder = mask.downward_compatible_masks if mask is not None else None
        return (
            tuple(
                dict.fromkeys(
                    item.ref_id
                    for item in holder.downward_compatible_mask
                    if item.ref_id != mask_id
                )
            )
            if holder is not None
            else ()
        )

    def mask_mediums(self, mask_id: str) -> tuple[str, ...]:
        """Declared device media; empty when the mask is unknown."""
        mask = self.mask(mask_id)
        if mask is None:
            return ()
        return tuple(
            dict.fromkeys(
                medium
                for medium in (mask.medium_type_ref_id, mask.other_medium_type_ref_id)
                if medium
            )
        )

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
