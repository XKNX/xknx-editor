"""Localized interface texts, separate from image metadata."""

from __future__ import annotations

import gettext
import json
import re
from functools import lru_cache
from importlib.resources import files
from typing import Any

from .metadata import native_nodes


def free_timer_label(label: str) -> str:
    """Describe numeric timer values in milliseconds."""
    label, count = re.subn(
        r"\((?:s|n|min|mm:ss|min:sec,msec|min:seg, mseg)\)|\[mm:ss\]", "(ms)", label
    )
    return label if count or "(ms)" in label else f"{label} (ms)"


@lru_cache(maxsize=1)
def _text_map() -> dict[str, dict[str, Any]]:
    return json.loads(
        files(__package__).joinpath("text-map.json").read_text(encoding="utf-8")
    )


@lru_cache(maxsize=25)
def _translation(language: str) -> gettext.NullTranslations:
    resource = files(__package__).joinpath(
        "locales", language, "LC_MESSAGES", "powertool.mo"
    )
    if not resource.is_file():
        return gettext.NullTranslations()
    with resource.open("rb") as stream:
        return gettext.GNUTranslations(stream)


class Presentation:
    def __init__(self, database_code: int, language: str = "en") -> None:
        language = language.replace("_", "-").split("-")[0].lower()
        self._translate = _translation(language).gettext
        self._nodes = _text_map().get(str(database_code), {})

    def text(
        self,
        node: str,
        fallback: str,
        *,
        option: int | None = None,
        subsystem: int | None = None,
    ) -> str:
        spec = self._nodes.get(node.removeprefix("BJE.toolB.ProductDatabase."))
        if spec is None:
            return fallback
        if option is not None or subsystem is not None:
            choices, selected = (
                (spec.get("options", []), option)
                if option is not None
                else (spec.get("subsystems", []), subsystem)
            )
            if selected is None or not 0 <= selected < len(choices):
                return fallback
            msgid = choices[selected]
        else:
            msgid = spec["label"]
        return self._translate(msgid) if msgid else fallback

    def native_node(
        self,
        function: str,
        field: str | None = None,
        object_key: str | None = None,
    ) -> str:
        spec = native_nodes()[function]
        if field is not None:
            return spec["fields"][field]
        if object_key is not None:
            return spec["objects"][object_key]
        return spec["node"]

    def schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Hydrate a separate view; numeric metadata stays untouched."""
        nodes = {}
        for key, node in schema["nodes"].items():
            localized = {**node, "label": self.text(key, "")}
            if "options" in node:
                localized["options"] = [
                    {**value, "label": self.text(key, "", option=i)}
                    if isinstance(value, dict)
                    else self.text(key, "", option=i)
                    for i, value in enumerate(node["options"])
                ]
            if "subsystems" in node:
                localized["subsystem_labels"] = [
                    self.text(key, "", subsystem=i)
                    for i in range(len(node["subsystems"]))
                ]
            nodes[key] = localized
        return {**schema, "nodes": nodes}
