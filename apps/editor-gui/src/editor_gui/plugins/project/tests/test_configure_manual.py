"""Manual lookup shares catalog identity checks and leaves search available as a fallback."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from editor_gui.plugins.project.ui import configure


@pytest.mark.parametrize("document", [None, "https://www.mdt.de/manual.pdf"])
def test_manual_button_passes_identity_and_opens_result(
    monkeypatch: pytest.MonkeyPatch, document: str | None
) -> None:
    panel = cast(Any, SimpleNamespace(_manual_busy=False))
    panel._open_manual = lambda *args: configure.ConfigurePanel._open_manual(
        panel, *args
    )
    info = cast(
        Any,
        SimpleNamespace(
            manufacturer_name="MDT technologies",
            order_number="JAL-0810M.02",
            product_name="Shutter Actuator",
            product_ref_id="M-0083_H-128-64_P-JAL.2D0810M.2E02",
        ),
    )
    resolve = Mock(return_value=document)
    opened = Mock()
    monkeypatch.setattr(configure.doc_links, "resolve_manual_url", resolve)
    monkeypatch.setattr(configure, "open_url", opened)
    monkeypatch.setattr(configure.imgui, "button", lambda _: True)
    monkeypatch.setattr(configure.imgui, "is_item_hovered", lambda: False)
    monkeypatch.setattr(
        configure.threading,
        "Thread",
        lambda *, target, args, daemon: SimpleNamespace(start=lambda: target(*args)),
    )
    assert configure.ConfigurePanel._render_manual_button(panel, info)
    resolve.assert_called_once_with(
        "MDT technologies", "JAL-0810M.02", "Shutter Actuator", manufacturer_id="M-0083"
    )
    opened.assert_called_once_with(
        document or "https://www.knx.org/de/gerate?title=JAL-0810M.02"
    )
    assert not panel._manual_busy


def test_failed_lookup_clears_busy_state(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = cast(Any, SimpleNamespace(_manual_busy=True))
    monkeypatch.setattr(
        configure.doc_links,
        "resolve_manual_url",
        Mock(side_effect=RuntimeError("failed")),
    )
    opened = Mock()
    monkeypatch.setattr(configure, "open_url", opened)
    configure.ConfigurePanel._open_manual(
        panel, "MDT", "JAL-0810M.02", "Shutter Actuator"
    )
    assert not panel._manual_busy
    opened.assert_not_called()
