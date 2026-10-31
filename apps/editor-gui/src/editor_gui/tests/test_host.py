"""Running inside another program: Home Assistant, and an MCP server its launcher sets up."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui import host, main
from editor_gui.host import HostOptions, host_options
from editor_gui.main import KnxGuiApp
from editor_gui.strings import S


@pytest.fixture(autouse=True)
def standalone() -> Iterator[None]:
    """Every test starts from a standalone editor and leaves one behind."""
    host.set_current(HostOptions())
    yield
    host.set_current(HostOptions())


def test_options_from_the_command_line() -> None:
    options = host_options(
        [
            "editor",
            "--web=127.0.0.1:9000",
            "--home-assistant",
            "--mcp=127.0.0.1:41000",
            "--mcp-url=https://ha.example/api/xknx_editor/mcp",
            "project.xknx",
        ]
    )
    assert options == HostOptions(
        True, "127.0.0.1", 41000, "https://ha.example/api/xknx_editor/mcp"
    )
    assert options.mcp_managed


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # Standalone: nothing set.
        (["editor"], HostOptions()),
        # A port alone listens on this machine only.
        (["editor", "--mcp=41000"], HostOptions(mcp_host="127.0.0.1", mcp_port=41000)),
        (["editor", "--home-assistant"], HostOptions(home_assistant=True)),
        # Unknown values are ignored: the flag takes none.
        (["editor", "--home-assistant=app", "--mcp=host:port"], HostOptions()),
    ],
)
def test_defaults_and_unknown_values(argv: list[str], expected: HostOptions) -> None:
    assert host_options(argv) == expected
    assert expected.mcp_managed == (expected.mcp_port is not None)


def test_home_assistant() -> None:
    assert host.home_assistant() is False
    host.set_current(HostOptions(home_assistant=True))
    assert host.home_assistant() is True


def _app(**fields: Any) -> SimpleNamespace:
    stub = SimpleNamespace(
        _mcp_host="127.0.0.1",
        _mcp_port="8765",
        _mcp_token="own-token",
        _mcp_url=lambda: KnxGuiApp._mcp_url(stub),  # type: ignore[arg-type]
    )
    stub.__dict__.update(fields)
    return stub


def test_mcp_url_and_token_standalone() -> None:
    stub = _app()
    assert KnxGuiApp._mcp_url(stub) == "http://127.0.0.1:8765/mcp"  # type: ignore[arg-type]
    assert KnxGuiApp._ki_command_token(stub) == "own-token"  # type: ignore[arg-type]
    assert KnxGuiApp._mcp_managed_text() == S.MCP_MANAGED
    host.set_current(HostOptions(True, "127.0.0.1", 41000))
    assert KnxGuiApp._mcp_managed_text() == S.MCP_HOME_ASSISTANT


def test_mcp_url_and_token_in_home_assistant() -> None:
    host.set_current(
        HostOptions(True, "127.0.0.1", 41000, "https://ha.example/api/xknx_editor/mcp")
    )
    stub = _app()
    # Clients reach the server through Home Assistant, signed in with their own token.
    assert KnxGuiApp._mcp_url(stub) == "https://ha.example/api/xknx_editor/mcp"  # type: ignore[arg-type]
    assert KnxGuiApp._ki_command_token(stub) == host.HOME_ASSISTANT_TOKEN_PLACEHOLDER  # type: ignore[arg-type]


def test_mcp_token_of_another_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    host.set_current(HostOptions(mcp_host="127.0.0.1", mcp_port=41000))
    monkeypatch.setenv("XKNX_MCP_AUTH_TOKEN", "launcher-token")
    assert KnxGuiApp._ki_command_token(_app()) == "launcher-token"  # type: ignore[arg-type]


def test_managed_mcp_settings_are_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "editor_gui.main.save_settings", lambda name, value: saved.append((name, value))
    )
    stub = _app()
    KnxGuiApp._save_mcp_settings(stub, running=True)  # type: ignore[arg-type]
    assert saved == [
        (
            "mcp",
            {
                "host": "127.0.0.1",
                "port": "8765",
                "token": "own-token",
                "running": True,
            },
        )
    ]
    host.set_current(HostOptions(mcp_host="127.0.0.1", mcp_port=41000))
    KnxGuiApp._save_mcp_settings(stub, running=False)  # type: ignore[arg-type]
    assert len(saved) == 1


@pytest.mark.parametrize(
    ("managed", "stored", "starts"),
    [(True, False, True), (False, False, False), (False, True, True)],
)
def test_session_starts_the_managed_mcp_server(
    monkeypatch: pytest.MonkeyPatch, managed: bool, stored: bool, starts: bool
) -> None:
    if managed:
        host.set_current(HostOptions(mcp_host="127.0.0.1", mcp_port=41000))
    monkeypatch.setattr(
        "editor_gui.main.load_settings",
        lambda name: {"running": stored} if name == "mcp" else {},
    )
    started: list[bool] = []
    stub = SimpleNamespace(
        _connection_plugin=SimpleNamespace(autostart=lambda: None),
        _start_mcp=lambda *, save: started.append(save),
        startup_file=None,
    )
    KnxGuiApp._start_session(stub)  # type: ignore[arg-type]
    # Started without storing anything.
    assert started == ([False] if starts else [])


_NOTES = "## New\n\n- **Things**"


def test_update_found_inside_home_assistant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The new version and its notes, and where to update; no install or download."""
    host.set_current(HostOptions(home_assistant=True))
    shown: list[str] = []
    buttons: list[tuple[str, ...]] = []
    skipped: list[str] = []

    def dialog_buttons(*labels: str, **_: Any) -> int:
        buttons.append(labels)
        return 0

    monkeypatch.setattr(main, "begin_dialog", lambda *a, **k: True)
    monkeypatch.setattr(main, "end_dialog", lambda: None)
    monkeypatch.setattr(main, "dialog_buttons", dialog_buttons)
    monkeypatch.setattr(main, "px_vec2", lambda *a: None)
    for name in (
        "open_popup",
        "close_current_popup",
        "spacing",
        "separator",
        "end_child",
    ):
        monkeypatch.setattr(main.imgui, name, lambda *a, **k: None)
    monkeypatch.setattr(main.imgui, "text_disabled", lambda *a, **k: None)
    monkeypatch.setattr(main.imgui, "begin_child", lambda *a, **k: True)
    monkeypatch.setattr(main.imgui, "text_wrapped", shown.append)
    notes: list[str] = []
    monkeypatch.setattr(main, "render_markdown", notes.append)
    stub = SimpleNamespace(
        _update_prompt_requested=False,
        _update_info=SimpleNamespace(version="9.9.0", notes=_NOTES, url="", assets=[]),
        _skip_update_version=skipped.append,
    )

    KnxGuiApp._render_update_modal(stub)  # type: ignore[arg-type]

    assert S.UPDATE_HOME_ASSISTANT_INTEGRATION in shown
    # The release notes are Markdown, rendered as such.
    assert notes == [_NOTES]
    # Only informs: one button; closed, the check at start does not show it again.
    assert buttons == [(S.BTN_CLOSE,)]
    assert skipped == ["9.9.0"]
