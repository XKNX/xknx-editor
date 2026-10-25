"""Round-trip verification of a recovered device against the bus.

Re-encodes the recovered group communication and compares it, read-only, to what
is actually on the device using :func:`xknxeditor.download.preflight`. A clean result
(zero changed bytes) means the reconstruction re-encodes exactly to the device's
tables - a strong correctness signal. Verification is limited to the group
communication scope: re-encoding parameters would drive the product evaluator with
values for possibly-inactive references, which is exactly the fragile path recovery
avoids, so parameter round-tripping is intentionally not attempted here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xknx.telegram import IndividualAddress

from xknxeditor.download import DownloadScope, GroupCommunication, GroupObjectLink
from xknxeditor.download.download import preflight

from .recover import com_object_ref_by_number

if TYPE_CHECKING:
    from xknx import XKNX

    from xknxeditor.download import PreflightReport
    from xknxeditor.prod import Application, MasterData

    from .recover import RecoveredDevice


def build_group_communication(
    recovered: RecoveredDevice, application: Application
) -> GroupCommunication:
    """Build the download ``GroupCommunication`` from a recovered device's links.

    The group object table is built from the device-read group objects
    (``recovered.group_objects``), not re-resolved from the application UI: a device
    can carry objects that its recoverable parameters no longer activate in the UI
    (optional channels on System B), so the read descriptors are authoritative.
    """
    from xknxeditor.download.tables import com_object_flag_byte

    number_to_ref = recovered.com_object_refs or com_object_ref_by_number(application)
    links = [
        GroupObjectLink(
            com_object_ref_id=number_to_ref[link.group_object_number],
            group_address=link.group_address,
            sending=link.sending,
        )
        for link in recovered.links
        if link.group_object_number in number_to_ref
    ]
    descriptors = {
        number: (
            com_object_flag_byte(
                priority=go.priority,
                communication=go.communication,
                read=go.read,
                write=go.write,
                transmit=go.transmit,
                update=go.update,
                read_on_init=bool(go.read_on_init),
            ),
            go.size_code,
        )
        for number, go in recovered.group_objects.items()
    }
    device_address = (
        recovered.device_address
        if recovered.device_address is not None
        else IndividualAddress(recovered.address).raw
    )
    return GroupCommunication(
        device_address=device_address,
        links=links,
        group_object_descriptors=descriptors,
    )


async def verify_recovered(
    xknx: XKNX,
    recovered: RecoveredDevice,
    application: Application,
    *,
    master: MasterData | None = None,
) -> PreflightReport:
    """Read-only re-encode-and-diff of the recovered group communication vs. device.

    Returns a :class:`PreflightReport`; ``total_changed_bytes == 0`` means the
    recovered tables re-encode exactly to what is on the device. ``xknx`` must be
    started; nothing is written.
    """
    group_communication = build_group_communication(recovered, application)
    return await preflight(
        xknx,
        recovered.address,
        application,
        master=master,
        group_communication=group_communication,
        scope=DownloadScope.GROUP_COMMUNICATION,
    )
