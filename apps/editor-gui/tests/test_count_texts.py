"""A text with a count takes the form of its language for that count.

English has a singular and a plural; Czech three forms (1, 2-4, 5 and more), Slovenian four
(by the last two digits), Japanese one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from editor_gui import strings
from editor_gui.plugins.health.strings import S as health_strings
from editor_gui.plugins.project.strings import S as project_strings
from editor_gui.plugins.recover.strings import S as recover_strings


@pytest.fixture(autouse=True)
def _english_afterwards() -> Iterator[None]:
    yield
    strings.set_locale("en")


def _group_addresses(count: int) -> str:
    return recover_strings.GROUP_ADDRESSES(count) % {"count": count}


def test_english_singular_and_plural() -> None:
    strings.set_locale("en")
    assert project_strings.TOOLS_COPY_DONE(1).format(count=1) == "Created 1 copy"
    assert project_strings.TOOLS_COPY_DONE(2).format(count=2) == "Created 2 copies"
    assert project_strings.TOOLS_COPY_DONE(0).format(count=0) == "Created 0 copies"
    assert health_strings.HEALTH_ERRORS(1).format(count=1) == "1 error"
    assert health_strings.HEALTH_WARNINGS(3).format(count=3) == "3 warnings"


def test_sentence_from_parts() -> None:
    strings.set_locale("en")
    text = project_strings.PREFLIGHT_WOULD_CHANGE.format(
        bytes=project_strings.PREFLIGHT_BYTES_DIFFER(1).format(count=1),
        locations=project_strings.PREFLIGHT_LOCATIONS(2).format(count=2),
    )
    assert text == "Mismatch: 1 byte differs from the device in 2 locations"


@pytest.mark.parametrize(
    ("count", "text"),
    [
        (1, "1 skupinová adresa"),
        (3, "3 skupinové adresy"),
        (5, "5 skupinových adres"),
        (22, "22 skupinových adres"),
    ],
)
def test_czech_three_forms(count: int, text: str) -> None:
    strings.set_locale("cs")
    assert _group_addresses(count) == text


@pytest.mark.parametrize(
    ("count", "text"),
    [
        (1, "1 skupinski naslov"),
        (102, "102 skupinska naslova"),
        (4, "4 skupinski naslovi"),
        (5, "5 skupinskih naslovov"),
    ],
)
def test_slovenian_four_forms(count: int, text: str) -> None:
    strings.set_locale("sl")
    assert _group_addresses(count) == text


def test_one_form_for_every_count() -> None:
    strings.set_locale("ja")
    assert _group_addresses(1) == "グループ・アドレス1件"
    assert _group_addresses(7) == "グループ・アドレス7件"
