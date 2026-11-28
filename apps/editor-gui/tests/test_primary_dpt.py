"""The type a com object is shown and linked with, from the types its product lists in order."""

from editor_gui.dpt import DPT_UNKNOWN, lookup_or_make_dpt, primary_dpt


def _types(*codes: str) -> list:
    return [lookup_or_make_dpt(code) for code in codes]


def test_a_main_type_listed_before_its_subtype_gives_the_subtype() -> None:
    # B.E.G. Gen7 follow-up time: DatapointType="DPT-7 DPST-7-6" -> minutes, with its unit.
    assert primary_dpt(_types("7", "7.6")).token == "DPST-7-6"
    # Brightness threshold: DatapointType="DPT-9 DPST-9-4" -> lux.
    assert primary_dpt(_types("9", "9.4")).token == "DPST-9-4"


def test_the_subtype_with_the_lowest_number_wins_whatever_the_order() -> None:
    assert primary_dpt(_types("5.1", "5.4")).token == "DPST-5-1"
    assert primary_dpt(_types("5.4", "5.1")).token == "DPST-5-1"
    assert primary_dpt(_types("1.2", "1.1")).token == "DPST-1-1"
    assert primary_dpt(_types("5.1", "5")).token == "DPST-5-1"


def test_of_equal_subtype_numbers_the_first_listed_wins() -> None:
    assert primary_dpt(_types("9.4", "7.4")).token == "DPST-9-4"
    assert primary_dpt(_types("7.4", "9.4")).token == "DPST-7-4"


def test_any_subtype_comes_before_a_main_type() -> None:
    assert primary_dpt(_types("7", "9.4")).token == "DPST-9-4"


def test_without_a_subtype_the_lowest_main_type_wins() -> None:
    assert primary_dpt(_types("9", "7")).token == "DPT-7"
    assert primary_dpt(_types("7")).token == "DPT-7"
    assert primary_dpt([]) is DPT_UNKNOWN
