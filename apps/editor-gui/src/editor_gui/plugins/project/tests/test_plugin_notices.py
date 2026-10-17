"""The notes on an unavailable manufacturer plugin: one sentence for one point, one note listing the
points when there are several."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import configure
from editor_gui.plugins.project.ui.configure import ConfigurePanel


def _notices(monkeypatch: pytest.MonkeyPatch, **plugins: Any) -> list[str]:
    shown: list[str] = []
    flags = {
        "download_required": False,
        "user_interface": False,
        "download": False,
        "data_handler": False,
        "external_software": False,
        "configuration_app": None,
    } | plugins
    monkeypatch.setattr(
        configure, "device_plugins", lambda _d: SimpleNamespace(**flags)
    )
    monkeypatch.setattr(configure.imgui, "text_wrapped", shown.append)
    monkeypatch.setattr(configure.imgui, "bullet", lambda: shown.append("•"))
    monkeypatch.setattr(configure.imgui, "same_line", lambda *_a: None)
    monkeypatch.setattr(configure.imgui, "push_style_color", lambda *_a: None)
    monkeypatch.setattr(configure.imgui, "pop_style_color", lambda *_a: None)
    panel = cast(Any, ConfigurePanel.__new__(ConfigurePanel))
    panel._render_plugin_notices(SimpleNamespace(app=None))
    return shown


def test_one_point_is_one_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _notices(monkeypatch, user_interface=True) == [S.PLUGIN_USER_INTERFACE]


def test_several_points_are_one_note(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = _notices(
        monkeypatch,
        user_interface=True,
        download=True,
        data_handler=True,
        external_software=True,
    )
    assert shown == [
        S.PLUGIN_NOTICES_HEADING,
        "•",
        S.PLUGIN_ITEM_USER_INTERFACE,
        "•",
        S.PLUGIN_ITEM_DOWNLOAD,
        "•",
        S.PLUGIN_ITEM_DATA_HANDLER,
        S.PLUGIN_EXTERNAL_SOFTWARE,
    ]


def test_without_plugins_nothing_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _notices(monkeypatch) == []
