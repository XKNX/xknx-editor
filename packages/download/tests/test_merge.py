"""Tests for Load Procedure resolution (default / product / merged styles)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from xknxeditor.download.merge import (
    mask_authorize_levels,
    mask_has_property_services,
    resolve_download_controls,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_merge_t import LdCtrlMerge
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)
from xknxeditor.namespaces.intermediate.master_data_t import MasterData
from xknxeditor.namespaces.intermediate.procedure_type_t import ProcedureType
from xknxeditor.namespaces.intermediate.resource_access_t import ResourceAccess

_MASK = "MV-0705"


def _application(style: LoadProcedureStyle, *fragments: object) -> object:
    return SimpleNamespace(
        load_procedure_style=style,
        load_procedures=LoadProcedures(load_procedure=list(fragments)),  # type: ignore[arg-type]
        program=SimpleNamespace(mask_version=_MASK),
    )


def _master_with_default(*controls: object) -> MasterData:
    default = SimpleNamespace(
        procedure_type=ProcedureType.LOAD,
        procedure_sub_type=LdCtrlProcType.ALL,
        access=[ResourceAccess.REMOTE],
        choice=list(controls),
    )
    mask = SimpleNamespace(
        id=_MASK,
        hawk_configuration_data=[
            SimpleNamespace(
                interface_objects=None,
                legacy_version=None,
                procedures=SimpleNamespace(procedure=[default]),
            )
        ],
    )
    return cast(
        "MasterData",
        SimpleNamespace(mask_versions=SimpleNamespace(mask_version=[mask])),
    )


def _fragment(merge_id: int, *controls: object) -> LoadProceduresLoadProcedure:
    return LoadProceduresLoadProcedure(merge_id=merge_id, choice=list(controls))  # type: ignore[arg-type]


def test_product_procedure_uses_application_controls() -> None:
    application = _application(
        LoadProcedureStyle.PRODUCT_PROCEDURE,
        _fragment(0, LdCtrlConnect(), LdCtrlRestart()),
    )
    controls = resolve_download_controls(cast("object", application))  # type: ignore[arg-type]
    assert [type(c).__name__ for c in controls] == ["LdCtrlConnect", "LdCtrlRestart"]


def test_merged_procedure_splices_fragments_into_default() -> None:
    master = _master_with_default(
        LdCtrlConnect(),
        LdCtrlMerge(merge_id=2),
        LdCtrlMerge(merge_id=4),
        LdCtrlRestart(),
    )
    write = LdCtrlWriteMem(address=0x10, size=1, verify=False, inline_data=b"\x01")
    application = _application(
        LoadProcedureStyle.MERGED_PROCEDURE,
        _fragment(2, LdCtrlConnect()),
        _fragment(4, write),
    )

    controls = resolve_download_controls(cast("object", application), master)  # type: ignore[arg-type]

    # Connect, <frag 2: Connect>, <frag 4: WriteMem>, Restart
    assert [type(c).__name__ for c in controls] == [
        "LdCtrlConnect",
        "LdCtrlConnect",
        "LdCtrlWriteMem",
        "LdCtrlRestart",
    ]


def test_default_procedure_drops_unmatched_merge_placeholders() -> None:
    master = _master_with_default(
        LdCtrlConnect(), LdCtrlMerge(merge_id=9), LdCtrlRestart()
    )
    application = _application(LoadProcedureStyle.DEFAULT_PROCEDURE)

    controls = resolve_download_controls(cast("object", application), master)  # type: ignore[arg-type]

    # no fragment for merge id 9 -> placeholder dropped
    assert [type(c).__name__ for c in controls] == ["LdCtrlConnect", "LdCtrlRestart"]


def test_merged_without_master_rejected() -> None:
    application = _application(
        LoadProcedureStyle.MERGED_PROCEDURE,
        _fragment(0, LdCtrlConnect()),
    )
    import pytest

    from xknxeditor.download.errors import UnsupportedProcedureError

    with pytest.raises(UnsupportedProcedureError, match="master data is required"):
        resolve_download_controls(application)  # type: ignore[arg-type]


def _master_with_unload(*controls: object) -> MasterData:
    unload = SimpleNamespace(
        procedure_type=ProcedureType.UNLOAD,
        procedure_sub_type=LdCtrlProcType.ALL,
        access=[ResourceAccess.REMOTE],
        choice=list(controls),
    )
    mask = SimpleNamespace(
        id=_MASK,
        hawk_configuration_data=[
            SimpleNamespace(
                interface_objects=None,
                legacy_version=None,
                procedures=SimpleNamespace(procedure=[unload]),
            )
        ],
    )
    return cast(
        "MasterData",
        SimpleNamespace(mask_versions=SimpleNamespace(mask_version=[mask])),
    )


def test_unload_uses_master_default_not_application_load_procedure() -> None:
    # A ProductProcedure app ships only its Load procedure; UNLOAD must come from
    # the master default, never the application's (Load) controls.
    application = _application(
        LoadProcedureStyle.PRODUCT_PROCEDURE,
        _fragment(
            0, LdCtrlWriteMem(address=0x10, size=1, verify=False, inline_data=b"\x01")
        ),
    )
    master = _master_with_unload(LdCtrlConnect(), LdCtrlRestart())
    controls = resolve_download_controls(
        cast("object", application), master, procedure_type=ProcedureType.UNLOAD
    )  # type: ignore[arg-type]
    assert [type(c).__name__ for c in controls] == ["LdCtrlConnect", "LdCtrlRestart"]


def test_unload_without_master_raises() -> None:
    import pytest

    from xknxeditor.download.errors import UnsupportedProcedureError

    application = _application(
        LoadProcedureStyle.PRODUCT_PROCEDURE,
        _fragment(0, LdCtrlConnect()),
    )
    with pytest.raises(UnsupportedProcedureError, match=r"[Uu]nload procedure"):
        resolve_download_controls(
            cast("object", application), None, procedure_type=ProcedureType.UNLOAD
        )  # type: ignore[arg-type]


def _master_with_features(*features: object) -> MasterData:
    mask = SimpleNamespace(
        id=_MASK,
        hawk_configuration_data=[
            SimpleNamespace(features=SimpleNamespace(feature=list(features)))
        ],
    )
    return cast(
        "MasterData",
        SimpleNamespace(mask_versions=SimpleNamespace(mask_version=[mask])),
    )


def _feature(name: str, value: int) -> object:
    # The parsed model wraps the name in an enum with a ``value`` attribute; mimic it.
    return SimpleNamespace(name=SimpleNamespace(value=name), value=value)


def test_bcu1_mask_has_no_property_services() -> None:
    from xknxeditor.namespaces.intermediate.mask_version_t_management_model import (
        MaskVersionManagementModel,
    )

    master = cast(
        "MasterData",
        SimpleNamespace(
            mask_versions=SimpleNamespace(
                mask_version=[
                    SimpleNamespace(
                        id="MV-0011",
                        management_model=MaskVersionManagementModel.BCU1,
                    ),
                    SimpleNamespace(
                        id="MV-0701",
                        management_model=MaskVersionManagementModel.BIM_M112,
                    ),
                ]
            )
        ),
    )
    assert mask_has_property_services(master, "MV-0011") is False
    assert mask_has_property_services(master, "MV-0701") is True
    assert mask_has_property_services(None, "MV-0011") is True
    assert mask_has_property_services(master, "MV-9999") is True


def test_mask_authorize_levels_returns_feature_value() -> None:
    master = _master_with_features(
        _feature("MaxApduLength", 254), _feature("AuthorizeLevels", 4)
    )
    assert mask_authorize_levels(master, _MASK) == 4


def test_mask_authorize_levels_absent_feature_is_zero() -> None:
    master = _master_with_features(_feature("MaxApduLength", 254))
    assert mask_authorize_levels(master, _MASK) == 0


def test_mask_authorize_levels_without_master_is_zero() -> None:
    assert mask_authorize_levels(None, _MASK) == 0


def test_mask_authorize_levels_unknown_mask_is_zero() -> None:
    master = _master_with_features(_feature("AuthorizeLevels", 16))
    assert mask_authorize_levels(master, "MV-9999") == 0


def test_system_b_selection_uses_scope_and_second_application() -> None:
    from xknxeditor.download.scope import DownloadScope
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad

    from .test_group_communication import mask_fixture

    app, master = mask_fixture()
    # Put "all" first to ensure selection does not depend on template order.
    procedures = (
        master.mask_versions.mask_version[0]
        .hawk_configuration_data[0]
        .procedures.procedure
    )
    procedures[0], procedures[1] = procedures[1], procedures[0]
    full = resolve_download_controls(app, master)
    both = resolve_download_controls(app, master, has_application_program2=True)
    group = resolve_download_controls(
        app, master, scope=DownloadScope.GROUP_COMMUNICATION
    )
    parameters = resolve_download_controls(app, master, scope=DownloadScope.PARAMETERS)
    assert [c.lsm_idx for c in full if isinstance(c, LdCtrlLoad)] == [4, 3, 1, 2]
    assert [c.lsm_idx for c in both if isinstance(c, LdCtrlLoad)] == [5, 4, 3, 1, 2]
    assert [c.lsm_idx for c in group if isinstance(c, LdCtrlLoad)] == [3, 1, 2]
    assert [c.lsm_idx for c in parameters if isinstance(c, LdCtrlLoad)] == [4]


def test_procedure_selection_enforces_access() -> None:
    import pytest

    from xknxeditor.download.errors import UnsupportedProcedureError
    from xknxeditor.download.scope import DownloadScope

    from .test_group_communication import mask_fixture

    app, master = mask_fixture()
    with pytest.raises(UnsupportedProcedureError, match="access=local1"):
        resolve_download_controls(
            app,
            master,
            scope=DownloadScope.GROUP_COMMUNICATION,
            access=ResourceAccess.LOCAL1,
        )
    assert resolve_download_controls(app, master, access=ResourceAccess.LOCAL2)


def test_partial_selection_prefers_combined_over_full_fallback() -> None:
    from xknxeditor.download.scope import DownloadScope

    from .test_group_communication import mask_fixture

    app, master = mask_fixture()
    procedures = (
        master.mask_versions.mask_version[0]
        .hawk_configuration_data[0]
        .procedures.procedure
    )
    combined = next(p for p in procedures if p.procedure_sub_type.value == "par,grp")
    procedures[:] = [p for p in procedures if p.procedure_sub_type.value != "grp"]
    combined.choice = [LdCtrlConnect()]
    assert resolve_download_controls(
        app, master, scope=DownloadScope.GROUP_COMMUNICATION
    ) == [LdCtrlConnect(applies_to=LdCtrlProcType.ALL)]


def test_legacy_configuration_is_not_mixed_with_current_metadata() -> None:
    from copy import deepcopy

    from xknxeditor.download.scope import mask_object_types

    from .test_group_communication import MAPPING, mask_fixture

    app, master = mask_fixture()
    configurations = master.mask_versions.mask_version[0].hawk_configuration_data
    legacy = deepcopy(configurations[0])
    legacy.legacy_version = 1
    legacy.interface_objects.interface_object[3].object_type = 99
    legacy.procedures.procedure[0].choice = [LdCtrlConnect()]
    configurations.insert(0, legacy)
    assert mask_object_types(master, "MV-07B0") == MAPPING
    assert resolve_download_controls(app, master) != [LdCtrlConnect()]


def test_a_mask_id_with_a_management_descriptor_resolves() -> None:
    from xknxeditor.download.scope import DownloadScope

    procedure = LoadProceduresLoadProcedure(choice=[LdCtrlRestart()])  # type: ignore[arg-type]
    app = SimpleNamespace(
        load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
        load_procedures=LoadProcedures(load_procedure=[procedure]),
        program=SimpleNamespace(mask_version="MV-0300-01000001000000000000"),
    )
    for scope in (DownloadScope.FULL, DownloadScope.PARAMETERS):
        controls = resolve_download_controls(app, None, scope=scope)  # type: ignore[arg-type]
        assert [type(c) for c in controls] == [LdCtrlRestart]


def test_the_object_table_lsm_is_only_loaded_where_the_mask_has_its_load_control() -> (
    None
):
    from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
    from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import (
        LdCtrlTaskSegment,
    )

    def controls(mask: str) -> list[object]:
        procedure = LoadProceduresLoadProcedure(
            choice=[  # type: ignore[arg-type]
                LdCtrlLoad(lsm_idx=5),
                LdCtrlTaskSegment(lsm_idx=5, address=0x4324),
                LdCtrlLoad(lsm_idx=3),
                LdCtrlRestart(),
            ]
        )
        app = SimpleNamespace(
            load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
            load_procedures=LoadProcedures(load_procedure=[procedure]),
            program=SimpleNamespace(mask_version=mask),
        )
        return resolve_download_controls(app, None)  # type: ignore[arg-type]

    # A mask without a group object table load control is not told the table's address.
    assert [
        (type(c).__name__, getattr(c, "lsm_idx", None)) for c in controls("MV-0701")
    ] == [
        ("LdCtrlLoad", 3),
        ("LdCtrlRestart", None),
    ]
    assert len(controls("MV-07B0")) == 4


def test_a_download_plugin_without_load_procedure_is_named() -> None:
    import pytest

    from xknxeditor.download.errors import UnsupportedProcedureError

    def app(plugin: str | None) -> object:
        return SimpleNamespace(
            load_procedure_style=LoadProcedureStyle.DEFAULT_PROCEDURE,
            load_procedures=None,
            program=SimpleNamespace(
                mask_version="MV-0701",
                static=SimpleNamespace(
                    extension=SimpleNamespace(ets_download_plugin=plugin)
                ),
            ),
        )

    with pytest.raises(UnsupportedProcedureError, match="download plug-in"):
        resolve_download_controls(app("{CC18BBDF}"), None)  # type: ignore[arg-type]
    with pytest.raises(UnsupportedProcedureError, match="master data is required"):
        resolve_download_controls(app(None), None)  # type: ignore[arg-type]
