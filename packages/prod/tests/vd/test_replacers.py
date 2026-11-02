"""Class ids of plugins and the programs whose plugins stay."""

from __future__ import annotations

import pytest

from xknxeditor.prod.vd.replacers import byte_swapper, guid, plugin_must_remain

_ID = "15053D1A12DE462AAEE9E967A27EFF5B"


@pytest.mark.parametrize(
    "text",
    [
        "{15053D1A-12DE-462A-AEE9-E967A27EFF5B}",
        "{15053d1a-12de-462a-aee9-e967a27eff5b}",
        "(15053D1A-12DE-462A-AEE9-E967A27EFF5B)",
        "15053D1A-12DE-462A-AEE9-E967A27EFF5B",
        " 15053D1A12DE462AAEE9E967A27EFF5B ",
    ],
)
def test_class_id_formats(text: str) -> None:
    assert guid(text) == _ID
    assert byte_swapper().replaces(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "x",
        "{15053D1A12-DE-462A-AEE9-E967A27EFF5B}",
        "{15053D1A-12DE-462A-AEE9-E967A27EFF5B",
    ],
)
def test_invalid_class_ids(text: str) -> None:
    with pytest.raises(ValueError, match="Guid"):
        guid(text)


@pytest.mark.parametrize(
    ("application", "original", "remains"),
    [
        ("M-0002_A-A058-11-0000", None, True),
        ("M-0002_A-A0A2-11-0000", None, True),
        ("M-0002_A-A057-11-0000", None, False),
        ("M-0007_A-A058-11-0000", None, False),
        ("M-0007_A-A058-11-0000-O0002", "M-0002", True),
    ],
)
def test_plugins_that_stay(
    application: str, original: str | None, remains: bool
) -> None:
    assert plugin_must_remain(application, application[:6], original) == remains


def test_replacer_bookkeeping() -> None:
    replacer = byte_swapper()
    replacer.add_application("M-0002_A-0001-01-0000")
    replacer.add_application("M-0002_A-0001-01-0000")
    assert replacer.applications == ["M-0002_A-0001-01-0000"]
    replacer.add_ref("M-0002_A-0001-01-0000", "M-0002_BG-AbbUsU-x.2Edll")
    assert replacer.is_baggage("M-0002_BG-AbbUsU-x.2Edll")
    assert not replacer.is_baggage("M-0002_BG-docs-x.2Edll")
    replacer.remove_ref("M-0002_A-0001-01-0000", "M-0002_BG-AbbUsU-x.2Edll")
    assert replacer.uses == {}
    assert replacer.removed == {"M-0002_A-0001-01-0000": ["M-0002_BG-AbbUsU-x.2Edll"]}
    # a program without uses
    replacer.remove_ref("M-0002_A-0002-01-0000", "M-0002_BG-AbbUsU-y.2Edll")
    assert replacer.remove_unused_baggages([]) == []
