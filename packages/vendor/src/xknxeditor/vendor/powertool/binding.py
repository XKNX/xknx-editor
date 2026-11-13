"""Bind logical roles to the product's numbered size variants and control fields."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import cast

from xknxeditor.prod import Application
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer

from .codec import CompiledPowerToolImage

SIZE_TEXT = {
    0: "1 Bit",
    1: "2 Bit",
    3: "4 Bit",
    7: "1 Byte",
    8: "2 Bytes",
    9: "3 Bytes",
    10: "4 Bytes",
    14: "14 Bytes",
}


@dataclass(frozen=True)
class CatalogBinding:
    references: dict[str, str]
    parameters: dict[str, str]


def bind_catalog(
    application: Application, image: CompiledPowerToolImage
) -> CatalogBinding:
    index = ApplicationIndexer(application.program)
    gates: dict[str, tuple[str, str]] = {}

    def walk(node: object, gate: str = "", value: str = "") -> None:
        if isinstance(node, list):
            for child in cast(list[object], node):
                walk(child, gate, value)
        elif is_dataclass(node) and not isinstance(node, type):
            if type(node).__name__.endswith("Choose"):
                gate = str(getattr(node, "param_ref_id", ""))
            if hasattr(node, "test"):
                value = str(getattr(node, "test", ""))
            if type(node).__name__ == "ComObjectRefRef" and gate and value:
                ref_id = str(getattr(node, "ref_id", ""))
                if ref_id in gates:
                    raise ValueError("ambiguous object visibility control")
                gates[ref_id] = (gate, value)
            for f in fields(node):
                child = getattr(node, f.name)
                if isinstance(child, list) or is_dataclass(child):
                    walk(cast(object, child), gate, value)

    walk(application.program.dynamic)
    parameters = dict.fromkeys((g for g, _ in gates.values()), "0")
    references: dict[str, str] = {}
    number_offset = 0 if any(o.number == 0 for o in index.com_objects.values()) else 1
    for role in image.objects:
        if not role.visible:
            continue
        candidates = [
            ref.id
            for ref in index.com_object_refs.values()
            if index.com_objects[ref.ref_id].number == role.number + number_offset
            and (ref.object_size or index.com_objects[ref.ref_id].object_size).value
            == SIZE_TEXT[role.size_code]
            and ref.id in gates
        ]
        if len(candidates) != 1:
            raise ValueError(f"no unique catalog reference for {role.key}")
        ref = candidates[0]
        references[role.key] = ref
        gate, value = gates[ref]
        parameters[gate] = value
    return CatalogBinding(references, parameters)
