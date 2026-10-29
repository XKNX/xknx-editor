"""Project-log comments: encrypted with an installed key, decrypted again, and recognised as
encrypted also without the key."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from xknxeditor.proj import (
    decrypt_comment,
    encrypt_comment,
    looks_encrypted,
    reset_trace_key,
    set_trace_key,
)

# A test key: the real one is read from the user's own installation, never shipped.
_KEY, _IV, _MARKER = os.urandom(32), os.urandom(16), "#t (x): "


@pytest.fixture(autouse=True)
def no_key_afterwards() -> Iterator[None]:
    yield
    reset_trace_key()


def _encrypted(text: str) -> str:
    set_trace_key(_KEY, _IV, _MARKER)
    comment = encrypt_comment(text)
    reset_trace_key()
    return comment


def test_round_trip_with_the_key() -> None:
    comment = _encrypted("Download 1.1.5")
    assert decrypt_comment(comment) is None  # no key
    set_trace_key(_KEY, _IV, _MARKER)
    assert decrypt_comment(comment) == "Download 1.1.5"


@pytest.mark.parametrize("text", ["", "x", "A longer comment, over several AES blocks"])
def test_recognised_as_encrypted_without_the_key(text: str) -> None:
    assert looks_encrypted(_encrypted(text))


@pytest.mark.parametrize(
    "comment",
    [
        "",
        "Programmed 1.1.5",
        "Imported: ABCDEFGHIJKLMNOPQRSTUVWX",  # base64 of 18 bytes: no whole AES blocks
        "QUJDREVGR0hJSktMTU5PUA==",  # base64 of 16 bytes, but no marker before it
    ],
)
def test_plain_comments_are_not_taken_for_encrypted(comment: str) -> None:
    assert not looks_encrypted(comment)


def test_with_a_key_its_marker_decides() -> None:
    comment = _encrypted("Download")
    set_trace_key(_KEY, _IV, "other: ")
    assert not looks_encrypted(comment)
    set_trace_key(_KEY, _IV, _MARKER)
    assert looks_encrypted(comment)
