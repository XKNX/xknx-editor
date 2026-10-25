"""What the Topology view's actions do: a new segment is selected, a change the media do not allow
is told to the user, and "Compare with…" opens the Tools panel."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from editor_gui.plugins.project.medium_text import medium_error_text
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.ui.topology import Line, Segment
from xknxeditor.proj.core.medium import MediumError


class _Project:
    """The project service calls of the handlers, each answering ``outcome``."""

    def __init__(self, outcome: BaseException | Any) -> None:
        self.outcome = outcome
        self.selected: list[tuple[str, int]] = []

    def _answer(self, *_args: Any) -> Any:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    add_segment = remove_segment = move_device_to_segment = _answer
    set_device_individual_address = _answer

    def select_element(self, kind: str, element_id: int) -> None:
        self.selected.append((kind, element_id))


def _plugin(
    outcome: BaseException | Any,
) -> tuple[ProjectPlugin, _Project, list[str], list[str]]:
    project = _Project(outcome)
    notices: list[str] = []
    warnings: list[str] = []
    plugin = ProjectPlugin.__new__(ProjectPlugin)
    plugin._api = SimpleNamespace(  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        project=project,
        notify=notices.append,
        log=SimpleNamespace(warning=lambda message, **_k: warnings.append(message)),
    )
    return plugin, project, notices, warnings


_LINE = Line(id=3, area_id=2, number=1, name="")
_SEGMENT = Segment(id=7, name="Radio")
_DEVICE = SimpleNamespace(node_id=5, individual_address="1.1.5")


def test_a_new_segment_is_selected() -> None:
    plugin, project, notices, _warnings = _plugin(9)
    plugin._on_add_segment(_LINE)  # pyright: ignore[reportPrivateUsage]
    assert project.selected == [("segment", 9)] and notices == []


def test_what_the_media_do_not_allow_is_told() -> None:
    error = MediumError("rf_no_sub_segments")
    for handler in (
        lambda p: p._on_add_segment(_LINE),
        lambda p: p._on_remove_segment(_SEGMENT),
        lambda p: p._on_move_device_to_segment(_DEVICE, _SEGMENT),
        lambda p: p._handle_individual_address_change(_DEVICE, "1.1.6"),
    ):
        plugin, project, notices, warnings = _plugin(error)
        handler(plugin)
        assert notices == [medium_error_text(error)]
        assert warnings == ["rejected by the medium"]
        assert project.selected == []
    assert _DEVICE.individual_address == "1.1.5"


def test_a_full_line_is_logged() -> None:
    plugin, _project, notices, warnings = _plugin(ValueError("line full"))
    plugin._on_move_device_to_segment(_DEVICE, _SEGMENT)  # pyright: ignore[reportPrivateUsage]
    assert (notices, warnings) == ([], ["could not move the device"])


def test_an_accepted_address_goes_onto_the_device() -> None:
    device = SimpleNamespace(node_id=5, individual_address="1.1.5")
    plugin, _project, _notices, _warnings = _plugin(True)
    plugin._handle_individual_address_change(device, "1.1.5")  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    plugin._handle_individual_address_change(device, "1.1.6")  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    assert device.individual_address == "1.1.6"
    plugin, _project, _notices, _warnings = _plugin(False)
    plugin._handle_individual_address_change(device, "1.1.7")  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    assert device.individual_address == "1.1.6"


def test_compare_with_opens_the_tools_panel() -> None:
    opened: list[int] = []
    plugin, _project, _notices, _warnings = _plugin(None)
    plugin._tools_panel = SimpleNamespace(open_compare=opened.append)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    plugin._focus_tools = False  # pyright: ignore[reportPrivateUsage]
    assert not plugin.take_focus_tools()
    plugin._open_compare(5)  # pyright: ignore[reportPrivateUsage]
    assert opened == [5]
    assert plugin.take_focus_tools()
    assert not plugin.take_focus_tools()
