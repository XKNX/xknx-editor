"""Orchestrate recovering a single device's configuration from the bus.

Ties the pieces together for one device whose application is already known
(identified via :mod:`.identify` and loaded from the catalog): read the group
communication tables, read the parameter memory, decode both, and return a
:class:`RecoveredDevice`. Adding it to a project is left to the caller (the editor's
recover plugin, ``editor_gui.plugins.recover.service``). All bus access is read-only.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from xknx.exceptions import ManagementConnectionError, XKNXException
from xknx.telegram import IndividualAddress

from xknxeditor.download import (
    DEFAULT_MAX_APDU_LENGTH,
    DeviceProgrammer,
    apdu_restriction,
    negotiated_apdu_length,
)
from xknxeditor.download.errors import MemoryReadNotAnswered
from xknxeditor.download.session import connect

from .dossier import DeviceDossier, read_dossier
from .identify import AppId, read_application_id
from .parameters import RecoveredParameters, RecoveryEffort, recover_parameters
from .read_config import (
    read_group_communication,
    read_parameter_memory,
    read_property_values,
)
from .tables_decode import DecodedGroupObject, DecodedLink

if TYPE_CHECKING:
    from xknx import XKNX
    from xknx.telegram.address import IndividualAddressableType

    from xknxeditor.prod import Application, MasterData
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI
    from xknxeditor.prod.parser_v2.encode import TypedPropertyKey

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
    # The property values read off the device, kept with the segments to search again.
    property_values: dict[TypedPropertyKey, bytes] | None = None
    # The effort the parameters were searched with.
    effort: RecoveryEffort = RecoveryEffort.MEDIUM


def seed_dynamic_ui(
    application: Application,
    parameter_values: Mapping[str, str],
    project_code_page: str | None = None,
    *,
    module_instances: Sequence[tuple[str, str]] | None = None,
    evaluated: bool = False,
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
    persists once the override applies (links then resolve to nothing). Given the
    ``module_instances`` (``(instance id, module ref id)``, as
    :meth:`~xknxeditor.prod.parser_v2.dynamic.DynamicUI.get_module_instances` lists them)
    the first build is left out. Returns ``None`` when the application has no dynamic
    section. ``project_code_page`` as for
    :func:`~xknxeditor.recover.parameters.recover_parameters`.

    With ``evaluated`` the UI may come back evaluated already: when seeding with the module
    instances the first build found gives the state the first build started from (each value
    in the scope it was in; an instance it lists that holds no value is made by the evaluation
    that reaches it, and the root's instances are found by their ids, not their order), the
    second build would evaluate it alike, and the first one is returned instead.
    """
    from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
    from xknxeditor.prod.parser_v2.dynamic import DynamicUI

    # The evaluation tree depends on the program only: build it once, not per seeding.
    program, tree_builder = application.resolved()
    if program.dynamic is None:
        return None
    if module_instances is None:
        probe = DynamicUI(
            program,
            parameter_values=parameter_values,
            project_code_page=project_code_page,
            tree_builder=tree_builder,
        )
        layout = probe.state_layout() if evaluated else None
        module_instances = probe.get_module_instances()
        if not (module_instances and parameter_values):
            return probe
        seeded = DynamicUI(
            program,
            parameter_values=parameter_values,
            tree_builder=tree_builder,
            module_instances=[
                ModuleInstance(id=instance_id, ref_id=ref_id)
                for instance_id, ref_id in module_instances
            ],
            project_code_page=project_code_page,
        )
        if layout is not None and DynamicUI.layouts_alike(
            layout, seeded.state_layout()
        ):
            return probe
        return seeded
    return DynamicUI(
        program,
        parameter_values=parameter_values,
        tree_builder=tree_builder,
        module_instances=[
            ModuleInstance(id=instance_id, ref_id=ref_id)
            for instance_id, ref_id in module_instances
        ],
        project_code_page=project_code_page,
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
    project_code_page: str | None = None,
    reads_done: Callable[[], Awaitable[None]] | None = None,
    effort: RecoveryEffort = RecoveryEffort.MEDIUM,
    cancel: Callable[[], bool] | None = None,
    master: MasterData | None = None,
) -> RecoveredDevice:
    """Read and decode one device's configuration over an open connection.

    ``application`` is the product application installed on the device (resolved
    from the catalog after identifying it). Reads the group communication tables
    and the parameter memory and decodes them. ``progress`` is called with the
    current stage (``STAGE_GROUP_COMMUNICATION``/``STAGE_PARAMETERS``) so a UI can
    show what the device read is doing. ``project_code_page`` as for
    :func:`~xknxeditor.recover.parameters.recover_parameters`. ``reads_done`` is awaited
    once everything is read, before the decoding (which takes seconds for a large
    application): the connection can be closed there, before the device closes it for
    being idle. ``effort``, ``cancel`` and ``master`` as for
    :func:`~xknxeditor.recover.parameters.recover_parameters`. Read-only.
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
    if reads_done is not None:
        await reads_done()
    # Decoding takes seconds for a large application: off the event loop, which keeps the
    # connection to the interface answered meanwhile.
    parameters, com_object_refs = await asyncio.to_thread(
        _decode_configuration,
        application,
        group_communication.links,
        group_communication.group_objects,
        parameter_segments,
        property_values,
        project_code_page,
        effort,
        cancel,
        master,
    )
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
        property_values=dict(property_values),
        effort=effort,
    )


def search_again(
    device: RecoveredDevice,
    application: Application,
    effort: RecoveryEffort,
    project_code_page: str | None = None,
    cancel: Callable[[], bool] | None = None,
    master: MasterData | None = None,
) -> RecoveredDevice:
    """``device`` with its parameters searched again in what was read off it, with
    ``effort`` (e.g. more for a device whose result still differs from it, see
    :attr:`~xknxeditor.recover.parameters.RecoveredParameters.complete`). Needs no bus;
    takes as long as the effort allows. ``project_code_page``, ``cancel`` and ``master``
    as for :func:`~xknxeditor.recover.parameters.recover_parameters`."""
    parameters, com_object_refs = _decode_configuration(
        application,
        device.links,
        device.group_objects,
        device.parameter_segments or {},
        device.property_values or {},
        project_code_page,
        effort,
        cancel,
        master,
    )
    return replace(
        device, parameters=parameters, com_object_refs=com_object_refs, effort=effort
    )


def _decode_configuration(
    application: Application,
    links: Sequence[DecodedLink],
    group_objects: Mapping[int, DecodedGroupObject],
    parameter_segments: Mapping[str, bytes],
    property_values: Mapping[TypedPropertyKey, bytes],
    project_code_page: str | None,
    effort: RecoveryEffort,
    cancel: Callable[[], bool] | None = None,
    master: MasterData | None = None,
) -> tuple[RecoveredParameters, dict[int, str]]:
    """The parameters read off a device, and the reference of each group object number."""
    linked = {link.group_object_number for link in links}
    parameters = recover_parameters(
        application,
        parameter_segments,
        property_values,
        project_code_page,
        linked_objects=linked,
        object_sizes={
            number: group_object.object_size
            for number, group_object in group_objects.items()
            if group_object.object_size is not None
        },
        effort=effort,
        cancel=cancel,
        master=master,
    )
    # Resolve com object numbers against a UI seeded with the recovered parameters,
    # so module instances (and their runtime numbers) match the device rather than
    # the application defaults.
    seeded_ui = seed_dynamic_ui(application, parameters.values, project_code_page)
    return parameters, com_object_ref_by_number(application, seeded_ui)


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
    interface_max_apdu_length: int | None = None,
    progress: Callable[[str], None] | None = None,
    attempts: int = 3,
    retry_delay: float = 1.5,
    project_code_page: str | None = None,
    effort: RecoveryEffort = RecoveryEffort.MEDIUM,
    cancel: Callable[[], bool] | None = None,
    master: MasterData | None = None,
) -> RecoveredDevice:
    """Open a connection to ``address``, recover the device, and close it again.

    ``xknx`` has to be started. When ``negotiate_apdu`` is set the device's maximum
    APDU length is read first so reads use larger, fewer telegrams, capped at
    ``interface_max_apdu_length`` (the connecting interface's maximum): a device behind
    a tunnelling-v1 interface may advertise longer frames than the interface can carry,
    and those frames are dropped. ``progress`` reports the current read stage.
    ``project_code_page``, ``effort``, ``cancel`` and ``master`` as for
    :func:`~xknxeditor.recover.parameters.recover_parameters`. Read-only.

    A transient transport failure (the KNXnet/IP tunnel dropping the point-to-point
    connection under sustained load - a ``DisconnectRequest``, a missing ACK, or a
    timeout that a table read did not tolerate) reconnects and retries the whole
    device up to ``attempts`` times, so one bus hiccup does not fail a device on a
    large scan. Every retry falls back to the mandatory 15-octet APDU, which every
    device and interface can carry: a device whose longer frames the interface drops
    then still recovers, just more slowly. The last error is raised if every attempt
    fails.
    """
    target = IndividualAddress(address)
    last_exc: Exception | None = None
    connected = False

    async def disconnect() -> None:
        nonlocal connected
        if connected:
            connected = False
            with contextlib.suppress(ManagementConnectionError, XKNXException):
                await xknx.management.disconnect(target)

    for attempt in range(max(attempts, 1)):
        try:
            connection = await connect(xknx, target)
            connected = True
            programmer = DeviceProgrammer(connection)
            if negotiate_apdu:
                if attempt == 0:
                    with contextlib.suppress(XKNXException):
                        device_max = await programmer.read_max_apdu_length()
                        programmer.max_apdu_length = negotiated_apdu_length(
                            device_max, interface_max_apdu_length
                        )
                        programmer.apdu_restricted_by = apdu_restriction(
                            device_max, interface_max_apdu_length
                        )
                else:
                    # An earlier attempt failed: drop to the mandatory 15 octets in case a
                    # longer frame the interface could not deliver was the cause.
                    programmer.max_apdu_length = DEFAULT_MAX_APDU_LENGTH
            return await recover_device(
                programmer,
                application,
                address=str(target),
                progress=progress,
                project_code_page=project_code_page,
                # Closed before the decoding: idle meanwhile, the device would close it
                # itself, and closing it afterwards fails.
                reads_done=disconnect,
                effort=effort,
                cancel=cancel,
                master=master,
            )
        except (ManagementConnectionError, XKNXException, MemoryReadNotAnswered) as exc:
            # Transport hiccup, or a long read the interface dropped (raised as
            # MemoryReadNotAnswered, which is not an XKNXException): reconnect and retry,
            # the next attempt at the 15-octet fallback.
            last_exc = exc
        finally:
            await disconnect()
        if attempt + 1 < attempts:
            await asyncio.sleep(retry_delay)
    assert last_exc is not None
    raise last_exc
