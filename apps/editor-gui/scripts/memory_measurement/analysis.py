"""Compare project snapshots and inspect Python application objects."""

from __future__ import annotations

import gc
import hashlib
import io
import json
import re
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from types import BuiltinFunctionType, FunctionType, MethodType, ModuleType
from typing import cast
from zipfile import ZipExtFile, ZipFile

import psutil


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_manifest(path: Path, password: str | None) -> dict[str, object]:
    """Hash all uncompressed members and, for protected projects, the decrypted project folder."""
    import pyzipper

    from xknxeditor.proj.core.knxproj_protect import (
        AES_SCHEMA,
        archive_schema,
        ets6_zip_password,
    )

    outer_members: dict[str, str] = {}
    inner_members: dict[str, dict[str, str]] = {}
    with ZipFile(path) as outer:
        for member in outer.infolist():
            name = member.filename
            if name in outer_members:
                raise ValueError(f"Duplicate export member: {name}")
            if not re.fullmatch(r"P-[0-9A-F]+\.zip", name, re.IGNORECASE):
                with outer.open(member) as stream:
                    outer_members[name] = hashlib.file_digest(
                        cast(ZipExtFile, stream), "sha256"
                    ).hexdigest()
                continue

            if password is None:
                raise ValueError("The archive needs --password-file")
            # The container is salted per export. Compare its presence and the actual contents.
            outer_members[name] = "encrypted project folder"
            aes = archive_schema(outer.read("knx_master.xml")) >= AES_SCHEMA
            reader = (
                cast(Callable[[io.BytesIO], ZipFile], pyzipper.AESZipFile)
                if aes
                else ZipFile
            )
            key = ets6_zip_password(password) if aes else password.encode("utf-8")
            with reader(io.BytesIO(outer.read(member))) as inner:
                inner.setpassword(key)
                entries = inner.namelist()
                if len(entries) != len(set(entries)):
                    raise ValueError(f"Duplicate member in project folder: {name}")
                inner_members[name] = {
                    entry: digest(inner.read(entry)) for entry in entries
                }
    return {"outer_members": outer_members, "decrypted_project_folders": inner_members}


def differences(a: object, b: object, path: str = "") -> Iterator[str]:
    """Report differences by path, preserving the distinction between JSON value types."""
    if type(a) is not type(b):
        yield path or "/"
    elif isinstance(a, dict) and isinstance(b, dict):
        left = cast(dict[str, object], a)
        right = cast(dict[str, object], b)
        for key in sorted(left.keys() | right.keys()):
            child = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            if key not in left or key not in right:
                yield child
            else:
                yield from differences(left[key], right[key], child)
    elif isinstance(a, list) and isinstance(b, list):
        left_items = cast(list[object], a)
        right_items = cast(list[object], b)
        if len(left_items) != len(right_items):
            yield (path or "/") + " (length)"
        for index, (left_item, right_item) in enumerate(
            zip(left_items, right_items, strict=False)
        ):
            yield from differences(left_item, right_item, f"{path}/{index}")
    elif a != b:
        yield path or "/"


def read_snapshot(path: Path) -> object:
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "snapshot" not in data:
        raise ValueError(f"{path} has no correctness snapshot (run with --snapshot)")
    snapshot = cast(dict[str, object], data)["snapshot"]
    if not isinstance(snapshot, dict) or not {"devices", "export"} <= snapshot.keys():
        raise ValueError(f"{path} has an invalid correctness snapshot")
    return cast(dict[str, object], snapshot)


def compare(a: Path, b: Path) -> int:
    found = list(differences(read_snapshot(a), read_snapshot(b)))
    if found:
        print(f"{len(found)} differences")
        for path in found[:30]:
            print(f"  {path}")
        return 1
    print(
        "Identical evaluated parameters, group objects, planned memory and export members."
    )
    return 0


@dataclass(frozen=True)
class DeepSize:
    bytes: int
    objects: int
    string_bytes: int
    strings: int


def deep_size(root: object, seen: set[int] | None = None) -> DeepSize:
    """Size Python referents once per identity, including slotted and dictionary fields."""
    if seen is None:
        seen = set()
    stack = [root]
    size = count = string_size = strings = 0
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(
            obj, (type, ModuleType, FunctionType, BuiltinFunctionType, MethodType)
        ):
            continue
        seen.add(id(obj))
        current = sys.getsizeof(obj)
        size += current
        count += 1
        if isinstance(obj, str):
            string_size += current
            strings += 1
        else:
            stack.extend(gc.get_referents(obj))
    return DeepSize(size, count, string_size, strings)


def measure_cached_app(
    cache: Path, app_id: str, manufacturer: str
) -> dict[str, object]:
    from editor_gui.device_snapshot import code_fingerprint

    from xknxeditor.prod.application import cached_applications
    from xknxeditor.prod.parse_cache import set_tree_code

    set_tree_code(code_fingerprint())
    process = psutil.Process()
    gc.collect()
    before = process.memory_info().rss
    start = time.perf_counter()
    apps = cached_applications(app_id, manufacturer, cache_dir=cache)
    app = next((candidate for candidate in apps or () if candidate.id == app_id), None)
    if app is None:
        raise ValueError(f"Parse cache miss for {app_id}")
    program, builder = app.resolved()
    elapsed = time.perf_counter() - start
    gc.collect()
    after = process.memory_info().rss

    seen: set[int] = set()
    parts = (
        ("ir_dynamic", program.dynamic),
        ("ir_static", program.static),
        ("ir_module_definitions", program.module_defs),
        ("ir_other", program),
        ("indexer", builder.idx),
        ("evaluation_tree", builder.tree),
        ("tree_indexes_and_other", builder),
    )
    rows = {name: asdict(deep_size(value, seen)) for name, value in parts}
    return {
        "application": app_id,
        "rss_before_bytes": before,
        "rss_after_bytes": after,
        "rss_delta_bytes": after - before,
        "load_s": elapsed,
        "deep_size": rows,
    }
