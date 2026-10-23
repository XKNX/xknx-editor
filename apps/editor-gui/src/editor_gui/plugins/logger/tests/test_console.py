"""Log lines reach a console in a legacy code page without stopping the app."""

from __future__ import annotations

import io

from editor_gui.plugins.logger.service import _TeeStream


def test_text_the_console_cannot_show_is_replaced_there_and_kept_in_the_file() -> None:
    line = "task: 言語を切り替えています… ok\n"
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", newline="")
    log_file = io.StringIO()
    _TeeStream(console, log_file).write(line)
    console.flush()
    assert raw.getvalue() == line.encode("cp1252", "replace")
    assert log_file.getvalue() == line
