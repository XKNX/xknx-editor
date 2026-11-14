"""Recovery catalog matching uses device capabilities and keeps layout variants visible."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui.plugins.recover.matching import catalog_candidates
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from xknxeditor.catalog.core.products import ProductFacts, ProductSummary
from xknxeditor.prod.master import MasterData, parse_master_xml
from xknxeditor.recover import AppId


def master_data() -> MasterData:
    return parse_master_xml(b"""<KNX xmlns="http://knx.org/xml/project/23">
      <MasterData Id="MD-1" Version="1" Signature="QUJD"><MaskVersions>
        <MaskVersion Id="MV-0010" Name="Basic" MaskVersion="16" ManagementModel="Bcu1" MediumTypeRefId="MT-0"/>
        <MaskVersion Id="MV-0011" Name="Old" MaskVersion="17" ManagementModel="Bcu1" MediumTypeRefId="MT-0">
          <DownwardCompatibleMasks><DownwardCompatibleMask RefId="MV-0010"/></DownwardCompatibleMasks>
        </MaskVersion>
        <MaskVersion Id="MV-0012" Name="Older" MaskVersion="18" ManagementModel="Bcu1" MediumTypeRefId="MT-0"/>
        <MaskVersion Id="MV-0013" Name="New" MaskVersion="19" ManagementModel="Bcu1" MediumTypeRefId="MT-0">
          <DownwardCompatibleMasks><DownwardCompatibleMask RefId="MV-0011"/><DownwardCompatibleMask RefId="MV-0012"/></DownwardCompatibleMasks>
        </MaskVersion>
        <MaskVersion Id="MV-1013" Name="Powerline" MaskVersion="4115" ManagementModel="Bcu1" MediumTypeRefId="MT-1">
          <DownwardCompatibleMasks><DownwardCompatibleMask RefId="MV-0011"/></DownwardCompatibleMasks>
        </MaskVersion>
      </MaskVersions></MasterData></KNX>""")


def product(
    key: str = "one",
    *,
    mask: str = "MV-0012",
    version: int = 0x23,
    number: int = 0x6322,
    medium: str | None = "MT-0",
    manufacturer: str = "M-0002",
    ref: str | None = None,
) -> tuple[ProductSummary, ProductFacts]:
    return (
        ProductSummary(
            ref or f"P-{key}",
            f"HP-{key}",
            key,
            key,
            f"{manufacturer}_A-{number:04X}-{version:02X}-{key}",
            manufacturer,
            "Manufacturer",
            version,
        ),
        ProductFacts(mask, False, None, None, (medium,) if medium else ()),
    )


class Catalog:
    def __init__(self, *items: tuple[ProductSummary, ProductFacts]) -> None:
        self.items = list(items)
        self.io_lock = threading.RLock()

    def find_products_for_application(self, **query: Any) -> list[ProductSummary]:
        return [
            p
            for p, _ in self.items
            if p.manufacturer_id == query["manufacturer_id"]
            and f"_A-{query['application_number']:04X}-" in (p.application_id or "")
        ]

    def product_facts(self, program_id: str) -> ProductFacts:
        return next(f for p, f in self.items if p.hardware2program_ref_id == program_id)

    def get_application(self, app_id: str) -> Any:
        return SimpleNamespace(id=app_id)


def identify(
    catalog: Any, mask: int = 0x0013, master: MasterData | None = None
) -> tuple[RecoverService, RecoverEntry]:
    service = RecoverService(
        SimpleNamespace(catalog=catalog, connection=SimpleNamespace(master=master))
    )
    entry = RecoverEntry("1.1.5", mask, app_id=AppId("M-0002", 0x6322, 0x23))
    service.entries = [entry]
    service._identify_catalog(entry)
    return service, entry


@pytest.mark.parametrize(
    "number,version,mask",
    [
        (0x6326, 0x21, "MV-0012"),
        (0x6327, 0x23, "MV-0012"),
        (0x6327, 0x12, "MV-0012"),
        (0x6322, 0x23, "MV-0012"),
        (0x3202, 0x21, "MV-0011"),
        (0x9702, 0x13, "MV-0013"),
    ],
)
def test_reported_application_ids(number: int, version: int, mask: str) -> None:
    candidates = catalog_candidates(
        Catalog(product(number=number, version=version, mask=mask)),
        AppId("M-0002", number, version),
        "MV-0013",
        master_data(),
    )
    assert len(candidates) == 1
    assert candidates[0].mask_version == mask
    assert candidates[0].match_reason == (
        "exact" if mask == "MV-0013" else "compatible_mask"
    )


def test_exact_version_precedes_exact_mask_and_all_variants_survive() -> None:
    catalog = Catalog(
        product("wrong-version", mask="MV-0013", version=0x24),
        product("a", mask="MV-0011", ref="same"),
        product("b", mask="MV-0012", ref="same"),
        product("a", mask="MV-0011", ref="same"),
    )
    service, entry = identify(catalog, master=master_data())
    assert entry.candidates == 2 and entry.ambiguous and not entry.selected
    assert {c.mask_version for c in entry.catalog_candidates} == {"MV-0011", "MV-0012"}
    service.choose_candidate(entry.address, 1)
    assert entry.hardware2program_ref_id == "HP-b"
    assert entry.match_reason == "compatible_mask"
    assert not entry.selected
    assert entry.mask_version == 0x0013
    assert "MV-0013" in service.entry_detail(
        entry
    ) and "MV-0012" in service.entry_detail(entry)


def test_exact_mask_wins_within_same_version() -> None:
    _, entry = identify(
        Catalog(product(), product("exact", mask="MV-0013")), master=master_data()
    )
    assert entry.candidates == 1 and entry.selected and not entry.ambiguous
    assert entry.match_reason == "exact" and entry.product_name == "exact"


@pytest.mark.parametrize("mask,medium", [(0x0013, "MT-0"), (0x1013, "MT-1")])
def test_hardware_medium_not_definition_mask_decides(mask: int, medium: str) -> None:
    _, entry = identify(
        Catalog(
            product("tp", mask="MV-0011"), product("pl", mask="MV-0011", medium="MT-1")
        ),
        mask=mask,
        master=master_data(),
    )
    assert entry.candidates == 1 and entry.selected and not entry.ambiguous
    assert entry.catalog_candidates[0].mediums == (medium,)


def test_unknown_medium_is_not_a_conflict() -> None:
    _, entry = identify(Catalog(product(medium=None)), master=master_data())
    assert entry.recoverable


@pytest.mark.parametrize("mask", [0x0010, 0x0701])
def test_no_reverse_or_unrelated_compatibility(mask: int) -> None:
    _, entry = identify(Catalog(product()), mask=mask, master=master_data())
    assert not entry.recoverable


def test_no_inferred_transitive_compatibility() -> None:
    _, entry = identify(Catalog(product(mask="MV-0010")), master=master_data())
    assert not entry.recoverable


def test_missing_master_keeps_exact_matches_only() -> None:
    service, entry = identify(Catalog(product(), product("exact", mask="MV-0013")))
    assert entry.product_name == "exact" and entry.selected
    assert not entry.compatibility_checked
    assert "unavailable" in service.entry_detail(entry)
    _, missing = identify(Catalog(product()))
    assert not missing.recoverable


def test_wrong_version_is_last_resort_and_requires_confirmation() -> None:
    _, entry = identify(Catalog(product(version=0x24)), master=master_data())
    assert entry.recoverable and entry.ambiguous and not entry.selected
    assert entry.match_reason == "different_version_compatible_mask"


def test_medium_conflict_rejected_even_for_exact_mask() -> None:
    _, entry = identify(
        Catalog(product(mask="MV-0013", medium="MT-1")), master=master_data()
    )
    assert not entry.recoverable


def test_unrelated_manufacturers_and_numbers_do_not_match() -> None:
    _, entry = identify(
        Catalog(product(manufacturer="M-0001"), product(number=0x6323)),
        master=master_data(),
    )
    assert not entry.recoverable


def test_candidate_change_guards() -> None:
    service, entry = identify(Catalog(product()), master=master_data())
    with pytest.raises(ValueError, match="out of range"):
        service.choose_candidate(entry.address, -1)
    entry.applied = True
    with pytest.raises(ValueError, match="scan again"):
        service.choose_candidate(entry.address, 0)
