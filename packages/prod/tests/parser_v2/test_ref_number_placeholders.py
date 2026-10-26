"""``{{<number>}}`` in texts: the value of the parameter-ref with that unique number (the number
after ``_R-`` in its id), as MDT names a channel's group objects "Channel A: {{5889}}" after the
channel's description parameter. Inside a module the module definition's refs come first."""

from __future__ import annotations

from typing import cast

from xknxeditor.prod.parser_v2._name import apply_text_args, fill_name
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.context import EvalContext
from xknxeditor.prod.parser_v2.state import ParameterState

DESCRIPTION = "A_P-1664_R-5889"
EMPTY = "A_P-1665_R-5891"
UNSET = "A_P-7_R-7"
MODULE_REF = "A_MD-1_P-1_R-7"


class _Index:
    """The parts of an ApplicationIndexer the placeholders read."""

    def __init__(self) -> None:
        self.refs_by_number: dict[str | None, dict[int, str]] = {
            None: {5889: DESCRIPTION, 5891: EMPTY, 7: UNSET},
            "A_MD-1": {7: MODULE_REF},
        }

    def default_value(self, ref_id: str) -> str | None:
        return {
            DESCRIPTION: "",
            EMPTY: "",
            UNSET: "Default",
            MODULE_REF: "Module default",
        }.get(ref_id)


def _text(template: str, ctx: EvalContext, name: str = "") -> str:
    return fill_name(apply_text_args(template, ctx.text_args()), name)


def _context() -> EvalContext:
    state = ParameterState({DESCRIPTION: "Living Room South"})
    return EvalContext(state, idx=cast("ApplicationIndexer", _Index()))


def test_a_number_stands_for_the_value_of_that_parameter_ref() -> None:
    ctx = _context()
    assert _text("Channel A: {{5889}}", ctx) == "Channel A: Living Room South"
    # A ref never set shows its default.
    assert _text("Value: {{7}}", ctx) == "Value: Default"


def test_an_empty_value_shows_the_fallback_after_the_colon() -> None:
    """A channel page without a description is named after its mode ("Kanal C: Rollladen")."""
    ctx = _context()
    assert _text("Channel A: {{5889:Shutter}}", ctx) == "Channel A: Living Room South"
    assert _text("Channel B: {{5891:Blinds}}", ctx) == "Channel B: Blinds"
    assert _text("Channel B: {{5891}}", ctx) == "Channel B:"


def test_zero_unknown_numbers_and_arguments_keep_their_meaning() -> None:
    ctx = _context()
    # {{0}} is the text parameter, filled by fill_name.
    assert _text("Button {{0}}", ctx, name="Kitchen") == "Button Kitchen"
    # A number no parameter-ref has is dropped, as before.
    assert _text("Channel {{42}}", ctx) == "Channel"


def test_a_module_resolves_its_own_refs_first() -> None:
    ctx = _context().module_ctx("A_MD-1_M-1", ref_id="A_MD-1")
    assert _text("{{7}}", ctx) == "Module default"
    assert _text("{{5889}}", ctx) == "Living Room South"
