"""The command line: ``--help`` lists every argument the editor takes, and ends."""

from __future__ import annotations

import sys
import textwrap

from editor_gui.web import DEFAULT_HOST, DEFAULT_PORT

# Where the descriptions start and how wide the text may be (a terminal of 80 columns).
_COLUMN = 30
_WIDTH = 80

_GENERAL = (
    ("-h, --help", "Show this help and end."),
    (
        "--new-instance",
        "Start another editor without offering to switch to one that runs already.",
    ),
    ("--profile", "Print where the time went when the editor ends (for developers)."),
)
_WEB = (
    (
        "--web[=[host:]port]",
        "Serve the editor to a browser instead of showing a window (default "
        f"{DEFAULT_HOST}:{DEFAULT_PORT}; a port given must be free).",
    ),
    (
        "--web-password-file=FILE",
        "Sign-in password from the first line of FILE (or XKNX_WEB_PASSWORD).",
    ),
    ("--web-trust-proxy", "A proxy in front signs users in; no password."),
    (
        "--web-proxy-ip=IP[,IP...]",
        "Answer only connections from these addresses (the proxy in front).",
    ),
    (
        "--web-files=DIR",
        "Folder for uploaded, saved and exported files (default: files in the app's data "
        "folder).",
    ),
    (
        "--web-idle=MINUTES",
        "Control passes to another browser after this much inactivity (default 10).",
    ),
    ("--web-cert=FILE --web-key=FILE", "Serve HTTPS with this certificate and key."),
    ("--web-no-browser", "Do not open the browser at start (an autostart, a service)."),
    ("--web-no-tray", "No tray icon."),
)


def wants_help(argv: list[str]) -> bool:
    return any(arg in ("-h", "--help") for arg in argv[1:])


def _options(entries: tuple[tuple[str, str], ...]) -> list[str]:
    lines: list[str] = []
    for option, meaning in entries:
        wrapped = textwrap.wrap(meaning, _WIDTH - _COLUMN)
        first = f"  {option}"
        if len(first) < _COLUMN:
            lines.append(first.ljust(_COLUMN) + wrapped[0])
            wrapped = wrapped[1:]
        else:
            lines.append(first)
        lines.extend(" " * _COLUMN + line for line in wrapped)
    return lines


def help_text() -> str:
    intro = textwrap.wrap(
        "Edits KNX projects. Opens the project file given (.xknx, or a .knxproj to import), "
        "otherwise starts on the Welcome screen.",
        _WIDTH,
    )
    return "\n".join(
        [
            "Usage: XKNX-Editor [options] [project file]",
            "",
            *intro,
            "",
            "Options:",
            *_options(_GENERAL),
            "",
            "Browser UI:",
            *_options(_WEB),
        ]
    )


def show_help() -> None:
    """Write the help to the terminal the editor was started from; without one (the Windows app
    has no console of its own when started from the desktop) in a message box."""
    text = help_text()
    if _write_terminal(text):
        return
    from imgui_bundle import portable_file_dialogs as pfd

    pfd.message("XKNX Editor", text, pfd.choice.ok, pfd.icon.info).result()


def _write_terminal(text: str) -> bool:
    if sys.stdout is not None:
        print(text, flush=True)
        return True
    if sys.platform != "win32":
        return False
    # The packaged Windows app is a window program: it writes into the console it was started
    # from once it is attached to it.
    import ctypes

    attach_parent_process = -1
    if not ctypes.windll.kernel32.AttachConsole(attach_parent_process):
        return False
    try:
        with open("CONOUT$", "w", encoding="utf-8") as console:
            console.write("\n" + text + "\n")
    except OSError:
        return False
    return True
