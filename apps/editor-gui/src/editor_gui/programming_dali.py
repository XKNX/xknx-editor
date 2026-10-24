"""Glue between the GUI/connection stack and the MDT DALI commissioning library.

Opens a (optionally Tool-Key secured) point-to-point connection to a DALI gateway, builds an
``MdtDaliCommissioner`` on the same ``DeviceProgrammer`` the download flows use, runs one caller
supplied coroutine against it, and always closes the connection. Kept out of the plugins so the
connection service stays thin and this is unit-testable.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from xknx import XKNX
from xknx.telegram import IndividualAddress

from xknxeditor.dali import MdtDaliCommissioner, MdtDaliConnection, profile_for
from xknxeditor.dali.connection import Programmer
from xknxeditor.dali.siemens import is_siemens_n141_03
from xknxeditor.download import DeviceProgrammer, DeviceSecurity

# Reuse the download package's own connection builders (plain or Tool-Key secured) so a DALI session
# gets the same secure handling as a normal download. Repo-internal; intentional cross-module use.
from xknxeditor.download.session import apdu_overhead, management_session

if TYPE_CHECKING:
    from editor_gui.device import Device
    from editor_gui.plugins.project.service import ProjectService
    from xknxeditor.dali import DaliBusState, EcgAssignment, Scene


def is_dali_dca_app(app_id: str | None) -> bool:
    """Whether ``app_id`` is a Type-A DALI gateway (MDT / Theben) — gates the commissioning tab."""
    return profile_for(app_id) is not None


def dali_object_index_base(app_id: str | None) -> int:
    """The KNX object-index base for ``app_id``'s DALI channel 0 (vendor-specific); 6 if unknown."""
    profile = profile_for(app_id)
    return profile.object_index_base if profile is not None else 6


def dali_channel_count(order_number: str | None) -> int:
    """Number of independent DALI bus channels a gateway has (1 if unknown).

    MDT encodes it in the order number: ``SCN-DA641`` is a 1x64 gateway (one bus), ``SCN-DA642``
    a 2x64 (two buses). The digit right after ``DA64`` is the channel count.
    """
    if order_number:
        match = re.search(r"DA64(\d)", order_number.upper())
        if match:
            return max(1, int(match.group(1)))
    return 1


async def run_dali_operation[T](
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    channel: int,
    op: Callable[[MdtDaliCommissioner], Awaitable[T]],
    *,
    object_index_base: int = 6,
    app_id: str | None = None,
) -> T:
    """Open a connection to ``address``, run ``op`` against an ``MdtDaliCommissioner``, then close.

    ``security`` (from the keyring) enables a Tool-Key secured session, matching the download flows.
    ``object_index_base`` is the vendor's DALI object index for channel 0 (see dali profiles).
    ``app_id`` picks the ``DaliProfile`` so the connection applies per-vendor element-size overrides
    (e.g. Theben P64's 10-byte Group_Status); when ``None`` the connection uses MDT defaults.
    """
    target = IndividualAddress(address)
    manager = management_session(xknx, target, security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        # Negotiate the device's real APDU length (defaults to the mandatory 15). Multi-byte DALI
        # property writes (and a secure session's overhead) would not fit the 15-octet default.
        programmer.max_apdu_length = await programmer.read_max_apdu_length()
        profile = profile_for(app_id) if app_id is not None else None
        commissioner = MdtDaliCommissioner(
            MdtDaliConnection(programmer, channel, object_index_base, profile=profile)
        )
        return await op(commissioner)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()


async def run_siemens_dali_operation[T](
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    op: Callable[[Programmer], Awaitable[T]],
) -> T:
    """Open a connection to ``address``, run ``op`` against a raw ``Programmer``, then close.

    Siemens N141/03 drives DALI through standard A_PropertyValue_Read on interface objects 35/36
    (no vendor-specific commissioner wrapper), so the glue hands the operation the programmer
    directly rather than a vendor-specific commissioner object.
    """
    target = IndividualAddress(address)
    manager = management_session(xknx, target, security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        programmer.max_apdu_length = await programmer.read_max_apdu_length()
        return await op(programmer)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()


__all__ = [
    "dali_channel_count",
    "dali_object_index_base",
    "is_dali_dca_app",
    "is_siemens_n141_03",
    "run_dali_operation",
    "run_siemens_dali_operation",
]


def persist_dali_backup(
    project: ProjectService,
    device: Device,
    scan: DaliBusState,
    assignments: list[EcgAssignment],
    scenes: list[Scene],
    channel: int,
    *,
    label: str,
) -> None:
    """Write a DALI channel's state back into the project blob after a bus commissioning run
    (one undo step named ``label``).

    MDT (gc16) blobs are fully modelled: the scan + edits are merged onto the existing
    ``DaliGC16-Backup-Store`` (``.N`` for channel N+1), preserving templates/descriptions.
    Theben/IPAS (e64pro) blobs use a wider schema we do not model, so only the group
    assignments are patched into the existing ``Dalie64-Backup-Store`` and every other
    element is preserved byte-exact; if no such blob exists yet it is left untouched.
    """
    from xknxeditor.dali import merge_commissioning_blob, patch_assignments_in_blob
    from xknxeditor.dali.profiles import backup_blob_name

    app_id = device.app.id if device.app is not None else None
    profile = profile_for(app_id)
    if profile is None:
        return
    name = backup_blob_name(profile.backup_blob_base, channel)
    existing = project.device_binary_data(device, name)
    if profile.key == "mdt":
        metadata = project.get_project_metadata()
        blob = merge_commissioning_blob(
            existing=existing,
            state=scan,
            assignments=assignments,
            scenes=scenes,
            individual_address=device.individual_address,
            project_name=metadata.name if metadata is not None else "",
        )
    elif existing is not None:
        # e64pro: patch group assignments into the vendor's blob, preserve the rest.
        blob = patch_assignments_in_blob(existing, assignments)
    else:
        return  # e64pro device with no existing blob: cannot synthesize the wide schema
    project.persist_dali_backup(device, name, blob, label=label)
