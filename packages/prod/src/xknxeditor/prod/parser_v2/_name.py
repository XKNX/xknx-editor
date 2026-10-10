from __future__ import annotations

import re

_NAME_PLACEHOLDER = re.compile(r"\{\{0(?::[^}]*)?\}\}")


def apply_text_args(text: str, text_args: dict[str, str]) -> str:
    """Replace {{ArgName}}/{{ArgName:fmt}} placeholders using module text args."""
    for name, value in text_args.items():
        text = re.sub(r"\{\{" + re.escape(name) + r"(?::[^}]*)?\}\}", value, text)
    return text


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

    text = _NAME_PLACEHOLDER.sub(replace, template)
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    if keep_whitespace:
        return text.rstrip()
    return re.sub(r"\s+", " ", text).strip()
