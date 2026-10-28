"""File > Exit (on the desktop only; the browser UI is stopped from the tray icon or by the
program that started it): the window closes at once."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from editor_gui.main import KnxGuiApp


@pytest.fixture
def runner_params(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    params = SimpleNamespace(app_shall_exit=False)
    monkeypatch.setattr("editor_gui.main.hello_imgui.get_runner_params", lambda: params)
    return params


def test_exit_closes_at_once(runner_params: SimpleNamespace) -> None:
    KnxGuiApp._exit(SimpleNamespace())  # type: ignore[arg-type]

    assert runner_params.app_shall_exit
