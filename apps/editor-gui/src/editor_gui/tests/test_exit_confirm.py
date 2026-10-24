"""File > Exit: confirmed in the browser view, where it stops the server; at once on the desktop."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from editor_gui import web
from editor_gui.main import KnxGuiApp


@pytest.fixture
def runner_params(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    params = SimpleNamespace(app_shall_exit=False)
    monkeypatch.setattr("editor_gui.main.hello_imgui.get_runner_params", lambda: params)
    return params


def test_exit_on_the_desktop_closes_at_once(
    monkeypatch: pytest.MonkeyPatch, runner_params: SimpleNamespace
) -> None:
    monkeypatch.setattr(web, "active_hub", None)
    stub = SimpleNamespace(_exit_confirm_requested=False)

    KnxGuiApp._exit(stub)  # type: ignore[arg-type]

    assert runner_params.app_shall_exit
    assert not stub._exit_confirm_requested


def test_exit_in_the_browser_view_asks_first(
    monkeypatch: pytest.MonkeyPatch, runner_params: SimpleNamespace
) -> None:
    monkeypatch.setattr(web, "active_hub", SimpleNamespace())
    stub = SimpleNamespace(_exit_confirm_requested=False)

    KnxGuiApp._exit(stub)  # type: ignore[arg-type]

    assert stub._exit_confirm_requested
    assert not runner_params.app_shall_exit
