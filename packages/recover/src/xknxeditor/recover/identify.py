"""Identify the application installed on a device and map it to the catalog.

A programmed device carries its application program id in the Application Program
interface object (object type 3, KNX Standard v3.0.0 3/5/1): PID_PROGRAM_VERSION
holds the 5 octets ``[manufacturer:2][application number:2][version:1]`` - the
same id :mod:`xknxeditor.download` writes during a download. Reading it back and
matching manufacturer + application number (+ version) against the catalog yields
the product refs a project device needs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from xknx.exceptions import XKNXException
from xknx.profile.const import ResourceGenericPropertyId, ResourceObjectType

from xknxeditor.download.errors import DownloadError, LoadStateError

if TYPE_CHECKING:
    from xknxeditor.catalog import ProductSummary
    from xknxeditor.download import DeviceProgrammer

OBJECT_TYPE_APPLICATION_PROGRAM = ResourceObjectType.OBJECT_APPLICATION_PROGRAM
# 5 octets [manufacturer:2][app number:2][version:1].
PID_PROGRAM_VERSION = ResourceGenericPropertyId.PID_PROGRAM_VERSION


@dataclass(frozen=True, slots=True)
class AppId:
    """An application program id as read from a device."""

    manufacturer_id: str
    application_number: int
    application_version: int


class ProductLookup(Protocol):
    """The catalog lookup :func:`match_application` needs (CatalogService fits)."""

    def find_products_for_application(
        self,
        *,
        manufacturer_id: str,
        application_number: int,
        application_version: int | None = None,
    ) -> list[ProductSummary]:
        """Return catalog products matching an application id."""
        ...


def parse_application_id(data: bytes) -> AppId | None:
    """Decode PID_PROGRAM_VERSION octets into an :class:`AppId`, or ``None``.

    Returns ``None`` for short data (an unprogrammed device may report fewer than
    the 5 octets). The manufacturer is rendered as ``M-XXXX`` with the same
    zero-padded, upper-case hex the catalog manufacturer ids use.
    """
    if len(data) < 5:
        return None
    manufacturer = int.from_bytes(data[0:2], "big")
    application_number = int.from_bytes(data[2:4], "big")
    application_version = data[4]
    return AppId(
        manufacturer_id=f"M-{manufacturer:04X}",
        application_number=application_number,
        application_version=application_version,
    )


async def read_application_id(
    programmer: DeviceProgrammer, *, attempts: int = 3, retry_delay: float = 0.1
) -> AppId | None:
    """Read the installed application program id from a device, or ``None``.

    Locates the Application Program object by type and reads PID_PROGRAM_VERSION.
    A clean read that yields no id (an unprogrammed device whose object exists but
    whose version is empty) returns ``None`` immediately. A *failed* read - the
    object walk or property read raising, e.g. a telegram lost on the tunnel - is
    retried up to ``attempts`` times, because that multi-telegram lookup is far
    more fragile than the single-telegram descriptor probe and a transient miss
    would otherwise mark a programmed device as unprogrammed.
    """
    for attempt in range(max(attempts, 1)):
        try:
            index = await programmer.locate_object(OBJECT_TYPE_APPLICATION_PROGRAM)
            data = await programmer.read_property(index, PID_PROGRAM_VERSION)
            # Confirm the value by reading it again: a rare non-erroring but wrong
            # reply (a stale/retransmitted frame) would otherwise yield a wrong
            # version, so identical devices could flip between matched and a
            # version-fallback "confirm". Require two consecutive equal reads.
            confirm = await programmer.read_property(index, PID_PROGRAM_VERSION)
        except (LoadStateError, DownloadError, XKNXException):
            if attempt + 1 < attempts:
                await asyncio.sleep(retry_delay)
            continue
        if data != confirm:
            if attempt + 1 < attempts:
                await asyncio.sleep(retry_delay)
            continue
        return parse_application_id(data)
    return None


def match_application(catalog: ProductLookup, app_id: AppId) -> list[ProductSummary]:
    """Return catalog products for an ``AppId``, preferring the exact version.

    Tries an exact manufacturer + application number + version match first; if the
    installed version is not in the catalog, falls back to matching manufacturer +
    application number alone so a slightly different version still resolves to the
    right product family. Empty when nothing matches (the caller should then offer
    an online-catalog fetch).
    """
    exact = catalog.find_products_for_application(
        manufacturer_id=app_id.manufacturer_id,
        application_number=app_id.application_number,
        application_version=app_id.application_version,
    )
    if exact:
        return exact
    return catalog.find_products_for_application(
        manufacturer_id=app_id.manufacturer_id,
        application_number=app_id.application_number,
    )
