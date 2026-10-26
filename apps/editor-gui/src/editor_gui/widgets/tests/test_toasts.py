"""Toasts: a confirmation for a moment, a refusal in an orange box for longer, and a click on one
closes it at once; refusals reach the app through ``PluginAPI.tell_refused``."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.base.api import PluginAPI
from editor_gui.widgets import toasts as toasts_module
from editor_gui.widgets.toasts import Toasts


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _frame(toasts: Toasts) -> None:
    imgui.new_frame()
    toasts.render()
    imgui.end_frame()


@pytest.mark.usefixtures("context")
def test_toasts_expire_and_a_click_closes_one(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    toasts = Toasts(clock=lambda: now[0])
    toasts.add("Copied", "success", 2.0)
    toasts.add("The limit to add was reached.", "warning", 8.0)
    _frame(toasts)
    assert [t.text for t in toasts.items] == [
        "Copied",
        "The limit to add was reached.",
    ]
    # The confirmation is gone after its two seconds, the refusal still shows.
    now[0] = 103.0
    _frame(toasts)
    assert [t.level for t in toasts.items] == ["warning"]
    # A click on it closes it before its time.
    monkeypatch.setattr(imgui, "is_item_clicked", lambda *_a: True)
    _frame(toasts)
    assert toasts.items == []


def test_refusals_and_failures_stand_out() -> None:
    assert toasts_module._boxed("success") is None  # pyright: ignore[reportPrivateUsage]
    warning = toasts_module._boxed("warning")  # pyright: ignore[reportPrivateUsage]
    error = toasts_module._boxed("error")  # pyright: ignore[reportPrivateUsage]
    assert warning is not None and error is not None and warning != error


def _api(**kwargs: Any) -> PluginAPI:
    none = cast(Any, None)
    return PluginAPI(1, none, none, none, none, **kwargs)


def test_tell_refused_uses_warn_else_notify() -> None:
    warned: list[str] = []
    notified: list[str] = []
    _api(notify=notified.append, warn=warned.append).tell_refused("No room")
    assert warned == ["No room"] and notified == []
    _api(notify=notified.append).tell_refused("No room")
    assert notified == ["No room"]
    # Nothing refused: nothing told.
    _api(warn=warned.append).tell_refused(None)
    _api(warn=warned.append).tell_refused("")
    assert warned == ["No room"]
