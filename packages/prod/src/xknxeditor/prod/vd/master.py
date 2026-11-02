"""The parts of the KNX master data the conversion needs.

The master data is read by element names and attributes only, so any schema version works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


@dataclass(slots=True)
class MasterManufacturer:
    id: str
    number: int
    default_language: str | None


@dataclass(slots=True)
class MasterMaskVersion:
    id: str
    version: int
    management_model: str | None
    medium_type: str | None
    other_medium_type: str | None
    first_app_object_idx: int


@dataclass(slots=True)
class MasterDatapointType:
    id: str
    number: int
    size_in_bit: int
    subtypes: dict[int, str] = field(default_factory=lambda: {})


class MasterData:
    """Manufacturers, mask versions, medium types and datapoint types of a master data file."""

    manufacturers: dict[str, MasterManufacturer]
    manufacturers_by_number: dict[int, MasterManufacturer]
    mask_versions: dict[str, MasterMaskVersion]
    medium_types: dict[str, int]
    datapoint_types: list[MasterDatapointType]
    mask_entries: dict[str, dict[str, str]]  # mask version -> entry name -> entry id
    ids: set[str]

    def __init__(self, xml: bytes) -> None:
        self.manufacturers = {}
        self.manufacturers_by_number = {}
        self.mask_versions = {}
        self.medium_types = {}
        self.datapoint_types = []
        self.mask_entries = {}
        self.ids = set()
        root = ET.fromstring(xml)
        for el in root.iter():
            tag = el.tag.rpartition("}")[2]
            ident = el.get("Id")
            if ident is not None:
                self.ids.add(ident)
            if (
                tag == "Manufacturer"
                and ident is not None
                and el.get("KnxManufacturerId")
            ):
                m = MasterManufacturer(
                    ident,
                    int(el.get("KnxManufacturerId", "0")),
                    el.get("DefaultLanguage"),
                )
                self.manufacturers[ident] = m
                self.manufacturers_by_number[m.number] = m
            elif tag == "MaskVersion" and ident is not None:
                self.mask_versions[ident] = MasterMaskVersion(
                    ident,
                    int(el.get("MaskVersion", "0")),
                    el.get("ManagementModel"),
                    el.get("MediumTypeRefId"),
                    el.get("OtherMediumTypeRefId"),
                    _first_app_object_idx(el),
                )
                entries: dict[str, str] = {}
                for sub in el.iter():
                    if (
                        sub.tag.rpartition("}")[2] == "MaskEntry"
                        and sub.get("Name") is not None
                    ):
                        entries.setdefault(sub.get("Name", ""), sub.get("Id", ""))
                self.mask_entries[ident] = entries
            elif tag == "MediumType" and ident is not None:
                self.medium_types[ident] = int(el.get("Number", "0"))
            elif tag == "DatapointType" and ident is not None:
                dpt = MasterDatapointType(
                    ident, int(el.get("Number", "0")), int(el.get("SizeInBit", "0"))
                )
                for sub in el.iter():
                    if sub.tag.rpartition("}")[2] == "DatapointSubtype":
                        number = int(sub.get("Number", "0"))
                        dpt.subtypes.setdefault(number, sub.get("Id", ""))
                self.datapoint_types.append(dpt)

    def manufacturer(self, ident: str | int) -> MasterManufacturer | None:
        if isinstance(ident, int):
            return self.manufacturers_by_number.get(ident)
        return self.manufacturers.get(ident)

    def datapoint(self, number: int, subtype: int = -1) -> str | None:
        """The id of a datapoint (sub)type; the type's id when the subtype is unknown."""
        for dpt in self.datapoint_types:
            if dpt.number == number:
                if subtype >= 0 and subtype in dpt.subtypes:
                    return dpt.subtypes[subtype]
                return dpt.id
        return None


def _first_app_object_idx(mask: ET.Element) -> int:
    for hawk in mask.iter():
        if hawk.tag.rpartition("}")[2] != "HawkConfigurationData" or hawk.get(
            "LegacyVersion"
        ):
            continue
        for el in hawk.iter():
            if (
                el.tag.rpartition("}")[2] == "Feature"
                and el.get("Name") == "FirstAppObjectIdx"
            ):
                return int(el.get("Value", "0"))
        return 0
    return 0
