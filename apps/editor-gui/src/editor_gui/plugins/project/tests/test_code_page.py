"""The project's codepage: devices encode their texts with it while it is set, also after an undo,
and the history names it."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.code_page import PROJECT_CODE_PAGES, code_page_text
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.prod.application import parse_application_xml


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "packages" / "prod" / "tests" / "fixtures"
        if candidate.exists():
            return candidate / "calculations_application.xml"
    raise FileNotFoundError("calculations_application.xml")


@pytest.fixture
def project(tmp_path: Path) -> ProjectService:
    proj = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    return proj


def test_devices_follow_the_project_code_page(project: ProjectService) -> None:
    # An application that encodes its text parameters with the project's codepage.
    xml = _fixture().read_text(encoding="utf-8")
    xml = xml.replace(
        "</Static>",
        '<Options TextParameterEncodingSelector="UseProjectCodePage" /></Static>',
        1,
    )
    app = parse_application_xml(xml.encode(), "M-00FA")[0]
    node_id = project.add_device("P-1", "HP-1", "Calc", app, address=5)
    device = next(d for d in project.devices if d.node_id == node_id)

    project.set_project_code_page("iso-8859-7")
    ui = device.dynamic_ui
    assert ui is not None and ui.text_encoding == "iso-8859-7"
    assert project.get_project_metadata().code_page == "iso-8859-7"  # type: ignore[union-attr]
    assert any(
        entry.display_text == "Set codepage to Greek (ISO 8859-7)"
        for entry in project.history()
    )

    assert project.undo()
    assert project.project_code_page() is None
    ui = device.dynamic_ui
    assert ui is not None and ui.project_code_page is None
    assert project.redo()
    assert project.project_code_page() == "iso-8859-7"


def test_an_unknown_code_page_is_refused(project: ProjectService) -> None:
    with pytest.raises(ValueError, match="cp1251"):
        project.set_project_code_page("cp1251")
    assert project.project_code_page() is None


def test_the_offered_code_pages_and_their_names() -> None:
    names = [code_page_text(code) for code in PROJECT_CODE_PAGES]
    assert names[0] == "Western European (ISO 8859-1)"
    assert names[-1] == "System language"
    assert len(set(names)) == len(names) == 13
    # Valid in a project file, but not offered: shown as they are.
    assert code_page_text("utf-8") == "UTF-8"
