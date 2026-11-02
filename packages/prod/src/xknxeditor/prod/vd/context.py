"""State shared by all steps of one conversion: database indexes, master data, id map, warnings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ids import manufacturer_id
from .replacers import Replacer, byte_swapper, comfort_plugin, optimized_download
from .text import culture_name, two_letter

if TYPE_CHECKING:
    from .baggage import BaggageDoc
    from .database import Database, Row
    from .dump import Value
    from .master import MasterData

# text_attribute.COLUMN_ID -> (object model class, XML attribute)
TEXT_COLUMNS: dict[int, tuple[str, str]] = {
    1: ("Product", "Text"),
    2: ("Product", "Attributes.Colour"),
    3: ("Product", "Attributes.Series"),
    4: ("Product", "Attributes.CatalogName"),
    10: ("ParameterRef", "Text"),
    11: ("ParameterTypeRestrictionEnumeration", "Text"),
    20: ("ComObjectRef", "Text"),
    22: ("ComObjectRef", "FunctionText"),
    30: ("CatalogSection", "Name"),
    31: ("CatalogSection", "VisibleDescription"),
    40: ("CatalogItem", "Name"),
    41: ("CatalogItem", "VisibleDescription"),
    80: ("ApplicationProgram", "Name"),
}


@dataclass(slots=True)
class TextAttr:
    lcid: int
    kind: str
    attribute: str
    text: str


class ConversionError(Exception):
    """The database cannot be converted (e.g. master data lacks a manufacturer or mask)."""


def sort_rows(rows: list[Row], *columns: str) -> list[Row]:
    """Rows sorted by columns like a sorted table select (stable, NULL first)."""

    def key(row: Row) -> tuple[tuple[int, Value], ...]:
        out: list[tuple[int, Value]] = []
        for c in columns:
            v = row[c]
            if v is None:
                out.append((0, 0))
            elif isinstance(v, str):
                # the data set compares texts without case
                out.append((1, v.casefold()))
            else:
                out.append((1, v))
        return tuple(out)

    return sorted(rows, key=key)


class Context:
    """One conversion run."""

    db: Database
    master: MasterData
    refids: dict[int, str]
    warnings: list[str]
    errors: list[str]
    default_language: str
    manufacturer_languages: dict[str, str]
    ip_programs: set[str]
    byte_swapper: Replacer
    comfort_plugin: Replacer
    optimized_download: Replacer
    baggage_docs: list[BaggageDoc]
    _groups: dict[tuple[str, str], dict[Value, list[Row]]]
    _primary_keys: dict[str, str | None]
    _texts: dict[int, list[TextAttr]] | None

    def __init__(self, db: Database, master: MasterData) -> None:
        self.db = db
        self.master = master
        self.refids = {}
        self.warnings = []
        self.errors = []
        self.default_language = "en-US"
        self.manufacturer_languages = {}
        self.ip_programs = set()
        self.byte_swapper = byte_swapper()
        self.comfort_plugin = comfort_plugin()
        self.optimized_download = optimized_download()
        # the baggages converted so far (programs move some to their manufacturer)
        self.baggage_docs = []
        self._groups = {}
        self._primary_keys = {}
        self._texts = None

    # database access

    def rows(self, table: str) -> list[Row]:
        t = self.db.table(table)
        return list(t) if t is not None else []

    def has_table(self, table: str) -> bool:
        return self.db.table(table) is not None

    def select(self, table: str, column: str, value: Value) -> list[Row]:
        """Rows of ``table`` whose ``column`` equals ``value``, like an unsorted table select:
        ordered by the primary key (a column ``<TABLE>_ID``), else in table order."""
        key = (table.lower(), column.upper())
        groups = self._groups.get(key)
        if groups is None:
            t = self.db.table(table)
            groups = t.group_by(column) if t is not None and t.has(column) else {}
            pk = self.primary_key(table)
            if t is not None and pk is not None:
                for rows in groups.values():
                    if len(rows) > 1:
                        rows.sort(key=lambda r: (r[pk] is not None, r[pk] or 0))
            self._groups[key] = groups
        return groups.get(value, [])

    def primary_key(self, table: str) -> str | None:
        """The primary key column of a table as the data set has it: the column ``<TABLE>_ID``
        when the dump flags it ("Y" in its column line)."""
        name = table.lower()
        if name not in self._primary_keys:
            self._primary_keys[name] = None
            t = self.db.table(name)
            wanted = f"{name}_ID".upper()
            if t is not None and wanted in t.nullable:
                self._primary_keys[name] = wanted
        return self._primary_keys[name]

    def first(self, table: str, column: str, value: Value) -> Row | None:
        rows = self.select(table, column, value)
        return rows[0] if rows else None

    # ids

    def add_refid(self, dbid: int, refid: str) -> None:
        old = self.refids.get(dbid)
        if old is None:
            self.refids[dbid] = refid
        elif old != refid:
            self.errors.append(f"DifferentRefIdsForOneDbId: {old}, {refid}")

    def refid(self, dbid: Value) -> str | None:
        if dbid is None:
            return None
        return self.refids.get(int(dbid))

    # texts

    def text_attrs(self, entity: int) -> list[TextAttr]:
        if self._texts is None:
            self._texts = self._load_texts()
        return self._texts.get(entity, [])

    def take_text_attrs(self, entity: int) -> list[TextAttr] | None:
        """The texts of an entity, which a translation consumes (a second call returns None)."""
        if self._texts is None:
            self._texts = self._load_texts()
        return self._texts.pop(entity, None)

    def _load_texts(self) -> dict[int, list[TextAttr]]:
        out: dict[int, list[TextAttr]] = {}
        table = self.db.table("text_attribute")
        if table is None:
            return out
        rows = [r for r in table if r["TEXT_ATTRIBUTE_TEXT"] is not None]
        rows = sort_rows(rows, "ENTITY_ID", "COLUMN_ID")
        for r in rows:
            prop = TEXT_COLUMNS.get(r.int("COLUMN_ID") or 0)
            if prop is None:
                continue
            entity = r.int("ENTITY_ID") or 0
            out.setdefault(entity, []).append(
                TextAttr(
                    r.int("LANGUAGE_ID") or 0,
                    prop[0],
                    prop[1],
                    r.str("TEXT_ATTRIBUTE_TEXT") or "",
                )
            )
        return out

    # manufacturers and languages

    def manufacturer_ref(self, number: Value) -> str:
        return manufacturer_id(int(number or 0))

    def known_manufacturer(self, ref: str) -> bool:
        """Whether the master data has manufacturer ``ref``. A database of any other manufacturer
        the master data lacks cannot be converted (the master data is outdated); only M-0041 and
        M-0100 are reported and left out."""
        if self.master.manufacturer(ref) is not None:
            return True
        if ref in ("M-0041", "M-0100"):
            return False
        raise ConversionError(f"manufacturer id = {ref}")

    def init_manufacturer_languages(self) -> None:
        """The conversion language per manufacturer: the master data default when the database has
        texts in it, else a language of the same family, the database language, the first
        language of the texts, en-US."""
        numbers: list[int] = []
        for table in ("application_program", "catalog_entry"):
            for r in sort_rows(self.rows(table), "MANUFACTURER_ID"):
                m = r["MANUFACTURER_ID"]
                if m is None or str(m) == "":
                    continue
                if int(m) not in numbers:
                    numbers.append(int(m))
        db_language: str | None = None
        for r in self.rows("ete_language"):
            if r.int("DATABASE_LANGUAGE") == 1:
                db_language = culture_name(r.int("LANGUAGE_ID") or 0)
                break
        for number in numbers:
            entities = [
                r.int("PROGRAM_ID") or 0
                for r in self.select("application_program", "MANUFACTURER_ID", number)
            ]
            entities += [
                r.int("CATALOG_ENTRY_ID") or 0
                for r in self.select("catalog_entry", "MANUFACTURER_ID", number)
            ]
            available: list[str] = []
            for entity in entities:
                for t in self.text_attrs(entity):
                    name = culture_name(t.lcid)
                    if name is not None and name not in available:
                        available.append(name)
            master = self.master.manufacturer(number)
            if master is None:
                continue
            self.manufacturer_languages[manufacturer_id(number)] = _best_language(
                available, master.default_language, db_language
            )

    def language_of(self, manufacturer: str) -> str:
        return self.manufacturer_languages.get(manufacturer, "en-US")


def _best_language(
    available: list[str], primary: str | None, secondary: str | None
) -> str:
    if primary is not None and primary in available:
        return primary
    ordered = sorted(available, key=str.lower)
    if primary is not None:
        for name in ordered:
            if two_letter(name) == two_letter(primary):
                return name
    if secondary is not None:
        return secondary
    if ordered:
        return ordered[0]
    return "en-US"
