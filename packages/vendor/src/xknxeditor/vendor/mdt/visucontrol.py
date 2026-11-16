"""VisuControl configuration containers and their communication objects."""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Any, cast

_LIMIT = 16 * 1024 * 1024
_EXTENSION = b"##FF0083|etsvcp\0"


def unpack_json(data: bytes) -> dict[str, Any]:
    if len(data) < 4:
        raise ValueError("Truncated compressed configuration")
    length = int.from_bytes(data[:4], "big")
    if length > _LIMIT:
        raise ValueError("Configuration exceeds the size limit")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(data[4:], length + 1)
        if len(raw) != length or not decoder.eof or decoder.unused_data:
            raise ValueError("Invalid compressed configuration length")
        result = json.loads(raw)
    except (zlib.error, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid compressed configuration") from exc
    if not isinstance(result, dict):
        raise ValueError("Configuration must contain a JSON object")
    return cast(dict[str, Any], result)


def pack_json(value: dict[str, Any]) -> bytes:
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    if len(raw) > _LIMIT:
        raise ValueError("Configuration exceeds the size limit")
    return len(raw).to_bytes(4, "big") + zlib.compress(raw)


@dataclass(frozen=True)
class Object:
    number: int
    name: str
    dpt: int
    subtype: int | None
    flags: str
    function_text: str


@dataclass(frozen=True)
class Configuration:
    parts: tuple[bytes, ...]

    @classmethod
    def read(cls, data: bytes) -> Configuration:
        if len(data) > _LIMIT:
            raise ValueError("Configuration exceeds the size limit")
        parts: list[bytes] = []
        offset = 0
        while offset < len(data):
            if offset + 4 > len(data):
                raise ValueError("Truncated configuration part length")
            length = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            if offset + length > len(data) or len(parts) >= 7:
                raise ValueError("Invalid configuration part length or count")
            parts.append(data[offset : offset + length])
            offset += length
        result = cls(tuple(parts))
        result.validate()
        return result

    def write(self) -> bytes:
        self.validate()
        return b"".join(struct.pack("<I", len(part)) + part for part in self.parts)

    @property
    def database(self) -> dict[str, Any]:
        return unpack_json(self.parts[1])

    @property
    def function_texts(self) -> bytes:
        return next((p for p in self.parts[4:6] if p.startswith(b"##01")), b"")

    @property
    def version(self) -> int:
        versions = unpack_json(self.parts[0])
        active = [versions.get(key) for key in ("dca", "device", "configurator")]
        active = [cast(dict[str, Any], v) for v in active if isinstance(v, dict)]
        active = [v for v in active if str(v.get("major", ""))]
        if len(active) != 1:
            raise ValueError("Configuration must identify exactly one source version")
        try:
            major, minor, revision = (
                int(active[0].get(k) or 0) for k in ("major", "minor", "revision")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid configuration version") from exc
        if major != 1 or not 0 <= minor <= 255 or not 0 <= revision <= 255:
            raise ValueError("Unsupported configuration version")
        return (major << 11) | ((minor & 31) << 6) | (revision & 63)

    def validate(self) -> None:
        if len(self.parts) not in (6, 7) or self.parts[-1].rstrip(
            b"\0"
        ) != _EXTENSION.rstrip(b"\0"):
            raise ValueError("Not an MDT VisuControl configuration")
        _ = self.version
        database = self.database
        try:
            api = int(database["versions"]["dca_api"] or 1)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError("Invalid DCA API version") from exc
        if api not in (0, 1):
            raise ValueError("Unsupported DCA API version")
        for part in self.parts[4:-1]:
            if part.startswith((b"##00", b"##01")):
                unpack_json(part[4:])
            elif part:
                raise ValueError("Unknown configuration metadata")
        self.objects()

    def objects(self, language: str = "en") -> tuple[Object, ...]:
        db = self.database
        texts = unpack_json(self.function_texts[4:]) if self.function_texts else {}
        try:
            items = db["items"]["objects_list"]
            elements = db.get("elements", {}).get("objects_list", {})
            version = str(db.get("versions", {}).get("database", "0.0.0"))
            modern = tuple(int(v) for v in version.split(".")) >= (3, 0, 0)
            functions: dict[str, tuple[str, str]] = {}
            for element in elements.values():
                function_map = dict(element.get("function_map", {}))
                if element.get("@class") == "psoe_thermo_logic" and element.get(
                    "thermo_id"
                ):
                    thermo = db["thermo"]["objects_list"][str(element["thermo_id"])]
                    values = thermo["thermo"]
                    function_map.update(values.get("items", {}))
                    if values.get("idactual"):
                        function_map["idactual"] = values["idactual"]
                    if values.get("win_enable") and thermo.get("id_win"):
                        function_map["id_win"] = thermo["id_win"]
                for key, item in function_map.items():
                    if not item:
                        continue
                    if str(item) in functions:
                        raise ValueError(
                            "A data point is used in multiple function mappings"
                        )
                    functions[str(item)] = (element.get("@class", ""), key)
            result: dict[int, Object] = {}
            status_text = (
                _localized(
                    texts.get("dca_generic", {}).get("com_status_label", {}), language
                )
                or "Status"
            )
            for item_id, item in items.items():
                if not item.get("id_driver") or not (
                    item.get("command") or item.get("status")
                ):
                    continue
                match = re.fullmatch(r"dtp_(\d+)", str(item.get("type", "")))
                if match is None:
                    raise ValueError(f"Invalid data point type for item {item_id}")
                dpt = int(match[1])
                subtype = int(item["typeformat"]) if item.get("typeformat") else None
                if not 0 < dpt <= 255 or (
                    subtype is not None and not 0 <= subtype <= 65535
                ):
                    raise ValueError("Invalid data point type")
                cls, key = functions.get(item_id, ("", ""))
                entries = texts.get(cls, {})
                localized = entries.get(key, entries.get(re.sub(r"\d+$", "", key), {}))
                for role, bit in (("command", 2), ("status", 3)):
                    if item.get(role) in (None, ""):
                        continue
                    number = int(item[role])
                    if not 0 <= number <= 65535 or number in result:
                        raise ValueError(
                            "Duplicate or invalid communication object number"
                        )
                    flags = "CTUW"
                    if modern:
                        flags = (
                            "".join(
                                c
                                for i, c in enumerate("CTURWI")
                                if int(item.get("flags", 0)) & (1 << (bit + 2 * i))
                            )
                            or "CTUW"
                        )
                    name = str(item.get("name") or "")
                    function_text = _localized(localized.get(role, {}), language)
                    if not function_text and "SYSTEM" in name:
                        function_text = _system_text(
                            db, texts, str(item_id), role, language
                        )
                    result[number] = Object(
                        number,
                        name + (" " + status_text if role == "status" else ""),
                        dpt,
                        subtype,
                        flags,
                        function_text,
                    )
            return tuple(result[n] for n in sorted(result))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid VisuControl data point structure") from exc


def _localized(value: dict[str, Any], language: str) -> str:
    return str(value.get(language) or value.get("en") or "")


def _system_text(
    db: dict[str, Any], texts: dict[str, Any], item_id: str, role: str, language: str
) -> str:
    excluded = {
        "information",
        "items",
        "elements",
        "general",
        "notify",
        "rules",
        "scenary",
        "scheduler",
        "users",
        "versions",
    }

    def search(value: Any, path: tuple[str, ...]) -> tuple[str, ...] | None:
        if isinstance(value, dict):
            for key, child in cast(dict[str, Any], value).items():
                if found := search(child, (*path, key)):
                    return found
        elif str(value) == item_id:
            return path
        return None

    path = search({k: v for k, v in db.items() if k not in excluded}, ())
    if path:
        value = texts.get("system", {})
        for key in (*path, role):
            value = value.get(key, {})
        return _localized(value, language)
    return ""


def create_configuration(
    blobs: tuple[bytes, bytes, bytes], function_texts: bytes, metadata: dict[str, Any]
) -> Configuration:
    versions = {
        key: {"major": "", "minor": "", "revision": ""}
        for key in ("dca", "device", "configurator")
    }
    versions["dca"] = {"major": "1", "minor": "0", "revision": "0"}
    result = Configuration(
        (
            pack_json(versions),
            *blobs,
            b"##00" + pack_json({"identifier": "##00", **metadata}),
            function_texts,
            _EXTENSION,
        )
    )
    result.validate()
    return result
