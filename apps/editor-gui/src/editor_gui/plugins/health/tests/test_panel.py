"""The Health panel: a checkbox per kind of findings (errors, warnings, hints) counts them and
shows or hides them; the findings have their kind's icon."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.health.service import Finding, Severity
from editor_gui.plugins.health.strings import S
from editor_gui.plugins.health.ui import SEVERITY_ICON, HealthPanel


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


class _Service:
    def __init__(self, findings: list[Finding]) -> None:
        self._findings = findings

    def findings(self) -> list[Finding]:
        return self._findings


_FINDINGS = [
    Finding(Severity.INFO, "com-object", "Hint A", 1),
    Finding(Severity.WARNING, "address", "Warning A", 2),
    Finding(Severity.WARNING, "address", "Warning B", 3),
    Finding(Severity.ERROR, "address", "Error A", 4),
]


class _Drawn:
    """The texts, icons and rows the last frame drew, and where."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.texts: dict[str, imgui.ImVec2] = {}
        self.icons: list[str] = []
        self.rows: list[str] = []
        text, colored, selectable = imgui.text, imgui.text_colored, imgui.selectable

        def record_text(value: str) -> None:
            text(value)
            self.texts[value] = imgui.get_item_rect_min()

        def record_colored(color: imgui.ImVec4, value: str) -> None:
            colored(color, value)
            self.icons.append(value)

        def record_selectable(label: str, *args: Any) -> Any:
            self.rows.append(label.split("##")[0])
            return selectable(label, *args)

        monkeypatch.setattr(imgui, "text", record_text)
        monkeypatch.setattr(imgui, "text_colored", record_colored)
        monkeypatch.setattr(imgui, "selectable", record_selectable)

    def clear(self) -> None:
        self.texts.clear()
        self.icons.clear()
        self.rows.clear()


def _frame(panel: HealthPanel, drawn: _Drawn) -> None:
    drawn.clear()
    imgui.new_frame()
    imgui.set_next_window_pos(imgui.ImVec2(0, 0))
    imgui.set_next_window_size(imgui.ImVec2(600, 400))
    imgui.begin("Health")
    panel.render()
    imgui.end()
    imgui.end_frame()


def _panel(findings: list[Finding], navigated: list[int] | None = None) -> HealthPanel:
    log = navigated if navigated is not None else []
    return HealthPanel(
        cast("Any", _Service(findings)),
        on_navigate=log.append,
        on_navigate_ga=log.append,
        is_open=lambda: True,
    )


def test_every_kind_is_counted_and_shown_errors_first(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = _Drawn(monkeypatch)
    panel = _panel(_FINDINGS)
    _frame(panel, drawn)
    for label, count in (
        (S.HEALTH_KIND_ERRORS, 1),
        (S.HEALTH_KIND_WARNINGS, 2),
        (S.HEALTH_KIND_HINTS, 1),
    ):
        assert f"{label} ({count})" in drawn.texts
    assert drawn.rows == ["Error A", "Warning A", "Warning B", "Hint A"]
    # The filters' three icons, then each finding's.
    assert drawn.icons[3:] == [
        SEVERITY_ICON[Severity.ERROR],
        SEVERITY_ICON[Severity.WARNING],
        SEVERITY_ICON[Severity.WARNING],
        SEVERITY_ICON[Severity.INFO],
    ]


def test_a_click_on_a_kinds_text_hides_and_shows_it(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = _Drawn(monkeypatch)
    panel = _panel(_FINDINGS)
    _frame(panel, drawn)
    at = drawn.texts[f"{S.HEALTH_KIND_WARNINGS} (2)"]
    io = imgui.get_io()
    io.add_mouse_pos_event(at.x + 2, at.y + 2)
    _frame(panel, drawn)
    io.add_mouse_button_event(0, True)
    _frame(panel, drawn)
    io.add_mouse_button_event(0, False)
    _frame(panel, drawn)
    assert Severity.WARNING not in panel.shown
    _frame(panel, drawn)
    assert drawn.rows == ["Error A", "Hint A"]
    # The count stays: it tells what is hidden.
    assert f"{S.HEALTH_KIND_WARNINGS} (2)" in drawn.texts


def test_with_every_kind_hidden_the_panel_says_so(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = _Drawn(monkeypatch)
    disabled: list[str] = []
    monkeypatch.setattr(imgui, "text_disabled", disabled.append)
    panel = _panel(_FINDINGS)
    panel.shown.clear()
    _frame(panel, drawn)
    assert drawn.rows == []
    assert disabled == [S.HEALTH_NONE_SHOWN]


def test_without_findings_all_is_good(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = _Drawn(monkeypatch)
    disabled: list[str] = []
    monkeypatch.setattr(imgui, "text_disabled", disabled.append)
    _frame(_panel([]), drawn)
    assert disabled == [S.HEALTH_ALL_GOOD]
    assert f"{S.HEALTH_KIND_ERRORS} (0)" in drawn.texts
