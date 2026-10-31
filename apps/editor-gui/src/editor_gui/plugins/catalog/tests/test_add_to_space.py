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


def test_a_product_without_an_application_is_added() -> None:
    # A power supply: no application program, a device with a name and an address only.
    added: list[dict[str, Any]] = []

    def add_device(**kwargs: Any) -> int:
        added.append(kwargs)
        return 8

    project = SimpleNamespace(
        is_open=True,
        add_device=add_device,
        device_target_segment=12,
        secure_commissioning_of=lambda _d: False,
    )
    notices: list[str] = []
    refused: list[str] = []
    plugin = _plugin(project, notices, refused)
    product = ProductSummary("P", "H", "Power Supply", None, None, "M-1", None)
    plugin._on_select(product, 3)  # pyright: ignore[reportPrivateUsage]
    assert [(a["app"], a["space_id"], a["segment_id"]) for a in added] == [
        (None, 3, 12)
    ]
    assert refused == [] and notices == [S.CATALOG_ADD_OK.format(name="Power Supply")]


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
        # A panel's size, so its tree area below the search has room for rows.
        imgui.set_next_window_size(imgui.ImVec2(600, 500))
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
    to_line: list[tuple[str, int, int | None]] = []
    panel = CatalogPanel(
        get_products=lambda: [product],
        on_select=lambda p, s: added.append((p.product_ref_id, s)),
        get_spaces=lambda: [(3, "House / Kitchen")],
        get_lines=lambda: _LINES,
        on_add_to_line=lambda p, segment, space: to_line.append(
            (p.product_ref_id, segment, space)
        ),
    )
    # Nothing selected: "Add Device" is grayed out and the row not highlighted.
    _frames(panel)
    assert disabled[-1] is True and selected == []
    # A click selects the product: highlighted, and the button adds it, into the room chosen
    # at the top (no dialog asks for it).
    panel._add_space_id = 3  # pyright: ignore[reportPrivateUsage]
    pressed["clicked"] = "row"
    _frames(panel, 1)
    pressed["clicked"] = ""
    pressed["add"] = True
    _frames(panel, 1)
    pressed["add"] = False
    assert disabled[-1] is False and selected
    assert added == [("P", 3)]
    assert not imgui.is_popup_open("", imgui.PopupFlags_.any_popup_id)
    # Its menu's "Add to" lists the lines and adds it on the one chosen, into the room.
    clicks = Clicks(monkeypatch, panel_module)
    clicks.context = "##catalog_ctx_P|H"
    clicks.menus.add(S.CATALOG_ADD_TO)
    clicks.items.add("  1.1 Garage##addto6")
    _frames(panel)
    assert to_line == [("P", 6, 3)]


class _SyncThread:
    """A thread that runs its target when started (the download of a test)."""

    def __init__(self, target: Any, args: tuple[Any, ...] = (), daemon: bool = False):
        self._target, self._args = target, args

    def start(self) -> None:
        self._target(*self._args)


def test_an_online_product_is_added_and_downloaded_first_when_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui.plugins.catalog.ui import panel as panel_module
    from editor_gui.plugins.catalog.ui.panel import CatalogPanel

    monkeypatch.setattr(panel_module.threading, "Thread", _SyncThread)
    product = ProductSummary("P", "H-7", "Dimmer", None, None, "M-1", None)
    local: dict[str, ProductSummary] = {}
    downloads: list[str] = []
    added: list[tuple[str, int | None]] = []
    to_line: list[tuple[str, int, int | None]] = []
    refused: list[str] = []

    def download(item_id: str) -> None:
        downloads.append(item_id)
        if item_id == "H-7_X":
            local[item_id] = product
        elif item_id == "broken":
            raise RuntimeError("no connection")

    panel = CatalogPanel(
        get_products=list,
        on_select=lambda p, s: added.append((p.product_ref_id, s)),
        download_online_item=download,
        find_local_product=local.get,
        on_add_to_line=lambda p, segment, space: to_line.append(
            (p.product_ref_id, segment, space)
        ),
        tell_refused=refused.append,
    )
    panel._add_space_id = 4  # pyright: ignore[reportPrivateUsage]
    # Not local yet: downloaded first, then added (on the GUI thread, the next frame).
    panel._add_online("H-7_X")  # pyright: ignore[reportPrivateUsage]
    assert downloads == ["H-7_X"] and added == []
    panel._add_downloaded()  # pyright: ignore[reportPrivateUsage]
    assert added == [("P", 4)]
    # Local now: added right away, "Add to" on the line given.
    panel._add_online("H-7_X", 6)  # pyright: ignore[reportPrivateUsage]
    assert downloads == ["H-7_X"] and to_line == [("P", 6, 4)]
    # A failed download adds nothing; a download that imported nothing usable says why.
    panel._add_online("broken")  # pyright: ignore[reportPrivateUsage]
    panel._add_downloaded()  # pyright: ignore[reportPrivateUsage]
    panel._add_online("empty")  # pyright: ignore[reportPrivateUsage]
    panel._add_downloaded()  # pyright: ignore[reportPrivateUsage]
    assert added == [("P", 4)] and refused == [S.CATALOG_ADD_NO_APP]
    # "Download to Local Catalog" only downloads.
    panel._start_download("other")  # pyright: ignore[reportPrivateUsage]
    panel._add_downloaded()  # pyright: ignore[reportPrivateUsage]
    assert downloads[-1] == "other" and added == [("P", 4)]


def test_the_online_item_becomes_the_local_product_it_was_imported_as() -> None:
    products = [
        ProductSummary("P1", "M-1_H-1", "Short", None, None, "M-1", None),
        ProductSummary("P2", "M-1_H-1-2", "Long", None, None, "M-1", None),
    ]
    plugin = _plugin(SimpleNamespace(), [])
    plugin._api.catalog = SimpleNamespace(get_products=lambda: products)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    found = plugin._local_product_of("M-1_H-1-2_HP-9")  # pyright: ignore[reportPrivateUsage]
    assert found is not None and found.product_ref_id == "P2"
    assert plugin._local_product_of("M-2_H-1") is None  # pyright: ignore[reportPrivateUsage]
