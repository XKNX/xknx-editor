"""Rank catalog layouts against the application identity read from a device."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from editor_gui.plugins.catalog.service import CatalogService
    from xknxeditor.prod.master import MasterData
    from xknxeditor.recover import AppId


@dataclass(frozen=True)
class CatalogCandidate:
    product_ref_id: str
    hardware2program_ref_id: str
    application_id: str
    product_name: str
    mask_version: str
    application_version: int | None
    mediums: tuple[str, ...]
    match_reason: str

    @property
    def label(self) -> str:
        """Distinguish products and application variants in a compact picker."""
        return (
            f"{self.product_name} | {self.application_id} | {self.mask_version} | "
            f"{self.hardware2program_ref_id}"
        )


def catalog_candidates(
    catalog: CatalogService, app_id: AppId, mask_id: str, master: MasterData | None
) -> list[CatalogCandidate]:
    """Return every candidate in the best tier, with exact versions preceding mask matches.

    Compatibility is directional and requires an explicit master declaration. Hardware
    media must overlap the actual device mask's media whenever both are known.
    """
    compatible = master.compatible_masks(mask_id) if master is not None else ()
    device_mediums = master.mask_mediums(mask_id) if master is not None else ()
    tiers: list[dict[tuple[str, str, str], CatalogCandidate]] = [{}, {}, {}, {}]
    for product in catalog.find_products_for_application(
        manufacturer_id=app_id.manufacturer_id,
        application_number=app_id.application_number,
    ):
        if product.application_id is None:
            continue
        facts = catalog.product_facts(product.hardware2program_ref_id)
        if facts is None or facts.mask_version is None:
            continue
        mask = facts.mask_version
        if mask != mask_id and mask not in compatible:
            continue
        if (
            device_mediums
            and facts.mediums
            and not set(device_mediums).intersection(facts.mediums)
        ):
            continue
        version_differs = product.application_version != app_id.application_version
        mask_differs = mask != mask_id
        tier = 2 * version_differs + mask_differs
        reason = (
            "exact",
            "compatible_mask",
            "different_version",
            "different_version_compatible_mask",
        )[tier]
        key = (
            product.product_ref_id,
            product.hardware2program_ref_id,
            product.application_id,
        )
        tiers[tier][key] = CatalogCandidate(
            product.product_ref_id,
            product.hardware2program_ref_id,
            product.application_id,
            product.name or product.order_number or product.product_ref_id,
            mask,
            product.application_version,
            facts.mediums,
            reason,
        )
    for candidates in tiers:
        if candidates:
            return sorted(candidates.values(), key=lambda item: item.label)
    return []
