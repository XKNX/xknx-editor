"""Only the most recently used reloadable applications keep their parsed program in memory."""

from collections import OrderedDict
from pathlib import Path

import pytest

import xknxeditor.prod.application as application_module
from xknxeditor.prod import parse_application_xml
from xknxeditor.prod.application import Application
from xknxeditor.prod.archive import Archive

_FIXTURE = Path(__file__).parent / "fixtures" / "gira_2gang_button_interface.knxprod"


@pytest.fixture(autouse=True)
def _isolated_residency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application_module, "_resident", OrderedDict())
    monkeypatch.setattr(application_module, "RESIDENT_APPLICATIONS", 1)


def _parse() -> Application:
    with Archive(str(_FIXTURE)) as archive:
        mid = min(archive.manufacturer_ids)
        _app_id, xml = next(iter(archive.get_application_xmls(mid).items()))
    return parse_application_xml(xml, mid)[0]


def _reloadable() -> tuple[Application, list[int]]:
    app = _parse()
    loads: list[int] = []

    def reload():
        loads.append(1)
        return _parse().program

    app.set_reloader(reload)
    return app, loads


def test_least_recently_used_application_is_dropped_and_reloaded() -> None:
    first, first_loads = _reloadable()
    app_id, name = first.id, first.name
    second, _ = _reloadable()
    assert first._program is None  # dropped for the more recently used one
    assert second._program is not None

    # id and name stay available without parsing again
    assert (first.id, first.name) == (app_id, name)
    assert first_loads == []

    assert first.program.id == app_id
    assert first_loads == [1]
    assert second._program is None


def test_program_and_tree_are_resolved_together() -> None:
    app, _ = _reloadable()
    program, tree_builder = app.resolved()
    assert app.tree_builder() is tree_builder
    assert app.program is program

    other, _ = _reloadable()
    other.resolved()
    assert app._tree_builder is None
    reloaded_program, reloaded_tree = app.resolved()
    assert reloaded_program is not program
    assert reloaded_tree is not tree_builder
    assert app.program is reloaded_program


def test_application_without_reloader_is_kept() -> None:
    plain = _parse()
    _reloadable()
    _reloadable()
    assert plain._program is not None
    assert application_module._resident.get(id(plain)) is None
