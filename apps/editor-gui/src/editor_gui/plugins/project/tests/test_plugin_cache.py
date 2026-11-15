"""Plugin reads stay current across edits and share work between consumers."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from editor_gui import plugin_params, plugin_providers
from editor_gui.device import Device
from editor_gui.plugin_params import PluginMapping, PluginSectionView
from editor_gui.plugin_providers import PluginCapabilities
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService


def test_plugin_cache_refreshes_values_errors_and_product_changes(
    tmp_path, monkeypatch
):
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    project = ProjectService(catalog)
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "cache.xknx")
    device = Device(1, "Device", None, "")
    mapping = PluginMapping((), "Example", (), provider="example")
    calls = []
    value = ["old"]
    rejected = [False]
    language = ["en-US"]

    def sections(*_):
        calls.append("sections")
        if rejected[0]:
            raise ValueError("invalid config")
        return [PluginSectionView(value[0])]

    def capabilities(_):
        calls.append("capabilities")
        return PluginCapabilities(True, True, True, None)

    monkeypatch.setattr(plugin_params, "mapping_for_device", lambda _: mapping)
    monkeypatch.setattr(plugin_params, "collect_from_device", sections)
    monkeypatch.setattr(
        plugin_providers,
        "provider_for",
        lambda _: SimpleNamespace(capabilities=capabilities),
    )
    monkeypatch.setattr("editor_gui.languages.product_language", lambda: language[0])
    try:
        initial = project.plugin_sections(device, project.plugin_mapping(device))
        initial_caps = project.plugin_capabilities(device)
        assert project.plugin_sections(device, mapping) is initial
        assert project.plugin_capabilities(device) is initial_caps
        assert calls == ["sections", "capabilities"]
        value[0] = "new"
        project._bump(structural=False)
        assert project.plugin_sections(device, mapping)[0].section == "new"
        assert project.plugin_capabilities(device) is not initial_caps
        for change in (
            lambda: language.__setitem__(0, "de-DE"),
            lambda: setattr(catalog, "generation", catalog.generation + 1),
            lambda: project._bump(structural=False),
        ):
            previous = project.plugin_sections(device, mapping)
            change()
            assert project.plugin_sections(device, mapping) is not previous
        previous = project.plugin_sections(device, mapping)
        assert project.plugin_sections(replace(device), mapping) is not previous
        rejected[0] = True
        project._bump(structural=False)
        count = len(calls)
        for _ in range(3):
            with pytest.raises(ValueError, match="invalid config"):
                project.plugin_sections(device, mapping)
        assert len(calls) == count + 1
        rejected[0] = False
        project.close()
        project.open(tmp_path / "cache.xknx")
        assert project.plugin_sections(device, mapping)[0].section == "new"
    finally:
        project.close()


def test_native_sections_and_capabilities_follow_edit_undo_redo(tmp_path, monkeypatch):
    from .test_powertool import current, native_project

    project, node_id = native_project(tmp_path, monkeypatch)

    def state():
        device = current(project, node_id)
        mapping = project.plugin_mapping(device)
        assert mapping is not None
        return (
            project.plugin_sections(device, mapping),
            project.plugin_capabilities(device),
        )

    def function(sections):
        return next(
            p.raw_value if p.raw_value is not None else p.value
            for section in sections
            for p in section.parameters
            if p.ref_id == "powertool:0:function"
        )

    try:
        before, capabilities = state()
        assert not before and not capabilities.configured
        project.setup_plugin(current(project, node_id))
        original, capabilities = state()
        assert capabilities.configured and capabilities.preflight
        assert function(original) == "switch_rocker"
        project.edit_plugin_parameter(
            current(project, node_id), "powertool:0:function", "dim_rocker"
        )
        edited, _ = state()
        assert function(edited) == "dim_rocker"
        assert function(original) == "switch_rocker"
        project.undo()
        assert function(state()[0]) == "switch_rocker"
        project.redo()
        assert function(state()[0]) == "dim_rocker"
        project.undo()
        project.undo()
        assert not state()[1].configured
    finally:
        project.close()
