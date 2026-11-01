"""Read + write the DALI gateway backup blob as generic viewer sections.

The MDT / Theben / IPAS DALI gateways keep everything the ETS DCA configures beyond the plain KNX
parameters in a ``DaliGC16-Backup-Store`` (MDT, ``type="gc16"``) or ``Dalie64-Backup-Store``
(Theben/IPAS, ``type="e64pro"``) BinaryData entry - UTF-16LE XML: group and ECG names, the physical
ECG table, scene values + colours, time-control templates, colour effects and (e64pro) the sensor /
push-button / general-input configuration. None of it is in the KNX parameter list.

The reader surfaces the whole document generically (one section per container, one or more rows per
populated item). Known enum fields (ECG ``type``, template ``mode``) become select boxes via
``choices`` and are editable; :func:`apply_edit` patches the chosen attribute back into the blob
byte-preserving. Other fields stay read-only for now. A two-channel gateway carries one blob per
channel; ``name`` (the BinaryData entry name, e.g. ``DaliGC16-Backup-Store.1``) disambiguates the
sections and routes an edit to the right blob.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from editor_gui.plugin_params import PluginParameter, PluginSectionView

_CONTAINERS: tuple[tuple[str, str, str], ...] = (
    ("groups", "DALI Groups", "Group"),
    ("devecgs", "DALI ECG Devices (physical)", "Device"),
    ("ecgs", "DALI ECGs", "ECG"),
    ("mds", "DALI Motion Detectors", "Sensor"),
    ("pbs", "DALI Push-Buttons", "Button"),
    ("gis", "DALI General Inputs", "Input"),
    ("inputs", "DALI Inputs", "Input"),
    ("scenes", "DALI Scenes", "Scene"),
    ("effects", "DALI Effects", "Effect"),
    ("templates", "DALI Time Control (Templates)", "Template"),
)

_SKIP_ITEM_ATTRS = frozenset({"index", "isDirty"})
_DEFAULT_VALUES = frozenset({"", "0", "-1", "255", "FFFFFF", "False"})

# Human labels for the blob's short attribute names, so the rows read as words instead of codes
# (``ctype`` -> "Colour type"). An unmapped name falls back to the raw name.
_ATTR_LABELS: dict[str, str] = {
    "description": "Name",
    "cmode": "Colour mode",
    "couple": "Couple index",
    "ctype": "Colour type",
    "color-type": "Colour type",
    "color": "Colour",
    "setting": "Setting",
    "value": "Value",
    "knxScene": "KNX scene",
    "fadeTime": "Fade time",
    "fadetime": "Fade time",
    "fadeIn": "Fade in",
    "groupindex": "Group",
    "short-address": "Short address",
    "long-address": "Long address",
    "min-value": "Min value",
    "min-ctemp": "Min colour temp.",
    "max-ctemp": "Max colour temp.",
    "flags": "Commissioning flag",
    "subtype": "Sub-type",
    "type": "Type",
    "mode": "Mode",
    "func": "Function",
    "time": "Time",
    "weekdays": "Weekdays",
    "delay": "Delay",
    "isOverwritable": "Overwritable",
}


def _attr_label(attr: str) -> str:
    return _ATTR_LABELS.get(attr, attr)


# Known enum fields -> (raw value, label) choices. Rendered as a select box. Only verified value
# sets are listed (no guessed vendor tables).
_DALI_DEVICE_TYPE = {
    "0": "Control gear (ECG)",
    "1": "Emergency inverter",
    "2": "Discharge lamp",
    "3": "Low-voltage halogen",
    "4": "Dimmable lamp",
    "5": "0/1-10 V converter",
    "6": "LED module",
    "7": "Switching function",
    "8": "Colour control",
    "9": "Sequencer",
}
_TPL_MODE = {"Disabled": "Disabled", "Enabled": "Enabled"}
# Group colour mode (GroupCMode) and the group's ETS colour type (EtsColorType).
_GROUP_COLOUR_MODE = {"0": "Normal", "1": "Master", "2": "Slave"}
_GROUP_COLOUR_TYPE = {
    "0": "No colour",
    "4": "Colour temperature",
    "5": "Colour XY",
    "6": "RGBW",
    "7": "RGB",
    "8": "HSV",
    "9": "HSVW",
    "10": "Temperature + RGBW",
    "11": "Temperature + RGB",
    "12": "Temperature + HSV",
    "13": "Temperature + HSVW",
    "14": "Master",
    "15": "Slave",
}
# A scene item's colour-value type (ColorValueType).
_SCENE_COLOUR_TYPE = {
    "0": "No colour",
    "1": "Colour XY",
    "2": "Colour temperature",
    "4": "RGB",
    "8": "RGBW",
    "16": "HSV",
    "32": "HSVW",
}
# Enum fields shown as a read-only humanised label, for one of two reasons:
#   * they mirror a KNX parameter (``type``/``mode``, and the group colour mode/type ``cmode``/
#     ``ctype`` which mirror the per-channel DCA colour parameters) - editing only the blob copy
#     would desync from the programmed value; the user edits those in the parameter sections;
#   * ``color-type`` is a scene item's colour space: changing it alone would leave the colour
#     value and the ``setting`` flags inconsistent (the DCA couples all three), so switching the
#     colour space needs a dedicated picker we do not have yet. The colour VALUE stays editable
#     for same-space tweaks (see ``_EDITABLE_SCALARS``). ``type`` is also a scanned property.
_READONLY_LABEL_ENUMS: dict[str, dict[str, str]] = {
    "type": _DALI_DEVICE_TYPE,
    "mode": _TPL_MODE,
    "cmode": _GROUP_COLOUR_MODE,
    "ctype": _GROUP_COLOUR_TYPE,
    "color-type": _SCENE_COLOUR_TYPE,
}
# Blob-only enum fields (no parameter mirror, no coupled state): editable select boxes. Template
# command functions are stored by name; offer the verified set.
_TPL_FUNC = {
    n: n
    for n in (
        "None",
        "SetValue",
        "SetMinValue",
        "SetMaxValue",
        "ColourTemp",
        "ColourXY",
        "ColourRGBW",
        "ColourRGB",
        "ColourHSV",
        "ColourHSVW",
        "SetMaxOnValue",
        "CallScene",
    )
}
_EDITABLE_ENUMS: dict[str, dict[str, str]] = {"func": _TPL_FUNC}

# Boolean attributes become a True/False select box.
_BOOL_CHOICES = (("True", "True"), ("False", "False"))

# Free-scalar attributes the user may edit directly as text (plain config numbers / times / scene
# colour values, not physical scan results like long-address or commissioning flags). A scene
# colour is the Kelvin value for colour-temperature items and a value CSV otherwise; it lives only
# in the blob, so editing it as text is safe (the bytes are written back verbatim).
_EDITABLE_SCALARS = frozenset(
    {
        "description",
        "knxScene",
        "fadeTime",
        "value",
        "color",
        "setting",
        "time",
        "weekdays",
        "fadetime",
        "delay",
    }
)

# Nested-leaf attributes that are the row target (label), not shown as their own value.
_TARGET_ATTRS = frozenset({"itemtype", "itemindex"})

_REF_PREFIX = "dali_backup"


def read(data: bytes, name: str = "") -> list[PluginSectionView]:
    """Surface every populated item of a DALI backup blob. ``name`` is the BinaryData entry
    name; it disambiguates a two-channel gateway and routes edits back to the right blob."""
    root = _parse_device(data)
    if root is None:
        return []
    channel = _channel_suffix(name)
    sections: list[PluginSectionView] = []
    for tag, label, noun in _CONTAINERS:
        rows = _container_rows(root, tag, noun, name)
        if rows:
            sections.append(
                PluginSectionView(section=f"{label}{channel}", parameters=rows)
            )
    return sections


def _field(attr: str, value: str) -> tuple[bool, tuple[tuple[str, str], ...], str]:
    """Editability, choices and display value for an attribute.

    Returns ``(writable, choices, display_value)``. A parameter-mirrored enum (``type``/``mode``)
    is read-only and shows its humanised label; a blob-only enum (``func``) and booleans are
    editable select boxes; the curated scalars are editable text; everything else is read-only
    with its raw value.
    """
    label_enum = _READONLY_LABEL_ENUMS.get(attr)
    if label_enum is not None:
        return False, (), label_enum.get(value, value)
    editable_enum = _EDITABLE_ENUMS.get(attr)
    if editable_enum is not None:
        return True, tuple(editable_enum.items()), value
    if value in ("True", "False"):
        return True, _BOOL_CHOICES, value
    if attr in _EDITABLE_SCALARS:
        return True, (), value
    return False, (), value


def apply_edit(data: bytes, ref_id: str, new_value: str) -> bytes:
    """Patch the attribute addressed by ``ref_id`` in the blob and return the new UTF-16LE bytes.

    ``ref_id`` addresses either a top-level item attribute
    (``dali_backup:<name>:<container>:<index>:<attr>``) or a nested-leaf attribute
    (``dali_backup:<name>:<container>:<index>:leaf:<n>:<attr>``, where ``n`` indexes the item's
    scene-items / effect-steps / template commands). Only that one attribute is changed; the rest
    of the document is re-serialised verbatim.
    """
    parts = ref_id.split(":")
    if len(parts) < 5 or parts[0] != _REF_PREFIX:
        raise ValueError(f"not a dali_backup ref: {ref_id!r}")
    root = _parse_device(data)
    if root is None:
        raise ValueError("payload is not a DALI backup blob")
    if len(parts) == 5:  # top-level item attribute
        _, _name, container, index, attr = parts
        target = root.find(f"{container}/*[@index='{index}']")
    elif len(parts) == 7 and parts[4] == "leaf":  # nested scene item / step / command
        _, _name, container, index, _leaf, n, attr = parts
        item = root.find(f"{container}/*[@index='{index}']")
        leaves = _nested_leaves(item) if item is not None else []
        target = leaves[int(n)] if n.isdigit() and int(n) < len(leaves) else None
    else:
        raise ValueError(f"unrecognised dali_backup ref: {ref_id!r}")
    if target is None or attr not in target.attrib:
        raise ValueError(f"target not found: {ref_id!r}")
    target.set(attr, new_value)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True).encode(
        "utf-16-le"
    )


def _parse_device(data: bytes) -> ET.Element | None:
    try:
        root = ET.fromstring(data.decode("utf-16-le"))
    except (UnicodeDecodeError, ET.ParseError):
        return None
    if root.tag != "device" or root.get("type") not in ("gc16", "e64pro"):
        return None
    return root


def _channel_suffix(name: str) -> str:
    """`` (Channel N)`` for a ``...-Store.K`` blob name (K is 0-based -> channel K+1), else ''."""
    base, _, tail = name.rpartition(".")
    if base and tail.isdigit():
        return f" (Channel {int(tail) + 1})"
    return ""


def _row(
    ref: str,
    label: str,
    value: str,
    *,
    writable: bool = False,
    choices: tuple[tuple[str, str], ...] = (),
    group: str = "",
    group_key: str = "",
) -> PluginParameter:
    return PluginParameter(
        ref_id=ref,
        name=ref,
        label=label,
        value=value,
        default_value="",
        section="",
        display_order=0,
        writable=writable,
        choices=choices,
        group=group,
        group_key=group_key,
    )


def _short_target(e: ET.Element) -> str:
    itemtype = e.get("itemtype")
    if itemtype is None:
        return e.tag
    kind = "ECG" if itemtype.endswith("ECG") else "Group"
    return f"{kind} {e.get('itemindex', '?')}"


def _nested_leaves(item: ET.Element) -> list[ET.Element]:
    leaves: list[ET.Element] = []
    for child in item:
        if len(child):
            leaves.extend(child)
        else:
            leaves.append(child)
    return leaves


def _is_populated(item: ET.Element) -> bool:
    if len(item):
        return True
    return any(
        k not in _SKIP_ITEM_ATTRS and v not in _DEFAULT_VALUES
        for k, v in item.attrib.items()
    )


def _container_rows(
    root: ET.Element, tag: str, noun: str, name: str
) -> list[PluginParameter]:
    rows: list[PluginParameter] = []
    for item in root.findall(f"{tag}/*"):
        if not _is_populated(item):
            continue
        idx = item.get("index", "?")
        desc = item.get("description", "")
        # ``head`` labels the collapsible tree node the panel builds per item; the row labels
        # below are just the field names, so each item's fields read as a short, nested list.
        # ``gkey`` is the node's stable identity (survives a rename of ``desc``).
        head = f"[{idx}] {noun}"
        if desc:
            head = f"{head} {desc!r}"
        gkey = f"{tag}:{idx}"
        own = [
            k for k in item.attrib if k not in _SKIP_ITEM_ATTRS and k != "description"
        ]
        # The description is editable (the item's user-facing name).
        if "description" in item.attrib:
            rows.append(
                _row(
                    f"{_REF_PREFIX}:{name}:{tag}:{idx}:description",
                    _attr_label("description"),
                    desc,
                    writable=True,
                    group=head,
                    group_key=gkey,
                )
            )
        for key in own:
            writable, choices, shown = _field(key, item.attrib[key])
            ref = (
                f"{_REF_PREFIX}:{name}:{tag}:{idx}:{key}"
                if writable
                else f"{tag}:{idx}:{key}"
            )
            rows.append(
                _row(
                    ref,
                    _attr_label(key),
                    shown,
                    writable=writable,
                    choices=choices,
                    group=head,
                    group_key=gkey,
                )
            )
        # Nested scene items / effect steps / template commands: one row per attribute, so the
        # value, colour, time and weekday schedule are individually readable (and editable where
        # they are plain config) instead of one cryptic "key=value, key=value" dump.
        for n, leaf in enumerate(_nested_leaves(item)):
            target = _short_target(leaf)
            for key, value in leaf.attrib.items():
                if key in _TARGET_ATTRS:
                    continue
                writable, choices, shown = _field(key, value)
                ref = (
                    f"{_REF_PREFIX}:{name}:{tag}:{idx}:leaf:{n}:{key}"
                    if writable
                    else f"{tag}:{idx}:leaf:{n}:{key}"
                )
                rows.append(
                    _row(
                        ref,
                        f"{target}: {_attr_label(key)}",
                        shown,
                        writable=writable,
                        choices=choices,
                        group=head,
                        group_key=gkey,
                    )
                )
    return rows


__all__ = ["apply_edit", "read"]
