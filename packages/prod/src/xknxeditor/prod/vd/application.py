"""Application programs: one XML file per program (static part, dynamic part, translations)."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .baggage import FOREIGN_BAGGAGES, move_to_manufacturer, referenced_elsewhere
from .compat import APPLICATION_COMPATIBILITY
from .context import ConversionError, sort_rows
from .ids import application_id, fragment
from .language import Translations, translate_descriptions, translate_entity
from .loadctl import LoadControls
from .netsort import list_sort
from .optimized import replace_load_controls
from .replacers import plugin_must_remain
from .text import from_db, lcid_of
from .xmlout import El, boolean

if TYPE_CHECKING:
    from collections.abc import Callable

    from .context import Context
    from .database import Row
    from .dump import Value

_MASK_EXCEPTIONS: list[tuple[int, int, int, int]] = [
    (1, 32792, 4, 1793),
    (1, 32792, 6, 1793),
    (1, 32800, 2, 1793),
    (1, 32780, 6, 1793),
    (1, 29952, 1, 1968),
    (1, 38940, 3, 1968),
    (1, 32800, 1, 1793),
    (2, 8449, 162, 17),
    (2, 8449, 178, 17),
    (2, 8449, 194, 17),
    (2, 8961, 162, 17),
    (2, 8961, 178, 17),
    (2, 8961, 194, 17),
    (2, 9217, 16, 18),
    (2, 9217, 17, 18),
    (2, 9217, 162, 18),
    (2, 9217, 178, 18),
    (2, 9217, 194, 18),
    (2, 9217, 210, 18),
    (2, 10241, 1, 17),
    (2, 12033, 4, 1793),
    (2, 13569, 161, 32),
    (2, 15361, 17, 32),
    (2, 15361, 18, 32),
    (2, 15361, 19, 32),
    (2, 38161, 81, 17),
    (2, 38674, 16, 1968),
    (2, 38674, 17, 1968),
    (2, 40967, 16, 1793),
    (2, 40968, 16, 1793),
    (2, 40969, 16, 1793),
    (2, 40970, 16, 1793),
    (2, 40971, 16, 1793),
    (2, 40973, 16, 1793),
    (2, 40987, 18, 1793),
    (2, 25380, 17, 18),
    (2, 25377, 97, 18),
    (7, 38674, 16, 1968),
    (7, 38674, 17, 1968),
    (9, 8292, 16, 1793),
    (9, 8295, 16, 1793),
    (131, 48, 18, 1793),
]

_EXTERNAL_SOFTWARE = frozenset(
    (m, t, v)
    for m, t, v in [
        (2, 14849, 32), (2, 24886, 16), (2, 24886, 32), (2, 24886, 34), (2, 24886, 48),
        (2, 26934, 16), (2, 26934, 32), (2, 38674, 16), (4, 5153, 1), (4, 5154, 1),
        (4, 5155, 1), (4, 5156, 1), (4, 5169, 1), (4, 5170, 1), (4, 5171, 1), (4, 5172, 1),
        (4, 5188, 1), (4, 5204, 1), (4, 20493, 1), (4, 20494, 1), (4, 20497, 1),
        (4, 22785, 1), (4, 45062, 1), (4, 45062, 2), (4, 49156, 1), (4, 49156, 2),
        (4, 49156, 3), (4, 49158, 1), (6, 5651, 1), (6, 5651, 2), (6, 5652, 1), (6, 5652, 2),
        (6, 5653, 1), (6, 5653, 2), (6, 5683, 1), (6, 5683, 2), (6, 5684, 1), (6, 5684, 2),
        (6, 20493, 1), (6, 20494, 1), (6, 20497, 1), (6, 22785, 1), (6, 45062, 1),
        (6, 45062, 2), (6, 49156, 1), (6, 49156, 2), (6, 49156, 3), (6, 49158, 1),
        (7, 20494, 16), (7, 24868, 16), (7, 24872, 16), (7, 24886, 48), (7, 25344, 16),
        (7, 25345, 48), (7, 26934, 48), (7, 33078, 48), (7, 38674, 16), (7, 45062, 1),
        (7, 45062, 2), (8, 20493, 1), (8, 20494, 1), (8, 20497, 1), (8, 20498, 1),
        (8, 22785, 1), (8, 24902, 1), (8, 24902, 2), (8, 24905, 1), (8, 24905, 2),
        (8, 24930, 1), (8, 24931, 1), (8, 24934, 1), (8, 45062, 2), (8, 49156, 1),
        (8, 49156, 2), (8, 49156, 3), (9, 20494, 16), (10, 5651, 1), (10, 5651, 2),
        (10, 5652, 1), (10, 5652, 2), (10, 5653, 1), (10, 5653, 2), (10, 5683, 1),
        (10, 5683, 2), (10, 5684, 1), (10, 5684, 2), (10, 24902, 1), (10, 24902, 2),
        (10, 24905, 1), (10, 24905, 2), (10, 24930, 1), (10, 24931, 1), (10, 24934, 1),
        (10, 25137, 16), (10, 49156, 1), (10, 49156, 2), (10, 49156, 3), (12, 24833, 16),
        (12, 25137, 16), (12, 45062, 1), (12, 45062, 2), (22, 257, 16), (25, 24833, 16),
        (73, 4096, 16), (94, 20494, 1), (100, 45062, 2), (244, 5153, 1), (244, 5154, 1),
        (244, 5155, 1), (244, 5156, 1), (244, 5169, 1), (244, 5170, 1), (244, 5171, 1),
        (244, 5172, 1), (244, 5188, 1), (244, 5204, 1), (244, 20493, 1), (244, 20494, 1),
        (244, 20494, 16), (244, 20497, 1), (244, 20498, 1), (244, 22785, 1), (244, 24833, 1),
        (244, 24833, 16), (244, 45062, 1), (244, 45062, 2), (244, 49158, 1), (249, 24833, 1),
    ]
)  # fmt: skip

# the PowerTool plugins: a program with one is broken when it ships a tool older than 1.1.54
_POWER_TOOL_PLUGINS = frozenset(
    {"{2BD47AB3-253C-47F1-A82C-55328D479B09}", "{86F09BDE-5F13-4E08-8B5C-1C6F85ADAD90}"}
)
_POWER_TOOLS = frozenset({"aetoolb.dll", "betoolb.dll"})
_FIXED_POWER_TOOL = (1, 1, 54, 0)


def dotnet_version(text: str) -> tuple[int, ...]:
    """A version as .NET compares it: 2 to 4 numbers, missing ones are -1."""
    parts = text.split(".")
    if not 2 <= len(parts) <= 4 or not all(
        re.fullmatch(r"\s*\+?\d+\s*", p) for p in parts
    ):
        raise ValueError(f"Version string portion was too short or too long: {text}")
    numbers = [int(p) for p in parts]
    if any(n > 0x7FFFFFFF for n in numbers):
        raise ValueError(f"Value was either too large or too small: {text}")
    return tuple(numbers + [-1] * (4 - len(numbers)))


def _broken_power_tool(ctx: Context, ref: str) -> bool:
    """Whether the baggage ``ref`` is a PowerTool older than the fixed version."""
    baggage = next(
        (b for doc in ctx.baggage_docs for b in doc.baggages if b.get("Id") == ref),
        None,
    )
    if baggage is None or (baggage.get("TargetPath") or "").lower() != "toolb":
        return False
    if (baggage.get("Name") or "").lower() not in _POWER_TOOLS:
        return False
    info = baggage.find("FileInfo")
    version = info.get("Version") if info is not None else None
    if not version:
        return False
    return dotnet_version(version) < _FIXED_POWER_TOOL


_LOW_ACCESS = frozenset(
    [
        (4, 49164, 1), (4, 5204, 17), (4, 5206, 17), (4, 5220, 17), (4, 5222, 17),
        (4, 49164, 17), (4, 49164, 18), (4, 49164, 19), (4, 49164, 2), (4, 49164, 20),
        (6, 49164, 1), (6, 5810, 17), (6, 5811, 17), (6, 5816, 17), (6, 49164, 17),
        (6, 49164, 18), (6, 49164, 19), (6, 49164, 2), (6, 49164, 20), (8, 49164, 1),
        (8, 49164, 17), (8, 49164, 18), (8, 49164, 19), (8, 49164, 2), (8, 49164, 20),
        (9, 49164, 17), (9, 49164, 18), (9, 49164, 19), (10, 49164, 1), (10, 49164, 17),
        (10, 49164, 19), (10, 49164, 2), (10, 49164, 20), (30, 49164, 16), (30, 49164, 32),
        (244, 49168, 1),
    ]
)  # fmt: skip

_PROXY_OBJECTS = frozenset(
    [
        (6, 20494, 1), (6, 20497, 1), (6, 22785, 1), (8, 20494, 1), (8, 20497, 1),
        (8, 22785, 1), (4, 20494, 1), (4, 20497, 1), (4, 22785, 1), (2, 14849, 32),
        (7, 20494, 16), (244, 20494, 1), (244, 20497, 1), (244, 22785, 1), (9, 20494, 16),
        (12, 25137, 16), (94, 20494, 1),
    ]
)  # fmt: skip

_UNEXPECTED_TYPE_SIZE = frozenset(
    [("M-0001", 32515, 1, None), ("M-0001", 32778, 1, None), ("M-0029", 57344, 16, "M-0001"),
     ("M-004C", 24705, 1, "M-0001")]
)  # fmt: skip

_CO_SIZES = {
    0: "1 Bit",
    1: "2 Bit",
    2: "3 Bit",
    3: "4 Bit",
    4: "5 Bit",
    5: "6 Bit",
    6: "7 Bit",
    7: "1 Byte",
    8: "2 Bytes",
    9: "3 Bytes",
    10: "4 Bytes",
    11: "6 Bytes",
    12: "8 Bytes",
    13: "10 Bytes",
    14: "14 Bytes",
    16: "7 Bytes",
    17: "9 Bytes",
    18: "11 Bytes",
    19: "12 Bytes",
}
# an integer as .NET parses it with NumberStyles.Integer
_DOTNET_INT = re.compile(r"[ \t\n\v\f\r]*[+-]?[0-9]+[ \t\n\v\f\r]*")

_PRIORITIES = {0: "Alert", 1: "High", 2: "Alert"}
_ACCESS = {0: "None", 1: "Read", 2: "ReadWrite"}


# --- mask dependent values ---------------------------------------------------------------------


def mask_number(mask_id: str | None) -> int:
    if not mask_id:
        return 0
    return int(mask_id[3:7], 16)


def load_procedure_style(program_style: int, mask_id: str) -> str:
    if mask_id in ("MV-0700", "MV-0701", "MV-0705", "MV-5705"):
        return "ProductProcedure"
    return {1: "ProductProcedure", 2: "MergedProcedure"}.get(
        program_style, "DefaultProcedure"
    )


def first_app_object_index(mask_id: str) -> int:
    if mask_id in ("MV-0020", "MV-0021", "MV-0025", "MV-0300-01000000000000000000"):
        return 4
    if mask_id in ("MV-0700", "MV-0701", "MV-0705", "MV-5705"):
        return 5
    if mask_id in ("MV-07B0", "MV-17B0"):
        return 6
    return 0


_BCU1 = frozenset({0x10, 0x11, 0x12, 0x13, 0x1011, 0x1012, 0x1013})
_BCU2 = frozenset({0x20, 0x21, 0x25})
_M112 = frozenset({0x700, 0x701, 0x705, 0x5705})
_COUPLER = frozenset({0x900, 0x910, 0x911, 0x912, 0x91A})


def default_address_table_address(mask: int) -> int:
    if mask in (0, 1):
        return 0
    if mask in _BCU1 or mask in _BCU2:
        return 0x116
    if mask in _M112:
        return 0x4000
    return 0


def default_segment_base(mask: int) -> int:
    if mask in (0, 1):
        return 0
    if mask in _BCU1 or mask in _BCU2 or mask in _COUPLER:
        return 0x100
    if mask in _M112 or mask == 0x1900:
        return 0x4000
    return 0


def default_segment_size(mask: int) -> int:
    if mask in (0, 1):
        return 0x10000
    if mask in _BCU1 or mask in _COUPLER:
        return 0x100
    if mask in _BCU2:
        return 880
    if mask in _M112 or mask == 0x1900:
        return 30208
    return 0x10000


def _eeprom_patch(mask: int) -> bool:
    return mask in _BCU1 or mask in _BCU2 or mask in _COUPLER or mask == 0x1900


# --- object model ------------------------------------------------------------------------------


@dataclass(eq=False)
class Segment:
    """An absolute (``address``) or relative (``offset``, ``lsm``) code segment."""

    relative: bool
    address: int
    size: int
    user_memory: bool = False
    lsm: int = 0
    data: bytes | None = None
    mask: bytes | None = None
    ident: str = ""


@dataclass(eq=False)
class ParamType:
    ident: str
    name: str
    kind: str  # none, number, text, restriction
    size: int = 0
    signed: bool = False
    minimum: int = 0
    maximum: int = 0
    ui_hint: str | None = None
    binary: bool = False
    enums: list[El] = field(default_factory=lambda: [])
    description: str | None = None


@dataclass(eq=False)
class Memory:
    segment: Segment
    offset: int
    bit_offset: int


@dataclass(eq=False)
class Property:
    object_index: int | None
    object_type: int | None
    occurrence: int
    property_id: int
    offset: int
    bit_offset: int


@dataclass(eq=False)
class Param:
    number: int
    name: str
    text: str
    type_id: str | None
    access: str = "ReadWrite"
    value: str | None = None
    item: Memory | Property | None = None
    patch_always: bool = False
    dbid: int = 0

    ident: str = ""


@dataclass(eq=False)
class UnionParam:
    number: int
    name: str
    text: str
    type_id: str | None
    access: str
    value: str | None
    offset: int
    bit_offset: int
    default: bool
    union: Union | None = None
    ident: str = ""


@dataclass(eq=False)
class Union:
    size: int
    item: Memory | Property
    params: list[UnionParam] = field(default_factory=lambda: [])


@dataclass(eq=False)
class ParamRef:
    number: int
    tag: str
    ident: str = ""
    ref: str = ""
    display_order: int | None = None
    access: str | None = None
    name: str | None = None
    text: str | None = None
    value: str | None = None
    target: Param | None = None
    dbid: int = 0


@dataclass(eq=False)
class ComObject:
    number: int
    el: El
    dpts: list[str] = field(default_factory=lambda: [])


@dataclass(eq=False)
class ComObjectRef:
    number: int
    el: El
    dpts: list[str] = field(default_factory=lambda: [])


@dataclass(slots=True)
class Condition:
    parent: int
    value: str | None


# --- the converter -----------------------------------------------------------------------------


class AppConverter:
    """Converts one ``application_program`` row."""

    def __init__(self, ctx: Context, row: Row) -> None:
        self.ctx = ctx
        self.row = row
        self.tr = Translations()
        self.manufacturer = ctx.manufacturer_ref(row["MANUFACTURER_ID"])
        self.manu_number = int(row["MANUFACTURER_ID"] or 0)
        self.attrs: dict[str, str] = {}
        self.app_id = ""
        self.mask = ""
        self.style = "DefaultProcedure"
        self.broken = False
        self.download_info_incomplete = False
        self.program_type = "ApplicationProgram"
        self.number = 0
        self.version = 0
        self.original: str | None = None
        self.language = ctx.default_language
        # static part
        self.abs_segments: list[Segment] = []
        self.rel_segments: list[Segment] = []
        self.types: list[ParamType] = []
        self.types_by_id: dict[str, ParamType] = {}
        self.strange_types: list[tuple[ParamType, int]] = []
        self.parameters: list[Param | Union] = []
        self.refs: list[ParamRef] = []
        self.union_of: dict[Param, UnionParam] = {}
        self.page_params: list[ParamRef] = []
        self.conditions: dict[object, Condition] = {}
        self.has_co_table = False
        self.co_table: El | None = None
        self.com_objects: list[ComObject] = []
        self.co_refs: list[ComObjectRef] = []
        self.address_table: El | None = None
        self.association_table: El | None = None
        self.no_assoc_address = False
        self.no_co_address = False
        self.fixups: list[El] = []
        self.calculations: list[El] = []
        self.load_procedures: list[El] = []
        self.extension: El | None = None
        self.options = El("Options")
        self.dynamic: list[El] = []
        self.empty_enum_types: dict[str, bool] = {}
        self.s19: list[Row] | None = None
        self.lcp = LoadControls(0)
        self._param_rows: list[Row] = []
        self._unique = 0
        # dynamic part
        self._refs_by_id: dict[str, ParamRef] = {}
        self._channel_chooses: set[int] = set()
        self._pages: list[ParamRef] = []
        self._block_of: dict[ParamRef, El] = {}
        self._cond_lists: dict[ParamRef, list[Condition] | None] = {}
        self._co_of_ref: dict[ParamRef, list[ComObjectRef]] = {}
        self._co_conds: dict[ComObjectRef, list[Condition]] = {}

    # ---- top level

    def convert(self) -> tuple[str, bytes] | None:
        ctx = self.ctx
        row = self.row
        if (
            self.manufacturer == "M-0001"
            and row["DEVICE_TYPE"] is not None
            and row["PROGRAM_VERSION"] is not None
            and not _DOTNET_INT.fullmatch(str(row["PROGRAM_VERSION"]))
        ):
            # the converter reads these program versions as integers before converting
            raise ValueError(
                f"Input string was not in a correct format: {row['PROGRAM_VERSION']}"
            )
        self.language = ctx.default_language
        name = from_db(self.language, row.str("PROGRAM_NAME"))
        self.attrs["Name"] = name
        if row["DEVICE_TYPE"] is not None:
            self.number = int(row["DEVICE_TYPE"] or 0) & 0xFFFF
        self.version = int((row.str("PROGRAM_VERSION") or "0").split(".")[0]) & 0xFF
        original = None
        if row["ORIGINAL_MANUFACTURER_ID"] is not None:
            original = f"M-{int(row['ORIGINAL_MANUFACTURER_ID'] or 0):04X}"
            manu = str(row["MANUFACTURER_ID"])
            if original == "M-003C" and manu == "126":
                original = None
            if original == "M-0044" and manu == "201":
                original = None
            if original in ("M-00DC", "M-00FA"):
                original = None
            if original is not None and not ctx.known_manufacturer(original):
                ctx.errors.append(
                    f"UnknownManufacturerId: OriginalManufacturer {original}"
                )
        program_type = row["PROGRAM_TYPE"]
        if program_type is not None:
            if int(program_type) == 1:
                self.program_type = "PeiProgram"
            elif int(program_type) != 0:
                raise ConversionError("Invalid ProgramType in export file!")
        if (self.manu_number, self.number, self.version) == (129, 65293, 17):
            self.program_type = "ApplicationProgram"
        if original == "M-003C" and self.manufacturer == "M-007E":
            original = None
        self.original = original
        self.app_id = application_id(
            self.manufacturer, self.number, self.version, original
        )
        ctx.add_refid(int(row["PROGRAM_ID"] or 0), self.app_id)
        mask = self._retrieve_mask_version()
        if mask is None:
            ctx.warnings.append(
                f"AppPrgHasNoMaskVersion: In the export file, no MASK_ID was given for "
                f"ApplicationProgram {self.app_id}. MaskVersion was set to 'MV-0001'"
            )
            self.mask = "MV-0001"
            self.broken = True
        else:
            self.mask = f"MV-{mask:04X}"
        if self.mask not in ctx.master.mask_versions:
            raise ConversionError(f"MaskVersion {self.mask}")
        siemens_9014 = (
            self.manufacturer == "M-0001" and self.number == 36884 and self.version == 1
        )
        if siemens_9014 and self.mask == "MV-0701":
            self.mask = "MV-0020"
        if self.manufacturer == "M-000B" and self.number == 1284 and self.version == 16:
            self.mask = "MV-0012"
        if self.mask == "MV-0300":
            self._system300_mask()
        if row["MINETSVERSION"] is not None:
            self.attrs["MinEtsVersion"] = _min_version(int(row["MINETSVERSION"] or 0))
        self.attrs["HelpFile"] = from_db(self.language, row.str("HELP_FILE_NAME"))
        if row["CONTEXT_ID"] is not None:
            self.attrs["HelpTopic"] = str(int(row["CONTEXT_ID"] or 0) & 0xFFFFFFFF)
        self.attrs["PeiType"] = str(
            int(row["PEI_TYPE"] or 0) if row["PEI_TYPE"] is not None else 0
        )
        linkable = row["LINKABLE"] is not None and int(row["LINKABLE"] or 0) == 1
        self.attrs["Linkable"] = boolean(linkable)
        if self.mask == "MV-091A" or (mask_number(self.mask) & 0xF000) == 0x5000:
            self.attrs["IPConfig"] = "Custom"
            ctx.ip_programs.add(self.app_id)
        program_style = (
            int(row["PROGRAM_STYLE"] or 0) if row["PROGRAM_STYLE"] is not None else 0
        )
        self.style = load_procedure_style(program_style, self.mask)
        mv = ctx.master.mask_versions.get(self.mask)
        if mv is None:
            raise ConversionError(f"MaskVersion {self.mask}")
        self.lcp = LoadControls(mv.first_app_object_idx)
        s19 = self._s19()
        if s19:
            if self.style == "DefaultProcedure":
                self.style = "ProductProcedure"
        elif self.style == "ProductProcedure" and row["EEPROM_DATA"] is not None:
            self.style = "DefaultProcedure"
        dynamic = row["DYNAMIC_MANAGEMENT"]
        self.attrs["DynamicTableManagement"] = boolean(
            dynamic is not None and int(dynamic or 0) == 1
        )
        if not ctx.known_manufacturer(self.manufacturer):
            ctx.errors.append(f"UnknownManufacturerId: {self.manufacturer}")
            return None
        self._static()
        self._dynamic()
        app_el = El("ApplicationProgram", {"Id": self.app_id})
        translated = translate_entity(
            ctx,
            self.tr,
            int(row["PROGRAM_ID"] or 0),
            "ApplicationProgram",
            app_el,
            self.app_id,
            self.app_id,
        )
        # descriptions and help files are only translated for a program with texts
        if translated is not None:
            descriptions = ctx.select(
                "program_description", "PROGRAM_ID", row["PROGRAM_ID"]
            )
            if descriptions:
                translate_descriptions(
                    ctx, self.tr, app_el, self.app_id, descriptions, "TEXT"
                )
        for name in ("Name", "VisibleDescription"):
            if name in app_el.attrs:
                self.attrs[name] = app_el[name]
        if translated is not None:
            self._help_file_translations()
        return self.app_id, self._to_bytes()

    def _retrieve_mask_version(self) -> int | None:
        row = self.row
        if row["MASK_ID"] is not None:
            mask = self.ctx.first("mask", "MASK_ID", row["MASK_ID"])
            if mask is None:
                return None
            return int(mask["MASK_VERSION"] or 0)
        for manu, number, version, mask in _MASK_EXCEPTIONS:
            if (
                manu == self.manu_number
                and number == self.number
                and version == self.version
            ):
                return mask
        return None

    def _system300_mask(self) -> None:
        found = False
        bcu = 0
        for p2p in self.ctx.select(
            "product_to_program", "PROGRAM_ID", self.row["PROGRAM_ID"]
        ):
            if p2p["PRODUCT_ID"] is None:
                continue
            for hw in self.ctx.select("hw_product", "PRODUCT_ID", p2p["PRODUCT_ID"]):
                if hw["BCU_TYPE_NUMBER"] is None:
                    continue
                value = int(hw["BCU_TYPE_NUMBER"] or 0)
                if found:
                    if value != bcu:
                        self.ctx.errors.append(
                            f"DifferentBcuTypesInHardwareForSystem300Application: {self.app_id}"
                        )
                else:
                    bcu = value
                    found = True
        if not found:
            self.mask = "MV-0300-01000001000000000000"
        elif bcu == 3:
            self.mask = "MV-0300-01000000000000000000"
        elif bcu == 4:
            self.mask = "MV-0300-01000001000000000000"
        else:
            self.ctx.errors.append(
                f"UnexpectedBcuTypeInHardwareForSystem300Application: {self.app_id}"
            )

    def _s19(self) -> list[Row]:
        if self.s19 is None:
            rows = self.ctx.select("s19_block", "PROGRAM_ID", self.row["PROGRAM_ID"])
            self.s19 = sort_rows(rows, "MERGE_ID", "BLOCK_NUMBER")
        return self.s19

    def _help_file_translations(self) -> None:
        rows = self.ctx.select("help_file", "ENTITY_ID", self.row["PROGRAM_ID"])
        if not rows:
            return
        from .text import culture_name

        for r in sort_rows(rows, "LANGUAGE_ID"):
            text = r.str("HELP_FILE_NAME") or ""
            if not text:
                continue
            language = culture_name(int(r["LANGUAGE_ID"] or 0))
            if language is None:
                continue
            value = from_db(language, from_db(language, text))
            self.tr.element(language, self.app_id, self.app_id)["HelpFile"] = value
            if language == self.ctx.default_language:
                self.attrs["HelpFile"] = value

    # ---- static part

    def _has_tables(self) -> bool:
        mask = mask_number(self.mask)
        if mask == 0 or self.program_type == "PeiProgram":
            return False
        return (mask & 0xFF0) not in (0x900, 0x910)

    def _static(self) -> None:
        row = self.row
        if self._has_tables():
            self.address_table = El("AddressTable")
            size = (
                int(row["ADDRESS_TAB_SIZE"] or 0)
                if row["ADDRESS_TAB_SIZE"] is not None
                else 0
            )
            self.address_table["MaxEntries"] = str((size - 3) // 2 if size >= 3 else 0)
            self.association_table = El("AssociationTable")
            size = (
                int(row["ASSOCTAB_SIZE"] or 0)
                if row["ASSOCTAB_SIZE"] is not None
                else 0
            )
            self.association_table["MaxEntries"] = str(
                (size - 1) // 2 if size >= 1 else 0
            )
        if self._has_tables() or mask_number(self.mask) == 0:
            self.co_table = El("ComObjectTable")
        self._load_controls()
        self._parameter_types()
        if self.program_type != "PeiProgram":
            self._com_objects()
            if self.mask:
                self._standard_code_segment()
        self._parameters()
        self._correct_empty_enums()
        self._parameter_calculations()
        self._options()
        if self.mask:
            self._fixups()
        self._extensions()
        if self.mask == "MV-0300-01000000000000000000":
            if self.extension is None:
                self.extension = El("Extension")
            self.extension["EtsDownloadPlugin"] = (
                "{2C37063B-67BC-4CAD-9BCA-3BF516DA7995}"
            )
        self._co_dpts_from_device()
        if self.extension is not None:
            plugin = self.extension.get("EtsDownloadPlugin")
            if plugin and plugin.upper() == "{15053D1A-12DE-462A-AEE9-E967A27EFF5B}":
                self.options["ParameterByteOrder"] = "LittleEndian"
                self.options["PreferPartialDownloadIfApplicationLoaded"] = "true"
        if self.download_info_incomplete:
            self.ctx.warnings.append(
                f"ApplicationProgramHasIncompleteDownloadInfo: The application program {self.app_id}"
                f" had an invalid LoadControl. The LoadControl was removed and the flag"
                f" 'DownloadInfoIncomplete' has been set. Number of CoRefs: {len(self.co_refs)},"
                f" Number of ParameterRefs: {len(self.refs)}"
            )

    # load controls and segments

    def _load_controls(self) -> None:
        if not self.ctx.has_table("s19_block"):
            return
        s19 = self._s19()
        current: int = -1
        proc: El | None = None
        for r in s19:
            merge = int(r["MERGE_ID"] or 0) if r["MERGE_ID"] is not None else 0
            if merge != current or proc is None:
                proc = El("LoadProcedure")
                if merge > 0:
                    proc["MergeId"] = str(merge)
                self.load_procedures.append(proc)
                current = merge
            self._load_control(proc, r)
        if not self.load_procedures:
            self.style = "DefaultProcedure"
        elif self.load_procedures[0].get("MergeId") not in (None, "0"):
            self.style = "MergedProcedure"

    def _load_control(self, proc: El, r: Row) -> None:
        applies = self.lcp.proc_type(
            int(r["PROC_MASK"] or 0) if r["PROC_MASK"] is not None else 0
        )
        record = _blob(r["RECORD"])
        if record is None:
            self.ctx.errors.append(
                f"S19EntryWithNullRecord: {self.app_id} DBID: {r['BLOCK_ID']}"
            )
            return
        if len(record) != 16:
            self.ctx.errors.append(f"S19RecordIsNot16Bytes: {self.app_id}")
            return
        el = self.lcp.process(record, applies)
        if el is None:
            self.download_info_incomplete = True
        self._take_lcp_errors()
        if el is None:
            return
        proc.children.append(el)
        data = _blob(r["BLOCK_DATA"])
        mask = _blob(r["BLOCK_MASK"])
        if el.tag == "LdCtrlAbsSegment":
            seg = self._abs_segment_for(int(el["Address"]), int(el["Size"]))
            self._add_data(seg, data)
            self._add_mask(seg, mask)
        elif el.tag == "LdCtrlRelSegment":
            lsm = int(el["LsmIdx"]) if "LsmIdx" in el.attrs else None
            seg = self._rel_segment_for(lsm, int(el["Size"]))
            self._add_data(seg, data)
            if mask is not None:
                fixed = bytes(0xFF if b else 0 for b in mask)
                if _longer(fixed, seg.mask):
                    seg.mask = fixed

    def _take_lcp_errors(self) -> None:
        for e in self.lcp.errors:
            self.ctx.errors.append(f"{self.app_id}: {e}")
        self.lcp.errors.clear()

    def _abs_segment_for(self, address: int, size: int) -> Segment:
        for seg in self.abs_segments:
            if seg.address == address:
                return seg
        seg = Segment(False, address, size)
        self._add_abs(seg)
        return seg

    def _rel_segment_for(self, lsm: int | None, size: int) -> Segment:
        if lsm is not None:
            for seg in self.rel_segments:
                if seg.lsm == lsm:
                    return seg
        seg = Segment(True, 0, size, lsm=lsm or 0)
        seg.ident = f"{self.app_id}_RS-{seg.lsm:02X}-{seg.address:05X}"
        self.rel_segments.append(seg)
        return seg

    def _add_abs(self, seg: Segment) -> None:
        if seg.user_memory:
            seg.ident = f"{self.app_id}_AS-U{seg.address:05X}"
        else:
            seg.ident = f"{self.app_id}_AS-{seg.address:04X}"
        self.abs_segments.append(seg)

    @staticmethod
    def _add_data(seg: Segment, data: bytes | None) -> None:
        if _longer(data, seg.data):
            seg.data = data

    @staticmethod
    def _add_mask(seg: Segment, mask: bytes | None) -> None:
        if mask is not None:
            mask = bytes(0xFF if b else 0 for b in mask)
        if not _longer(mask, seg.mask):
            return
        assert mask is not None
        seg.mask = mask
        if seg.data is not None and len(mask) < len(seg.data):
            seg.mask = mask + bytes(len(seg.data) - len(mask))

    def _find_and_enlarge(
        self, address: int, size_in_bit: int, bit_offset: int, user_memory: bool
    ) -> Segment | None:
        end = address + (size_in_bit + bit_offset + 7) // 8 - 1
        best: Segment | None = None
        for seg in self.abs_segments:
            if seg.user_memory != user_memory or seg.address > address:
                continue
            if seg.address + seg.size <= end:
                if best is None or seg.address + seg.size > best.address + best.size:
                    best = seg
            else:
                return seg
        if best is None or best.address + best.size + 8 < address:
            return None
        grow = end - best.address - best.size + 1
        best.size += grow
        if best.data is not None:
            best.data = best.data + bytes(best.size - len(best.data))
        if best.mask is not None:
            best.mask = best.mask + bytes(best.size - len(best.mask))
        return best

    def _abs_segment(
        self, address: int, bit_offset: int, size_in_bit: int, user_memory: bool
    ) -> Segment:
        seg = self._find_and_enlarge(address, size_in_bit, bit_offset, user_memory)
        if seg is not None:
            return seg
        end = address + (size_in_bit + bit_offset + 7) // 8 - 1
        seg = Segment(False, address, end - address + 1, user_memory)
        self._add_abs(seg)
        return seg

    def _rel_segment(self, base: int, bit_offset: int, size_in_bit: int) -> Segment:
        end = base + (size_in_bit + bit_offset + 7) // 8 - 1
        best: Segment | None = None
        for seg in self.rel_segments:
            if seg.address > base:
                continue
            if seg.address + seg.size > end:
                return seg
            if best is None or seg.address + seg.size > best.address + best.size:
                best = seg
        if best is not None and best.address + best.size + 8 >= base:
            best.size += end - best.address + 1
            return best
        seg = Segment(True, base, end - base + 1)
        seg.ident = f"{self.app_id}_RS-{seg.lsm:02X}-{seg.address:05X}"
        self.rel_segments.append(seg)
        return seg

    def _standard_code_segment(self) -> None:
        mask = mask_number(self.mask)
        row = self.row
        if self.style == "DefaultProcedure":
            if (mask & 0xF0) != 0xB0:
                self._default_segment()
            return
        if (mask & 0xFF0) in (0x900, 0x910):
            return
        if (mask & 0xFF0) in (0x20, 0x700) and self.address_table is not None:
            address = default_address_table_address(mask)
            size = int(self.address_table["MaxEntries"]) * 2 + 3
            seg = self._find_and_enlarge(address, 8, 0, False)
            if seg is None:
                seg = Segment(False, address, size)
                self._add_abs(seg)
            self.address_table["CodeSegment"] = seg.ident
            self.address_table["Offset"] = str(address - seg.address)
        if row["ASSOCTAB_ADDRESS"] is not None and self.association_table is not None:
            address = int(row["ASSOCTAB_ADDRESS"] or 0)
            size = int(self.association_table["MaxEntries"]) * 2 + 1
            seg = self._find_and_enlarge(address, 8, 0, False)
            if seg is None:
                seg = Segment(False, address, size)
                self._add_abs(seg)
            self.association_table["CodeSegment"] = seg.ident
            self.association_table["Offset"] = str(address - seg.address)
        if row["COMMSTAB_ADDRESS"] is None or self.co_table is None:
            return
        address = int(row["COMMSTAB_ADDRESS"] or 0)
        last = self.com_objects[-1].number if self.com_objects else 0
        size = (last + 1) * 2 + 1
        seg = self._find_and_enlarge(address, 8, 0, False)
        if seg is None:
            seg = Segment(False, address, size)
            self._add_abs(seg)
        self.co_table["CodeSegment"] = seg.ident
        self.co_table["Offset"] = str(address - seg.address)

    def _default_segment(self) -> None:
        mask = mask_number(self.mask)
        row = self.row
        base = default_segment_base(mask)
        size = default_segment_size(mask)
        eeprom = _blob(row["EEPROM_DATA"])
        if eeprom is not None and len(eeprom) > size:
            size = len(eeprom)
        seg = Segment(False, base, size)
        self._add_abs(seg)
        data: bytes | None = None
        mask_bytes: bytes | None = None
        if eeprom is not None:
            buf = bytearray(eeprom)
            mbuf = bytearray(b"\xff" * len(buf))
            if _eeprom_patch(mask):
                if len(buf) > 22:
                    buf[22] = 1
                for offset, count in ((1, 3), (23, 2)):
                    i = offset
                    while count and i < len(mbuf):
                        mbuf[i] = 0
                        i += 1
                        count -= 1
            data = bytes(buf)
            mask_bytes = bytes(mbuf)
        self._add_data(seg, data)
        self._add_mask(seg, mask_bytes)
        if not self._has_tables():
            return
        assert self.address_table is not None and self.association_table is not None
        self.address_table["CodeSegment"] = seg.ident
        self.address_table["Offset"] = str(
            (default_address_table_address(mask) - base) & 0xFFFFFFFF
        )
        self.association_table["CodeSegment"] = seg.ident
        if row["ASSOCTAB_ADDRESS"] is not None:
            assoc = int(row["ASSOCTAB_ADDRESS"] or 0)
            if assoc >= base:
                self.association_table["Offset"] = str(assoc - base)
            else:
                del self.association_table.attrs["CodeSegment"]
                self.no_assoc_address = True
        if self.co_table is None:
            return
        self.co_table["CodeSegment"] = seg.ident
        if row["COMMSTAB_ADDRESS"] is not None:
            co = int(row["COMMSTAB_ADDRESS"] or 0)
            if co < base:
                del self.co_table.attrs["CodeSegment"]
                self.no_co_address = True
            else:
                self.co_table["Offset"] = str(co - base)

    # parameter types

    def _parameter_types(self) -> None:
        ctx = self.ctx
        if not ctx.has_table("parameter_type"):
            return
        values_table = ctx.has_table("parameter_list_of_values")
        for r in ctx.select("parameter_type", "PROGRAM_ID", self.row["PROGRAM_ID"]):
            name = r.str("PARAMETER_TYPE_NAME") or ""
            existing = next((t for t in self.types if t.name == name), None)
            old_restriction: list[El] | None = None
            if existing is None:
                pt = ParamType(f"{self.app_id}_PT-{fragment(name)}", name, "none")
                if r["PARAMETER_TYPE_DESCRIPTION"] is not None:
                    pt.description = from_db(None, r.str("PARAMETER_TYPE_DESCRIPTION"))
                self.types.append(pt)
                self.types_by_id[pt.ident] = pt
            else:
                # a redefinition keeps the first row's description
                pt = existing
                if pt.kind == "restriction":
                    old_restriction = pt.enums
            ptid = int(r["PARAMETER_TYPE_ID"] or 0)
            ctx.add_refid(ptid, pt.ident)
            atomic = (
                int(r["ATOMIC_TYPE_NUMBER"] or 0)
                if r["ATOMIC_TYPE_NUMBER"] is not None
                else 0
            )
            size = (
                int(r["PARAMETER_TYPE_SIZE"] or 0)
                if r["PARAMETER_TYPE_SIZE"] is not None
                else 0
            )
            if atomic in (1, 2):
                unsigned = atomic == 1
                pt.kind = "number"
                pt.size = size
                pt.signed = not unsigned
                pt.enums = []
                minimum = 0 if unsigned else -(2 ** (size - 1))
                maximum = (2**size - 1) if unsigned else (2 ** (size - 1) - 1)
                if r["PARAMETER_MINIMUM_VALUE"] is not None:
                    v = int(r["PARAMETER_MINIMUM_VALUE"] or 0)
                    minimum = v & 0xFFFFFFFF if unsigned else v
                if r["PARAMETER_MAXIMUM_VALUE"] is not None:
                    v = int(r["PARAMETER_MAXIMUM_VALUE"] or 0)
                    maximum = v & 0xFFFFFFFF if unsigned else v
                pt.minimum = minimum
                pt.maximum = maximum
                pt.ui_hint = None
                if r["PARAMETER_UI_HINT"] is not None:
                    hint = r.str("PARAMETER_UI_HINT") or ""
                    if not hint:
                        pt.ui_hint = "CheckBox"
                    elif hint.upper() == "SLIDER":
                        pt.ui_hint = "Slider"
            elif atomic == 3:
                pt.kind = "text"
                pt.size = size
                pt.enums = []
            elif atomic in (4, 5):
                pt.kind = "restriction"
                pt.size = size
                pt.binary = atomic == 5
                enums: list[El] = []
                if old_restriction is not None:
                    enums.extend(old_restriction)
                pt.enums = enums
                if values_table:
                    rows = ctx.select(
                        "parameter_list_of_values", "PARAMETER_TYPE_ID", ptid
                    )
                    for v in sort_rows(rows, "DISPLAY_ORDER", "REAL_VALUE"):
                        self._enumeration(pt, v, size)
                else:
                    self.empty_enum_types.setdefault(pt.ident, False)
                if pt.size == 0:
                    self.broken = True
                    highest = max((int(e["Value"]) for e in pt.enums), default=0)
                    pt.size = highest.bit_length()
            else:
                pt.kind = "none"
                pt.enums = []
                if size != 0:
                    self.strange_types.append((pt, size))

    def _enumeration(self, pt: ParamType, v: Row, size: int) -> None:
        el = El("Enumeration")
        value = (
            int(v["REAL_VALUE"] or 0) & 0xFFFFFFFF if v["REAL_VALUE"] is not None else 0
        )
        el["Value"] = str(value)
        if v["DISPLAY_ORDER"] is not None:
            el["DisplayOrder"] = str(int(v["DISPLAY_ORDER"] or 0))
        el["Text"] = from_db(self.language, v.str("DISPLAYED_VALUE"))
        if pt.binary:
            data = _blob(v["BINARY_VALUE"])
            want = (size + 7) // 8
            have = len(data) if data is not None else 0
            if want != have:
                data = (data or b"")[:want] + bytes(max(0, want - have))
            if data is not None:
                el["BinaryValue"] = base64.b64encode(data).decode("ascii")
        for old in pt.enums:
            if old["Value"] == el["Value"]:
                pt.enums.remove(old)
                break
        el["Id"] = f"{pt.ident}_EN-{value}"
        pt.enums.append(el)
        translate_entity(
            self.ctx,
            self.tr,
            int(v["PARAMETER_VALUE_ID"] or 0),
            "ParameterTypeRestrictionEnumeration",
            el,
            self.app_id,
            el["Id"],
        )

    # communication objects

    def _co_texts(self, object_id: int) -> tuple[str | None, str | None]:
        lcid = lcid_of(self.ctx.default_language)
        text: str | None = None
        function: str | None = None
        from .text import culture_name

        for t in self.ctx.text_attrs(object_id):
            if not t.text or t.lcid != lcid:
                continue
            language = culture_name(t.lcid)
            if language is None:
                continue
            if t.kind == "ComObjectRef" and t.attribute == "Text":
                text = from_db(language, t.text)
            elif t.kind == "ComObjectRef" and t.attribute == "FunctionText":
                function = from_db(language, t.text)
        return text, function

    def _co_size(self, value: int) -> str:
        if value == 15:
            if self.mask in ("MV-07B0", "MV-17B0"):
                return "5 Bytes"
            return "LegacyVarData"
        size = _CO_SIZES.get(value)
        if size is None:
            self.ctx.errors.append(f"ComObjIllegalObjectType: {self.app_id} {value}")
            return "1 Bit"
        return size

    def _dpt(self, r: Row) -> str | None:
        if r["EIB_DATA_TYPE_CODE"] is None:
            return None
        sub = (
            int(r["EIB_DATA_SUBTYPE_CODE"] or 0)
            if r["EIB_DATA_SUBTYPE_CODE"] is not None
            else -1
        )
        dpt = self.ctx.master.datapoint(int(r["EIB_DATA_TYPE_CODE"] or 0), sub)
        if dpt is None:
            raise ConversionError(f"eibDataTypeCode {r['EIB_DATA_TYPE_CODE']}")
        return dpt

    @staticmethod
    def _flag(r: Row, column: str) -> str:
        v = r[column]
        return "Enabled" if v is not None and int(v) != 0 else "Disabled"

    def _com_objects(self) -> None:
        ctx = self.ctx
        if not ctx.has_table("communication_object"):
            return
        rows = sort_rows(
            ctx.select("communication_object", "PROGRAM_ID", self.row["PROGRAM_ID"]),
            "OBJECT_NUMBER",
            "OBJECT_UNIQUE_NUMBER",
        )
        if not rows or self.co_table is None:
            return
        by_number: dict[int, ComObject] = {}
        current: ComObject | None = None
        current_type = 0
        flags = (
            ("ReadFlag", "OBJECT_READENABLED"),
            ("WriteFlag", "OBJECT_WRITEENABLED"),
            ("CommunicationFlag", "OBJECT_COMMENABLED"),
            ("TransmitFlag", "OBJECT_TRANSENABLED"),
            ("UpdateFlag", "OBJECT_UPDATEENABLED"),
            ("ReadOnInitFlag", "OBJECT_READONINITENABLED"),
        )
        for r in rows:
            number = (
                int(r["OBJECT_NUMBER"] or 0) if r["OBJECT_NUMBER"] is not None else 0
            )
            if current is not None and current.number == number:
                kind = int(r["OBJECT_TYPE"] or 0)
                if kind > current_type:
                    current_type = kind
                    current.el["ObjectSize"] = self._co_size(kind)
                    dpt = self._dpt(r)
                    current.dpts = [dpt] if dpt else []
                continue
            current_type = int(r["OBJECT_TYPE"] or 0)
            object_id = int(r["OBJECT_ID"] or 0)
            text, function = self._co_texts(object_id)
            if text is None:
                text = from_db(self.language, r.str("OBJECT_NAME"))
            if function is None:
                function = from_db(self.language, r.str("OBJECT_FUNCTION"))
            el = El("ComObject")
            el["Name"] = from_db(self.language, r.str("OBJECT_NAME"))
            el["Text"] = text
            el["Number"] = str(number)
            el["FunctionText"] = function
            priority = _PRIORITIES.get(int(r["OBJECT_PRIORITY"] or 0), "Low")
            if priority != "Low":
                el["Priority"] = priority
            el["ObjectSize"] = self._co_size(current_type)
            for attr, column in flags:
                el[attr] = self._flag(r, column)
            el["Id"] = f"{self.app_id}_O-{number}"
            co = ComObject(number, el)
            dpt = self._dpt(r)
            if dpt:
                co.dpts.append(dpt)
            current = co
            self.com_objects.append(co)
            by_number[number] = co
        translated: dict[ComObject, list[tuple[str, str, str]]] = {}
        for r in rows:
            number = (
                int(r["OBJECT_NUMBER"] or 0) if r["OBJECT_NUMBER"] is not None else 0
            )
            co = by_number[number]
            unique = int(r["OBJECT_UNIQUE_NUMBER"] or 0)
            ref = El("ComObjectRef")
            ref["Id"] = f"{co.el['Id']}_R-{unique}"
            ref["RefId"] = co.el["Id"]
            ref["Tag"] = str(unique)
            object_id = int(r["OBJECT_ID"] or 0)
            text, function = self._co_texts(object_id)
            if text is None:
                text = from_db(self.language, r.str("OBJECT_NAME"))
            if function is None:
                function = from_db(self.language, r.str("OBJECT_FUNCTION"))
            cref = ComObjectRef(unique, ref)
            dpt: str | None = None
            has_dpt = r["EIB_DATA_TYPE_CODE"] is not None
            if has_dpt:
                dpt = self._dpt(r)
            if text != co.el["Text"]:
                ref["Text"] = text
            if function != co.el["FunctionText"]:
                ref["FunctionText"] = function
            size = self._co_size(int(r["OBJECT_TYPE"] or 0))
            if size != co.el["ObjectSize"]:
                ref["ObjectSize"] = size
            if (not co.dpts and has_dpt) or (
                len(co.dpts) == 1 and has_dpt and co.dpts[0] != dpt
            ):
                if dpt not in cref.dpts:
                    cref.dpts.append(dpt or "")
            elif len(co.dpts) == 1 and not has_dpt and "" not in cref.dpts:
                cref.dpts.append("")
            for attr, column in flags:
                value = self._flag(r, column)
                if value != co.el[attr]:
                    ref[attr] = value
            priority = _PRIORITIES.get(int(r["OBJECT_PRIORITY"] or 0), "Low")
            if priority != (co.el.get("Priority") or "Low"):
                ref["Priority"] = priority
            parent = int(r["PARAMETER_ID"] or 0) if r["PARAMETER_ID"] is not None else 0
            value_text: str | None = None
            if r["PARENT_PARAMETER_VALUE"] is not None:
                value_text = str(r["PARENT_PARAMETER_VALUE"])
            self.conditions[cref] = Condition(parent, value_text)
            self.co_refs.append(cref)
            ctx.add_refid(object_id, ref["Id"])
            if co not in translated:
                translated[co] = (
                    translate_entity(
                        ctx,
                        self.tr,
                        object_id,
                        "ComObject",
                        co.el,
                        self.app_id,
                        co.el["Id"],
                    )
                    or []
                )
            else:
                translate_entity(
                    ctx,
                    self.tr,
                    object_id,
                    "ComObjectRef",
                    ref,
                    self.app_id,
                    ref["Id"],
                    reference=translated[co],
                )

    def _co_dpts_from_device(self) -> None:
        ctx = self.ctx
        if not ctx.has_table("device_template_object") or not ctx.has_table(
            "virtual_device"
        ):
            return
        devices = ctx.select("virtual_device", "PROGRAM_ID", self.row["PROGRAM_ID"])
        if not devices:
            return
        refs = {cr.el["Id"]: cr for cr in self.co_refs}
        for r in ctx.select(
            "device_template_object", "DEVICE_ID", devices[0]["VIRTUAL_DEVICE_ID"]
        ):
            dpt = self._dpt(r)
            if not dpt:
                continue
            refid = ctx.refid(r["OBJECT_ID"])
            cref = refs.get(refid or "")
            if cref is not None and dpt not in cref.dpts:
                cref.dpts.append(dpt)

    # parameters

    def _parameters(self) -> None:
        ctx = self.ctx
        if not ctx.has_table("parameter"):
            return
        rows = sort_rows(
            ctx.select("parameter", "PROGRAM_ID", self.row["PROGRAM_ID"]),
            "ADDRESS_SPACE",
            "EIB_OBJECT_REF",
            "EIB_PROPERTY_ID",
            "PARAMETER_ADDRESS",
            "PARAMETER_BITOFFSET",
            "PARAMETER_NUMBER",
        )
        self._param_rows = rows
        self._unique = 0
        params: list[Param] = []
        refs: list[ParamRef] = []
        creator: dict[int, int] = {}
        for r in rows:
            param, is_page = self._create_parameter(r)
            creator[param.number] = int(r["PARAMETER_ID"] or 0)
            existing = self._same_parameter(params, param)
            if existing is None:
                existing = param
                params.append(param)
            ref = ParamRef(param.number, str(param.number))
            patch = r["PATCH_ALWAYS"] is not None and int(r["PATCH_ALWAYS"] or 0) != 0
            if patch and existing.value:
                existing.patch_always = True
            if r["PARAMETER_DISPLAY_ORDER"] is not None:
                ref.display_order = int(r["PARAMETER_DISPLAY_ORDER"] or 0)
            if existing is not param:
                if existing.name != param.name:
                    ref.name = param.name
                if existing.text != param.text:
                    ref.text = param.text
                if existing.access != param.access:
                    ref.access = param.access
                if existing.value != param.value:
                    ref.value = param.value
            ref.target = existing
            refs.append(ref)
            ref.dbid = int(r["PARAMETER_ID"] or 0)
            parent = (
                int(r["PAR_PARAMETER_ID"] or 0)
                if r["PAR_PARAMETER_ID"] is not None
                else 0
            )
            value: str | None = None
            if r["PARENT_PARM_VALUE"] is not None and r["PAR_PARAMETER_ID"] is not None:
                value = str(r["PARENT_PARM_VALUE"])
            self.conditions[ref] = Condition(parent, value)
            if is_page:
                self.page_params.append(ref)
        self._unions(params)
        union: Union | None = None
        for p in params:
            up = self.union_of.get(p)
            if up is None:
                self.parameters.append(p)
            elif up.union is not union:
                assert up.union is not None
                self.parameters.append(up.union)
                union = up.union
        created = list(refs)
        list_sort(refs, _compare_refs)
        translated: dict[object, list[tuple[str, str, str]]] = {}
        for ref in refs:
            ref_dbid = ref.dbid
            param = ref.target
            assert param is not None
            up = self.union_of.get(param)
            ref.ref = up.ident if up is not None else param.ident
            ref.ident = f"{ref.ref}_R-{ref.number}"
            self.refs.append(ref)
            ctx.add_refid(ref_dbid, ref.ident)
            key: object
            if up is not None:
                key = up
                if up not in translated:
                    original = 0
                    for p, u in self.union_of.items():
                        if u is up:
                            for other in created:
                                if other.target is p:
                                    original = other.dbid
                                    break
                            break
                    translated[up] = (
                        self._translate_param(original, "UnionParameter", up.ident)
                        or []
                    )
                    first = original
                else:
                    first = 0
            else:
                key = param
                first = 0
                if param not in translated:
                    first = creator[param.number]
                    translated[param] = (
                        self._translate_param(first, "Parameter", param.ident) or []
                    )
            if first != ref_dbid:
                translate_entity(
                    ctx,
                    self.tr,
                    ref_dbid,
                    "ParameterRef",
                    El("ParameterRef"),
                    self.app_id,
                    ref.ident,
                    reference=translated[key],
                )

    def _translate_param(
        self, dbid: int, kind: str, ident: str
    ) -> list[tuple[str, str, str]] | None:
        return translate_entity(
            self.ctx, self.tr, dbid, kind, El(kind), self.app_id, ident
        )

    def _default_param_text(self, dbid: int) -> str | None:
        lcid = lcid_of(self.ctx.default_language)
        from .text import culture_name

        for t in self.ctx.text_attrs(dbid):
            if (
                t.text
                and t.kind == "ParameterRef"
                and t.attribute == "Text"
                and t.lcid == lcid
            ):
                language = culture_name(t.lcid)
                if language is not None:
                    return from_db(language, t.text)
        return None

    def _first_free_number(self) -> int:
        numbers = {
            int(r["PARAMETER_NUMBER"] or 0)
            for r in self._param_rows
            if r["PARAMETER_NUMBER"] is not None
        }
        while self._unique in numbers:
            self._unique += 1
        return self._unique

    def _create_parameter(self, r: Row) -> tuple[Param, bool]:
        ctx = self.ctx
        is_page = False
        name = from_db(None, r.str("PARAMETER_NAME"))
        if r["PARAMETER_NUMBER"] is None:
            number = self._first_free_number()
        else:
            number = int(r["PARAMETER_NUMBER"] or 0)
        dbid = int(r["PARAMETER_ID"] or 0)
        text = self._default_param_text(dbid)
        if not text:
            text = from_db(self.language, r.str("PARAMETER_DESCRIPTION"))
        p = Param(number, name, text, None, dbid=dbid)
        p.ident = f"{self.app_id}_P-{number}"
        if r["PARAMETER_TYPE_ID"] is not None:
            p.type_id = ctx.refid(r["PARAMETER_TYPE_ID"])
        else:
            dummy = next((t for t in self.types if t.name == "_dummy"), None)
            if dummy is None:
                dummy = ParamType(f"{self.app_id}_PT-.5Fdummy", "_dummy", "none")
                self.types.append(dummy)
                self.types_by_id[dummy.ident] = dummy
            p.type_id = dummy.ident
        low_exception = (self.manu_number, self.number, self.version) in _LOW_ACCESS
        p.access = "None"
        if r["PARAMETER_LOW_ACCESS"] is not None and low_exception:
            p.access = _ACCESS[int(r["PARAMETER_LOW_ACCESS"] or 0)]
        if r["PARAMETER_HIGH_ACCESS"] is not None and not low_exception:
            p.access = _ACCESS[int(r["PARAMETER_HIGH_ACCESS"] or 0)]
        default_long: int | None = None
        if r["PARAMETER_DEFAULT_LONG"] is not None:
            default_long = int(r["PARAMETER_DEFAULT_LONG"] or 0)
        default_string = (
            r.str("PARAMETER_DEFAULT_STRING")
            if r["PARAMETER_DEFAULT_STRING"] is not None
            else None
        )
        address = (
            int(r["PARAMETER_ADDRESS"] or 0) & 0xFFFFFFFF
            if r["PARAMETER_ADDRESS"] is not None
            else 0
        )
        has_address = r["PARAMETER_ADDRESS"] is not None
        bit_offset = (
            int(r["PARAMETER_BITOFFSET"] or 0) & 0xFF
            if r["PARAMETER_BITOFFSET"] is not None
            else 0
        )
        has_bit_offset = r["PARAMETER_BITOFFSET"] is not None
        if r["ADDRESS_SPACE"] is None:
            space = (1 if address == 0 else 2) if has_address else 0
        else:
            space = int(r["ADDRESS_SPACE"] or 0)
        object_ref = 0
        has_object_ref = r["EIB_OBJECT_REF"] is not None
        if has_object_ref:
            object_ref = int(r["EIB_OBJECT_REF"] or 0) & 0xFFFF
        property_id = 0
        if r["EIB_PROPERTY_ID"] is not None:
            pid = int(r["EIB_PROPERTY_ID"] or 0) & 0xFFFFFFFF
            property_id = pid & 0xFF
            if pid > 255 and space in (4, 5, 11):
                ctx.errors.append(f"EibPropertyIdOutOfRange: {self.app_id} {name}")
        pt = self.types_by_id.get(p.type_id or "")
        if pt is not None:
            if pt.kind == "text":
                p.value = from_db(None, default_string)
            elif default_long is not None:
                if pt.kind == "restriction" or (pt.kind == "number" and not pt.signed):
                    p.value = str(default_long & 0xFFFFFFFF)
                elif pt.kind == "number":
                    p.value = str(default_long)
        if pt is not None and not p.value:
            if pt.kind == "number":
                p.value = str(pt.minimum)
            elif pt.kind == "restriction":
                if pt.enums:
                    p.value = pt.enums[0]["Value"]
                else:
                    downloadable = space not in (0, 1)
                    if downloadable:
                        self.empty_enum_types[pt.ident] = True
                    else:
                        self.empty_enum_types.setdefault(pt.ident, False)
        if p.value is None:
            p.value = ""
        size = pt.size if pt is not None else 0
        if space == 1:
            pass
        elif space == 11:
            p.item = Property(
                None,
                object_ref,
                (address >> 24) & 0xFF,
                property_id,
                address & 0xFFFFFF,
                bit_offset,
            )
        elif space in (2, 3):
            if has_address:
                if not has_bit_offset:
                    ctx.warnings.append(
                        f"MemoryParameterHasNoBitOffsetInformation: BitOffset is null of Param # {number} `{name}'"
                    )
                seg = self._abs_segment(address, bit_offset, size, space == 3)
                p.item = Memory(seg, address - seg.address, bit_offset)
        elif space == 0:
            is_page = True
        elif space in (4, 5):
            index = object_ref & 0xFF
            if space == 5:
                index = (index + first_app_object_index(self.mask)) & 0xFF
            p.item = Property(index, None, 0, property_id, address, bit_offset)
        elif space == 12:
            seg = self._rel_segment(address, bit_offset, size)
            p.item = Memory(seg, address - seg.address, bit_offset)
        elif self.manufacturer == "M-00FD" and self.number == 24 and self.version == 16:
            first = first_app_object_index(self.mask)
            if name == "DISTR_SEGM_COOL_ZONE":
                p.item = Property((8 + first) & 0xFF, None, 0, 112, address, bit_offset)
            elif name == "ValveType":
                p.item = Property((8 + first) & 0xFF, None, 0, 156, address, bit_offset)
        if p.item is not None and pt is not None and pt.kind == "none":
            # every sized definition of the void type counts; only the programs of the
            # exception list get a replacement type, all others keep the void type
            key = (self.manufacturer, self.number, self.version, self.original)
            for spt, ssize in self.strange_types:
                if spt is not pt or ssize == 0:
                    continue
                if key not in _UNEXPECTED_TYPE_SIZE:
                    ctx.warnings.append(
                        f"UnexpectedDownloadParameterTypeSize: Param # {number} `{name}' has address"
                        f" and void type of size {ssize}"
                    )
                elif (
                    self.number == 32515
                    and self.manufacturer == "M-0001"
                    and name == "Text_"
                ):
                    p.type_id = self._dummy_none_type()
                    p.item = None
                else:
                    dummy = self._dummy_type(ssize)
                    p.type_id = dummy.ident
                    p.value = str(dummy.minimum)
        return p, is_page

    def _dummy_none_type(self) -> str:
        for t in self.types:
            if t.name == "DummyParameterTypeNone":
                return t.ident
        t = ParamType(
            f"{self.app_id}_PT-DummyParameterTypeNone", "DummyParameterTypeNone", "none"
        )
        self.types.append(t)
        self.types_by_id[t.ident] = t
        return t.ident

    def _dummy_type(self, size: int) -> ParamType:
        name = f"DummyParameterTypeNumber{size}"
        for t in self.types:
            if t.kind == "number" and t.name == name:
                return t
        half = 2 ** (size - 1)
        t = ParamType(
            f"{self.app_id}_PT-{fragment(name)}",
            name,
            "number",
            size,
            True,
            -half,
            half - 1,
        )
        self.types.append(t)
        self.types_by_id[t.ident] = t
        return t

    @staticmethod
    def _same_parameter(params: list[Param], new: Param) -> Param | None:
        if new.item is None:
            return None
        for cur in params:
            if (
                cur.type_id != new.type_id
                or cur.item is None
                or type(cur.item) is not type(new.item)
            ):
                continue
            a, b = cur.item, new.item
            if isinstance(a, Memory) and isinstance(b, Memory):
                if (
                    a.segment is b.segment
                    and a.offset == b.offset
                    and a.bit_offset == b.bit_offset
                ):
                    return cur
            elif (
                isinstance(a, Property)
                and isinstance(b, Property)
                and a.property_id == b.property_id
                and (a.object_index is None) == (b.object_index is None)
                and (a.object_type is None) == (b.object_type is None)
                and a.offset == b.offset
                and a.bit_offset == b.bit_offset
                and (a.object_index is None or a.object_index == b.object_index)
                and (a.object_type is None or a.object_type == b.object_type)
            ):
                return cur
        return None

    def _type_size(self, p: Param) -> int:
        pt = self.types_by_id.get(p.type_id or "")
        if pt is None or pt.kind == "none":
            return 0
        return pt.size

    def _unions(self, params: list[Param]) -> None:
        start = 0
        while start < len(params) - 1:
            p = params[start]
            if isinstance(p.item, Memory):
                start = self._memory_union(params, start)
            elif isinstance(p.item, Property):
                start = self._property_union(params, start)
            start += 1

    def _union_params(
        self, params: list[Param], start: int, end: int, base: int, union: Union
    ) -> None:
        for i in range(start, end):
            p = params[i]
            item = p.item
            assert item is not None
            pos = item.offset * 8 + item.bit_offset - base
            up = UnionParam(
                p.number,
                p.name,
                p.text,
                p.type_id,
                p.access,
                p.value,
                pos // 8,
                pos % 8,
                i == start,
                union,
            )
            up.ident = f"{self.app_id}_UP-{p.number}"
            self.union_of[p] = up
            union.params.append(up)

    def _memory_union(self, params: list[Param], start: int) -> int:
        p = params[start]
        item = p.item
        assert isinstance(item, Memory)
        size = self._type_size(p)
        first = item.offset * 8 + item.bit_offset
        last = first + size - 1
        n = start + 1
        while n < len(params):
            q = params[n]
            qi = q.item
            if not isinstance(qi, Memory) or size == 0:
                break
            qsize = self._type_size(q)
            if qsize == 0 or qi.segment is not item.segment:
                break
            qs = qi.offset * 8 + qi.bit_offset
            qe = qs + qsize - 1
            if qs < first or qs > last:
                break
            last = max(last, qe)
            n += 1
        if start + 1 < n:
            union = Union(
                last - first + 1, Memory(item.segment, item.offset, item.bit_offset)
            )
            self._union_params(params, start, n, first, union)
            return n - 1
        return start

    def _property_union(self, params: list[Param], start: int) -> int:
        p = params[start]
        base = p.item
        assert isinstance(base, Property)
        first = base.offset * 8 + base.bit_offset
        last = first + self._type_size(p) - 1
        n = start + 1
        while n < len(params):
            q = params[n]
            qi = q.item
            if not isinstance(qi, Property):
                break
            same = False
            if base.object_index is not None:
                same = (
                    qi.object_index is not None
                    and qi.object_index == base.object_index
                    and qi.property_id == base.property_id
                )
            else:
                same = (
                    qi.object_type == base.object_type
                    and qi.property_id == base.property_id
                    and qi.occurrence == base.occurrence
                )
            qs = qi.offset * 8 + qi.bit_offset
            qe = qs + self._type_size(q) - 1
            if same and not (qs <= last and qe >= first):
                same = False
            if not same:
                break
            first = min(first, qs)
            last = max(last, qe)
            n += 1
        if start + 1 < n:
            union = Union(
                last - first + 1,
                Property(
                    base.object_index,
                    base.object_type,
                    base.occurrence,
                    base.property_id,
                    base.offset,
                    base.bit_offset,
                ),
            )
            self._union_params(params, start, n, first, union)
            return n - 1
        return start

    def _correct_empty_enums(self) -> None:
        for ident, downloadable in self.empty_enum_types.items():
            if downloadable:
                self.ctx.errors.append(
                    f"DownloadParameterWithEmptyParameterTypeRestriction: {self.app_id} {ident}"
                )
                continue
            pt = self.types_by_id.get(ident)
            if pt is None or pt.kind != "restriction" or pt.enums:
                continue
            pt.kind = "none"
            for p in self.parameters:
                if isinstance(p, Param) and p.type_id == ident:
                    p.value = ""

    def _parameter_calculations(self) -> None:
        ctx = self.ctx
        ref_ids = {ref.ident for ref in self.refs}
        for row in ctx.select(
            "parametercalculation", "PROGRAMID", self.row["PROGRAM_ID"]
        ):
            number = int(row["NUMBER"] or 0)
            pc = El("ParameterCalculation", {"Id": f"{self.app_id}_PC-{number}"})
            # the name is required: a missing one is written empty
            pc["Name"] = row.str("NAME") or ""
            pc["Language"] = "VBScript"
            if row["LANGUAGE"] is not None:
                language = (row.str("LANGUAGE") or "").upper()
                if language == "JAVASCRIPT":
                    pc["Language"] = "JavaScript"
                elif language != "VBSCRIPT":
                    ctx.errors.append(
                        f"UnknownParameterCalculcationLanguage: {pc['Id']}"
                    )
                lr = _transformation(row["LRTRANSFORMATION"])
                rl = _transformation(row["RLTRANSFORMATION"])
                if not lr or not rl:
                    continue
                pc.sub("RLTransformation", text=rl)
                pc.sub("LRTransformation", text=lr)
            left = El("LParameters")
            right = El("RParameters")
            unique = True
            for p in ctx.select("parameter", "CALCULATIONID", row["ID"]):
                if p["PROGRAM_ID"] != self.row["PROGRAM_ID"]:
                    continue
                side = (p.str("CALCULATIONSET") or "").upper()
                if side not in ("L", "R"):
                    # the converter fails the whole program here (its log message reads a
                    # column the calculation row does not have)
                    raise ValueError(
                        f"CalculationSet of Parameter {p['PARAMETER_ID']} is neither 'L' nor"
                        f" 'R', but is {p['CALCULATIONSET']}"
                    )
                refid = ctx.refid(p["PARAMETER_ID"])
                if not refid or refid not in ref_ids:
                    raise ValueError(
                        f"No ParameterRef for parameter {p['PARAMETER_ID']}"
                    )
                alias = p.str("ALIASNAME") if p["ALIASNAME"] is not None else None
                side_el = left if side == "L" else right
                # an alias used twice on one side (two without alias, too) drops the calculation
                if any(c.get("AliasName") == alias for c in side_el.children):
                    unique = False
                el = side_el.sub("ParameterRefRef", {"RefId": refid})
                el.cls = "ParameterCalculationLParameterRefRef"
                if alias is not None:
                    el["AliasName"] = alias
            if unique and left.children and right.children:
                pc.add(left)
                pc.add(right)
                self.calculations.append(pc)

    # options, fixups, extensions

    def _options(self) -> None:
        ctx = self.ctx
        flags = 0
        attrs = ctx.select(
            "applicationprogramattributes",
            "APPLICATIONPROGRAMID",
            self.row["PROGRAM_ID"],
        )
        if attrs:
            for a in attrs:
                name = a.str("ATTRIBUTENAME") or ""
                if name == "HawkCompatibilityFlags":
                    flags = int(str(a["ATTRIBUTEVALUE"] or "0")) & 0xFFFFFFFF
                elif name == "DomCompatibilityFlags":
                    dom = int(str(a["ATTRIBUTEVALUE"] or "0"))
                    self._option("LegacyDoNotSupportUndoDelete", dom & 1)
                    self._option("LegacyAlwaysReloadAppIfCoVisibilityChanged", dom & 2)
                    self._option("LegacyNeverReloadAppIfCoVisibilityChanged", dom & 4)
                else:
                    ctx.errors.append(
                        f"UnknownApplicationProgramAttributeName: {self.app_id} {name}"
                    )
        else:
            flags = APPLICATION_COMPATIBILITY.get(self.app_id[:16], 0)
        for bit, name in (
            (1, "LegacyNoOptimisticWrite"),
            (2, "LegacyNoPartialDownload"),
            (4, "LegacyDoNotReportPropertyWriteErrors"),
            (8, "LegacyDoNotCheckManufacturerId"),
            (0x10, "LegacyNoBackgroundDownload"),
            (0x20, "EasyCtrlModeModeStyleEmptyGroupComTables"),
            (0x40, "LegacyNoMemoryVerifyMode"),
            (0x80, "PreferPartialDownloadIfApplicationLoaded"),
            (0x100, "SetObjectTableLengthAlwaysToOne"),
            (0x200, "LegacyAllowPartialDownloadIfAp2Mismatch"),
            (0x400, "LegacyKeepObjectTableGaps"),
        ):
            self._option(name, flags & bit)
        if self.row["MANUFACTURER_ID"] is not None and self.manu_number == 2:
            self.options["PartialDownloadOnlyVisibleParameters"] = "true"
        if self.mask in ("MV-0700", "MV-0701"):
            self.options["LegacyAllowPartialDownloadIfAp2Mismatch"] = "true"
        if (self.manu_number, self.number, self.version) in _PROXY_OBJECTS:
            self.options["LegacyProxyCommunicationObjects"] = "true"

    def _option(self, name: str, on: int) -> None:
        if on:
            self.options[name] = "true"
        else:
            self.options.attrs.pop(name, None)

    def _fixups(self) -> None:
        ctx = self.ctx
        if not ctx.has_table("address_fixup") or self.mask in ("MV-0000", "MV-0001"):
            return
        entries = ctx.master.mask_entries.get(self.mask, {})
        rows = sort_rows(
            ctx.select("address_fixup", "PROGRAM_ID", self.row["PROGRAM_ID"]),
            "FIXUP_NAME",
            "FIXUP_ADDRESS",
        )
        for r in rows:
            address = int(r["FIXUP_ADDRESS"] or 0)
            found = False
            for seg in self.abs_segments:
                if not (seg.address <= address <= seg.address + seg.size):
                    continue
                found = True
                ref = entries.get(r.str("FIXUP_NAME") or "")
                offset = address - seg.address
                for f in self.fixups:
                    if (
                        f.get("CodeSegment") == seg.ident
                        and f.get("FunctionRef") == ref
                    ):
                        f.sub("Offset", text=str(offset))
                        break
                else:
                    if ref is not None:
                        fixup = El(
                            "Fixup", {"FunctionRef": ref, "CodeSegment": seg.ident}
                        )
                        fixup.sub("Offset", text=str(offset))
                        self.fixups.append(fixup)
            if not found:
                ctx.errors.append(f"NoAbsoluteSegmentForFixup: {self.app_id}")

    def _extensions(self) -> None:
        ctx = self.ctx
        plugins = ctx.select("program_plugin", "PROGRAM_ID", self.row["PROGRAM_ID"])
        swapper = ctx.byte_swapper
        comfort = ctx.comfort_plugin
        optimized = ctx.optimized_download
        keep_plugins = plugin_must_remain(self.app_id, self.manufacturer, self.original)
        power_tool = False
        if plugins:
            self.extension = El("Extension")
            for p in plugins:
                clsid = (p.str("PLUGIN_CLSID") or "").upper()
                if clsid in _POWER_TOOL_PLUGINS:
                    power_tool = True
                kind = int(p["PLUGIN_TYPE"] or 0)
                if kind == 1:
                    if optimized.replaces(clsid):
                        # replaced by master resets and the default data of the hardware
                        optimized.add_application(self.app_id)
                        replace_load_controls(self.load_procedures, self.mask)
                    elif swapper.replaces(clsid) and not keep_plugins:
                        # replaced by options and a check of the program number
                        swapper.add_application(self.app_id)
                        self._insert_program_number_check()
                        self.options["ParameterByteOrder"] = "LittleEndian"
                        self.options["PromptBeforeFullDownload"] = "true"
                        self.options["PreferPartialDownloadIfApplicationLoaded"] = (
                            "true"
                        )
                        self.options["LegacyPatchManufacturerIdInTaskSegment"] = "true"
                    else:
                        self.extension["EtsDownloadPlugin"] = clsid
                elif kind == 2:
                    if comfort.replaces(clsid) and not keep_plugins:
                        comfort.add_application(self.app_id)
                    else:
                        self.extension["EtsUiPlugin"] = clsid
                elif kind == 3:
                    self.extension["EtsDataHandler"] = clsid
                else:
                    raise ConversionError("Invalid PluginType!")
            handler = self.extension.get("EtsDataHandler")
            if handler in (
                "{DD53ACE6-6D8E-4715-B7F8-5850EF2DBF28}",
                "{FCE7505A-B38C-4824-BF81-9A2A8691CA9C}",
            ):
                self.options["LegacyProxyCommunicationObjects"] = "true"
        if (self.manu_number, self.number, self.version) in _EXTERNAL_SOFTWARE:
            if self.extension is None:
                self.extension = El("Extension")
            self.extension["RequiresExternalSoftware"] = "true"
        baggages = ctx.select(
            "applicationprogrambaggage", "APPLICATIONPROGRAMID", self.row["PROGRAM_ID"]
        )
        if baggages and self.extension is None:
            self.extension = El("Extension")
        for b in baggages:
            ref = ctx.refid(b["BAGGAGEID"]) or ""
            if not ref:
                ctx.errors.append(
                    f"InvalidBaggageRefId: {self.app_id} {b['BAGGAGEID']}"
                )
            elif (
                ref[2:6] != f"{self.manu_number:04X}"
                and self.app_id in FOREIGN_BAGGAGES
            ):
                old, new, number = FOREIGN_BAGGAGES[self.app_id]
                dbid = int(b["BAGGAGEID"] or 0)
                must_stay = referenced_elsewhere(ctx, dbid, number)
                ref = move_to_manufacturer(
                    ctx, ref, old, new, must_stay, self.manufacturer
                )
            assert self.extension is not None
            for replacer in (optimized, comfort, swapper):
                replacer.add_ref(self.app_id, ref)
            # (a baggage referred to twice stays once)
            skip = any(c.get("RefId") == ref for c in self.extension.children)
            # the baggages of replaced plugins leave every program (the ABB ones unless the
            # program's plugins stay)
            if optimized.is_baggage(ref):
                optimized.remove_ref(self.app_id, ref)
                skip = True
            for replacer in (comfort, swapper):
                if replacer.is_baggage(ref) and not keep_plugins:
                    replacer.remove_ref(self.app_id, ref)
                    skip = True
            if not skip:
                self.extension.sub("Baggage", {"RefId": ref})
                if power_tool and _broken_power_tool(ctx, ref):
                    self.broken = True
        ext = self.extension
        if (
            ext is not None
            and not ext.children
            and not ext.get("EtsDownloadPlugin")
            and not ext.get("EtsUiPlugin")
            and not ext.get("EtsDataHandler")
            and ext.get("RequiresExternalSoftware") != "true"
        ):
            self.extension = None

    def _insert_program_number_check(self) -> None:
        """Every load procedure compares the program number (property 13 of object 3) right
        after connecting (first where there is no connect)."""
        for proc in self.load_procedures:
            check = El(
                "LdCtrlCompareProp",
                {
                    "ObjIdx": "3",
                    "PropId": "13",
                    "InlineData": f"0000{self.number:04X}00",
                    "Mask": "0000FFFF00",
                },
            )
            connect = next(
                (i for i, c in enumerate(proc.children) if c.tag == "LdCtrlConnect"),
                None,
            )
            proc.children.insert(0 if connect is None else connect + 1, check)

    # ---- dynamic part

    def _ref_by_dbid(self, dbid: int) -> ParamRef:
        return self._refs_by_id[self.ctx.refids[dbid]]

    def _condition_list(self, element: object) -> list[Condition] | None:
        info = self.conditions[element]
        if info.parent == 0:
            return None
        result: list[Condition] = []
        while True:
            result.insert(0, info)
            info = self.conditions[self._ref_by_dbid(info.parent)]
            if info.parent == 0:
                return result

    def _dynamic(self) -> None:
        if not self.parameters and (self.co_table is None or not self.com_objects):
            return
        self._refs_by_id = {}
        self._channel_chooses = set()
        for ref in self.refs:
            self._refs_by_id.setdefault(ref.ident, ref)
        channel = El("Channel", {"Name": "Generic", "Text": "", "Number": "0"})
        channel["Id"] = f"{self.app_id}_CH-{fragment('0')}"
        self.dynamic.append(channel)
        pages = list(self.page_params)
        list_sort(pages, lambda a, b: _cmp(a.display_order or 0, b.display_order or 0))
        self._pages = pages
        blocks: list[El] = []
        block_of: dict[ParamRef, El] = {}
        highest = 0
        params_by_id = {p.ident: p for p in self.parameters if isinstance(p, Param)}
        for ref in pages:
            param = params_by_id.get(ref.ref)
            block = El("ParameterBlock")
            number = param.number if param else 0
            block["Name"] = param.name if param else ""
            access = param.access if param else "ReadWrite"
            if access != "ReadWrite":
                block["Access"] = access
            block["ParamRefId"] = ref.ident
            block["Id"] = f"{self.app_id}_PB-{number}"
            highest = max(highest, number)
            blocks.append(block)
            block_of[ref] = block
        self._block_of = block_of
        self._cond_lists = {}
        for ref in self.refs:
            self._cond_lists[ref] = self._condition_list(ref)
        default_block: El | None = None
        refrefs: list[El] = []
        page_set = set(pages)
        for ref in self.refs:
            if ref in page_set:
                continue
            refrefs.append(El("ParameterRefRef", {"RefId": ref.ident}))
            if self._closest_page(ref) is None and default_block is None:
                default_block = El(
                    "ParameterBlock", {"Name": "Added by Converter", "Text": ""}
                )
                default_block["Id"] = f"{self.app_id}_PB-{highest + 1}"
                blocks.insert(0, default_block)
        self._co_of_ref = {}
        self._co_conds = {}
        for cref in self.co_refs:
            info = self.conditions.get(cref)
            if info is not None and info.parent != 0:
                conds = self._condition_list(cref)
                assert conds is not None
                self._co_conds[cref] = conds
                owner = self._ref_by_dbid(conds[-1].parent)
                self._co_of_ref.setdefault(owner, []).append(cref)
            else:
                channel.sub("ComObjectRefRef", {"RefId": cref.el["Id"]})
        for block in blocks:
            pr = block.get("ParamRefId")
            if not pr or self._cond_lists[self._refs_by_id[pr]] is None:
                channel.children.append(block)
        for block in blocks:
            pr = block.get("ParamRefId")
            if not pr:
                continue
            ref = self._refs_by_id[pr]
            conds = self._cond_lists[ref]
            if conds is not None:
                self._channel_choose(
                    conds,
                    0,
                    channel.children,
                    ref.ident,
                    lambda rid: self._block_of[self._refs_by_id[rid]],
                )
        for rr in refrefs:
            ref = self._refs_by_id[rr["RefId"]]
            conds = self._cond_lists[ref]
            page = self._closest_page(ref)
            if page is None:
                assert default_block is not None
                block = default_block
                if conds is None:
                    block.children.append(El("ParameterRefRef", {"RefId": ref.ident}))
                    self._add_co_refrefs(ref.ident, block.children)
                else:
                    self._choose(
                        conds, 0, block.children, ref.ident, _param_refref, None
                    )
                continue
            block = block_of[page]
            page_conds = self._cond_lists[self._refs_by_id[block["ParamRefId"]]]
            start = 0
            if conds is not None and page_conds is not None:
                n = len(page_conds)
                if page_conds[n - 1].value == conds[n - 1].value:
                    start = n
            if conds is not None and start != len(conds):
                self._choose(
                    conds, start, block.children, ref.ident, _param_refref, None
                )
            else:
                block.children.append(El("ParameterRefRef", {"RefId": ref.ident}))
                self._add_co_refrefs(ref.ident, block.children)
        for block in blocks:
            pr = block.get("ParamRefId")
            if pr:
                self._add_co_refrefs(self._refs_by_id[pr].ident, block.children)

    def _closest_page(self, ref: ParamRef) -> ParamRef | None:
        pages = self._pages
        if ref.display_order is None or not pages:
            return None
        for i in range(len(pages) - 1, -1, -1):
            page = pages[i]
            if ref.display_order >= (page.display_order or 0) and self._weaker(
                page, ref
            ):
                return page
        access = ref.access
        if access is None:
            access = "None"
            target = ref.target
            if target is not None and target not in self.union_of:
                access = target.access
        if access == "None" and self._weaker(pages[0], ref):
            return pages[0]
        return None

    def _weaker(self, left: ParamRef, right: ParamRef) -> bool:
        lc = self._cond_lists[left]
        rc = self._cond_lists[right]
        if lc is None:
            return True
        if rc is None:
            return False
        if len(lc) > len(rc):
            return False
        for a, b in zip(lc, rc, strict=False):
            if a.parent != b.parent or a.value != b.value:
                return False
        return True

    @staticmethod
    def _when(info: Condition) -> El:
        when = El("when")
        if info.value is None:
            when["default"] = "true"
        else:
            when["test"] = info.value
        return when

    def _find_or_create_when(self, info: Condition, choose: El) -> El | None:
        whens = choose.children
        if info.value is None:
            if len(whens) != 1 or whens[0].get("default") != "true":
                return None
        elif len(whens) == 1 and whens[0].get("default") == "true":
            return None
        found: El | None = None
        for w in whens:
            if info.value is not None:
                if w.get("test") == info.value:
                    found = w
                    break
            elif w.get("default") == "true":
                found = w
                break
        if found is None:
            found = self._when(info)
            whens.append(found)
        return found

    def _channel_choose(
        self,
        conds: list[Condition],
        i: int,
        level: list[El],
        orig: str,
        creator: Callable[[str], El],
    ) -> El | None:
        make = creator
        info = conds[i]
        last = i == len(conds) - 1
        pr = self._ref_by_dbid(info.parent)
        element: El | None = None
        for current in list(level):
            if element is not None:
                break
            if (
                current.tag == "choose"
                and id(current) in self._channel_chooses
                and current.get("ParamRefId") == pr.ident
            ):
                when = self._find_or_create_when(info, current)
                if when is not None:
                    if last:
                        element = make(orig)
                        when.children.append(element)
                    else:
                        element = self._channel_choose(
                            conds, i + 1, when.children, orig, creator
                        )
        if element is None:
            choose = El("choose", {"ParamRefId": pr.ident})
            self._channel_chooses.add(id(choose))
            level.append(choose)
            when = self._when(info)
            choose.children.append(when)
            if not last:
                element = self._channel_choose(
                    conds, i + 1, when.children, orig, creator
                )
            else:
                element = make(orig)
                when.children.append(element)
        return element

    def _choose(
        self,
        conds: list[Condition],
        i: int,
        level: list[El],
        orig: str,
        creator: Callable[[str], El],
        co_owner: ParamRef | None,
    ) -> El | None:
        make = creator
        info = conds[i]
        last = i == len(conds) - 1
        refid = co_owner.ident if co_owner is not None else orig
        pr = self._ref_by_dbid(info.parent)
        element: El | None = None
        for current in list(level):
            if element is not None:
                break
            if current.tag == "ParameterBlock":
                if current.get("ParamRefId") == pr.ident:
                    if info.value is None and last:
                        element = make(orig)
                        current.children.append(element)
                        self._add_co_refrefs(refid, current.children)
                    if element is None:
                        element = self._choose(
                            conds, i + 1, current.children, orig, creator, co_owner
                        )
                continue
            if current.tag == "choose" and current.get("ParamRefId") == pr.ident:
                if id(current) in self._channel_chooses:
                    when = self._find_or_create_when(info, current)
                    if when is not None:
                        if last:
                            element = make(orig)
                            when.children.append(element)
                            self._add_co_refrefs(refid, when.children)
                        else:
                            element = self._channel_choose(
                                conds, i + 1, when.children, orig, creator
                            )
                else:
                    when = self._find_or_create_when(info, current)
                    if when is not None:
                        if not last:
                            element = self._choose(
                                conds, i + 1, when.children, orig, creator, co_owner
                            )
                        else:
                            element = make(orig)
                            when.children.append(element)
                            self._add_co_refrefs(refid, when.children)
        if element is None:
            choose = El("choose", {"ParamRefId": pr.ident})
            level.append(choose)
            when = self._when(info)
            choose.children.append(when)
            if last:
                element = make(orig)
                when.children.append(element)
                self._add_co_refrefs(refid, when.children)
            else:
                element = self._choose(
                    conds, i + 1, when.children, orig, creator, co_owner
                )
        return element

    def _add_co_refrefs(self, refid: str, level: list[El]) -> None:
        ref = self._refs_by_id.get(refid)
        if ref is None:
            return
        crefs = self._co_of_ref.pop(ref, None)
        if crefs is None:
            return
        conds = self._cond_lists[ref]
        for cref in crefs:
            co_conds = self._co_conds[cref]
            start = len(conds) if conds is not None else 0
            self._choose(co_conds, start, level, cref.el["Id"], _co_refref, ref)

    # ---- output

    def _to_bytes(self) -> bytes:
        from .xmlout import document, serialize

        app = El("ApplicationProgram")
        a = app.attrs
        a["Id"] = self.app_id
        a["ApplicationNumber"] = str(self.number)
        a["ApplicationVersion"] = str(self.version)
        a["ProgramType"] = self.program_type
        a["MaskVersion"] = self.mask
        a.update(self.attrs)
        a["LoadProcedureStyle"] = self.style
        a["DefaultLanguage"] = self.language
        if self.original:
            a["OriginalManufacturer"] = self.original
        a["PreEts4Style"] = "true"
        a["ConvertedFromPreEts4Data"] = "true"
        if self.broken:
            a["Broken"] = "true"
        if self.download_info_incomplete:
            a["DownloadInfoIncomplete"] = "true"
        static = app.sub("Static")
        code = static.sub("Code")
        for seg in self.abs_segments:
            el = code.sub(
                "AbsoluteSegment",
                {"Id": seg.ident, "Address": str(seg.address), "Size": str(seg.size)},
            )
            if seg.user_memory:
                el["UserMemory"] = "true"
            _segment_data(el, seg)
        for seg in self.rel_segments:
            el = code.sub(
                "RelativeSegment",
                {
                    "Id": seg.ident,
                    "Offset": str(seg.address),
                    "Size": str(seg.size),
                    "LoadStateMachine": str(seg.lsm),
                },
            )
            _segment_data(el, seg)
        if self.types:
            container = static.sub("ParameterTypes")
            for pt in self.types:
                container.add(_type_xml(pt))
        if self.parameters:
            container = static.sub("Parameters")
            for p in self.parameters:
                container.add(self._parameter_xml(p))
        if self.refs:
            container = static.sub("ParameterRefs")
            for ref in self.refs:
                container.add(_ref_xml(ref))
        if self.calculations:
            static.sub("ParameterCalculations").children.extend(self.calculations)
        if self.co_table is not None:
            static.add(self.co_table)
            for co in self.com_objects:
                if co.dpts:
                    co.el["DatapointType"] = " ".join(co.dpts)
                self.co_table.add(co.el)
        if self.co_refs:
            container = static.sub("ComObjectRefs")
            for cref in self.co_refs:
                if cref.dpts:
                    cref.el["DatapointType"] = " ".join(cref.dpts)
                container.add(cref.el)
        if self.address_table is not None:
            static.add(self.address_table)
        if self.association_table is not None:
            static.add(self.association_table)
        if self.fixups:
            static.sub("FixupList").children.extend(self.fixups)
        if self.load_procedures:
            static.sub("LoadProcedures").children.extend(self.load_procedures)
        if self.extension is not None:
            static.add(self.extension)
        static.add(self.options)
        if self.dynamic:
            app.sub("Dynamic").children.extend(self.dynamic)
        manufacturer = El("Manufacturer", {"RefId": self.manufacturer})
        manufacturer.sub("ApplicationPrograms").add(app)
        languages = self.tr.to_xml()
        if languages is not None:
            manufacturer.add(languages)
        return serialize(document(manufacturer), bom=False)

    def _parameter_xml(self, p: Param | Union) -> El:
        if isinstance(p, Union):
            el = El("Union", {"SizeInBit": str(p.size)})
            el.add(_item_xml(p.item))
            for up in p.params:
                u = El("Parameter", cls="UnionParameter")
                u["Id"] = up.ident
                u["Name"] = up.name
                if up.type_id:
                    u["ParameterType"] = up.type_id
                u["Offset"] = str(up.offset)
                u["BitOffset"] = str(up.bit_offset)
                u["Text"] = up.text
                if up.access != "ReadWrite":
                    u["Access"] = up.access
                if up.value is not None:
                    u["Value"] = up.value
                if up.default:
                    u["DefaultUnionParameter"] = "true"
                el.add(u)
            return el
        el = El("Parameter")
        if p.patch_always:
            el["LegacyPatchAlways"] = "true"
        el["Id"] = p.ident
        el["Name"] = p.name
        if p.type_id:
            el["ParameterType"] = p.type_id
        el["Text"] = p.text
        if p.access != "ReadWrite":
            el["Access"] = p.access
        if p.value is not None:
            el["Value"] = p.value
        if p.item is not None:
            el.add(_item_xml(p.item))
        return el


def _transformation(value: Value) -> str:
    """A calculation script; the dump holds it hex encoded (Windows-1252 bytes)."""
    if isinstance(value, bytes):
        return value.decode("cp1252", errors="replace")
    text = "" if value is None else str(value)
    if text and len(text) % 2 == 0:
        try:
            return bytes.fromhex(text).decode("cp1252", errors="replace")
        except ValueError:
            return text
    return text


def _blob(value: Value) -> bytes | None:
    return value if isinstance(value, bytes) else None


def _param_refref(refid: str) -> El:
    return El("ParameterRefRef", {"RefId": refid})


def _co_refref(refid: str) -> El:
    return El("ComObjectRefRef", {"RefId": refid})


def _cmp(a: int, b: int) -> int:
    return (a > b) - (a < b)


def _compare_refs(a: ParamRef, b: ParamRef) -> int:
    da = a.display_order or 0
    db = b.display_order or 0
    if da != db:
        return _cmp(da, db)
    return _cmp(a.number, b.number)


def _longer(new: bytes | None, old: bytes | None) -> bool:
    return (len(old) if old is not None else 0) - (
        len(new) if new is not None else 0
    ) < 0


def _segment_data(el: El, seg: Segment) -> None:
    if seg.data is not None:
        el.sub("Data", text=base64.b64encode(seg.data).decode("ascii"))
    if seg.mask is not None:
        el.sub("Mask", text=base64.b64encode(seg.mask).decode("ascii"))


def _item_xml(item: Memory | Property) -> El:
    if isinstance(item, Memory):
        return El(
            "Memory",
            {
                "CodeSegment": item.segment.ident,
                "Offset": str(item.offset),
                "BitOffset": str(item.bit_offset),
            },
        )
    el = El("Property")
    if item.object_index is not None:
        el["ObjectIndex"] = str(item.object_index)
    if item.object_type is not None:
        el["ObjectType"] = str(item.object_type)
    if item.occurrence:
        el["Occurrence"] = str(item.occurrence)
    el["PropertyId"] = str(item.property_id)
    el["Offset"] = str(item.offset)
    el["BitOffset"] = str(item.bit_offset)
    return el


def _type_xml(pt: ParamType) -> El:
    el = El("ParameterType", {"Id": pt.ident, "Name": pt.name})
    if pt.description is not None:
        el["InternalDescription"] = pt.description
    if pt.kind == "number":
        n = el.sub("TypeNumber")
        n["SizeInBit"] = str(pt.size)
        n["Type"] = "signedInt" if pt.signed else "unsignedInt"
        n["minInclusive"] = str(pt.minimum)
        n["maxInclusive"] = str(pt.maximum)
        if pt.ui_hint:
            n["UIHint"] = pt.ui_hint
    elif pt.kind == "text":
        el.sub("TypeText", {"SizeInBit": str(pt.size)})
    elif pt.kind == "restriction":
        r = el.sub(
            "TypeRestriction",
            {
                "Base": "BinaryValue" if pt.binary else "Value",
                "SizeInBit": str(pt.size),
            },
        )
        r.children.extend(pt.enums)
    else:
        el.sub("TypeNone")
    return el


def _ref_xml(ref: ParamRef) -> El:
    el = El("ParameterRef", {"Id": ref.ident, "RefId": ref.ref})
    if ref.name is not None:
        el["Name"] = ref.name
    if ref.text is not None:
        el["Text"] = ref.text
    el["Tag"] = ref.tag
    if ref.display_order is not None:
        el["DisplayOrder"] = str(ref.display_order)
    if ref.access is not None:
        el["Access"] = ref.access
    if ref.value is not None:
        el["Value"] = ref.value
    return el


def _min_version(number: int) -> str:
    major = number >> 24
    if major == 3:
        letter = chr(number & 0xFF)
        if letter == "d":
            return "3.0d"
        if letter == "f":
            return "3.0f"
        return "3.0"
    if major == 4:
        return "4.0"
    return "3.0"
