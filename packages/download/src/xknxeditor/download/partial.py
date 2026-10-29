"""Whether a device holds the application a partial download needs.

A partial download (parameters and/or group communication) only fits a device that
runs the projected application. Before such a download the device's mask version,
application load state, application id and PEI program state are compared with the
application, using the locations the mask version's resources define.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

from .errors import DownloadError, PropertyAccessRejected

if TYPE_CHECKING:
    from xknxeditor.prod import Application

    from .programmer import DeviceProgrammer
    from .resources import MaskResources

logger = logging.getLogger(__name__)

# Load state value of a loaded application or PEI program.
_LOADED = 1
_UNLOADED = 0
# Manufacturer id the legacy task segment patch accepts on the device.
_PATCHED_MANUFACTURER = 0x0002


class DifferentProgramLoaded(DownloadError):
    """The device does not run the projected application, so a partial download is
    not possible."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            "The application program currently loaded in the device does not comply "
            "with the projected application program. You may try a full instead of a "
            "partial download."
        )
        self.reason = reason


class IncompatibleMaskVersion(DownloadError):
    """The device's mask version does not fit the application."""

    def __init__(self, found: str, expected: str) -> None:
        super().__init__(f"Incompatible mask version: {found} instead of {expected}")
        self.found = found
        self.expected = expected


class FullDownloadDeclined(DownloadError):
    """The user declined the full download a partial one had to become."""


def application_id(application: Application, length: int = 5) -> bytes:
    """The application id as the device stores it: manufacturer, number and version,
    with a one-octet manufacturer in the four-octet form."""
    manufacturer = int(application.manufacturer_id.split("-")[-1], 16)
    program = application.program
    head = (
        manufacturer.to_bytes(2, "big") if length >= 5 else bytes([manufacturer & 0xFF])
    )
    return (
        head
        + program.application_number.to_bytes(2, "big")
        + bytes([program.application_version & 0xFF])
    )


async def _read(
    programmer: DeviceProgrammer, resources: MaskResources, name: ResourceName
) -> bytes | None:
    """The resource's value, or ``None`` when the mask or the device does not have it
    (e.g. a device without a PEI program object)."""
    location = resources.location(name)
    if location is None or location.address_space == ResourceAddrSpace.NONE:
        return None
    reference = resources.property_ref(name)
    if reference is not None:
        try:
            data = await programmer.read_property(*reference)
        except PropertyAccessRejected:
            logger.debug("partial download: device has no %s", name.value)
            return None
        return data or None
    address = resources.memory_address(name)
    length = resources.length(name)
    if address is None or not length:
        return None
    return await programmer.read_memory(address, length)


async def partial_download_mismatch(
    programmer: DeviceProgrammer,
    application: Application,
    resources: MaskResources | None,
    application2: Application | None = None,
) -> str | None:
    """Why the device cannot take a partial download of ``application``, or ``None``.

    ``application2`` is the product's second application program (its PEI program), which the
    device has to run as well. Raises :class:`IncompatibleMaskVersion` when the device's mask
    version does not fit; that error stops any download.
    """
    program = application.program
    mask = f"MV-{await programmer.read_device_descriptor():04X}"
    if not mask.startswith(program.mask_version) and not program.linkable:
        raise IncompatibleMaskVersion(mask, program.mask_version)
    if resources is None:
        return None
    status = await _read(programmer, resources, ResourceName.APPLICATION_LOAD_STATUS)
    if status is not None and status[:1] != bytes([_LOADED]):
        return (
            "Partial download not possible because application program not loaded: "
            f"load status={status[0] if status else None}"
        )
    loaded = await _read(programmer, resources, ResourceName.APPLICATION_ID)
    if loaded is not None:
        found, expected = program_id(loaded), program_id_of(application)
        options = program.static.options
        if found != expected:
            if (
                options is not None
                and options.legacy_patch_manufacturer_id_in_task_segment
                and found is not None
                and found[0] == _PATCHED_MANUFACTURER
                and found[1:] == expected[1:]
            ):
                logger.info(
                    "Partial download: Different application program: %s instead of %s "
                    "accepted because of LegacyDoNotPatchManufacturerId",
                    _program_text(found),
                    _program_text(expected),
                )
            else:
                return (
                    "Partial download not possible because different application program: "
                    f"{_program_text(found)} instead of {_program_text(expected)}"
                )
    options = program.static.options
    if options is not None and options.legacy_allow_partial_download_if_ap2_mismatch:
        return None
    pei = await _read(programmer, resources, ResourceName.PEIPROG_LOAD_STATUS)
    if application2 is not None:
        if pei is not None and pei[:1] != bytes([_LOADED]):
            return (
                "Partial download not possible because PEI program not loaded: "
                f"load status={pei[0] if pei else None}"
            )
        pei_id = await _read(programmer, resources, ResourceName.PEIPROG_ID)
        if pei_id is not None:
            found, expected = program_id(pei_id), program_id_of(application2)
            if found != expected:
                return (
                    "Partial download not possible because different PEI program: "
                    f"{_program_text(found)} instead of {_program_text(expected)}"
                )
        return None
    if pei is None or pei[:1] == bytes([_UNLOADED]):
        return None
    if pei[:1] == bytes([_LOADED]):
        logger.warning(
            "Partial download without PEI program, but PEI program in device is loaded"
        )
        return None
    reason = (
        "Partial download without PEI program, but PEI program in device is not unloaded: "
        f"load status={pei[0] if pei else None}"
    )
    pei_id = await _read(programmer, resources, ResourceName.PEIPROG_ID)
    if pei_id is not None and program_id(pei_id) == (0, 0, 0):
        logger.warning(reason)
        logger.warning("Accepted because loaded PEI program is 00 0000 00")
        return None
    return reason


def program_id(data: bytes) -> tuple[int, int, int] | None:
    """The (manufacturer, number, version) of a program id as a device stores it: five octets,
    or four with a one-octet manufacturer; ``None`` for another length."""
    if len(data) == 5:
        return (
            int.from_bytes(data[:2], "big"),
            int.from_bytes(data[2:4], "big"),
            data[4],
        )
    if len(data) == 4:
        return data[0], int.from_bytes(data[1:3], "big"), data[3]
    return None


def program_id_of(application: Application) -> tuple[int, int, int]:
    """The (manufacturer, number, version) of ``application``."""
    program = application.program
    return (
        int(application.manufacturer_id.split("-")[-1], 16),
        program.application_number,
        program.application_version & 0xFF,
    )


def _program_text(program: tuple[int, int, int] | None) -> str:
    if program is None:
        return "?"
    manufacturer, number, version = program
    return f"{manufacturer} {number:04X} {version:02X}"
