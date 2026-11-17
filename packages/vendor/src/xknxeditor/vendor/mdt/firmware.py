"""Firmware version inspection and product release metadata."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from io import BytesIO
from typing import Protocol
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from xknx.exceptions import XKNXException

from xknxeditor.download.errors import DownloadError

MAX_ARCHIVE_SIZE = 128 * 1024 * 1024
MAX_CATALOG_SIZE = 2 * 1024 * 1024


@dataclass(frozen=True)
class BootloaderInfo:
    version_code: int
    crc_type_code: int
    max_telegram_length: int
    encryption_type_code: int


def decode_bootloader_info(data: bytes | None) -> BootloaderInfo | None:
    """Decode reported numeric fields without guessing version or algorithm names."""
    if data is None or len(data) != 28 or data[0] == 0xFF or data[3] < 12:
        return None
    return BootloaderInfo(int.from_bytes(data[:2], "little"), data[2], data[3], data[8])


@dataclass(frozen=True)
class FirmwareInfo:
    manufacturer_id: int
    version: str | None
    order_info: bytes | None
    hardware_type: bytes | None
    version_property: bytes | None
    bsl_info: bytes | None = None
    identity_error: str | None = None

    @property
    def bootloader(self) -> BootloaderInfo | None:
        return decode_bootloader_info(self.bsl_info)

    @property
    def hardware_key(self) -> bytes | None:
        return (
            self.bsl_info[4:8]
            if self.bsl_info is not None and len(self.bsl_info) == 28
            else None
        )


def installed_version(order_info: bytes | None, version: bytes | None) -> str | None:
    if order_info is None or len(order_info) != 10:
        return None
    if order_info[8] == 0xFF:
        if version is None or len(version) != 2 or version == b"\xff\xff":
            return None
        word = int.from_bytes(version, "big")
        text = f"{word >> 11}.{(word >> 6) & 31}.{word & 63}"
        beta = order_info[9] >> 4 == 11
    else:
        if order_info[9] < 0x10:
            return None
        text = f"{order_info[9] >> 4}.{order_info[9] & 15}"
        beta = order_info[8] == 11
    return text + ("-beta" if beta else "")


class PropertyReader(Protocol):
    async def read_property(self, object_index: int, property_id: int) -> bytes: ...

    async def read_bsl_info_packet(self, sequence: int) -> bytes: ...


async def read_bsl_info(reader: PropertyReader) -> bytes:
    """Assemble the fixed identity structure from informational replies."""
    result = bytearray()
    total = None
    for sequence in range(28):
        packet = await reader.read_bsl_info_packet(sequence)
        if (
            len(packet) < 5
            or packet[0] != 0x91
            or not 1 <= packet[1] <= 28
            or packet[2] != sequence
            or packet[3] == 0
            or len(packet) < 4 + packet[3]
            or (total is not None and packet[1] != total)
        ):
            raise ValueError("Invalid MDT firmware identity packet")
        total = packet[1]
        result.extend(packet[4 : 4 + packet[3]])
        if len(result) > 28:
            raise ValueError("Oversized MDT firmware identity")
        if sequence == total - 1:
            if decode_bootloader_info(bytes(result)) is None:
                raise ValueError("Invalid MDT firmware identity")
            return bytes(result)
    raise ValueError("Incomplete MDT firmware identity")


async def read_firmware(reader: PropertyReader) -> FirmwareInfo:
    """Read version properties and identity without enabling firmware programming."""
    manufacturer = await reader.read_property(0, 12)
    if manufacturer != b"\x00\x83":
        raise ValueError("Device does not report MDT manufacturer 0083")

    async def optional(pid: int, size: int) -> bytes | None:
        try:
            data = await reader.read_property(0, pid)
        except (DownloadError, XKNXException):
            return None
        return data if len(data) == size else None

    order = await optional(15, 10)
    version = await optional(25, 2) if order and order[8] == 0xFF else None
    hardware = await optional(78, 6)
    bsl_info = None
    identity_error = None
    try:
        bsl_info = await read_bsl_info(reader)
    except (DownloadError, XKNXException, ValueError) as exc:
        identity_error = str(exc)
    return FirmwareInfo(
        131,
        installed_version(order, version),
        order,
        hardware,
        version,
        bsl_info,
        identity_error,
    )


@dataclass(frozen=True)
class FirmwareRelease:
    filename: str
    version: str
    product: str
    specific_product: str
    hardware_key: str
    modified: str
    source: str
    blacklisted: bool

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.hardware_key, self.version, self.specific_product


_PRODUCT_HARDWARE_TYPES = {
    "SCN-P360D4.03": bytes.fromhex("000000000314"),
    "SCN-P360K4.03": bytes.fromhex("00000000031b"),
    "SCN-P360D3.03": bytes.fromhex("000000000316"),
    "SCN-P360K3.03": bytes.fromhex("000000000317"),
    "SCN-G360D3.03": bytes.fromhex("00000000031d"),
    "SCN-G360K3.03": bytes.fromhex("000000000319"),
}


@dataclass(frozen=True)
class FirmwareCatalog:
    source: str
    releases: tuple[FirmwareRelease, ...]

    @property
    def latest_modified(self) -> str | None:
        dates = [r.modified for r in self.releases if r.modified]
        return max(dates, key=datetime.fromisoformat) if dates else None

    def for_product(self, product: str) -> tuple[FirmwareRelease, ...]:
        """Catalog entries for a product name, without a device compatibility check."""
        return tuple(
            r
            for r in self.public_releases()
            if r.product == product and r.specific_product in ("none", product)
        )

    def public_releases(self) -> tuple[FirmwareRelease, ...]:
        blocked = {r.identity for r in self.releases if r.blacklisted}
        return tuple(
            r
            for r in self.releases
            if r.source == "Public" and r.identity not in blocked
        )

    def compatible_releases(
        self, product: str, info: FirmwareInfo
    ) -> tuple[FirmwareRelease, ...]:
        """Match the device key and any additional product/hardware constraints."""
        if info.manufacturer_id != 131 or info.hardware_key is None:
            return ()
        key = info.hardware_key.hex()
        return tuple(
            sorted(
                (
                    r
                    for r in self.public_releases()
                    if r.hardware_key == key
                    and (
                        r.specific_product == "none"
                        or (
                            r.specific_product == product
                            and (
                                product not in _PRODUCT_HARDWARE_TYPES
                                or info.hardware_type
                                == _PRODUCT_HARDWARE_TYPES[product]
                            )
                        )
                    )
                ),
                key=lambda r: version_key(r.version),
            )
        )

    def variants_for_product(
        self, product: str
    ) -> tuple[tuple[str, tuple[FirmwareRelease, ...]], ...]:
        """Keep incompatible hardware/software variants separate."""
        variants: dict[str, list[FirmwareRelease]] = {}
        for release in self.for_product(product):
            variants.setdefault(release.hardware_key, []).append(release)
        return tuple(
            (key, tuple(sorted(releases, key=lambda r: version_key(r.version))))
            for key, releases in sorted(variants.items())
        )

    def dump(self) -> bytes:
        return json.dumps(
            asdict(self), ensure_ascii=False, separators=(",", ":")
        ).encode()


def version_key(version: str) -> tuple[tuple[int, ...], bool, str]:
    match = re.fullmatch(r"(\d+(?:\.\d+){0,3})(?:-([A-Za-z0-9.]+))?", version)
    if match is None:
        raise ValueError("Invalid firmware version")
    parts = tuple(map(int, match[1].split(".")))
    parts += (0,) * max(0, 3 - len(parts))
    return parts + (-1,) * (4 - len(parts)), match[2] is None, match[2] or ""


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 512


def _validate(catalog: FirmwareCatalog) -> FirmwareCatalog:
    if not _valid_text(catalog.source):
        raise ValueError("Invalid firmware catalog source")
    if not catalog.releases or len(catalog.releases) > 10000:
        raise ValueError("Invalid firmware catalog size")
    for entry in catalog.releases:
        values = (
            entry.filename,
            entry.version,
            entry.product,
            entry.specific_product,
            entry.hardware_key,
            entry.modified,
            entry.source,
        )
        if any(not _valid_text(v) for v in values):
            raise ValueError("Invalid firmware metadata")
        if (
            not entry.filename
            or not entry.product
            or type(entry.blacklisted) is not bool
        ):
            raise ValueError("Invalid firmware metadata")
        version_key(entry.version)
        if not re.fullmatch("[0-9a-f]{8}", entry.hardware_key):
            raise ValueError("Invalid firmware hardware key")
        date = datetime.fromisoformat(entry.modified)
        if date.tzinfo is None:
            raise ValueError("Firmware metadata date has no timezone")
    return catalog


def load_catalog(data: bytes) -> FirmwareCatalog:
    if len(data) > MAX_CATALOG_SIZE:
        raise ValueError("Firmware catalog is too large")
    try:
        raw = json.loads(data)
        return _validate(
            FirmwareCatalog(
                raw["source"], tuple(FirmwareRelease(**r) for r in raw["releases"])
            )
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid firmware catalog") from exc


@lru_cache(maxsize=1)
def bundled_catalog() -> FirmwareCatalog:
    return load_catalog(
        files(__package__).joinpath("firmware-catalog.json").read_bytes()
    )


def _xml_entries(data: bytes) -> tuple[FirmwareRelease, ...]:
    if len(data) > MAX_CATALOG_SIZE:
        raise ValueError("Firmware catalog is too large")
    root = ET.fromstring(data)
    if root.tag != "ArrayOfFirmwareCache":
        raise ValueError("Not an MDT firmware catalog")
    result: list[FirmwareRelease] = []
    for node in root:

        def text(key: str, node: ET.Element = node) -> str:
            return node.findtext(key) or ""

        flag = text("IsBlacklisted")
        if flag not in ("true", "false"):
            raise ValueError("Invalid firmware blacklist flag")
        result.append(
            FirmwareRelease(
                text("FilenameFW"),
                text("Info"),
                text("DeviceInfo"),
                text("SpecificProduct"),
                base64.b64decode(text("HWSWKey"), validate=True).hex(),
                text("LastModified"),
                text("Source"),
                flag == "true",
            )
        )
    return tuple(result)


def _member(archive: ZipFile, name: str, limit: int) -> bytes:
    entries = [e for e in archive.infolist() if e.filename == name]
    if len(entries) != 1 or entries[0].file_size > limit:
        raise ValueError(f"Missing, duplicate or oversized firmware metadata: {name}")
    with archive.open(entries[0]) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Firmware metadata is too large")
    return data


def import_catalog(data: bytes, source: str) -> FirmwareCatalog:
    """Read public release metadata and its blacklist from an app or Firmware.zip."""
    if len(data) > MAX_ARCHIVE_SIZE:
        raise ValueError("Firmware archive is too large")
    try:
        with ZipFile(BytesIO(data)) as outer:
            if "Firmware.zip" in outer.namelist():
                data = _member(outer, "Firmware.zip", MAX_ARCHIVE_SIZE)
        with ZipFile(BytesIO(data)) as archive:
            entries = _xml_entries(
                _member(archive, "Databases/firmware_cache.xml", MAX_CATALOG_SIZE)
            )
            entries += tuple(
                replace(entry, blacklisted=True)
                for entry in _xml_entries(
                    _member(archive, "Databases/blacklist_cache.xml", MAX_CATALOG_SIZE)
                )
            )
        catalog = _validate(FirmwareCatalog(source, entries))
        if len(catalog.dump()) > MAX_CATALOG_SIZE:
            raise ValueError("Firmware catalog is too large")
        return catalog
    except (
        ET.ParseError,
        BadZipFile,
        binascii.Error,
        OSError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        raise ValueError(f"Invalid firmware metadata: {exc}") from exc
