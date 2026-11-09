from __future__ import annotations

import contextlib
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import cast

_NAME_PLACEHOLDER = re.compile(r"\{\{0(?::[^{}]*)?\}\}")
_ARG_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")
_MODIFIER = re.compile(r"\b(Format|Offset)\s*=\s*(\S+)")
_LEFTOVER = re.compile(r"\{\{[^{}]+\}\}")
_WHITESPACE = re.compile(r"\s+")


def apply_text_args(text: str, text_args: Mapping[str, str]) -> str:
    """Replace ``{{Name}}`` / ``{{Name Format=Alpha|Decimal Offset=N}}`` placeholders.

    ``Format`` / ``Offset`` mirror the modifiers accepted by applications in dynamic text:
    an integer argument value is shifted by ``Offset`` and rendered as a letter
    (``Format=Alpha``, 0=A) or as a decimal (``Format=Decimal``, which is also the default
    when only ``Offset`` is set). A non-integer value is emitted raw (``Alpha`` falls back
    on parse failure). Argument-name lookup is case-sensitive. ``{{Name:fallback}}`` shows the
    fallback when the value is empty ("Channel A: {{5889:Shutter}}" while a channel has no
    description). Placeholders whose name is not in ``text_args`` are left for a later
    :func:`fill_name` pass, which handles the internal ``{{0}}`` / ``{{0:fallback}}`` form.

    The placeholder regex refuses to match across nested braces, so nested forms like
    ``{{0:Channel {{ChannelNum}}}}`` are resolved inside-out across iterations.
    """
    if "{{" not in text:
        # No placeholder (most texts): every evaluation of a large application asks for
        # thousands of them.
        return text
    # The text depends on the arguments it looks up alone: what it gave with the same ones
    # (each module instance of each evaluation fills the same texts again) is given again.
    known = _APPLIED.get(text)
    if known is not None:
        for names, results in known.items():
            result = results.get(tuple(_argument(text_args, name) for name in names))
            if result is not None:
                return result
    looked: dict[str, tuple[bool, str | None]] = {}

    def lookup(name: str) -> tuple[bool, str | None]:
        answer = looked.get(name)
        if answer is None:
            answer = looked[name] = _argument(text_args, name)
        return answer

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        head, _, rest = inner.partition(" ")
        name, colon, _ = head.partition(":")
        present, found = lookup(name)
        if not present:
            return match.group(0)
        value = cast(str, found)
        if not value and colon:
            return inner.partition(":")[2]
        fmt: str | None = None
        offset = 0
        for mod in _MODIFIER.finditer(rest):
            if mod.group(1) == "Format":
                fmt = mod.group(2)
            else:
                with contextlib.suppress(ValueError):
                    offset = int(mod.group(2))
        if fmt is None and offset == 0:
            return value
        try:
            shifted = int(value) + offset
        except ValueError:
            return value
        if fmt == "Alpha":
            return chr(ord("A") + shifted)
        return str(shifted)

    # Iterate until the string stops changing so inner placeholders surface after each pass.
    # Bound the loop against pathological inputs.
    original = text
    for _ in range(16):
        new_text = _ARG_PLACEHOLDER.sub(replace, text)
        if new_text == text:
            break
        text = new_text
    if len(_APPLIED) >= 1 << 14:
        _APPLIED.clear()
    results = _APPLIED.setdefault(original, {}).setdefault(tuple(looked), {})
    if len(results) >= 1 << 12:
        results.clear()
    results[tuple(looked.values())] = text
    return text


def _argument(text_args: Mapping[str, str], name: str) -> tuple[bool, str | None]:
    """Whether ``text_args`` has ``name``, and its value."""
    if name not in text_args:
        return False, None
    return True, text_args[name]


# What texts with placeholders gave: by the names of the arguments looked up, and what each
# was (whether there, its value).
_APPLIED: dict[
    str, dict[tuple[str, ...], dict[tuple[tuple[bool, str | None], ...], str]]
] = {}


# Every evaluation fills the same few texts with the same values again (tens of thousands).
@lru_cache(maxsize=1 << 16)
def fill_name(template: str, name_value: str, *, keep_whitespace: bool = False) -> str:
    """Resolve {{0}}/{{0:fallback}} to name_value (or its fallback) and drop leftover {{...}}.

    Whitespace is collapsed to single spaces unless ``keep_whitespace``, which keeps
    leading spaces and line breaks (texts laid out with them) and only drops trailing ones.
    """

    def replace(m: re.Match[str]) -> str:
        if name_value:
            return name_value
        s = m.group(0)
        colon = s.find(":")
        return s[colon + 1 : -2] if colon != -1 else ""

    text = template
    if "{{" in text:
        text = _NAME_PLACEHOLDER.sub(replace, text)
        text = _LEFTOVER.sub("", text)
    if keep_whitespace:
        return text.rstrip()
    return _WHITESPACE.sub(" ", text).strip()
