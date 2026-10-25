"""Adding a device from the catalog into a space: the room picker lists the spaces that hold
devices, and a device whose hardware does not support the medium of the space's line is not
added, with the reason."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.plugins.catalog.plugin import CatalogPlugin
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
    plugin = _plugin(SimpleNamespace(add_device=add_device), notices)
    product = ProductSummary("P", "H", "Dimmer", None, None, "M-1", None)
    plugin._add(product, cast(Any, SimpleNamespace(name="App")), 3)  # pyright: ignore[reportPrivateUsage]
    assert notices == [medium_error_text(error)]
