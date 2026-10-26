"""Module instances in a .knxproj: written with relative ids and their arguments, read back
relative also from files written with the application id; missing project files are not opened."""

import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from xknxeditor.proj import ProjectService, export_knxproj
from xknxeditor.proj.core.knxproj_export import module_repeat_indexes
from xknxeditor.proj.core.knxproj_import import _build_module_instance
from xknxeditor.proj.db import make_engine, url_for
from xknxeditor.proj.models import ModuleInstance

APP = "M-0083_A-0317-31-7DC6"


def _project_with_modules(path: Path) -> int:
    svc = ProjectService()
    pid = svc.create(path, "P-MI")
    line = svc.create_line(pid, svc.create_area(pid, 0, 2, "A"), 1, "L")
    segment_id = next(
        ln.segments[0].id
        for area in svc.topology(pid, 0).areas
        for ln in area.lines
        if ln.id == line
    )
    # As a device added in the editor stores them: with the application id.
    device_id = svc.add_device(
        pid,
        segment_id,
        "M-0083_H-1_P-1",
        address=1,
        name="Dev",
        hardware2program_ref_id="M-0083_H-1_HP-1",
        module_instances=[
            (f"{APP}_MD-2_M-12_MI-1", f"{APP}_MD-2"),
            (f"{APP}_MD-2_M-2_MI-1", f"{APP}_MD-2"),
        ],
    )
    svc.close(pid)
    return device_id


def _module_instances(knxproj: Path) -> list[str]:
    with zipfile.ZipFile(knxproj) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")
    return re.findall(r"<ModuleInstance\b[^>]*?(?:/>|>.*?</ModuleInstance>)", xml, re.S)


def test_export_writes_relative_ids_and_arguments(tmp_path: Path) -> None:
    src = tmp_path / "p.xknx"
    device_id = _project_with_modules(src)
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src,
        out,
        module_arguments={
            device_id: {
                f"{APP}_MD-2_M-12_MI-1": [("MD-2_A-1", "32"), ("MD-2_A-3", "A")],
                f"{APP}_MD-2_M-2_MI-1": [("MD-2_A-1", "48"), ("MD-2_A-3", "B")],
            }
        },
    )
    first, second = _module_instances(out)
    assert 'Id="MD-2_M-12_MI-1" RefId="MD-2_M-12"' in first
    assert re.findall(r'Argument RefId="([^"]+)" Value="([^"]*)"', first) == [
        ("MD-2_A-1", "32"),
        ("MD-2_A-3", "A"),
    ]
    assert 'Id="MD-2_M-2_MI-1" RefId="MD-2_M-2"' in second
    assert 'Value="B"' in second


def test_export_keeps_stored_arguments(tmp_path: Path) -> None:
    src = tmp_path / "p.xknx"
    device_id = _project_with_modules(src)
    engine = make_engine(url_for(src))
    with Session(engine) as session:
        for row in session.query(ModuleInstance):
            row.arguments = [{"ref_id": "MD-2_A-1", "value": "7"}]
        session.commit()
    engine.dispose()
    out = tmp_path / "out.knxproj"
    export_knxproj(
        src,
        out,
        module_arguments={device_id: {f"{APP}_MD-2_M-12_MI-1": [("MD-2_A-1", "32")]}},
    )
    for instance in _module_instances(out):
        assert re.findall(r'Argument RefId="([^"]+)" Value="([^"]*)"', instance) == [
            ("MD-2_A-1", "7")
        ]


@pytest.mark.parametrize(
    ("identifier", "module_def_id"),
    [(f"{APP}_MD-2_M-12_MI-1", "M-0083"), ("MD-2_M-12_MI-1", "MD-2")],
)
def test_import_keeps_module_instances_relative(
    identifier: str, module_def_id: str
) -> None:
    row = _build_module_instance(
        SimpleNamespace(identifier=identifier, module_def_id=module_def_id),
        {identifier: ("", [{"ref_id": "MD-2_A-1", "value": "32"}])},
    )
    assert (row.instance_id, row.ref_id) == ("MD-2_M-12_MI-1", "MD-2")
    assert row.arguments == [{"ref_id": "MD-2_A-1", "value": "32"}]


@pytest.mark.parametrize("empty", [False, True])
def test_open_refuses_a_missing_or_empty_file(tmp_path: Path, empty: bool) -> None:
    path = tmp_path / "gone.xknx"
    if empty:
        path.touch()
    with pytest.raises(FileNotFoundError):
        ProjectService().open(path)
    assert path.exists() == empty
    assert not empty or path.stat().st_size == 0


_APP_XML = f"""<KNX xmlns="http://knx.org/xml/project/23"><ManufacturerData><Manufacturer>
<ApplicationPrograms><ApplicationProgram Id="{APP}">
<ModuleDefs><ModuleDef Id="{APP}_MD-2"><Dynamic><Module Id="{APP}_MD-2_SM-1_M-1" />
</Dynamic></ModuleDef></ModuleDefs>
<Dynamic>
  <ChannelIndependentBlock><ParameterBlock Id="{APP}_PB-1" /></ChannelIndependentBlock>
  <Module Id="{APP}_MD-2_M-12" />
  <Repeat Id="{APP}_X-1" Count="3"><Module Id="{APP}_MD-2_M-2" /></Repeat>
</Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def test_repeat_indexes_count_the_places_in_the_dynamic_section() -> None:
    # The program's <Dynamic> is place 0; a repeat's iterations number the instances; the
    # modules of a module definition's own <Dynamic> are not counted.
    assert module_repeat_indexes(_APP_XML.encode()) == {
        "MD-2_M-12_MI-1": "3x1",
        "MD-2_M-2_MI-1": "4x1 5x1",
        "MD-2_M-2_MI-2": "4x2 5x1",
        "MD-2_M-2_MI-3": "4x3 5x1",
    }
    assert module_repeat_indexes(b"<KNX />") == {}


def test_export_numbers_the_module_instances_of_a_device_added_in_the_editor(
    tmp_path: Path,
) -> None:
    # Without a RepeatIndex the importer fails on the device.
    src = tmp_path / "p.xknx"
    _project_with_modules(src)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out, extra_files={f"M-0083/{APP}.xml": _APP_XML.encode()})
    first, second = _module_instances(out)
    assert 'Id="MD-2_M-12_MI-1" RefId="MD-2_M-12" RepeatIndex="3x1"' in first
    assert 'Id="MD-2_M-2_MI-1" RefId="MD-2_M-2" RepeatIndex="4x1 5x1"' in second
    # Without the application program there is nothing to number them by.
    export_knxproj(src, out)
    assert all("RepeatIndex" not in m for m in _module_instances(out))


def test_function_group_address_refs_are_numbered_in_the_project(
    tmp_path: Path,
) -> None:
    svc = ProjectService()
    src = tmp_path / "f.xknx"
    pid = svc.create(src, "P-FN")
    building = svc.create_space(pid, 0, "Building", "House")
    links = []
    for name in ("Light", "Blind"):
        function = svc.create_function(pid, building, "FT-1", name)
        for sub in (1, 2):
            ga = svc.create_group_address(pid, 0, (1 << 11) + sub + len(links) * 2, "")
            links.append(svc.add_function_group_address(pid, function, ga))
    svc.close(pid)
    out = tmp_path / "out.knxproj"
    export_knxproj(src, out)
    with zipfile.ZipFile(out) as zf:
        zero = next(n for n in zf.namelist() if n.endswith("/0.xml"))
        xml = zf.read(zero).decode("utf-8")
    ids = re.findall(r'<GroupAddressRef Id="([^"]+)"', xml)
    # As genuine exports: P-xxxx-0_GF-n from 1 across the project, not under the function.
    assert [i.split("_", 1)[1] for i in ids] == ["GF-1", "GF-2", "GF-3", "GF-4"]
    assert all(re.fullmatch(r"P-[^_]+-0_GF-\d+", i) for i in ids)
    # Each with its role from the function type (switchable light), though none is stored.
    roles = re.findall(r'<GroupAddressRef [^>]*Role="([^"]+)"', xml)
    assert roles == ["SwitchOnOff", "InfoOnOff"] * 2
