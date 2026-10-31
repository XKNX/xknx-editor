"""An application without a dynamic section (an interface without parameters) still has its
code segments: a device with it is read like any other, with no parameters."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.recover.read_config import (
    _segment_layout,  # pyright: ignore[reportPrivateUsage]
)

_FIXTURE = (
    Path(__file__).parents[2]
    / "prod"
    / "tests"
    / "fixtures"
    / "download_invisible_application.xml"
)
_SEGMENT = "M-00FA_A-0002-01-0000_AS-0000"


def test_the_segment_layout_without_a_dynamic_section() -> None:
    xml = _FIXTURE.read_text(encoding="utf-8")
    # The application's own dynamic section is the last one (a module definition has one too).
    start, end = xml.rindex("<Dynamic>"), xml.rindex("</Dynamic>") + len("</Dynamic>")
    with_ui = parse_application_xml(xml.encode(), "M-00FA")[0]
    without = parse_application_xml((xml[:start] + xml[end:]).encode(), "M-00FA")[0]
    assert with_ui.dynamic_ui() is not None
    assert without.dynamic_ui() is None
    assert (
        _segment_layout(without)
        == _segment_layout(with_ui)
        == ({_SEGMENT: 0}, {_SEGMENT: 8})
    )
