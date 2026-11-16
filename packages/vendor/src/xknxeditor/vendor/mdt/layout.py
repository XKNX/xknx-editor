"""GTS tile layouts mapped to ordinary application parameter references."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Literal

Orientation = Literal["V", "H"]
_REF = re.compile(r"MD-3_M-\d+_MI-(\d+)_P-(\d+)_R-(\d+)$")
TRANSPARENCY = (255, 32, 29, 26, 23, 20, 16, 13, 10, 6, 3, 0)
TILE_SIZES = (
    (680, 680),
    (680, 330),
    (330, 330),
    (330, 213),
    (330, 680),
    (213, 330),
    (680, 115),
)


@cache
def parameter_map() -> dict[tuple[int, int], tuple[str, int, str]]:
    rows = json.loads(files(__package__).joinpath("layout-map.json").read_text())
    return {(p, r): (kind, slot, orientation) for p, r, kind, slot, orientation in rows}


@cache
def status_map() -> dict[tuple[int, int], tuple[str, int, str]]:
    rows = json.loads(files(__package__).joinpath("status-map.json").read_text())
    return {(p, r): (kind, slot, phase) for p, r, kind, slot, phase in rows}


@dataclass(frozen=True)
class Tile:
    x: int
    y: int
    kind: int
    function: int = 0
    transparency: int = 255
    color: int = 0

    def validate(self, orientation: Orientation = "V", height: int = 1440) -> None:
        values = (
            self.x,
            self.y,
            self.kind,
            self.function,
            self.transparency,
            self.color,
        )
        if any(type(value) is not int for value in values):
            raise ValueError("Tile values must be integers")
        if not 0 <= self.kind < len(TILE_SIZES):
            raise ValueError("Unknown tile size")
        width, tile_height = TILE_SIZES[self.kind]
        x, y = self.position(orientation, height)
        display_width, display_height = (
            (720, height) if orientation == "V" else (height, 720)
        )
        if not (
            self.x > 0
            and self.y > 0
            and x >= 0
            and x + width <= display_width
            and y >= 0
            and y + tile_height <= display_height
        ):
            raise ValueError("Tile coordinates are outside the display")
        if not (
            0 <= self.function <= 65535
            and self.transparency in TRANSPARENCY
            and self.color in (0, 1, 2)
        ):
            raise ValueError("Invalid tile function, transparency or color")

    def position(self, orientation: Orientation, height: int = 1440) -> tuple[int, int]:
        width, _ = TILE_SIZES[self.kind]
        return (
            (self.x + 1, self.y + 1)
            if orientation == "V"
            else (height - self.y - 1 - width, self.x + 11)
        )


@dataclass(frozen=True)
class Page:
    number: int
    name: str
    orientation: Orientation
    tiles: tuple[Tile, ...]
    status: bool = False


class Layout:
    def __init__(self, values: Mapping[str, str | None]) -> None:
        self.values = values
        self.height = (
            720
            if any(
                ref.endswith("_P-646_R-1") and value in ("2", "3")
                for ref, value in values.items()
            )
            else 1440
        )
        self.refs: dict[tuple[int, str, str, int], str] = {}
        mapping = parameter_map()
        for ref in values:
            match = _REF.search(ref)
            if match is None:
                match = (
                    re.search(r"_P-(\d+)_R-(\d+)$", ref) if "_MD-" not in ref else None
                )
                if match is not None:
                    field = status_map().get((int(match[1]), int(match[2])))
                    if field is not None:
                        kind, slot, phase = field
                        self.refs[(0 if phase == "day" else -1, "", kind, slot)] = ref
                continue
            page, parameter, reference = map(int, match.groups())
            field = mapping.get((parameter, reference), mapping.get((parameter, -1)))
            if field is not None:
                kind, slot, orientation = field
                key = (page, orientation, kind, slot)
                if key in self.refs:
                    raise ValueError("Ambiguous page parameter reference")
                self.refs[key] = ref

    def _value(self, page: int, orientation: str, kind: str, slot: int = 0) -> str:
        ref = self.refs.get((page, orientation, kind, slot))
        if ref is None or self.values[ref] is None:
            raise ValueError(
                f"Missing page {page} parameter: {orientation}/{kind}/{slot}"
            )
        return str(self.values[ref])

    def pages(self, orientation: Orientation) -> tuple[Page, ...]:
        result: list[Page] = []
        for number in sorted({key[0] for key in self.refs}):
            status = number <= 0
            if status and not any(
                ref.endswith("_P-569_R-352") and value == "1"
                for ref, value in self.values.items()
            ):
                continue
            if number == -1 and not any(
                ref.endswith("_P-336_R-353") and value == "1"
                for ref, value in self.values.items()
            ):
                continue
            bank = "" if status else orientation
            tiles: list[Tile] = []
            for slot in range(1, 13):
                x = int(self._value(number, bank, "X", slot))
                y = int(self._value(number, bank, "Y", slot))
                if x > 0 and y > 0:
                    color = 0 if status else int(self._value(number, bank, "CL", slot))
                    tile = Tile(
                        x,
                        y,
                        int(self._value(number, bank, "T", slot)),
                        int(self._value(number, bank, "F", slot)),
                        int(self._value(number, bank, "TP", slot)),
                        0 if color == 255 else color,
                    )
                    tile.validate(orientation, self.height)
                    tiles.append(tile)
            result.append(
                Page(
                    number,
                    "" if status else self._value(number, "", "PageName"),
                    orientation,
                    tuple(tiles),
                    status,
                )
            )
        return tuple(result)

    def changes(self, page: Page) -> dict[str, str]:
        if page.orientation not in ("V", "H") or len(page.tiles) > 12:
            raise ValueError("Invalid page orientation or tile count")
        result: dict[str, str] = {}
        status = page.number <= 0
        bank = "" if status else page.orientation

        def put(
            kind: str, value: object, slot: int = 0, orientation: str = bank
        ) -> None:
            key = (page.number, orientation, kind, slot)
            if key not in self.refs:
                raise ValueError(f"Missing page parameter: {key}")
            result[self.refs[key]] = str(value)

        for tile in page.tiles:
            tile.validate(page.orientation, self.height)
        if page.tiles:
            if status:
                ref = next((r for r in self.values if r.endswith("_P-570_R-408")), None)
                if ref is None:
                    raise ValueError("Missing status page control parameter")
                result[ref] = "1"
            else:
                put("DCAFlag", 1, orientation="")
        for slot, tile in enumerate(sorted(page.tiles, key=lambda t: (t.x, t.y)), 1):
            for kind, value in (
                ("X", tile.x),
                ("Y", tile.y),
                ("T", tile.kind),
                ("F", tile.function),
                ("TP", tile.transparency),
                ("CL", tile.color or 255),
            ):
                if status and kind == "CL":
                    continue
                put(kind, value, slot)
        for slot in range(len(page.tiles) + 1, 13):
            for kind in ("X", "Y", "F", "TP"):
                put(kind, 0, slot)
        if not status:
            put("PageName", page.name, orientation="")
        return {
            ref: value for ref, value in result.items() if self.values.get(ref) != value
        }
