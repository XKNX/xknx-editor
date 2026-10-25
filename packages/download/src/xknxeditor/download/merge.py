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
from xknxeditor.namespaces.intermediate.ld_ctrl_merge_t import LdCtrlMerge
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.mask_version_t_management_model import (
    MaskVersionManagementModel,
)
from xknxeditor.namespaces.intermediate.procedure_type_t import ProcedureType
from xknxeditor.namespaces.intermediate.resource_access_t import ResourceAccess
from xknxeditor.prod.parser_v2.nodes.choose import satisfies

from .errors import UnsupportedProcedureError
from .scope import (
    DownloadScope,
    control_applies,
    control_in_scope,
    mask_configuration,
    mask_object_types,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from xknxeditor.namespaces.intermediate.load_procedure_t import LoadProcedure
    from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
    from xknxeditor.namespaces.intermediate.master_data_t import MasterData
    from xknxeditor.prod import Application


def resolve_download_controls(
    application: Application,
    master_data: MasterData | None = None,
    *,
    procedure_type: ProcedureType = ProcedureType.LOAD,
    scope: DownloadScope = DownloadScope.FULL,
    access: ResourceAccess = ResourceAccess.REMOTE,
    has_application_program2: bool = False,
    parameter_values: Mapping[str, str] | None = None,
) -> list[object]:
    """Return the flat, ordered list of Load Controls to run for a download.

    ``master_data`` is required for default and merged procedures; missing mask
    configuration is rejected before any device access.
    ``has_application_program2`` describes the programs supplied by the caller,
    not whether the mask optionally supports a second application.
    ``parameter_values`` holds the values of the device's active parameters that a ``choose``
    in the procedure tests (see :func:`procedure_parameter_refs`); a ``choose`` on a parameter
    missing there selects no controls.
    """
    style = application.load_procedure_style
    load_procedures = application.load_procedures
    values = parameter_values or {}

    # An application ships only its Load procedure (ProductProcedure) or Load
    # fragments (MergedProcedure). The Unload procedure always comes from the
    # mask version's default in the master data, so never take the application's
    # own procedure for a non-Load request.
    if (
        procedure_type is ProcedureType.LOAD
        and style == LoadProcedureStyle.PRODUCT_PROCEDURE
    ):
        return _finalize_controls(
            _flatten(load_procedures, values),
            master_data,
            application.program.mask_version,
            scope,
            has_application_program2,
        )

    default = _default_procedure(
        master_data,
        application.program.mask_version,
        procedure_type,
        scope=scope,
        access=access,
        has_application_program2=has_application_program2,
    )
    if default is None:
        if procedure_type is not ProcedureType.LOAD:
            raise UnsupportedProcedureError(
                f"no {procedure_type.value} procedure available for mask "
                f"{application.program.mask_version}; master data is required"
            )
        raise UnsupportedProcedureError(
            f"master data is required for {style} on mask {application.program.mask_version}"
        )

    controls = [
        c
        for c in _splice(
            [c for c in _expand(default.choice, values) if control_applies(c, scope)],
            _fragments_by_merge_id(load_procedures, values),
        )
        if control_applies(c, scope)
    ]
    return _finalize_controls(
        controls,
        master_data,
        application.program.mask_version,
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
    remove_second = (
        partial_parameters
        and not has_application_program2
        and int(mask_version.removeprefix("MV-"), 16) & 0xFF0 == 0x7B0
    )
    # Automatic transformations precede removal of the absent second program.
    return [
        _explicit(c)
        for c in controls
        if control_in_scope(
            c, scope, object_types, has_application_program2=has_application_program2
        )
        and not (remove_second and _second_program_control(c))
    ]


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
            candidate = _SUBTYPE_OPTIONS.get(procedure.procedure_sub_type.value)
            if candidate is None or not candidate & requested:
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
