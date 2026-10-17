"""Orchestrate recovering a single device's configuration from the bus.

Ties the pieces together for one device whose application is already known
(identified via :mod:`.identify` and loaded from the catalog): read the group
communication tables, read the parameter memory, decode both, and return a
:class:`RecoveredDevice`. Building this into a project is :mod:`.project_build`.
All bus access is read-only.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError, XKNXException
from xknx.telegram import IndividualAddress

from xknxeditor.download import DeviceProgrammer
from xknxeditor.download.session import connect

from .dossier import DeviceDossier, read_dossier
from .identify import AppId, read_application_id
from .parameters import RecoveredParameters, recover_parameters
from .read_config import (
    read_group_communication,
    read_parameter_memory,
    read_property_values,
)
from .tables_decode import DecodedGroupObject, DecodedLink

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram.address import IndividualAddressableType

    from xknxeditor.prod import Application
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI

# Recovery stages reported through the progress callback (see recover_device).
STAGE_GROUP_COMMUNICATION = "group_communication"
STAGE_PARAMETERS = "parameters"


@dataclass(frozen=True, slots=True)
class RecoveredDevice:
    """A device's configuration as read back from the bus."""

    address: str
    application_id: str
    device_address: int | None
    group_addresses: list[int]
    links: list[DecodedLink]
    group_objects: dict[int, DecodedGroupObject]
    parameters: RecoveredParameters
    mask_version: int = 0
    dossier: DeviceDossier = field(default_factory=DeviceDossier)
    # Raw code-segment bytes read off the device, kept for a forensic snapshot.
    parameter_segments: dict[str, bytes] | None = None
    # com object number -> reference id, resolved from a UI seeded with the
    # recovered structural parameters (so module instances match the device).
    # ``None`` falls back to the application's default-state mapping.
    com_object_refs: dict[int, str] | None = None


def seed_dynamic_ui(
    application: Application, parameter_values: Mapping[str, str]
) -> DynamicUI | None:
    """Build a dynamic UI evaluated with recovered parameter reference values.

    Seeding the reference values the device is programmed with (rather than the
    application defaults) makes the UI materialise the same module instances - and
    therefore the same runtime com object numbers - as the device. Seeding goes
    through the state constructor, not ``set_parameter_ref``, so inactive/conditional
    references are stored without the active-only check that would raise.

    A module-scoped override (``…_M-100_MI-1_P-…``) only routes into its module scope
    once that scope is materialised, so a first build discovers the module instances
    and a second build is seeded with them; without this the override is ignored and
    the map emits the default ref, which no longer matches the object the project
    persists once the override applies (links then resolve to nothing). Returns
    ``None`` when the application has no dynamic section.
    """
    from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
    from xknxeditor.namespaces.intermediate.parameter_instance_ref_t import (
        ParameterInstanceRef,
    )
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI

    if application.dynamic_ui() is None:
        return None
    pirs = [
        ParameterInstanceRef(ref_id=ref_id, value=value)
        for ref_id, value in parameter_values.items()
    ]
    probe = DynamicUI(application.program, parameter_instance_refs=pirs)
    module_instances = probe.get_module_instances()
    if not (module_instances and pirs):
        return probe
    return DynamicUI(
        application.program,
        parameter_instance_refs=pirs,
        module_instances=[
            ModuleInstance(id=instance_id, ref_id=ref_id)
            for instance_id, ref_id in module_instances
        ],
    )


def com_object_ref_by_number(
    application: Application, ui: DynamicUI | None = None
) -> dict[int, str]:
    """Map each com object number to a com object reference id in the application.

    Recovered links reference a com object by its number (as stored in the tables);
    a project links by com object reference id, so a consumer resolves the number
    through this map. The map is built from a RESOLVED dynamic UI so module
    instances contribute their runtime-offset numbers and instance-qualified
    reference ids - the same numbers the group communication tables carry. Pass a
    ``ui`` seeded with the recovered parameters (see :func:`seed_dynamic_ui`) so the
    module structure matches the device; without one the application defaults are
    used. When several references share a number the first seen wins.
    """
    if ui is None:
        ui = application.dynamic_ui()
    if ui is None:
        return {}
    from xknxeditor.prod.parser_v2.ui import UiComObject, UiParameterBlock, UiTab

    result: dict[int, str] = {}
    stack: list[object] = list(ui.ui())
    while stack:
        node = stack.pop()
        if isinstance(node, UiComObject):
            result.setdefault(node.number, node.ref_id)
        elif isinstance(node, UiTab | UiParameterBlock):
            stack.extend(node.children)
    # Fall back to the application's static com object references for numbers the
    # parameter-driven UI does not expose: a device can link objects that its
    # recoverable parameters no longer activate (optional channels on System B).
    # For the address/association tables only the number matters (group object flags
    # come from the device-read descriptors), so any reference of that number serves.
    from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

    indexer = ApplicationIndexer(application.program)
    for ref_id, ref in indexer.com_object_refs.items():
        com_object = indexer.com_objects.get(ref.ref_id)
        if com_object is not None:
            result.setdefault(com_object.number, ref_id)
    return result


async def recover_device(
    programmer: DeviceProgrammer,
    application: Application,
    *,
    address: str,
    progress: Callable[[str], None] | None = None,
) -> RecoveredDevice:
    """Read and decode one device's configuration over an open connection.

    ``application`` is the product application installed on the device (resolved
    from the catalog after identifying it). Reads the group communication tables
    and the parameter memory and decodes them. ``progress`` is called with the
    current stage (``STAGE_GROUP_COMMUNICATION``/``STAGE_PARAMETERS``) so a UI can
    show what the device read is doing. Read-only.
    """
    mask_version = await programmer.read_device_descriptor()
    dossier = await read_dossier(programmer)
    if progress is not None:
        progress(STAGE_GROUP_COMMUNICATION)
    group_communication = await read_group_communication(programmer, application)
    if progress is not None:
        progress(STAGE_PARAMETERS)
    parameter_segments = await read_parameter_memory(programmer, application)
    property_values = await read_property_values(programmer, application)
    parameters = recover_parameters(application, parameter_segments, property_values)
    # Resolve com object numbers against a UI seeded with the recovered parameters,
    # so module instances (and their runtime numbers) match the device rather than
    # the application defaults.
    seeded_ui = seed_dynamic_ui(application, parameters.values)
    com_object_refs = com_object_ref_by_number(application, seeded_ui)
    return RecoveredDevice(
        address=address,
        application_id=application.id,
        device_address=group_communication.device_address,
        group_addresses=group_communication.group_addresses,
        links=group_communication.links,
        group_objects=group_communication.group_objects,
        parameters=parameters,
        mask_version=mask_version,
        dossier=dossier,
        parameter_segments=parameter_segments,
        com_object_refs=com_object_refs,
    )


async def identify_device_at(
    xknx: XKNX, address: IndividualAddressableType
) -> AppId | None:
    """Open a connection to ``address``, read its application id, and close it.

    Returns ``None`` for an unprogrammed device (no application id). ``xknx`` has
    to be started. Read-only.
    """
    target = IndividualAddress(address)
    connection = await connect(xknx, target)
    try:
        return await read_application_id(DeviceProgrammer(connection))
    finally:
        with contextlib.suppress(ManagementConnectionError, XKNXException):
            await xknx.management.disconnect(target)


async def recover_device_at(
    xknx: XKNX,
    address: IndividualAddressableType,
    application: Application,
    *,
    negotiate_apdu: bool = True,
    progress: Callable[[str], None] | None = None,
    attempts: int = 3,
    retry_delay: float = 1.5,
) -> RecoveredDevice:
    """Open a connection to ``address``, recover the device, and close it again.

    ``xknx`` has to be started. When ``negotiate_apdu`` is set the device's maximum
    APDU length is read first so reads use larger, fewer telegrams. ``progress``
    reports the current read stage. Read-only.

    A transient transport failure (the KNXnet/IP tunnel dropping the point-to-point
    connection under sustained load - a ``DisconnectRequest``, a missing ACK, or a
    timeout that a table read did not tolerate) reconnects and retries the whole
    device up to ``attempts`` times, so one bus hiccup does not fail a device on a
    large scan. The last error is raised if every attempt fails.
    """
    target = IndividualAddress(address)
    last_exc: Exception | None = None
    for attempt in range(max(attempts, 1)):
        connection = None
        try:
            connection = await connect(xknx, target)
            programmer = DeviceProgrammer(connection)
            if negotiate_apdu:
                with contextlib.suppress(XKNXException):
                    programmer.max_apdu_length = await programmer.read_max_apdu_length()
            return await recover_device(
                programmer, application, address=str(target), progress=progress
            )
        except (ManagementConnectionError, XKNXException) as exc:
            last_exc = exc  # transport hiccup: reconnect and retry
        finally:
            if connection is not None:
                with contextlib.suppress(ManagementConnectionError, XKNXException):
                    await xknx.management.disconnect(target)
        if attempt + 1 < attempts:
            await asyncio.sleep(retry_delay)
    assert last_exc is not None
    raise last_exc
