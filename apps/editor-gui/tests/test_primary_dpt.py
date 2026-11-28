"""The type a com object is shown and linked with, from the types its product lists in order."""

from editor_gui.dpt import DPT_UNKNOWN, lookup_or_make_dpt, primary_dpt


def _types(*codes: str) -> list:
    return [lookup_or_make_dpt(code) for code in codes]


def test_a_main_type_listed_before_its_subtype_gives_the_subtype() -> None:
    # B.E.G. Gen7 follow-up time: DatapointType="DPT-7 DPST-7-6" -> minutes, with its unit.
    assert primary_dpt(_types("7", "7.6")).token == "DPST-7-6"
    # Brightness threshold: DatapointType="DPT-9 DPST-9-4" -> lux.
    assert primary_dpt(_types("9", "9.4")).token == "DPST-9-4"


def test_the_first_subtype_is_kept() -> None:
    assert primary_dpt(_types("5.1", "5.4")).token == "DPST-5-1"
    assert primary_dpt(_types("5.1", "5")).token == "DPST-5-1"


def test_a_subtype_of_another_main_type_does_not_replace_the_first() -> None:
    assert primary_dpt(_types("7", "9.4")).token == "DPT-7"


def test_a_main_type_alone_stays_and_none_is_unknown() -> None:
    assert primary_dpt(_types("7")).token == "DPT-7"
    assert primary_dpt([]) is DPT_UNKNOWN
