"""Dynamic-text placeholder substitution: `{{Name Format=Alpha|Decimal Offset=N}}` modifiers.

The modifier grammar mirrors the one applications use to label repeated channels, buttons and
communication objects (e.g. ``Row {{ChannelIdx Format=Alpha Offset=1}}`` renders ``Row A`` on
channel 0, ``Row B`` on channel 1, ...). Reference values below come from invoking the real
template renderer at runtime.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from xknxeditor.prod.parser_v2._name import apply_text_args, fill_name


def test_plain_name_substitutes_the_argument_value() -> None:
    assert apply_text_args("Channel {{ChannelIdx}}", {"ChannelIdx": "3"}) == "Channel 3"


def test_offset_shifts_an_integer_argument_value() -> None:
    assert (
        apply_text_args("Row {{ChannelIdx Offset=10}}", {"ChannelIdx": "3"}) == "Row 13"
    )


def test_format_decimal_emits_the_shifted_integer() -> None:
    assert (
        apply_text_args("Row {{ChannelIdx Format=Decimal}}", {"ChannelIdx": "3"})
        == "Row 3"
    )


def test_format_alpha_emits_a_letter_starting_at_a() -> None:
    assert (
        apply_text_args("Row {{ChannelIdx Format=Alpha}}", {"ChannelIdx": "3"})
        == "Row D"
    )


def test_format_alpha_with_offset_shifts_the_letter() -> None:
    assert (
        apply_text_args("Row {{ChannelIdx Format=Alpha Offset=1}}", {"ChannelIdx": "3"})
        == "Row E"
    )


def test_format_alpha_falls_back_to_the_raw_value_when_not_an_integer() -> None:
    assert (
        apply_text_args("Numeric {{Name Format=Alpha}}", {"Name": "Light"})
        == "Numeric Light"
    )


def test_multiple_placeholders_in_one_text() -> None:
    assert (
        apply_text_args(
            "Pair {{Name}} #{{ChannelIdx}}", {"Name": "Light", "ChannelIdx": "3"}
        )
        == "Pair Light #3"
    )


def test_an_empty_argument_shows_the_fallback() -> None:
    assert apply_text_args("Room {{Name:unnamed}}", {"Name": ""}) == "Room unnamed"
    assert apply_text_args("Room {{Name:unnamed}}", {"Name": "Hall"}) == "Room Hall"


def test_unknown_argument_name_is_left_for_fill_name() -> None:
    # fill_name's final pass strips any surviving {{...}} placeholder.
    assert (
        apply_text_args("Hello {{Unknown}}", {"ChannelIdx": "3"}) == "Hello {{Unknown}}"
    )
    assert fill_name(apply_text_args("Hello {{Unknown}}", {}), "") == "Hello"


def test_internal_name_placeholder_is_not_consumed() -> None:
    # ``{{0}}`` and ``{{0:fallback}}`` are the name-value form resolved by fill_name later.
    assert (
        apply_text_args("Header {{0:no name}}", {"ChannelIdx": "3"})
        == "Header {{0:no name}}"
    )
    assert fill_name("Header {{0:no name}}", "") == "Header no name"
    assert fill_name("Header {{0:no name}}", "Living") == "Header Living"


def test_a_text_is_filled_with_the_arguments_it_is_given_each_time() -> None:
    # What a text gave is given again only for the same arguments.
    text = "Channel {{Number}} ({{Name:none}})"
    assert apply_text_args(text, {"Number": "1", "Name": "A"}) == "Channel 1 (A)"
    assert apply_text_args(text, {"Number": "2", "Name": "A"}) == "Channel 2 (A)"
    assert apply_text_args(text, {"Number": "1", "Name": ""}) == "Channel 1 (none)"
    assert apply_text_args(text, {"Number": "1"}) == "Channel 1 ({{Name:none}})"
    assert apply_text_args(text, {"Number": "1", "Name": "A"}) == "Channel 1 (A)"


class _Counted(Mapping[str, str]):
    """Arguments that count how often each is looked up."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.lookups: dict[str, int] = {}

    def __getitem__(self, key: str) -> str:
        self.lookups[key] = self.lookups.get(key, 0) + 1
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


def test_each_argument_is_looked_up_once_per_text() -> None:
    # A module scope's arguments work a value out on each lookup.
    text = "Button {{Key}} of {{Row:none}}"
    for _ in range(2):
        arguments = _Counted({"Key": "4"})
        assert apply_text_args(text, arguments) == "Button 4 of {{Row:none}}"
        assert arguments.lookups == {"Key": 1, "Row": 1}
