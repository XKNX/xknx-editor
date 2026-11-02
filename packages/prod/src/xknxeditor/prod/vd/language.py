"""Translations: the ``Languages`` element of a manufacturer in each written file."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .text import culture_name, from_db
from .xmlout import El

if TYPE_CHECKING:
    from collections.abc import Callable

    from .context import Context
    from .database import Row

# Element kinds whose texts are only translations (the attribute stays as the base table has it).
_NO_DEFAULT_ATTRIBUTE = frozenset(
    {"ParameterRef", "Parameter", "ComObjectRef", "ComObject", "UnionParameter"}
)


class Translations:
    """Language -> translation unit -> translation element -> attribute -> text, in creation order."""

    __slots__ = ("languages",)

    languages: dict[str, dict[str, dict[str, dict[str, str]]]]

    def __init__(self) -> None:
        self.languages = {}

    def element(self, language: str, unit: str, element: str) -> dict[str, str]:
        units = self.languages.setdefault(language, {})
        elements = units.setdefault(unit, {})
        return elements.setdefault(element, {})

    def set(
        self, language: str, unit: str, element: str, attribute: str, text: str
    ) -> None:
        texts = self.element(language, unit, element)
        texts.pop(attribute, None)  # a new text replaces the old one at the end
        texts[attribute] = text

    def prune(self, language: str, unit: str, element: str) -> None:
        """Remove an element, its unit and its language once empty."""
        units = self.languages.get(language)
        if units is None:
            return
        elements = units.get(unit)
        if elements is not None:
            if element in elements and not elements[element]:
                del elements[element]
            if not elements:
                del units[unit]
        if not units:
            del self.languages[language]

    def to_xml(self) -> El | None:
        if not self.languages:
            return None
        root = El("Languages")
        for language, units in self.languages.items():
            lang = root.sub("Language", {"Identifier": language})
            for unit, elements in units.items():
                tu = lang.sub("TranslationUnit", {"RefId": unit})
                for element, texts in elements.items():
                    te = tu.sub("TranslationElement", {"RefId": element})
                    for attribute, text in texts.items():
                        te.sub(
                            "Translation", {"AttributeName": attribute, "Text": text}
                        )
        return root


def _type_matches(kind: str, prop_kind: str) -> bool:
    if kind == prop_kind or kind + "Ref" == prop_kind:
        return True
    return prop_kind == "ParameterRef" and kind == "UnionParameter"


def translate_entity(
    ctx: Context,
    tr: Translations,
    dbid: int,
    kind: str,
    el: El,
    unit: str,
    element_id: str,
    *,
    product_attribute: Callable[[str], El] | None = None,
    reference: list[tuple[str, str, str]] | None = None,
) -> list[tuple[str, str, str]] | None:
    """Add the translations of database entity ``dbid`` for element ``el`` (kind = object model
    class); texts in the conversion language also become the element's attributes.

    ``product_attribute`` returns (and creates when missing) the product attribute element of a
    name like ``Colour``; its texts are translated as its ``Value``.

    ``reference`` (the result of an earlier call) skips texts that are already translated the same
    way there. Returns the translated (language, unit, element) triples, or None without texts.
    """
    entries = ctx.take_text_attrs(dbid)
    if entries is None:
        return None
    done: list[tuple[str, str, str]] = []
    for entry in entries:
        language = culture_name(entry.lcid)
        if language is None:
            continue
        if reference is not None and _in_reference(
            tr, reference, language, entry.attribute, entry.text
        ):
            continue
        attribute = entry.attribute
        target = element_id
        attr_el: El | None = None
        if attribute.startswith("Attributes.") and product_attribute is not None:
            attr_el = product_attribute(attribute.split(".", 1)[1])
            target = attr_el["Id"]
        if not _type_matches(kind, entry.kind):
            # The texts of other element kinds are dropped, empty containers removed.
            tr.element(language, unit, target)
            tr.prune(language, unit, target)
            continue
        name = "Value" if attribute.startswith("Attributes.") else attribute
        text = from_db(language, entry.text)
        tr.set(language, unit, target, name, text)
        key = (language, unit, target)
        if key not in done:
            done.append(key)
        if kind not in _NO_DEFAULT_ATTRIBUTE and language == ctx.default_language:
            if attr_el is not None:
                attr_el["Value"] = text
            else:
                el[attribute] = text
    return done


def _in_reference(
    tr: Translations,
    reference: list[tuple[str, str, str]],
    language: str,
    attribute: str,
    original: str,
) -> bool:
    text = from_db(language, original)
    for ref_language, unit, element in reference:
        if ref_language != language:
            continue
        texts = tr.languages.get(language, {}).get(unit, {}).get(element)
        if texts is not None and texts.get(attribute) == text:
            return True
    return False


def translate_descriptions(
    ctx: Context, tr: Translations, el: El, unit: str, rows: list[Row], column: str
) -> None:
    """``VisibleDescription`` translations from description rows (one text per line, ordered by
    language and display order)."""
    from .context import sort_rows

    rows = sort_rows(rows, "LANGUAGE_ID", "DISPLAY_ORDER")
    i = 0
    while i < len(rows):
        lcid = rows[i]["LANGUAGE_ID"]
        if lcid is None:
            ctx.warnings.append(
                f"NoLanguageIdForTranslation: The language id for a translatable attribute of {unit} was null."
            )
            i += 1
            continue
        language = culture_name(int(lcid))
        if language is None:
            i += 1
            continue
        lines: list[str] = []
        while i < len(rows) and rows[i]["LANGUAGE_ID"] == lcid:
            lines.append(from_db(language, rows[i].str(column)))
            i += 1
        # the joined text is converted from the code page a second time
        description = from_db(language, "\r\n".join(lines))
        if not description:
            continue
        tr.set(language, unit, el["Id"], "VisibleDescription", description)
        if language == ctx.default_language:
            el["VisibleDescription"] = description
