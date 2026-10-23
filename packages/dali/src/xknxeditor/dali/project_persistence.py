"""High-level DALI project-persistence API.

Thin wrapper over :mod:`xknxeditor.dali.backup_store` that builds a MDT ``DaliGC16-Backup-Store``
blob from the live commissioning dataclasses (:class:`~xknxeditor.dali.model.DaliBusState`,
:class:`~xknxeditor.dali.model.Scene`, :class:`~xknxeditor.dali.model.Template`). The low-level
round-trip (deserialize + byte-exact serialize) lives in :mod:`.backup_store`; this module keeps
the GUI-facing surface small and stable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from xml.etree import ElementTree as ET

from xknxeditor.dali.backup_store import (
    BACKUP_STORE_BASE_NAME,
    DEFAULT_DCA_VERSION,
    BackupDevEcg,
    BackupEcg,
    BackupGroup,
    BackupScene,
    BackupSceneItem,
    BackupStore,
    BackupTpl,
    BackupTplCommand,
    BackupTplMember,
    backup_store_name,
    dump_backup_store,
    load_backup_store,
)
from xknxeditor.dali.model import (
    MAX_ECG,
    MAX_GROUP,
    MAX_SCENES,
    MAX_TEMPLATES,
    SHORT_ADDR_UNASSIGNED,
    DaliBusState,
    EcgAssignment,
    EcgState,
    Scene,
    SceneValue,
    Template,
    TemplateCommand,
)

# Enum names MDT writes for the TplMode / TplFunc fields; verified against a real .knxproj.
_TPL_MODE_NAMES: dict[int, str] = {0: "Disabled", 1: "Enabled"}
_TPL_FUNC_NAMES: dict[int, str] = {
    0: "None",
    1: "SetValue",
    2: "SetMinValue",
    3: "SetMaxValue",
    4: "ColourTemp",
    5: "ColourXY",
    6: "ColourRGBW",
    7: "ColourRGB",
    8: "ColourHSV",
    9: "ColourHSVW",
    16: "SetMaxOnValue",
    17: "CallScene",
}


def _tpl_mode_name(mode: int) -> str:
    return _TPL_MODE_NAMES.get(mode, str(mode))


def _tpl_func_name(func: int) -> str:
    return _TPL_FUNC_NAMES.get(func, str(func))


def _is_value_func(func: int) -> bool:
    # Value functions (level/brightness) take the "V" fade prefix; colour functions take "C".
    # Value set: SetValue/SetMinValue/SetMaxValue (1-3), None (0) and SetMaxOnValue (16).
    return func < 4 or func == 16


def _color_csv(color: bytes) -> str:
    return ";".join(str(b) for b in color)


def _weekdays_str(mask: int) -> str:
    return "".join("1" if mask & (1 << b) else "0" for b in range(7))


def build_backup_store_xml(
    *,
    state: DaliBusState,
    scenes: list[Scene] | None = None,
    templates: list[Template] | None = None,
    individual_address: str,
    project_name: str = "",
    can_restore: bool = False,
    dca_version: str = DEFAULT_DCA_VERSION,
) -> bytes:
    """Build the UTF-16LE XML payload of a MDT ``DaliGC16-Backup-Store`` entry.

    Convenience over :func:`xknxeditor.dali.backup_store.dump_backup_store`: produces the full
    16/64/64/16/16-slot layout MDT writes, filling untouched slots with defaults. Returns the
    raw bytes caller can hand to ``DeviceBinaryData.data``.
    """
    scenes = list(scenes or [])
    templates = list(templates or [])
    store = _store_from_live_state(
        state=state,
        scenes=scenes,
        templates=templates,
        individual_address=individual_address,
        project_name=project_name,
        can_restore=can_restore,
        dca_version=dca_version,
    )
    return dump_backup_store(store)


def _store_from_live_state(
    *,
    state: DaliBusState,
    scenes: list[Scene],
    templates: list[Template],
    individual_address: str,
    project_name: str,
    can_restore: bool,
    dca_version: str,
) -> BackupStore:
    store = BackupStore.defaults(
        individual_address=individual_address,
        project=project_name,
        can_restore=can_restore,
        version=dca_version,
    )

    by_slot: dict[int, EcgState] = {
        e.slot: e for e in state.ecgs if 0 <= e.slot < MAX_ECG
    }
    for i, dev in enumerate(store.devecgs):
        ecg = by_slot.get(i)
        if ecg is None:
            continue
        dev.type = ecg.ecg_type
        dev.subtype = ecg.sub_type
        dev.groupindex = ecg.group_index
        dev.short_address = ecg.slot if ecg.present else SHORT_ADDR_UNASSIGNED
        dev.long_address = ecg.long_address
        dev.min_value = ecg.min_value
        if ecg.ecg_type == 8 and (ecg.sub_type & 2):  # colour-LED + ctemp bit
            dev.min_ctemp = ecg.min_color_temp
            dev.max_ctemp = ecg.max_color_temp

    for i, ecg_row in enumerate(store.ecgs):
        ecg = by_slot.get(i)
        if ecg is None:
            continue
        ecg_row.groupindex = ecg.group_index
        ecg_row.short_address = ecg.slot if ecg.present else SHORT_ADDR_UNASSIGNED
        ecg_row.type = ecg.ecg_type & 0xFF
        if not ecg.present:
            ecg_row.flags = 0
        elif ecg.ets_ecg_index == SHORT_ADDR_UNASSIGNED:
            ecg_row.flags = 1
        else:
            ecg_row.flags = 2

    by_scene = {s.index: s for s in scenes if 0 <= s.index < len(store.scenes)}
    for i, sc_row in enumerate(store.scenes):
        src = by_scene.get(i)
        if src is None:
            # MDT seeds knxScene = index + 1 on a fresh project; keep the default stable so a
            # caller that only edits the levels does not accidentally reset the KNX mapping.
            sc_row.knx_scene = i + 1
            continue
        sc_row.fade_time = src.fade_time
        sc_row.knx_scene = src.knx_scene
        sc_row.items = [_scene_item_from_live(v) for v in src.values]

    by_tpl = {t.index: t for t in templates if 0 <= t.index < len(store.templates)}
    for i, tpl_row in enumerate(store.templates):
        src = by_tpl.get(i)
        if src is None:
            continue
        tpl_row.mode = _tpl_mode_name(src.mode)
        tpl_row.is_overwritable = src.overwritable
        tpl_row.members = [
            BackupTplMember(itemtype="Dali.DcaApp.Group", itemindex=g)
            for g in src.groups
        ] + [BackupTplMember(itemtype="Dali.DcaApp.ECG", itemindex=e) for e in src.ecgs]
        tpl_row.commands = [_cmd_from_live(c) for c in src.commands]

    return store


def _scene_item_from_live(value: SceneValue) -> BackupSceneItem:
    if value.target_index < MAX_GROUP:
        itemtype = "Dali.DcaApp.Group"
        itemindex = value.target_index
    else:
        itemtype = "Dali.DcaApp.ECG"
        itemindex = value.target_index - MAX_GROUP
    item = BackupSceneItem(
        itemtype=itemtype,
        itemindex=itemindex,
        value=value.level,
        setting=value.flags,
    )
    if any(value.color):
        # ColorXY (2) covers the usual case; a round-trip caller that keeps the subtype in its
        # own model is expected to drive BackupStore directly.
        item.color_type = 2
        item.color = _color_csv(value.color)
    return item


def _cmd_from_live(cmd: TemplateCommand) -> BackupTplCommand:
    prefix = "V" if _is_value_func(cmd.func) else "C"
    return BackupTplCommand(
        func=_tpl_func_name(cmd.func),
        value=_color_csv(cmd.value),
        fade_in=f"{prefix}FadeIn{cmd.fade_time}",
        time=f"{cmd.hour:02d}:{cmd.minute:02d}",
        weekdays=_weekdays_str(cmd.weekdays),
    )


def patch_assignments_in_blob(
    existing: bytes, assignments: list[EcgAssignment]
) -> bytes:
    """Apply group assignments to a backup blob of any schema, preserving everything else.

    Unlike :func:`merge_commissioning_blob` (which models the gc16 document fully), this parses
    the blob generically and only rewrites the ``groupindex``/``short-address``/``flags`` of the
    ECG and DevECG rows named by ``assignments``. Every other element - including the wider
    e64pro containers (sensor/button/general inputs, effects) that the editor does not model -
    is re-serialised verbatim, so a Theben/IPAS blob stays byte-identical outside the edited rows.

    Index spaces match the gc16 convention: ``<DevECG index>`` is the physical slot (its
    ``short-address`` is the logical ECG number); ``<ECG index>`` is the logical ETS ECG number
    (its ``short-address`` is the physical slot).
    """
    root = ET.fromstring(existing.decode("utf-16-le"))
    by_slot = {a.slot: a for a in assignments}
    by_ets = {
        a.ets_index: a for a in assignments if a.ets_index != SHORT_ADDR_UNASSIGNED
    }
    for dev in root.findall("devecgs/DevECG"):
        a = by_slot.get(_int_attr(dev, "index"))
        if a is None:
            continue
        dev.set("groupindex", str(a.group_index))
        if a.ets_index != SHORT_ADDR_UNASSIGNED:
            dev.set("short-address", str(a.ets_index))
    for ecg in root.findall("ecgs/ECG"):
        a = by_ets.get(_int_attr(ecg, "index"))
        if a is None:
            continue
        ecg.set("groupindex", str(a.group_index))
        ecg.set("short-address", str(a.slot))
        ecg.set("flags", "2")
    return ET.tostring(root, encoding="unicode", short_empty_elements=True).encode(
        "utf-16-le"
    )


def _int_attr(el: ET.Element, name: str) -> int:
    try:
        return int(el.get(name, "-1"))
    except ValueError:
        return -1


def merge_commissioning_blob(
    *,
    existing: bytes | None,
    state: DaliBusState,
    assignments: list[EcgAssignment],
    scenes: list[Scene],
    individual_address: str,
    project_name: str = "",
    dca_version: str = DEFAULT_DCA_VERSION,
) -> bytes:
    """Produce the ``DaliGC16-Backup-Store`` blob after a commissioning run, least-destructively.

    When ``existing`` is a blob previously written by the DCA (or by us), it is taken as the base
    so that fields we do not model - templates, group/ECG/scene descriptions, colour-control
    detail - survive untouched. When it is ``None`` (no prior blob), the base is seeded from the
    freshly scanned ``state``. Only the slots named by ``assignments`` and the scenes named by
    ``scenes`` are overwritten; every other row is carried over verbatim.
    """
    if existing is not None:
        store = load_backup_store(existing)
        _ensure_full_shape(store)
        _seed_slots_from_scan(store, state, only_missing=True)
    else:
        store = BackupStore.defaults(
            individual_address=individual_address,
            project=project_name,
            version=dca_version,
        )
        _seed_slots_from_scan(store, state, only_missing=False)
    # Keep the header in step with the live project even on an existing blob.
    store.individual_address = individual_address
    if project_name:
        store.project = project_name

    _apply_group_assignments(store, assignments)
    _apply_scene_edits(store, scenes)
    return dump_backup_store(store)


def _ensure_full_shape(store: BackupStore) -> None:
    """Pad a loaded store to the fixed 16/64/64/16/16 slot counts MDT always materialises."""
    _pad(store.groups, MAX_GROUP, lambda i: BackupGroup(index=i))
    _pad(store.devecgs, MAX_ECG, lambda i: BackupDevEcg(index=i))
    _pad(store.ecgs, MAX_ECG, lambda i: BackupEcg(index=i, type=0))
    _pad(store.scenes, MAX_SCENES, lambda i: BackupScene(index=i, knx_scene=i + 1))
    _pad(store.templates, MAX_TEMPLATES, lambda i: BackupTpl(index=i))


class _Indexed(Protocol):
    index: int


def _pad[T: _Indexed](rows: list[T], count: int, factory: Callable[[int], T]) -> None:
    present = {r.index for r in rows}
    for i in range(count):
        if i not in present:
            rows.append(factory(i))
    rows.sort(key=lambda r: r.index)


def _seed_slots_from_scan(
    store: BackupStore, state: DaliBusState, *, only_missing: bool
) -> None:
    """Populate the physical-slot rows (DevECG) and logical rows (ECG) from a scan.

    Two index spaces: ``<DevECG index=N>`` is keyed by the physical DALI slot and carries the
    logical ECG number in its ``short-address``; ``<ECG index=M>`` is keyed by the logical ETS
    ECG number and carries the physical slot in its ``short-address``. The scan yields one
    :class:`EcgState` per physical slot (with ``ets_ecg_index`` naming its logical ECG), so the
    two tables are filled through two different keys.

    With ``only_missing`` the scan only fills a slot that still has no detected ballast in the
    base blob, so manual edits in a prior configuration-app session are not clobbered by a re-scan.
    """
    by_slot: dict[int, EcgState] = {
        e.slot: e for e in state.ecgs if 0 <= e.slot < MAX_ECG
    }
    for dev in store.devecgs:  # dev.index is the physical slot
        ecg = by_slot.get(dev.index)
        if ecg is None or (only_missing and dev.type != -1):
            continue
        dev.type = ecg.ecg_type
        dev.subtype = ecg.sub_type
        dev.groupindex = ecg.group_index
        # DevECG.short-address is the logical ECG number bound to this slot, not the slot.
        dev.short_address = (
            ecg.ets_ecg_index
            if ecg.present and ecg.ets_ecg_index != SHORT_ADDR_UNASSIGNED
            else SHORT_ADDR_UNASSIGNED
        )
        dev.long_address = ecg.long_address
        dev.min_value = ecg.min_value
        if ecg.ecg_type == 8 and (ecg.sub_type & 2):
            dev.min_ctemp = ecg.min_color_temp
            dev.max_ctemp = ecg.max_color_temp
    by_ets: dict[int, EcgState] = {
        e.ets_ecg_index: e
        for e in state.ecgs
        if e.present and 0 <= e.ets_ecg_index < MAX_ECG
    }
    for row in store.ecgs:  # row.index is the logical ETS ECG number
        ecg = by_ets.get(row.index)
        if ecg is None or (only_missing and row.type != 0):
            continue
        row.type = ecg.ecg_type & 0xFF
        row.groupindex = ecg.group_index
        # ECG.short-address is the physical slot this logical ECG is bound to.
        row.short_address = ecg.slot


def _apply_group_assignments(
    store: BackupStore, assignments: list[EcgAssignment]
) -> None:
    """Apply group assignments to both index spaces.

    Each :class:`EcgAssignment` binds a physical ``slot`` to a logical ``ets_index`` with a
    ``group_index``. The physical row (``<DevECG>``) is keyed by slot and stores the logical
    number in ``short-address``; the logical row (``<ECG>``) is keyed by the ETS number and
    stores the physical slot in ``short-address``.
    """
    by_slot = {a.slot: a for a in assignments}
    by_ets = {
        a.ets_index: a for a in assignments if a.ets_index != SHORT_ADDR_UNASSIGNED
    }
    for dev in store.devecgs:  # keyed by physical slot
        a = by_slot.get(dev.index)
        if a is None:
            continue
        dev.groupindex = a.group_index
        if a.ets_index != SHORT_ADDR_UNASSIGNED:
            dev.short_address = a.ets_index
    for row in store.ecgs:  # keyed by logical ETS ECG number
        a = by_ets.get(row.index)
        if a is None:
            continue
        row.groupindex = a.group_index
        row.short_address = a.slot
        row.flags = 2  # Assigned: the row's own index is a valid ETS ECG number


def _apply_scene_edits(store: BackupStore, scenes: list[Scene]) -> None:
    by_index = {s.index: s for s in scenes if 0 <= s.index < len(store.scenes)}
    for row in store.scenes:
        src = by_index.get(row.index)
        if src is None:
            continue
        row.fade_time = src.fade_time
        if src.knx_scene:
            row.knx_scene = src.knx_scene
        row.is_dirty = True
        row.items = [_scene_item_from_live(v) for v in src.values]


__all__ = [
    "BACKUP_STORE_BASE_NAME",
    "DEFAULT_DCA_VERSION",
    "BackupDevEcg",
    "BackupEcg",
    "BackupScene",
    "BackupSceneItem",
    "BackupStore",
    "BackupTpl",
    "BackupTplCommand",
    "BackupTplMember",
    "backup_store_name",
    "build_backup_store_xml",
    "dump_backup_store",
    "load_backup_store",
    "merge_commissioning_blob",
    "patch_assignments_in_blob",
]
