"""A com object a product references at two places of its parameter tree is one object."""

from pathlib import Path
from types import SimpleNamespace

from editor_gui.device import Device, _collect_ui_com_objects
from xknxeditor.prod import load

_APP_ID = "M-0008_A-7072-21-5CC3-O000A"


def _fixture_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "packages"
            / "prod"
            / "tests"
            / "fixtures"
            / "gira_2gang_button_interface.knxprod"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("gira_2gang_button_interface.knxprod not found")


def test_an_object_referenced_twice_is_created_once() -> None:
    app = load(_fixture_path().read_bytes()).applications[_APP_ID]
    device = Device(node_id=1, name="Gira", app=app, individual_address="1.1.1")
    dyn = device.dynamic_ui
    assert dyn is not None
    first = _collect_ui_com_objects(dyn.ui())[0]
    device._dynamic_ui = SimpleNamespace(  # pyright: ignore[reportAttributeAccessIssue,reportPrivateUsage]
        ui=lambda: [first, first],
        instantiated_com_object_ref_ids=set,
    )
    created = device._create_com_objects_from_app()  # pyright: ignore[reportPrivateUsage]
    assert [co.id for co in created] == [first.ref_id]
