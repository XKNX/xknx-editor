"""Colour controls work on the e64 Pro backup representation without lossy display conversion."""

import pytest
from editor_gui.plugins.project.ui.e64pro_colour import Colour


@pytest.mark.parametrize(
    ("mode", "raw"),
    [
        ("2", "2800"),
        ("1", "0.3300;0.3300;1.0000"),
        ("4", "255;127;155;0"),
        ("8", "255;120;57;121"),
        ("16", "0.7843;0.3686;0.7490"),
        ("32", "0.1194;0.8400;0.7600;255"),
    ],
)
def test_confirmed_colour_encodings(mode: str, raw: str) -> None:
    colour = Colour.parse(mode, raw)
    assert colour is not None
    assert colour.raw == raw
    assert all(0 <= channel <= 1 for channel in colour.rgb)


def test_xy_and_hsvw_edits_retain_unselected_channels() -> None:
    xy = Colour.parse("1", "0.3300;0.3300;1.0000")
    hsvw = Colour.parse("32", "0.1194;0.8400;0.7600;255")
    assert xy is not None and hsvw is not None
    assert xy.with_component(0, "0.4000").raw == "0.4000;0.3300;1.0000"
    assert hsvw.with_component(3, "57").raw == "0.1194;0.8400;0.7600;57"


def test_rgb_preview_and_edit_preserve_dca_encoding() -> None:
    rgb = Colour.parse("4", "255;127;155;0")
    assert rgb is not None
    assert rgb.rgb == (1.0, 127 / 255, 155 / 255)
    assert rgb.summary == "R 255  G 127  B 155"
    assert rgb.with_component(1, "64").raw == "255;64;155;0"
    rgbw = Colour.parse("8", "255;120;57;121")
    assert rgbw is not None
    assert rgbw.rgb == (1.0, 120 / 255, 57 / 255)
    assert rgbw.summary == "R 255  G 120  B 57  W 121"
    assert rgbw.with_component(3, "66").raw == "255;120;57;66"


@pytest.mark.parametrize(
    ("mode", "raw"),
    [
        ("4", "1;0;0"),
        ("4", "256;127;155;0"),
        ("4", "1.0;0;0;0"),
        ("8", "255;120;57;256"),
        ("32", "0;0;0;256"),
        ("2", "68000"),
        ("16", "1.21;0;0"),
    ],
)
def test_unverified_and_unknown_encodings_are_not_normalised(
    mode: str, raw: str
) -> None:
    assert Colour.parse(mode, raw) is None
