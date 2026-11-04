"""Opening a project: once its devices are loaded, the progress dialog says that the views are
prepared, which the worker then does."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.main import KnxGuiApp
from editor_gui.strings import S


def test_preparing_the_views_is_shown_first() -> None:
    calls: list[object] = []
    app = SimpleNamespace(
        set_progress=lambda fraction, stage="": calls.append((fraction, stage)),
        _project_service=SimpleNamespace(
            prepare_views=lambda: calls.append("prepared")
        ),
    )
    KnxGuiApp._prepare_views(cast(Any, app))
    assert calls == [(None, S.PROGRESS_PREPARING_VIEWS), "prepared"]
