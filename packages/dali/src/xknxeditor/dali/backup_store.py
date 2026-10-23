"""Round-trip persistence of the MDT DALI ``DaliGC16-Backup-Store`` BinaryData blob.

The gateway's configuration app stores everything a .knxproj-level export needs about the
per-channel DALI state (group descriptions, device-ECG table, ECG commissioning flags +
descriptions, scenes with per-target levels/colours, timed colour-control templates) inside a
single ``DeviceBinaryData`` row, UTF-16LE-encoded ``XmlDocument.InnerXml``, named
``DaliGC16-Backup-Store`` for channel 0 and ``DaliGC16-Backup-Store.N`` for channel N+1.

This module exposes a 1:1 Python representation of that document (:class:`BackupStore` and the
leaf dataclasses) plus :func:`load_backup_store` / :func:`dump_backup_store`; a byte-exact
round-trip against a real .knxproj export is covered by the test suite.

Theben P64 shares the same transport (UTF-16LE InnerXml) but uses a wider, OEM-specific schema
(``type="e64pro"`` + extra ``<mds>/<pbs>/<gis>/<inputs>/<effects>`` containers); that
serialization is NOT handled here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar
from xml.etree import ElementTree as ET

from xknxeditor.dali.model import (
    MAX_ECG,
    MAX_GROUP,
    MAX_SCENES,
    MAX_TEMPLATES,
)

BACKUP_STORE_BASE_NAME = "DaliGC16-Backup-Store"
DEFAULT_DCA_VERSION = "3.9.2.0"


def backup_store_name(channel: int) -> str:
    """BinaryData ``Name`` MDT writes for ``channel`` (0-based)."""
    return (
        BACKUP_STORE_BASE_NAME
        if channel == 0
        else f"{BACKUP_STORE_BASE_NAME}.{channel}"
    )


def _bool_str(v: bool) -> str:
    return "True" if v else "False"


def _parse_bool(s: str) -> bool:
    return s == "True"


def _opt(attrib: dict[str, str], key: str, default: str = "") -> str:
    return attrib.get(key, default)


@dataclass(slots=True)
class BackupGroup:
    """One ``<Group>`` row (0..15). ``description`` is only written when non-empty."""

    index: int
    description: str = ""


@dataclass(slots=True)
class BackupDevEcg:
    """One ``<DevECG>`` row (0..63). Carries the physical bus state the DCA caches."""

    index: int
    type: int = -1
    subtype: int = 0
    groupindex: int = 255
    short_address: int = 255
    long_address: int = 0xFFFFFF  # 24-bit, serialised as uppercase 6-hex "X6"
    min_value: int = 0
    description: str = ""
    # Only emitted when the ballast is a colour-LED with the colour-temp bit set.
    min_ctemp: int | None = None
    max_ctemp: int | None = None


@dataclass(slots=True)
class BackupEcg:
    """One ``<ECG>`` row (0..63). The ETS-side ECG mapping (not the physical slot)."""

    index: int
    groupindex: int = 255
    short_address: int = 255
    flags: int = (
        0  # CommissioningFlag: 0 NotPresent / 1 NotAssigned / 2 Assigned / 3/4 planned
    )
    type: int = 0
    description: str = ""


@dataclass(slots=True)
class BackupSceneItem:
    """One target value inside a ``<Scene>``."""

    itemtype: str  # "Dali.DcaApp.Group" or "Dali.DcaApp.ECG"
    itemindex: int
    value: int = 0
    setting: int = 0  # SettingFlags bitfield
    color_type: int | None = None  # ColorValueType; emitted iff >= ColorXY
    color: str = ""  # semicolon-separated CSV from ColoredValue.ToCsvString()


@dataclass(slots=True)
class BackupScene:
    """One ``<Scene>`` row (0..15). ``items`` are the per-target scene values."""

    index: int
    is_dirty: bool = False
    fade_time: int = 0  # stored as the raw integer (not the "CFadeInN" enum name)
    knx_scene: int = 0
    description: str = ""
    items: list[BackupSceneItem] = field(default_factory=list["BackupSceneItem"])


@dataclass(slots=True)
class BackupTplMember:
    """One ``<TplItem>`` row inside a template's ``<Members>``."""

    itemtype: str
    itemindex: int


@dataclass(slots=True)
class BackupTplCommand:
    """One ``<TplCmd>`` row inside a template's ``<Commands>``."""

    func: str  # TplFunc enum name (or integer fallback)
    value: str  # ColoredValue CSV
    fade_in: str  # "VFadeIn<n>" for value funcs, "CFadeIn<n>" for colour funcs
    time: str  # "HH:MM" zero-padded
    weekdays: str  # 7-char "1"/"0" string, bit0 = Monday


@dataclass(slots=True)
class BackupTpl:
    """One ``<Tpl>`` row (0..15). Both ``<Members>`` and ``<Commands>`` are always emitted."""

    index: int
    mode: str = "Disabled"  # TplMode enum name
    is_dirty: bool = False
    is_overwritable: bool = False
    description: str = ""
    members: list[BackupTplMember] = field(default_factory=list["BackupTplMember"])
    commands: list[BackupTplCommand] = field(default_factory=list["BackupTplCommand"])


@dataclass(slots=True)
class BackupStore:
    """The whole ``DaliGC16-Backup-Store`` document for one DALI channel.

    MDT always materialises 16 groups / 64 DevECGs / 64 ECGs / 16 scenes / 16 templates in
    index order; callers that construct a store from scratch should either pre-fill those or
    use :meth:`defaults`.
    """

    can_restore: bool = False
    individual_address: str = ""
    project: str = ""
    type: str = "gc16"
    version: str = DEFAULT_DCA_VERSION
    groups: list[BackupGroup] = field(default_factory=list["BackupGroup"])
    devecgs: list[BackupDevEcg] = field(default_factory=list["BackupDevEcg"])
    ecgs: list[BackupEcg] = field(default_factory=list["BackupEcg"])
    scenes: list[BackupScene] = field(default_factory=list["BackupScene"])
    templates: list[BackupTpl] = field(default_factory=list["BackupTpl"])

    # Element ordering of the children under <device> is fixed and must be preserved.
    _CHILDREN: ClassVar[tuple[str, ...]] = (
        "groups",
        "devecgs",
        "ecgs",
        "scenes",
        "templates",
    )

    @classmethod
    def defaults(
        cls,
        *,
        individual_address: str,
        project: str = "",
        can_restore: bool = False,
        version: str = DEFAULT_DCA_VERSION,
    ) -> BackupStore:
        """Build a fresh store with all 16 groups / 64 DevECGs / 64 ECGs / 16 scenes / 16 tpls."""
        return cls(
            can_restore=can_restore,
            individual_address=individual_address,
            project=project,
            version=version,
            groups=[BackupGroup(index=i) for i in range(MAX_GROUP)],
            devecgs=[BackupDevEcg(index=i) for i in range(MAX_ECG)],
            ecgs=[BackupEcg(index=i, type=0) for i in range(MAX_ECG)],
            scenes=[BackupScene(index=i) for i in range(MAX_SCENES)],
            templates=[BackupTpl(index=i) for i in range(MAX_TEMPLATES)],
        )


# ---------- deserialization ----------


def load_backup_store(payload: bytes) -> BackupStore:
    """Parse a UTF-16LE ``DaliGC16-Backup-Store`` payload into a :class:`BackupStore`."""
    root = ET.fromstring(payload.decode("utf-16-le"))
    if root.tag != "device":
        raise ValueError(f"expected <device> root, got <{root.tag}>")
    store = BackupStore(
        can_restore=_parse_bool(_opt(root.attrib, "CanRestore", "False")),
        individual_address=_opt(root.attrib, "IndividualAddress"),
        project=_opt(root.attrib, "Project"),
        type=_opt(root.attrib, "type", "gc16"),
        version=_opt(root.attrib, "version", DEFAULT_DCA_VERSION),
    )
    for group_el in root.findall("groups/Group"):
        store.groups.append(
            BackupGroup(
                index=int(group_el.attrib["index"]),
                description=_opt(group_el.attrib, "description"),
            )
        )
    for d in root.findall("devecgs/DevECG"):
        a = d.attrib
        store.devecgs.append(
            BackupDevEcg(
                index=int(a["index"]),
                type=int(_opt(a, "type", "-1")),
                subtype=int(_opt(a, "subtype", "0")),
                groupindex=int(_opt(a, "groupindex", "255")),
                short_address=int(_opt(a, "short-address", "255")),
                long_address=int(_opt(a, "long-address", "FFFFFF"), 16),
                min_value=int(_opt(a, "min-value", "0")),
                description=_opt(a, "description"),
                min_ctemp=int(a["min-ctemp"]) if "min-ctemp" in a else None,
                max_ctemp=int(a["max-ctemp"]) if "max-ctemp" in a else None,
            )
        )
    for e in root.findall("ecgs/ECG"):
        a = e.attrib
        store.ecgs.append(
            BackupEcg(
                index=int(a["index"]),
                groupindex=int(_opt(a, "groupindex", "255")),
                short_address=int(_opt(a, "short-address", "255")),
                flags=int(_opt(a, "flags", "0")),
                type=int(_opt(a, "type", "0")),
                description=_opt(a, "description"),
            )
        )
    for s in root.findall("scenes/Scene"):
        a = s.attrib
        scene = BackupScene(
            index=int(a["index"]),
            is_dirty=_parse_bool(_opt(a, "isDirty", "False")),
            fade_time=int(_opt(a, "fadeTime", "0")),
            knx_scene=int(_opt(a, "knxScene", "0")),
            description=_opt(a, "description"),
        )
        for item in s.findall("SceneItem"):
            ia = item.attrib
            scene.items.append(
                BackupSceneItem(
                    itemtype=_opt(ia, "itemtype"),
                    itemindex=int(_opt(ia, "itemindex", "0")),
                    value=int(_opt(ia, "value", "0")),
                    setting=int(_opt(ia, "setting", "0")),
                    color_type=int(ia["color-type"]) if "color-type" in ia else None,
                    color=_opt(ia, "color"),
                )
            )
        store.scenes.append(scene)
    for t in root.findall("templates/Tpl"):
        a = t.attrib
        tpl = BackupTpl(
            index=int(a["index"]),
            mode=_opt(a, "mode", "Disabled"),
            is_dirty=_parse_bool(_opt(a, "isDirty", "False")),
            is_overwritable=_parse_bool(_opt(a, "isOverwritable", "False")),
            description=_opt(a, "description"),
        )
        for mi in t.findall("Members/TplItem"):
            tpl.members.append(
                BackupTplMember(
                    itemtype=_opt(mi.attrib, "itemtype"),
                    itemindex=int(_opt(mi.attrib, "itemindex", "0")),
                )
            )
        for c in t.findall("Commands/TplCmd"):
            ca = c.attrib
            tpl.commands.append(
                BackupTplCommand(
                    func=_opt(ca, "func"),
                    value=_opt(ca, "value"),
                    fade_in=_opt(ca, "fadeIn"),
                    time=_opt(ca, "time"),
                    weekdays=_opt(ca, "weekdays"),
                )
            )
        store.templates.append(tpl)
    return store


# ---------- serialization ----------


def dump_backup_store(store: BackupStore) -> bytes:
    """Serialise a :class:`BackupStore` into a UTF-16LE blob, matching MDT's layout.

    The output is the raw ``XmlDocument.InnerXml`` encoded as UTF-16LE (no XML declaration,
    no BOM). Attribute order matches MDT's writer and content-free elements are kept open
    (``<Members></Members>``) rather than self-closed, so a round-trip stays byte-identical.
    """
    root = ET.Element(
        "device",
        {
            "CanRestore": _bool_str(store.can_restore),
            "IndividualAddress": store.individual_address,
            "Project": store.project,
            "type": store.type,
            "version": store.version,
        },
    )

    groups_el = ET.SubElement(root, "groups")
    for g in store.groups:
        attrs = {"index": str(g.index)}
        if g.description:
            attrs["description"] = g.description
        ET.SubElement(groups_el, "Group", attrs)

    devecgs_el = ET.SubElement(root, "devecgs")
    for d in store.devecgs:
        attrs = {
            "index": str(d.index),
            "type": str(d.type),
            "subtype": str(d.subtype & 0xFF),
            "groupindex": str(d.groupindex & 0xFF),
            "short-address": str(d.short_address & 0xFF),
            "long-address": f"{d.long_address & 0xFFFFFF:06X}",
            "min-value": str(d.min_value & 0xFF),
        }
        if d.description:
            attrs["description"] = d.description
        if d.min_ctemp is not None and d.max_ctemp is not None:
            attrs["min-ctemp"] = str(d.min_ctemp & 0xFFFF)
            attrs["max-ctemp"] = str(d.max_ctemp & 0xFFFF)
        ET.SubElement(devecgs_el, "DevECG", attrs)

    ecgs_el = ET.SubElement(root, "ecgs")
    for e in store.ecgs:
        attrs = {
            "index": str(e.index),
            "groupindex": str(e.groupindex & 0xFF),
            "short-address": str(e.short_address & 0xFF),
            "flags": str(e.flags),
            "type": str(e.type & 0xFF),
            # MDT always emits ``description`` on <ECG>, even when empty (unlike <Group> and
            # <DevECG>, which only emit it when the user set one).
            "description": e.description,
        }
        ET.SubElement(ecgs_el, "ECG", attrs)

    scenes_el = ET.SubElement(root, "scenes")
    for sc in store.scenes:
        attrs = {"index": str(sc.index)}
        if sc.description:
            attrs["description"] = sc.description
        attrs["isDirty"] = _bool_str(sc.is_dirty)
        attrs["fadeTime"] = str(sc.fade_time)
        attrs["knxScene"] = str(sc.knx_scene)
        scene_el = ET.SubElement(scenes_el, "Scene", attrs)
        for item in sc.items:
            iattrs: dict[str, str] = {}
            if item.itemtype:
                iattrs["itemtype"] = item.itemtype
            iattrs["itemindex"] = str(item.itemindex)
            iattrs["value"] = str(item.value & 0xFF)
            iattrs["setting"] = str(item.setting)
            if item.color_type is not None:
                iattrs["color-type"] = str(item.color_type)
                iattrs["color"] = item.color
            ET.SubElement(scene_el, "SceneItem", iattrs)

    templates_el = ET.SubElement(root, "templates")
    for tpl in store.templates:
        tattrs = {
            "index": str(tpl.index),
            "mode": tpl.mode,
            "isDirty": _bool_str(tpl.is_dirty),
            "isOverwritable": _bool_str(tpl.is_overwritable),
            "description": tpl.description,
        }
        tpl_el = ET.SubElement(templates_el, "Tpl", tattrs)
        members_el = ET.SubElement(tpl_el, "Members")
        for member in tpl.members:
            ET.SubElement(
                members_el,
                "TplItem",
                {"itemtype": member.itemtype, "itemindex": str(member.itemindex)},
            )
        commands_el = ET.SubElement(tpl_el, "Commands")
        for cmd in tpl.commands:
            ET.SubElement(
                commands_el,
                "TplCmd",
                {
                    "func": cmd.func,
                    "value": cmd.value,
                    "fadeIn": cmd.fade_in,
                    "time": cmd.time,
                    "weekdays": cmd.weekdays,
                },
            )

    inner_xml = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return inner_xml.encode("utf-16-le")


__all__ = [
    "BACKUP_STORE_BASE_NAME",
    "DEFAULT_DCA_VERSION",
    "BackupDevEcg",
    "BackupEcg",
    "BackupGroup",
    "BackupScene",
    "BackupSceneItem",
    "BackupStore",
    "BackupTpl",
    "BackupTplCommand",
    "BackupTplMember",
    "backup_store_name",
    "dump_backup_store",
    "load_backup_store",
]
