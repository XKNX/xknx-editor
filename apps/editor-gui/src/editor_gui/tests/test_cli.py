"""``--help`` lists every argument of the editor and ends without starting anything."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from editor_gui import cli, main
from editor_gui.web import web_options

# Every argument the editor reads (main.py, editor_gui.web.web_options).
_ARGUMENTS = (
    "--help",
    "--new-instance",
    "--profile",
    "--web[=",
    "--web-password-file=",
    "--web-trust-proxy",
    "--web-proxy-ip=",
    "--web-files=",
    "--web-idle=",
    "--web-cert=",
    "--web-key=",
    "--web-no-browser",
    "--web-no-tray",
)


def test_help_names_every_argument() -> None:
    text = cli.help_text()
    for argument in _ARGUMENTS:
        assert argument in text
    assert all(len(line) <= 80 for line in text.splitlines())


def test_the_web_arguments_in_the_help_are_understood() -> None:
    options = web_options(
        [
            "editor",
            "--web=0.0.0.0:9000",
            "--web-password-file=pw.txt",
            "--web-trust-proxy",
            "--web-proxy-ip=172.30.32.2",
            "--web-files=files",
            "--web-idle=5",
            "--web-cert=c.pem",
            "--web-key=k.pem",
            "--web-no-browser",
            "--web-no-tray",
        ]
    )
    assert options is not None
    assert (options.host, options.port, options.idle_minutes) == ("0.0.0.0", 9000, 5.0)
    assert options.proxy_ips == ("172.30.32.2",)
    assert not options.open_browser and not options.tray


@pytest.mark.parametrize("argument", ["-h", "--help"])
def test_help_ends_without_starting(
    argument: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["XKNX-Editor", "--web", argument])

    def start() -> None:
        raise AssertionError("the editor started")

    monkeypatch.setattr(main, "_main", start)
    main.main()
    assert capsys.readouterr().out.startswith("Usage: XKNX-Editor")


def test_without_a_terminal_the_help_shows_in_a_message_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(cli, "_write_terminal", lambda text: False)
    monkeypatch.setattr(
        "imgui_bundle.portable_file_dialogs.message",
        lambda title, text, *_: (
            shown.append(text) or SimpleNamespace(result=lambda: None)
        ),
    )
    cli.show_help()
    assert shown == [cli.help_text()]


def test_no_terminal_elsewhere_than_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "platform", "linux")
    assert not cli._write_terminal("text")
