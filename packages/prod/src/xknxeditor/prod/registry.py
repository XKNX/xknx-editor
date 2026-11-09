"""`Registry`: the queryable index for a loaded .knxprod.

Keyed by id throughout (as KNX is): flat stores (`id -> object`) plus edges
(`parent id -> [child ids]`). Objects are never mutated or pre-linked; helpers resolve refs
on demand and return `id -> object` dicts instead of lists to scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .application import Application
from .catalog import CatalogItem, CatalogSection
from .hardware import DeviceProgram, Hardware, Product
from .master import MasterData


@dataclass(frozen=True, slots=True)
class ApplicationFacts:
    """What identifies an application program, known without its parsed program in memory."""

    application_number: int
    application_version: int
    mask_version: str
    is_secure_enabled: bool


@dataclass(slots=True)
class Registry:
    master: MasterData

    # stores: id -> object
    hardware: dict[str, Hardware] = field(default_factory=dict[str, Hardware])
    products: dict[str, Product] = field(default_factory=dict[str, Product])
    programs: dict[str, DeviceProgram] = field(default_factory=dict[str, DeviceProgram])
    applications: dict[str, Application] = field(default_factory=dict[str, Application])
    # The facts of each of `applications`, read while it was parsed.
    application_facts: dict[str, ApplicationFacts] = field(
        default_factory=dict[str, ApplicationFacts]
    )
    catalog_sections: dict[str, CatalogSection] = field(
        default_factory=dict[str, CatalogSection]
    )
    catalog_items: dict[str, CatalogItem] = field(
        default_factory=dict[str, CatalogItem]
    )

    # edges: parent id -> [child ids]
    manufacturer_to_hardware: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )
    manufacturer_to_section: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )
    hardware_to_product: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )
    hardware_to_program: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )
    program_to_application: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )
    section_to_subsection: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )
    section_to_item: dict[str, list[str]] = field(default_factory=dict[str, list[str]])

    def update_master(self, master: MasterData) -> None:
        """Swap out the global master data without touching the rest."""
        self.master = master

    # --- resolvers (each returns id -> object) ------------------------------
    def hardware_for_manufacturer(self, manufacturer_id: str) -> dict[str, Hardware]:
        return {
            h: self.hardware[h]
            for h in self.manufacturer_to_hardware.get(manufacturer_id, [])
        }

    def products_for_hardware(self, hardware_id: str) -> dict[str, Product]:
        return {
            p: self.products[p] for p in self.hardware_to_product.get(hardware_id, [])
        }

    def programs_for_hardware(self, hardware_id: str) -> dict[str, DeviceProgram]:
        return {
            p: self.programs[p] for p in self.hardware_to_program.get(hardware_id, [])
        }

    def applications_for_program(self, program_id: str) -> dict[str, Application]:
        return {
            a: self.applications[a]
            for a in self.program_to_application.get(program_id, [])
            if a in self.applications
        }

    def applications_for_hardware(self, hardware_id: str) -> dict[str, Application]:
        out: dict[str, Application] = {}
        for program_id in self.hardware_to_program.get(hardware_id, []):
            out.update(self.applications_for_program(program_id))
        return out

    def product_for_item(self, item: CatalogItem) -> Product | None:
        return self.products.get(item.product_ref_id or "")

    def program_for_item(self, item: CatalogItem) -> DeviceProgram | None:
        return self.programs.get(item.hardware2_program_ref_id or "")

    def sections_for_manufacturer(
        self, manufacturer_id: str
    ) -> dict[str, CatalogSection]:
        return {
            s: self.catalog_sections[s]
            for s in self.manufacturer_to_section.get(manufacturer_id, [])
        }

    def subsections(self, section_id: str) -> dict[str, CatalogSection]:
        return {
            s: self.catalog_sections[s]
            for s in self.section_to_subsection.get(section_id, [])
        }

    def items_for_section(self, section_id: str) -> dict[str, CatalogItem]:
        return {
            i: self.catalog_items[i] for i in self.section_to_item.get(section_id, [])
        }
