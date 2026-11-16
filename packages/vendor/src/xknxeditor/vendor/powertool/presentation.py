"""Product-language labels, independent of stored values and image encoding."""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads(
        gzip.decompress(
            files(__package__).joinpath("presentation.json.gz").read_bytes()
        )
    )


class Presentation:
    def __init__(self, database_code: int, language: str = "en") -> None:
        language = language.replace("_", "-").split("-")[0].lower()
        self._data = _data() if language != "en" else {}
        languages = self._data.get("languages", [])
        self._language = languages.index(language) if language in languages else None
        self._nodes: dict[str, int] = self._data.get("databases", {}).get(
            str(database_code), {}
        )

    def text(
        self,
        node: str,
        fallback: str,
        *,
        option: int | None = None,
        subsystem: int | None = None,
    ) -> str:
        index = self._nodes.get(node.removeprefix("BJE.toolB.ProductDatabase."))
        if index is None or self._language is None:
            return fallback
        label, options, subsystems = self._data["descriptions"][index]
        if option is not None or subsystem is not None:
            choices, selected = (
                (options, option) if option is not None else (subsystems, subsystem)
            )
            if selected is None or not 0 <= selected < len(choices):
                return fallback
            label = choices[selected]
        return self._data["texts"][label][self._language] or fallback

    def native_node(
        self,
        function: str,
        field: str | None = None,
        object_key: str | None = None,
    ) -> str:
        spec = _data()["native"][function]
        if field is not None:
            return spec["fields"][field]
        if object_key is not None:
            return spec["objects"][object_key]
        return spec["node"]

    def schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        if self._language is None:
            return schema
        nodes = {}
        for key, node in schema["nodes"].items():
            localized = {**node, "label": self.text(key, node["label"])}
            if "options" in node:
                localized["options"] = [
                    {
                        **value,
                        "label": self.text(key, cast(str, value["label"]), option=i),
                    }
                    if isinstance(value, dict)
                    else self.text(key, value, option=i)
                    for i, value in enumerate(node["options"])
                ]
            if "subsystem_labels" in node:
                localized["subsystem_labels"] = [
                    self.text(key, value, subsystem=i)
                    for i, value in enumerate(node["subsystem_labels"])
                ]
            nodes[key] = localized
        return {**schema, "nodes": nodes}
