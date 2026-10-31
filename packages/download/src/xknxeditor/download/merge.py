"""Resolve the effective Load Procedure to execute for a download.

An application declares one of three load procedure styles:

- ``ProductProcedure``: the application ships the complete procedure; use it as is.
- ``DefaultProcedure``: the application ships no procedure; use the mask version's
  default procedure from the master data.
- ``MergedProcedure``: the application ships fragments identified by a merge id;
  splice them into the mask version's default procedure at the matching
  ``LdCtrlMerge`` placeholders.

The default procedures live per mask version in the master data
(``MaskVersion`` -> configuration data -> ``Procedures``), keyed by procedure
type (``Load`` for a download, ``Unload`` for removal).

A ``choose`` in a procedure selects its controls by a parameter of the device: it is replaced
by the controls of the branches the parameter's value selects (see :func:`_expand`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate.ld_ctrl_base_choose_t import LdCtrlBaseChoose
from xknxeditor.namespaces.intermediate.ld_ctrl_base_t import LdCtrlBase
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_merge_t import LdCtrlMerge
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.mask_version_t_management_model import (
    MaskVersionManagementModel,
)
from xknxeditor.namespaces.intermediate.procedure_type_t import ProcedureType
from xknxeditor.namespaces.intermediate.resource_access_t import ResourceAccess
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName
from xknxeditor.prod.parser_v2.nodes.choose import satisfies

from .errors import UnsupportedProcedureError
from .resources import bundled_mask_resources, mask_number
from .scope import (
    DownloadScope,
    control_applies,
    mask_configuration,
    mask_object_types,
    scoped_controls,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.load_procedure_t import LoadProcedure
    from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
    from xknxeditor.namespaces.intermediate.master_data_t import MasterData
    from xknxeditor.prod import Application


# A System 300 application's mask version, which the device's management descriptor completes.
_SYSTEM_300 = "MV-0300"
_PID_MANAGEMENT_DESCRIPTOR = 72
_device_masks: dict[str, str] = {}


def device_mask_version(application: Application) -> str:
    """The mask version id of the device the application is for, as the master data and the
    mask resources know it.

    A System 300 application names the mask "MV-0300" alone. The device's management
    descriptor (PID 72 of its device object) selects the configuration, e.g.
    "MV-0300-01000001000000000000"; the application's procedure checks that descriptor first,
    so the configuration is the one of the descriptor it expects."""
    mask = application.program.mask_version
    procedures = getattr(application, "load_procedures", None)
    if mask != _SYSTEM_300 or procedures is None:
        return mask
    key = getattr(application, "id", "")
    known = _device_masks.get(key)
    if known is not None:
        return known
    resolved = mask
    for control in _flatten(procedures, {}):
        if (
            isinstance(control, LdCtrlCompareProp)
            and control.obj_idx == 0
            and control.prop_id == _PID_MANAGEMENT_DESCRIPTOR
            and control.inline_data
        ):
            resolved = f"{mask}-{control.inline_data.hex().upper()}"
            break
    _device_masks[key] = resolved
    return resolved


def system300_mask_unresolved(application: Application) -> bool:
    """Whether ``application`` is a System 300 application whose procedure does not say
    which configuration it expects: only the device's management descriptor tells."""
    program = getattr(application, "program", None)
    if getattr(program, "mask_version", None) != _SYSTEM_300:
        return False
    return device_mask_version(application) == _SYSTEM_300


def learn_device_mask(application: Application, descriptor: bytes) -> None:
    """Take the configuration of a System 300 application from the management descriptor
    (PID 72) the device reported, when the application does not name it."""
    if descriptor and system300_mask_unresolved(application):
        _device_masks[getattr(application, "id", "")] = (
            f"{_SYSTEM_300}-{descriptor.hex().upper()}"
        )


def resolve_download_controls(
    application: Application,
    master_data: MasterData | None = None,
    *,
    procedure_type: ProcedureType = ProcedureType.LOAD,
    scope: DownloadScope = DownloadScope.FULL,
    access: ResourceAccess = ResourceAccess.REMOTE,
    has_application_program2: bool = False,
    parameter_values: Mapping[str, str] | None = None,
    application2: Application | None = None,
) -> list[object]:
    """Return the flat, ordered list of Load Controls to run for a download.

    ``master_data`` is required for default and merged procedures; missing mask
    configuration is rejected before any device access.
    ``has_application_program2`` describes the programs supplied by the caller,
    not whether the mask optionally supports a second application.
    ``parameter_values`` holds the values of the device's active parameters that a ``choose``
    in the procedure tests (see :func:`procedure_parameter_refs`); a ``choose`` on a parameter
    missing there selects no controls.
    ``application2`` is the product's second application program (its PEI program): the mask's
    procedure for both programs is taken, with the fragments of both merged in, or with
    procedures of their own (``ProductProcedure``) both in one (see
    :func:`_with_second_procedure`). An Unload always takes the mask's procedure for both
    programs, with the application's fragments only.
    """
    if procedure_type is not ProcedureType.LOAD:
        has_application_program2, application2 = True, None
        if scope not in (DownloadScope.UNLOAD, DownloadScope.UNLOAD_ALL):
            scope = DownloadScope.UNLOAD
    elif application2 is not None:
        has_application_program2 = True
    try:
        return _resolve_controls(
            application,
            master_data,
            procedure_type=procedure_type,
            scope=scope,
            access=access,
            has_application_program2=has_application_program2,
            values=parameter_values or {},
            application2=application2,
        )
    except UnsupportedProcedureError:
        # A product the manufacturer's download plug-in downloads may ship no Load procedure
        # of its own, on a mask without a default one.
        if (
            procedure_type is ProcedureType.LOAD
            and not (
                application.load_procedures is not None
                and application.load_procedures.load_procedure
            )
            and _download_plugin(application)
        ):
            raise UnsupportedProcedureError(
                "the product data has no load procedure for this application: "
                "the manufacturer's download plug-in downloads it"
            ) from None
        raise


def _download_plugin(application: Application) -> bool:
    """Whether the application names a manufacturer download plug-in."""
    static = getattr(application.program, "static", None)
    extension = getattr(static, "extension", None)
    return bool(getattr(extension, "ets_download_plugin", None))


def _resolve_controls(
    application: Application,
    master_data: MasterData | None,
    *,
    procedure_type: ProcedureType,
    scope: DownloadScope,
    access: ResourceAccess,
    has_application_program2: bool,
    values: Mapping[str, str],
    application2: Application | None = None,
) -> list[object]:
    style = application.load_procedure_style
    load_procedures = application.load_procedures

    # An application ships only its Load procedure (ProductProcedure) or Load
    # fragments (MergedProcedure). The Unload procedure always comes from the
    # mask version's default in the master data, so never take the application's
    # own procedure for a non-Load request.
    load = procedure_type is ProcedureType.LOAD
    bim_m112 = (
        _management_model(master_data, device_mask_version(application))
        is MaskVersionManagementModel.BIM_M112
    )
    second = application2 is not None
    if load and style == LoadProcedureStyle.PRODUCT_PROCEDURE:
        controls = _program_controls(
            application, _flatten(load_procedures, values), bim_m112, False, second
        )
        if application2 is not None:
            controls = _with_second_procedure(
                controls,
                _program_controls(
                    application2,
                    _flatten(application2.load_procedures, values),
                    bim_m112,
                    True,
                    second,
                ),
                scope,
            )
        return _finalize_controls(
            correct_anomalies(controls),
            master_data,
            device_mask_version(application),
            scope,
            has_application_program2,
        )

    default = _default_procedure(
        master_data,
        device_mask_version(application),
        procedure_type,
        scope=scope,
        access=access,
        has_application_program2=has_application_program2,
    )
    if default is None:
        mask = application.program.mask_version
        if master_data is not None:
            # The master data is there, but the mask has no such procedure (e.g. the
            # "Dummy" mask MV-0001 of products that are not programmed).
            raise UnsupportedProcedureError(
                f"the master data has no {procedure_type.value.lower()} procedure for mask "
                f"{mask}: "
                "a device with it cannot be programmed"
            )
        if procedure_type is not ProcedureType.LOAD:
            raise UnsupportedProcedureError(
                f"no {procedure_type.value} procedure available for mask "
                f"{mask}; master data is required"
            )
        raise UnsupportedProcedureError(
            f"master data is required for {style} on mask {mask}"
        )

    fragments = {
        merge_id: _program_controls(application, more, bim_m112, False, second)
        for merge_id, more in _fragments_by_merge_id(load_procedures, values).items()
    }
    if application2 is not None:
        # The second program's fragments follow the first program's of the same merge id.
        for merge_id, more in _fragments_by_merge_id(
            application2.load_procedures, values
        ).items():
            fragments.setdefault(merge_id, []).extend(
                _program_controls(application2, more, bim_m112, True, second)
            )
    # An Unload runs every control of the procedure; the merge points always take their
    # fragments.
    controls = _splice(
        [
            c
            for c in _expand(default.choice, values)
            if not load or isinstance(c, LdCtrlMerge) or control_applies(c, scope)
        ],
        fragments,
    )
    if load:
        controls = correct_anomalies([c for c in controls if control_applies(c, scope)])
    return _finalize_controls(
        controls,
        master_data,
        device_mask_version(application),
        scope,
        has_application_program2,
    )


def _finalize_controls(
    controls: Sequence[object],
    master_data: MasterData | None,
    mask_version: str,
    scope: DownloadScope,
    has_application_program2: bool,
) -> list[object]:
    object_types = mask_object_types(master_data, mask_version)
    partial_parameters = scope in (
        DownloadScope.PARAMETERS,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION,
    )
    number = mask_number(mask_version)
    remove_second = (
        partial_parameters
        and not has_application_program2
        and number is not None
        and number & 0xFF0 == 0x7B0
    )
    announces_table = _announces_object_table(mask_version)
    # Automatic transformations precede removal of the absent second program.
    return [
        _explicit(c)
        for c in scoped_controls(
            [c for c in controls if announces_table or not _object_table_lsm(c)],
            scope,
            object_types,
            has_application_program2=has_application_program2,
        )
        if not (remove_second and _second_program_control(c))
    ]


# The load state machine a procedure announces the group object table address with.
_OBJECT_TABLE_LSM = 5


def _announces_object_table(mask_version: str) -> bool:
    """Whether the mask takes the group object table through its own load state machine
    (it has a ``GroupObjectTableLoadControl``); other masks are not told its address."""
    resources = bundled_mask_resources(mask_version)
    return (
        resources is None
        or resources.location(ResourceName.GROUP_OBJECT_TABLE_LOAD_CONTROL) is not None
    )


def _object_table_lsm(control: object) -> bool:
    """Whether ``control`` loads or announces the group object table's load state machine
    (``LdCtrlLoad`` / ``LdCtrlTaskSegment`` of LSM 5)."""
    return (
        isinstance(control, (LdCtrlLoad, LdCtrlTaskSegment))
        and control.lsm_idx == _OBJECT_TABLE_LSM
    )


def _explicit(control: object) -> object:
    """Keep manufacturer controls and finalized controls out of automatic filtering."""
    if isinstance(control, LdCtrlBase) and control.applies_to is LdCtrlProcType.AUTO:
        return replace(control, applies_to=LdCtrlProcType.ALL)
    return control


def _second_program_control(control: object) -> bool:
    return (
        type(control).__name__
        in {"LdCtrlLoadImageProp", "LdCtrlCompareProp", "LdCtrlWriteProp"}
        and getattr(control, "obj_idx", None) == 5
    ) or (
        type(control).__name__ in {"LdCtrlUnload", "LdCtrlLoad", "LdCtrlLoadCompleted"}
        and getattr(control, "lsm_idx", None) == 5
    )


def _flatten(
    load_procedures: LoadProcedures | None, values: Mapping[str, str]
) -> list[object]:
    """Concatenate the controls of every application load procedure."""
    if load_procedures is None:
        raise UnsupportedProcedureError("application has no load procedure")
    controls: list[object] = []
    for procedure in load_procedures.load_procedure:
        controls.extend(_expand(procedure.choice, values))
    return controls


# On a BIM M112 the second program has load state machine 4, the application 1 to 3; the
# controls of these applications are taken as they are.
_SECOND_PROGRAM_LSM = 4
_DO_NOT_FILTER_LOAD_CONTROLS = (
    "M-0001_A-8018-04",
    "M-0001_A-8018-06",
    "M-0001_A-8018-08",
    "M-0001_A-8020-01",
    "M-0001_A-8020-02",
    "M-0001_A-8020-03",
    "M-0001_A-8023-11",
)


def _management_model(
    master_data: MasterData | None, mask_version_id: str
) -> MaskVersionManagementModel | None:
    if master_data is None or master_data.mask_versions is None:
        return None
    for mask_version in master_data.mask_versions.mask_version:
        if mask_version.id == mask_version_id:
            return getattr(mask_version, "management_model", None)
    return None


def _program_controls(
    application: Application,
    controls: Sequence[object],
    bim_m112: bool,
    second: bool,
    has_second: bool,
) -> list[object]:
    """``controls`` of ``application`` (the second program when ``second``) without, on a
    BIM M112, the other program's load state machines: the second program loads only from
    LSM 4, the application not LSM 4, except its Unload when the product has no second
    program."""
    if not bim_m112 or application.program.id.startswith(_DO_NOT_FILTER_LOAD_CONTROLS):
        return list(controls)
    result: list[object] = []
    for control in controls:
        lsm = getattr(control, "lsm_idx", None)
        if lsm is None:
            keep = True
        elif second:
            keep = lsm >= _SECOND_PROGRAM_LSM
        else:
            keep = lsm != _SECOND_PROGRAM_LSM or (
                isinstance(control, LdCtrlUnload) and not has_second
            )
        if keep:
            result.append(control)
        else:
            logger.debug(
                "removing %s (%s load control in %s)",
                type(control).__name__,
                "AP1" if second else "AP2",
                "AP2" if second else "AP1",
            )
    return result


def _with_second_procedure(
    controls: Sequence[object], second: Sequence[object], scope: DownloadScope
) -> list[object]:
    """One procedure of the application's own procedure (``controls``) and the second
    program's (``second``): the second program's first, the application's Unloads behind its
    last Unload (else behind its first Connect, else at the start), the application's other
    controls at the end."""
    result = [c for c in second if control_applies(c, scope)]
    unloads = [n for n, c in enumerate(result) if isinstance(c, LdCtrlUnload)]
    connects = [n for n, c in enumerate(result) if isinstance(c, LdCtrlConnect)]
    position = unloads[-1] + 1 if unloads else connects[0] + 1 if connects else 0
    for control in controls:
        if not control_applies(control, scope):
            continue
        if isinstance(control, LdCtrlUnload):
            result.insert(position, control)
            position += 1
        else:
            result.append(control)
    return result


def correct_anomalies(controls: Sequence[object]) -> list[object]:
    """``controls`` without a Disconnect directly followed by a Connect, a Connect while
    connected, a Disconnect while not connected and a second Restart in a row (the procedure
    runner connects before a control that needs it and disconnects at the end)."""
    result: list[object] = []
    connected = False
    for control in controls:
        if isinstance(control, LdCtrlConnect):
            if connected:
                continue
            connected = True
            if result and isinstance(result[-1], LdCtrlDisconnect):
                result.pop()
                continue
        elif isinstance(control, LdCtrlDisconnect):
            if not connected:
                continue
            connected = False
        elif (
            isinstance(control, LdCtrlRestart)
            and result
            and isinstance(result[-1], LdCtrlRestart)
        ):
            continue
        result.append(control)
    return result


def _fragments_by_merge_id(
    load_procedures: LoadProcedures | None, values: Mapping[str, str]
) -> dict[int, list[object]]:
    """Group application procedure fragments by their merge id."""
    fragments: dict[int, list[object]] = {}
    if load_procedures is None:
        return fragments
    for procedure in load_procedures.load_procedure:
        if procedure.merge_id is None:
            continue
        fragments.setdefault(procedure.merge_id, []).extend(
            _explicit(c) for c in _expand(procedure.choice, values)
        )
    return fragments


def _expand(controls: Sequence[object], values: Mapping[str, str]) -> list[object]:
    """``controls`` with each ``choose`` replaced by the controls of the branches it selects:
    every ``when`` whose test the parameter's value satisfies, in their order, else the
    default ``when``. A ``choose`` on a parameter without a value (inactive) selects nothing,
    not even its default. The selected controls are expanded in turn (a nested ``choose``)."""
    result: list[object] = []
    for control in controls:
        if not isinstance(control, LdCtrlBaseChoose):
            result.append(control)
            continue
        value = values.get(control.param_ref_id)
        if value is None:
            continue
        selected = [
            when
            for when in control.when
            if when.test is not None and satisfies(when.test, value)
        ] or [when for when in control.when if when.default]
        for when in selected:
            result.extend(_expand(when.choice, values))
    return result


def iter_load_controls(controls: Sequence[object]) -> Iterator[object]:
    """Every control in ``controls``, those of every branch of a ``choose`` included."""
    for control in controls:
        if isinstance(control, LdCtrlBaseChoose):
            for when in control.when:
                yield from iter_load_controls(when.choice)
        else:
            yield control


def procedure_parameter_refs(application: Application) -> frozenset[str]:
    """The parameter refs a ``choose`` in the application's load procedures tests."""
    refs: set[str] = set()

    def collect(controls: Sequence[object]) -> None:
        for control in controls:
            if isinstance(control, LdCtrlBaseChoose):
                refs.add(control.param_ref_id)
                for when in control.when:
                    collect(when.choice)

    procedures = application.load_procedures
    for procedure in procedures.load_procedure if procedures is not None else ():
        collect(procedure.choice)
    return frozenset(refs)


def _splice(
    controls: Sequence[object], fragments: dict[int, list[object]]
) -> list[object]:
    """Replace each merge placeholder with the fragment of matching merge id."""
    result: list[object] = []
    for control in controls:
        if isinstance(control, LdCtrlMerge):
            result.extend(fragments.get(control.merge_id, []))
        else:
            result.append(control)
    return result


# Clear the second-application option (0x40) when no second program is supplied,
# then score procedure subtypes by their overlap with the requested options.
_SUBTYPE_OPTIONS = {
    "all": 0xFF,
    "full": 0xFF,
    "full,par": 0xFF,
    "full,grp": 0xFF,
    "ap1": 0xBF,
    "par": 1,
    "grp": 2,
    "par,grp": 3,
    "cfg": 0xC0008,
}


def _procedure_score(
    requested: int, candidate: int, has_application_program2: bool
) -> int:
    if requested == candidate:
        return 32
    if has_application_program2 and candidate == 0xBF:
        return -32
    configuration = 0xC0008
    if (
        requested & configuration
        and not requested & ~configuration
        and candidate & ~configuration
    ):
        return -32
    if (
        candidate & configuration
        and not candidate & ~configuration
        and requested & ~configuration
    ):
        return -32
    score = (candidate & requested).bit_count() - (candidate & ~requested).bit_count()
    for required in (1, 2):
        if requested & required and not candidate & required:
            score -= 16
    return score


def _default_procedure(
    master_data: MasterData | None,
    mask_version_id: str,
    procedure_type: ProcedureType,
    *,
    scope: DownloadScope = DownloadScope.FULL,
    access: ResourceAccess = ResourceAccess.REMOTE,
    has_application_program2: bool = False,
) -> LoadProcedure | None:
    """Select a compatible subtype from the same configuration as InterfaceObjects."""
    configuration = mask_configuration(master_data, mask_version_id)
    if configuration is None:
        return None
    requested = {
        DownloadScope.PARAMETERS: 1,
        DownloadScope.GROUP_COMMUNICATION: 2,
        DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION: 3,
    }.get(scope, 0xFF)
    if not has_application_program2:
        requested &= ~0x40
    best = None
    best_score = -32
    if configuration.procedures is not None:
        for procedure in configuration.procedures.procedure:
            if (
                procedure.procedure_type != procedure_type
                or access not in procedure.access
            ):
                continue
            # A subtype without options of its own is one for everything.
            candidate = _SUBTYPE_OPTIONS.get(procedure.procedure_sub_type.value, 0xFF)
            if not candidate & requested:
                continue
            score = _procedure_score(requested, candidate, has_application_program2)
            if score > best_score:
                best, best_score = procedure, score
    if best is None:
        raise UnsupportedProcedureError(
            f"no compatible {procedure_type.value} procedure for mask {mask_version_id}, "
            f"scope={scope.value}, access={access.value}, second application={has_application_program2}"
        )
    logger.debug(
        "selected procedure: mask=%s type=%s subtype=%s scope=%s access=%s second-application=%s",
        mask_version_id,
        procedure_type.value,
        best.procedure_sub_type.value,
        scope.value,
        access.value,
        has_application_program2,
    )
    return best


# Feature name (in a mask version's configuration data) carrying the number of
# access-protection levels; greater than zero means an A_Authorize handshake is
# expected before writing.
_AUTHORIZE_LEVELS_FEATURE = "AuthorizeLevels"


def mask_authorize_levels(master_data: MasterData | None, mask_version_id: str) -> int:
    """Return the mask version's AuthorizeLevels, or 0 when unknown or absent.

    A value greater than zero means the device uses access protection, so an
    A_Authorize handshake is performed before writing. Without master data the
    value is unknown and 0 is returned (no authorize attempted).
    """
    if master_data is None or master_data.mask_versions is None:
        return 0
    for mask_version in master_data.mask_versions.mask_version:
        if mask_version.id != mask_version_id:
            continue
        for configuration in mask_version.hawk_configuration_data:
            features = configuration.features
            if features is None:
                continue
            for feature in features.feature:
                name = getattr(feature.name, "value", feature.name)
                if name == _AUTHORIZE_LEVELS_FEATURE:
                    return feature.value or 0
    return 0


def mask_has_property_services(
    master_data: MasterData | None, mask_version_id: str
) -> bool:
    """Whether the mask answers ``A_PropertyValue_Read``.

    BCU1 (system 1) has no interface-object properties. Probing them times out.
    An unknown mask keeps the probes, which newer devices answer.
    """
    if master_data is None or master_data.mask_versions is None:
        return True
    for mask_version in master_data.mask_versions.mask_version:
        if mask_version.id != mask_version_id:
            continue
        model = getattr(mask_version, "management_model", None)
        return model is not MaskVersionManagementModel.BCU1
    return True
