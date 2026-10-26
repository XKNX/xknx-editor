"""Adding a device from the catalog into a space: the room picker lists the spaces that hold
devices, and a device whose hardware does not support the medium of the space's line is not
added, with the reason."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.catalog.plugin import CatalogPlugin
from editor_gui.plugins.catalog.strings import S
from editor_gui.plugins.project.medium_text import medium_error_text
from xknxeditor.catalog import ProductSummary
from xknxeditor.proj.core.medium import MediumError


def _space(sid: int, name: str, space_type: str, *children: Any) -> Any:
    return SimpleNamespace(
        id=sid, name=name, space_type=space_type, children=list(children)
    )


def _plugin(project: Any, notices: list[str]) -> CatalogPlugin:
    plugin = CatalogPlugin.__new__(CatalogPlugin)
    plugin._api = cast(  # pyright: ignore[reportPrivateUsage]
        Any,
        SimpleNamespace(
            project=project, notify=notices.append, ask_device_certificate=None
        ),
    )
    plugin._log = cast(  # pyright: ignore[reportPrivateUsage]
        Any,
        SimpleNamespace(warning=lambda *_a, **_k: None, info=lambda *_a, **_k: None),
    )
    return plugin


def test_the_room_picker_lists_the_spaces_that_hold_devices() -> None:
    tree = [
        _space(
            1,
            "House",
            "Building",
            _space(
                2,
                "",
                "Floor",
                _space(3, "Kitchen", "Room", _space(4, "UV", "DistributionBoard")),
            ),
            _space(5, "Stairs", "Stairway"),
        )
    ]
    project = SimpleNamespace(is_open=True, get_space_tree=lambda: tree)
    plugin = _plugin(project, [])
    assert plugin._space_options() == [  # pyright: ignore[reportPrivateUsage]
        (3, "House / Floor / Kitchen"),
        (4, "House / Floor / Kitchen / UV"),
        (5, "House / Stairs"),
    ]
    project.is_open = False
    assert plugin._space_options() == []  # pyright: ignore[reportPrivateUsage]


def test_a_device_the_medium_refuses_is_not_added() -> None:
    error = MediumError("device_line_medium", device="Dimmer")

    def add_device(**_kwargs: Any) -> int:
        raise error

    notices: list[str] = []
    plugin = _plugin(
        SimpleNamespace(add_device=add_device, device_target_segment=None), notices
    )
    product = ProductSummary("P", "H", "Dimmer", None, None, "M-1", None)
    plugin._add(product, cast(Any, SimpleNamespace(name="App")), 3)  # pyright: ignore[reportPrivateUsage]
    assert notices == [medium_error_text(error)]


def test_a_device_goes_on_the_line_chosen() -> None:
    added: list[dict[str, Any]] = []

    def add_device(**kwargs: Any) -> int:
        added.append(kwargs)
        return 7

    project = SimpleNamespace(
        add_device=add_device,
        device_target_segment=12,
        secure_commissioning_of=lambda _d: False,
    )
    notices: list[str] = []
    plugin = _plugin(project, notices)
    product = ProductSummary("P", "H", "Dimmer", None, None, "M-1", None)
    plugin._add(product, cast(Any, SimpleNamespace(name="App")), 3)  # pyright: ignore[reportPrivateUsage]
    assert added[0]["segment_id"] == 12 and added[0]["space_id"] == 3
    # The picker shows the line chosen, and a choice is kept in the project.
    plugin._set_line(None)  # pyright: ignore[reportPrivateUsage]
    assert project.device_target_segment is None
    assert len(notices) == 1


def test_the_line_chosen_shows_at_the_top_of_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from imgui_bundle import imgui

    from editor_gui.plugins.catalog.ui import panel as panel_module
    from editor_gui.plugins.catalog.ui.panel import CatalogPanel

    chosen: list[int | None] = [5]
    shown: list[tuple[str, list[str]]] = []

    def combo(label: str, current: int, items: list[str]) -> tuple[bool, int]:
        shown.append((label, items))
        return True, 0  # "Automatic" picked

    monkeypatch.setattr(panel_module, "combo", combo)
    panel = CatalogPanel(
        get_products=list,
        on_select=lambda _p, _s: None,
        get_lines=lambda: [(5, "1.1 Office"), (6, "  1.1 Garage")],
        get_line=lambda: chosen[0],
        set_line=lambda segment: chosen.__setitem__(0, segment),
    )
    ctx = imgui.create_context()
    try:
        io = imgui.get_io()
        io.display_size = imgui.ImVec2(800, 600)
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        for _ in range(2):
            imgui.new_frame()
            imgui.begin("Catalog")
            panel.render()
            imgui.end()
            imgui.end_frame()
    finally:
        imgui.destroy_context(ctx)
    assert shown[0] == (
        "##catalog_add_line_top",
        [S.CATALOG_ADD_LINE_AUTO, "1.1 Office", "  1.1 Garage"],
    )
    # Back to "Automatic": the picker is gone from the top.
    assert chosen == [None] and len(shown) == 1
