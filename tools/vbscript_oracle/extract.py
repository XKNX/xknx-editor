"""Extract the VBScript parameter calculations of a folder of .knxprod files.

Writes one JSON object per distinct calculation (code and parameter types) to a JSON lines
file. The output contains manufacturer code: keep it outside the repository.

    uv run python tools/vbscript_oracle/extract.py C:/knxprod-archive out/calculations.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

_APP = re.compile(r"^M-[0-9A-F]{4}/M-[0-9A-F]{4}_A-[^/]+\.xml$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _type_of(el: ElementTree.Element) -> dict[str, Any] | None:
    for child in el:
        kind = _local(child.tag)
        a = child.attrib
        if kind == "TypeNumber":
            return {
                "kind": "number",
                "type": a.get("Type", "unsignedInt"),
                "size": int(a.get("SizeInBit", "8")),
                "min": int(a.get("minInclusive", "0")),
                "max": int(a.get("maxInclusive", "0")),
            }
        if kind == "TypeRestriction":
            values = [
                int(e.attrib["Value"])
                for e in child
                if _local(e.tag) == "Enumeration" and "Value" in e.attrib
            ]
            return {"kind": "restriction", "values": values}
        if kind == "TypeFloat":
            return {
                "kind": "float",
                "min": float(a.get("minInclusive", "0")),
                "max": float(a.get("maxInclusive", "0")),
            }
        if kind == "TypeTime":
            return {
                "kind": "time",
                "min": int(a.get("minInclusive", "0")),
                "max": int(a.get("maxInclusive", "0")),
            }
        if kind == "TypeText":
            return {"kind": "text", "size": int(a.get("SizeInBit", "8"))}
        if kind in ("TypeDate", "TypeIPAddress", "TypeColor", "TypeRawData"):
            return {"kind": "string"}
        if kind == "TypeNone":
            return {"kind": "none"}
    return None


def _scan_app(data: bytes) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(data)
    types: dict[str, dict[str, Any] | None] = {}
    params: dict[str, tuple[str, str | None, str]] = {}
    refs: dict[str, tuple[str, str | None]] = {}
    calcs: list[ElementTree.Element] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "ParameterType":
            types[el.attrib.get("Id", "")] = _type_of(el)
        elif tag == "Parameter" and "ParameterType" in el.attrib:
            params[el.attrib["Id"]] = (
                el.attrib["ParameterType"],
                el.attrib.get("Value"),
                el.attrib.get("Name", ""),
            )
        elif tag == "ParameterRef":
            refs[el.attrib["Id"]] = (el.attrib["RefId"], el.attrib.get("Value"))
        elif tag == "ParameterCalculation" and el.attrib.get("Language") == "VBScript":
            calcs.append(el)
    out: list[dict[str, Any]] = []
    for calc in calcs:
        sides: dict[str, list[dict[str, Any]]] = {"L": [], "R": []}
        code = {"LR": "", "RL": ""}
        for child in calc:
            tag = _local(child.tag)
            if tag in ("LParameters", "RParameters"):
                for prr in child:
                    ref_id = prr.attrib.get("RefId", "")
                    param_id, ref_value = refs.get(ref_id, ("", None))
                    type_id, param_value, param_name = params.get(
                        param_id, ("", None, "")
                    )
                    sides[tag[0]].append(
                        {
                            "alias": prr.attrib.get("AliasName") or param_name,
                            "type": types.get(type_id),
                            "default": ref_value
                            if ref_value is not None
                            else param_value,
                        }
                    )
            elif tag in ("LRTransformation", "RLTransformation"):
                code[tag[:2]] = child.text or ""
        out.append({"id": calc.attrib.get("Id", ""), "code": code, **sides})
    return out


def _scan_product(path: str) -> list[tuple[str, list[dict[str, Any]]]]:
    found: list[tuple[str, list[dict[str, Any]]]] = []
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not _APP.match(name):
                    continue
                data = z.read(name)
                if b'Language="VBScript"' not in data:
                    continue
                found.append((name, _scan_app(data)))
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    paths = sorted(str(p) for p in args.archive.glob("*.knxprod"))
    seen_apps: set[str] = set()
    seen: set[str] = set()
    apps = calcs = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with (
        args.out.open("w", encoding="utf-8", newline="\n") as out,
        ProcessPoolExecutor() as pool,
    ):
        for result in pool.map(_scan_product, paths, chunksize=8):
            for app, items in result:
                if app in seen_apps:
                    continue
                seen_apps.add(app)
                apps += 1
                for item in items:
                    calcs += 1
                    key = json.dumps(
                        [item["code"], item["L"], item["R"]], sort_keys=True
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    item["app"] = app
                    out.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"{apps} applications, {calcs} calculations, {len(seen)} distinct")


if __name__ == "__main__":
    main()
