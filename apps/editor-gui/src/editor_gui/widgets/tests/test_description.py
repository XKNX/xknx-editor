from editor_gui.widgets.description import (
    MIN_DESCRIPTION_LENGTH,
    shows_description_icon,
)


def test_none_and_empty_get_no_icon() -> None:
    assert shows_description_icon(None) is False
    assert shows_description_icon("") is False


def test_short_description_gets_no_icon() -> None:
    assert shows_description_icon("x" * MIN_DESCRIPTION_LENGTH) is False
    assert shows_description_icon("Küche EG") is False


def test_long_description_gets_an_icon() -> None:
    assert shows_description_icon("x" * (MIN_DESCRIPTION_LENGTH + 1)) is True
    assert shows_description_icon("Taster links, Dimmen langer Tastendruck") is True


def test_multi_line_description_gets_an_icon() -> None:
    assert shows_description_icon("EG\nKüche") is True
    assert shows_description_icon("Küche EG\n") is False


def test_whitespace_only_gets_no_icon() -> None:
    assert shows_description_icon("   " * 10) is False
