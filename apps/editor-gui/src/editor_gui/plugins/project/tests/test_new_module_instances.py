"""The module instances a parameter change creates or ends are saved with it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.project.service import _module_instance_changes
from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance

APP = "M-0004_A-D164-21-815F-O000A"


def test_new_and_ended_instances() -> None:
    # Imported devices keep their instance ids relative to the application.
    device = SimpleNamespace(
        module_instances=[
            ModuleInstance(id="MD-1_M-1_MI-1", ref_id="MD-1"),
            ModuleInstance(id=f"{APP}_MD-1_M-3_MI-1", ref_id=f"{APP}_MD-1"),
        ],
        get_module_instances=lambda: [
            (f"{APP}_MD-1_M-1_MI-1", f"{APP}_MD-1"),
            (f"{APP}_MD-1_M-2_MI-1", f"{APP}_MD-1"),
        ],
    )
    new, ended = _module_instance_changes(cast("Any", device), APP)
    assert new == [(f"{APP}_MD-1_M-2_MI-1", f"{APP}_MD-1")]
    assert ended == [f"{APP}_MD-1_M-3_MI-1"]
