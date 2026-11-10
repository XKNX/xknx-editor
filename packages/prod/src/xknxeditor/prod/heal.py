"""Heal non-conformant legacy application XML before xsdata binds it.

Products converted from very old databases (ETS2/3-origin) sometimes omit attributes that the
current KNX project schema, and therefore the generated xsdata binding, treat as required. A
``<TypeNumber>``/``<TypeFloat>``/``<TypeTime>`` without ``minInclusive``/``maxInclusive`` makes the
binding's dataclass constructor raise a ``TypeError`` for the missing keyword-only argument.

These helpers add such a missing bound back with a best-effort value so the bytes bind. The values
are not authoritative ranges - they only let non-conformant legacy data load. The heal is a no-op on
conformant XML: it inserts an attribute only when it is absent, so a file that already carries both
bounds comes back as the very same ``bytes`` object, byte for byte.

Group objects of such products can also have the object size ``"Variable"``, which no KNX schema
lists: it is the variable-length size the schemas call ``"LegacyVarData"`` (a BCU1/BCU2 record
of type 15), and is read as that.

Some product data places parameters, separators, buttons or assigns directly in a ``when`` of a
``choose`` inside a channel, where the schemas allow pages and group objects only. Such a file
fails to bind; it is bound again without those items (:func:`drop_channel_when_items`): the
product imports, and the items never show.
"""

from __future__ import annotations

import re

# Elements whose min/max bound the binding requires. Matched by their start tag's local name, so the
# default-namespace tags (unprefixed in these files) are covered whatever the schema version is.
_BOUNDED_ELEMENTS = (b"TypeNumber", b"TypeFloat", b"TypeTime")

# A start tag of one of the bounded elements: name, its attribute text, and an optional self-closing
# slash. Attribute values in this data never contain ">", so stopping at the first ">" is safe.
_TAG = re.compile(
    rb"<(?P<name>"
    + rb"|".join(_BOUNDED_ELEMENTS)
    + rb")(?P<attrs>\s[^>]*?)?(?P<slash>\s*/)?>"
)
_HAS_MIN = re.compile(rb"\bminInclusive\s*=")
_HAS_MAX = re.compile(rb"\bmaxInclusive\s*=")
_SIZE = re.compile(rb'\bSizeInBit\s*=\s*"(\d+)"')

# An ObjectSize attribute (of a ComObject or a ComObjectRef) with the value "Variable".
_VARIABLE_SIZE = re.compile(
    rb"""(?P<head>\sObjectSize\s*=\s*)(?P<quote>["'])Variable(?P=quote)"""
)

# Best-effort upper bound when the element carries no SizeInBit to derive one from.
_FALLBACK_MAX = 2147483647


def _max_for(attrs: bytes) -> int:
    size = _SIZE.search(attrs)
    if size is not None:
        return 2 ** int(size.group(1)) - 1
    return _FALLBACK_MAX


def _heal_tag(match: re.Match[bytes]) -> bytes:
    attrs = match.group("attrs") or b""
    injected = b""
    if _HAS_MIN.search(attrs) is None:
        injected += b' minInclusive="0"'
    if _HAS_MAX.search(attrs) is None:
        injected += b' maxInclusive="%d"' % _max_for(attrs)
    if not injected:
        return match.group(0)
    slash = match.group("slash") or b""
    return b"<" + match.group("name") + attrs + injected + slash + b">"


def _heal_bounds(xml_bytes: bytes) -> bytes:
    # Cheap gate: only the presence of a bounded element can need a heal, and most files need none.
    if not any(
        tag in xml_bytes for tag in (b"<TypeNumber", b"<TypeFloat", b"<TypeTime")
    ):
        return xml_bytes
    return _TAG.sub(_heal_tag, xml_bytes)


def _heal_object_sizes(xml_bytes: bytes) -> bytes:
    if b"Variable" not in xml_bytes:
        return xml_bytes
    return _VARIABLE_SIZE.sub(rb"\g<head>\g<quote>LegacyVarData\g<quote>", xml_bytes)


# What a ``when`` of a ``choose`` inside a channel can hold in some product data although no
# schema lists it there (what a parameter block's ``when`` holds besides that). Such an item is
# left out: the product imports, and the item never shows nor counts.
_CHANNEL_WHEN_ITEMS = frozenset(
    {"ParameterRefRef", "ParameterSeparator", "Button", "Assign"}
)
# Elements whose ``choose`` children are channel chooses.
_CHANNEL_ELEMENTS = frozenset({"Channel", "ChannelIndependentBlock"})


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def drop_channel_when_items(xml_bytes: bytes) -> bytes | None:
    """``xml_bytes`` without the parameters, separators, buttons and assigns that a ``when`` of a
    channel's ``choose`` (or of a ``choose`` nested in it) holds directly, so the version's
    binding reads the rest. ``None`` when the XML holds no such item."""
    from xml.etree import ElementTree

    root = ElementTree.fromstring(xml_bytes)
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    dropped = 0

    def drop_in(choose: ElementTree.Element) -> None:
        nonlocal dropped
        for when in choose:
            if _local(when.tag) != "when":
                continue
            for child in list(when):
                name = _local(child.tag)
                if name in _CHANNEL_WHEN_ITEMS:
                    when.remove(child)
                    dropped += 1
                elif name == "choose":
                    drop_in(child)

    for channel in root.iter():
        if _local(channel.tag) in _CHANNEL_ELEMENTS:
            for child in channel:
                if _local(child.tag) == "choose":
                    drop_in(child)
    if not dropped:
        return None
    if namespace:
        ElementTree.register_namespace("", namespace)
    return ElementTree.tostring(root, encoding="utf-8")


def heal_application_xml(xml_bytes: bytes) -> bytes:
    """Return ``xml_bytes`` with any missing numeric-range bound injected into bounded elements and
    every object size ``"Variable"`` read as ``"LegacyVarData"``.

    Conformant input is returned unchanged (the same object), so caches and conformant parses stay
    byte-identical. Runs in linear passes over the bytes."""
    healed = _heal_object_sizes(_heal_bounds(xml_bytes))
    return healed if healed != xml_bytes else xml_bytes
