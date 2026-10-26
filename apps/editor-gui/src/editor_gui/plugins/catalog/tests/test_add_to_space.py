"""Adding a device from the catalog into a space: the room picker lists the spaces that hold
devices, and a device whose hardware does not support the medium of the space's line is not
added, with the reason."""

from __future__ import annotations

from collections.abc import Iterator
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


def _plugin(
    project: Any, notices: list[str], refused: list[str] | None = None
) -> CatalogPlugin:
    """The plugin on ``project``: confirmations go to ``notices``, refusals to ``refused``."""
    plugin = CatalogPlugin.__new__(CatalogPlugin)
    plugin._api = cast(  # pyright: ignore[reportPrivateUsage]
        Any,
        SimpleNamespace(
            project=project,
            notify=notices.append,
            tell_refused=(refused if refused is not None else []).append,
            ask_device_certificate=None,
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
    refused: list[str] = []
    plugin = _plugin(
        SimpleNamespace(add_device=add_device, device_target_segment=None),
        notices,
        refused,
    )
    product = ProductSummary("P", "H", "Dimmer", None, None, "M-1", None)
    plugin._add(product, cast(Any, SimpleNamespace(name="App")), 3)  # pyright: ignore[reportPrivateUsage]
    # Told as a refusal (the toast that stands out), not as a confirmation.
    assert refused == [medium_error_text(error)] and notices == []


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
    # A line given ("Add to") goes before the one chosen.
    plugin._add(product, cast(Any, SimpleNamespace(name="App")), None, 30)  # pyright: ignore[reportPrivateUsage]
    assert added[1]["segment_id"] == 30 and added[1]["space_id"] is None
    # The picker shows the line chosen, and a choice is kept in the project.
    plugin._set_line(None)  # pyright: ignore[reportPrivateUsage]
    assert project.device_target_segment is None
    assert len(notices) == 2


@pytest.fixture
def context() -> Iterator[None]:
    from imgui_bundle import imgui

    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frames(panel: Any, count: int = 2) -> None:
    from imgui_bundle import imgui

    for _ in range(count):
        imgui.new_frame()
        imgui.begin("Catalog")
        panel.render()
        imgui.end()
        imgui.end_frame()


_LINES = [(5, "1.1 Office"), (6, "  1.1 Garage")]


@pytest.mark.usefixtures("context")
def test_the_line_picker_stays_at_the_top_of_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        get_lines=lambda: _LINES,
        get_line=lambda: chosen[0],
        set_line=lambda segment: chosen.__setitem__(0, segment),
    )
    _frames(panel)
    assert shown[0] == (
        "##catalog_add_line_top",
        [S.CATALOG_ADD_LINE_AUTO, "1.1 Office", "  1.1 Garage"],
    )
    # Back to "Automatic": the picker stays, for the next device.
    assert chosen == [None] and len(shown) == 2


@pytest.mark.usefixtures("context")
def test_a_product_is_selected_added_by_the_button_and_added_to_a_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from imgui_bundle import imgui

    from editor_gui.plugins.catalog.ui import panel as panel_module
    from editor_gui.plugins.catalog.ui.panel import CatalogPanel
    from editor_gui.plugins.project.tests._clicks import Clicks

    product = ProductSummary("P", "H", "Dimmer", None, None, "M-1", None)
    selected: list[str] = []
    disabled: list[bool] = []
    selected_flag = int(imgui.TreeNodeFlags_.selected)

    real_tree_node = panel_module.tree_node

    # The manufacturer shown open; the rows shown highlighted noted.
    def tree_node(label: str, flags: int = 0) -> bool:
        if flags & selected_flag:
            selected.append(label)
        if label.endswith("##M-1"):
            imgui.set_next_item_open(True)
        return real_tree_node(label, flags)

    monkeypatch.setattr(panel_module, "tree_node", tree_node)
    real_begin_disabled = imgui.begin_disabled

    def begin_disabled(on: bool = True) -> None:
        disabled.append(on)
        real_begin_disabled(on)

    monkeypatch.setattr(imgui, "begin_disabled", begin_disabled)
    pressed = {"add": False, "clicked": ""}
    real_button = imgui.button
    monkeypatch.setattr(
        imgui,
        "button",
        lambda label, *a: (
            real_button(label, *a) or (label == S.CATALOG_DETAIL_ADD and pressed["add"])
        ),
    )
    monkeypatch.setattr(
        imgui, "is_item_clicked", lambda *_a: pressed["clicked"] == "row"
    )
    added: list[tuple[str, int | None]] = []
    to_line: list[tuple[str, int]] = []
    panel = CatalogPanel(
        get_products=lambda: [product],
        on_select=lambda p, s: added.append((p.product_ref_id, s)),
        get_lines=lambda: _LINES,
        on_add_to_line=lambda p, segment: to_line.append((p.product_ref_id, segment)),
    )
    # Nothing selected: "Add Device" is grayed out and the row not highlighted.
    _frames(panel)
    assert disabled[-1] is True and selected == []
    # A click selects the product: highlighted, and the button adds it.
    pressed["clicked"] = "row"
    _frames(panel, 1)
    pressed["clicked"] = ""
    pressed["add"] = True
    _frames(panel, 1)
    pressed["add"] = False
    assert disabled[-1] is False and selected
    assert added == [("P", None)]
    # Its menu's "Add to" lists the lines and adds it on the one chosen.
    clicks = Clicks(monkeypatch, panel_module)
    clicks.context = "##catalog_ctx_P|H"
    clicks.menus.add(S.CATALOG_ADD_TO)
    clicks.items.add("  1.1 Garage##addto6")
    _frames(panel)
    assert to_line == [("P", 6)]
