"""A product with a second application program (a PEI program): a full download loads it with
the application, instead of leaving it unloaded."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import cast

import pytest
from xknx import XKNX
from xknx.telegram.apci import PropertyValueWrite
from xsdata.formats.dataclass.parsers import XmlParser

from xknxeditor.download.image import with_second_program
from xknxeditor.download.load_state import LoadState
from xknxeditor.download.merge import resolve_download_controls
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.master_data_t import (
    MasterData as RawMasterData,
)
from xknxeditor.prod import Application, MasterData

from .test_wire_audit import (
    CaptureDevice,
    capture_application,
    capture_master,
    seeded_application,
)

_PEI_DATA = bytes.fromhex("00000020")
# The PEI program's fragments, as a Busch-Jaeger presence detector's has them.
_PEI_FRAGMENTS = """<LoadProcedures>
  <LoadProcedure MergeId="3">
    <LdCtrlRelSegment LsmIdx="5" Size="4" Mode="1" Fill="0" AppliesTo="full" />
    <LdCtrlRelSegment LsmIdx="5" Size="4" Mode="0" Fill="0" AppliesTo="par" />
  </LoadProcedure>
  <LoadProcedure MergeId="5">
    <LdCtrlWriteRelMem ObjIdx="5" Offset="0" Size="4" Verify="true" AppliesTo="full,par" />
  </LoadProcedure>
  <LoadProcedure MergeId="7">
    <LdCtrlLoadImageProp ObjIdx="5" PropId="27" />
  </LoadProcedure>
</LoadProcedures>"""


def _pei_program() -> Application:
    app, _ = seeded_application((5, 0, _PEI_DATA))
    app.program.application_number = 0x1234
    app.program.application_version = 0x43
    app.load_procedure_style = LoadProcedureStyle.MERGED_PROCEDURE  # type: ignore[misc]
    app.load_procedures = XmlParser().from_string(  # type: ignore[misc]
        _PEI_FRAGMENTS, LoadProcedures
    )
    return app


def test_the_procedure_of_both_programs() -> None:
    app, _ = capture_application()
    master = capture_master()
    alone = resolve_download_controls(app, master, scope=DownloadScope.APPLICATION)
    both = resolve_download_controls(
        app, master, scope=DownloadScope.APPLICATION, application2=_pei_program()
    )
    assert 5 not in [c.lsm_idx for c in alone if isinstance(c, LdCtrlLoad)]
    assert next(c.lsm_idx for c in both if isinstance(c, LdCtrlLoad)) == 5
    # The second program's fragments follow the application's of the same merge id.
    assert [
        getattr(c, "obj_idx", None)
        for c in both
        if type(c).__name__ == "LdCtrlLoadImageProp"
    ] == [1, 2, 3, 4, 5]


# A BIM M112 RF converter: application on LSM 1 to 3, PEI program on LSM 4, both with a
# procedure of their own (each also names the other's load state machine, filtered out).
_M112_APPLICATION = """<LoadProcedures><LoadProcedure>
  <LdCtrlConnect /><LdCtrlUnload LsmIdx="1" /><LdCtrlUnload LsmIdx="2" />
  <LdCtrlUnload LsmIdx="4" /><LdCtrlLoad LsmIdx="1" /><LdCtrlLoadCompleted LsmIdx="1" />
  <LdCtrlLoad LsmIdx="4" /><LdCtrlDisconnect />
</LoadProcedure></LoadProcedures>"""
_M112_PEI = """<LoadProcedures><LoadProcedure>
  <LdCtrlConnect /><LdCtrlUnload LsmIdx="4" /><LdCtrlUnload LsmIdx="1" />
  <LdCtrlLoad LsmIdx="4" /><LdCtrlLoadCompleted LsmIdx="4" /><LdCtrlDisconnect />
</LoadProcedure></LoadProcedures>"""


def _m112(procedures: str, application_id: str) -> Application:
    app, _ = seeded_application()
    app.program.mask_version = "MV-0701"
    app.program.id = application_id
    app.load_procedure_style = LoadProcedureStyle.PRODUCT_PROCEDURE  # type: ignore[misc]
    app.load_procedures = XmlParser().from_string(procedures, LoadProcedures)  # type: ignore[misc]
    return app


def _steps(controls: list[object]) -> list[str]:
    return [
        type(c).__name__.removeprefix("LdCtrl") + str(getattr(c, "lsm_idx", "") or "")
        for c in controls
    ]


def test_two_programs_with_procedures_of_their_own() -> None:
    from xknxeditor.namespaces.intermediate.mask_version_t_management_model import (
        MaskVersionManagementModel,
    )

    master = cast(
        RawMasterData,
        SimpleNamespace(
            mask_versions=SimpleNamespace(
                mask_version=[
                    SimpleNamespace(
                        id="MV-0701",
                        management_model=MaskVersionManagementModel.BIM_M112,
                        hawk_configuration_data=[],
                    )
                ]
            )
        ),
    )
    app = _m112(_M112_APPLICATION, "M-0008_A-C001-02-8C6A")
    pei = _m112(_M112_PEI, "M-0008_A-C001-F1-EC24")
    # The PEI program first, the application's Unloads behind its Unload, one connection.
    assert _steps(resolve_download_controls(app, master, application2=pei)) == [
        "Connect",
        "Unload4",
        "Unload1",
        "Unload2",
        "Load4",
        "LoadCompleted4",
        "Load1",
        "LoadCompleted1",
        "Disconnect",
    ]
    # Without one, the application keeps only its Unload of the PEI program's LSM.
    assert _steps(resolve_download_controls(app, master)) == [
        "Connect",
        "Unload1",
        "Unload2",
        "Unload4",
        "Load1",
        "LoadCompleted1",
        "Disconnect",
    ]
    # These applications' controls are taken as they are.
    plain = _m112(_M112_APPLICATION, "M-0001_A-8018-04-1234")
    assert "Load4" in _steps(resolve_download_controls(plain, master))


def test_the_image_holds_the_second_programs_segments() -> None:
    _, image = capture_application()
    both = with_second_program(image, _pei_program())
    assert both.object_segments == {**image.object_segments, 5: _PEI_DATA}
    segment = next(s for s in both.application_segments if s.object_index == 5)
    assert segment.memory.initialized_mask == b"\xff" * 4
    # Nothing to write: the image stays as it is.
    empty = _pei_program()
    assert empty.program.static.code is not None
    empty.program.static.code.relative_segment = []
    assert with_second_program(image, empty) is image


async def test_a_full_download_loads_the_pei_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dl = importlib.import_module("xknxeditor.download.download")
    app, image = capture_application()
    pei = _pei_program()
    device = CaptureDevice()
    device.table_references[5] = 0x6000
    device.load_states[5] = LoadState.LOADED

    class OfflineConnection:
        async def open(self) -> CaptureDevice:
            return device

        async def close(self) -> None:
            pass

    monkeypatch.setattr(dl, "_connection_manager", lambda *a: OfflineConnection())
    await dl.download(
        cast(XKNX, object()),
        "1.1.5",
        app,
        image=image,
        master=cast(MasterData, SimpleNamespace(raw=capture_master())),
        scope=DownloadScope.APPLICATION,
        max_apdu_length=15,
        expected_descriptor=0x07B0,
        application2=pei,
    )
    assert device.load_states[5] == LoadState.LOADED
    assert bytes(device.memory[0x6000 + n] for n in range(4)) == _PEI_DATA
    events = [
        (p.object_index, p.data[0])
        for p in device.sent
        if isinstance(p, PropertyValueWrite) and p.property_id == 5
    ]
    # Unloaded, loaded and completed with the application.
    assert [event for index, event in events if index == 5] == [4, 1, 3, 2]
    # Its id is the PEI program's, not the application's.
    assert device.properties[5, 13] == bytes.fromhex("00fa123443")
    assert device.properties[4, 13] == bytes.fromhex("00fa020010")


def test_the_image_holds_the_second_programs_absolute_segments() -> None:
    from xknxeditor.namespaces.intermediate.application_program_static_t_code_absolute_segment import (
        ApplicationProgramStaticCodeAbsoluteSegment,
    )

    _, image = capture_application()
    pei = _m112(_M112_PEI, "M-0008_A-C001-F1-EC24")
    assert pei.program.static.code is not None
    pei.program.static.code.absolute_segment = [
        ApplicationProgramStaticCodeAbsoluteSegment(
            id="AS-5F68", address=0x5F68, size=4, data=b"\x01\x02"
        ),
        ApplicationProgramStaticCodeAbsoluteSegment(
            id="AS-3FFA", address=0x3FFA, size=0
        ),
    ]
    both = with_second_program(image, pei)
    (added,) = both.segments[len(image.segments) :]
    assert (added.address, added.data) == (0x5F68, b"\x01\x02\x00\x00")
    # Only the data it brings is written, nothing over it.
    assert (added.mask, added.initialized_mask) == (bytes(4), b"\xff\xff\x00\x00")
