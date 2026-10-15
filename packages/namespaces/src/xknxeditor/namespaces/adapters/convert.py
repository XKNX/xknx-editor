"""Build unified `intermediate` instances from per-version `files.vXX` models.

Works by reflection: each target field is filled from the same-named source field, recursing
through nested dataclasses and lists. Real per-version differences are handled by the override
functions in OVERRIDES, and a shared rule fills a PUID when the target needs one the source lacks.
"""

# The reflection walks arbitrary `files.vXX` dataclasses via getattr, so `src` stays `Any` and the
# unknown-type checks are moot here. Override handlers reach OVERRIDES through the decorator and are
# never called by name, so pyright also reads them as unused.
# pyright: reportUnusedFunction=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false
from __future__ import annotations

import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, get_args, get_origin

if TYPE_CHECKING:
    import structlog


class ConversionError(Exception):
    """Raised when a source value has no faithful target mapping."""


class _NullLogger:
    """Silent default so the converter needs no logging dependency. Pass a real structlog logger
    via `Context(logger=...)` to capture the trace. Mirrors structlog's `bind` plus the level
    methods so the two are drop-in interchangeable."""

    def bind(self, **_kw: Any) -> _NullLogger:
        return self

    def debug(self, *_a: Any, **_kw: Any) -> None: ...
    def info(self, *_a: Any, **_kw: Any) -> None: ...
    def warning(self, *_a: Any, **_kw: Any) -> None: ...
    def error(self, *_a: Any, **_kw: Any) -> None: ...


@dataclass(slots=True)
class PuidAllocator:
    """Issues synthetic PUIDs for pre-v12 projects, which carry none of their own.

    Numbering is sequential within one conversion, so every issued value is unique there.
    """

    _next: int = 1

    def allocate(self) -> int:
        value = self._next
        self._next += 1
        return value


@dataclass(slots=True)
class Context:
    version: str
    puid: PuidAllocator = field(default_factory=PuidAllocator)
    # Injected structlog logger, or the silent default when none is given.
    logger: structlog.typing.FilteringBoundLogger = field(default_factory=_NullLogger)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Tag every log event with the source version.
        self.logger = self.logger.bind(version=self.version)


# type key (Meta.name, else class name) -> override returning {field_name: value}. An override runs
# ahead of the generic copy and claims the fields it returns; the copy then leaves those alone.
OverrideFn = Callable[[Context, Any], dict[str, Any]]
OVERRIDES: dict[str, OverrideFn] = {}


def override(type_key: str) -> Callable[[OverrideFn], OverrideFn]:
    def register(fn: OverrideFn) -> OverrideFn:
        OVERRIDES[type_key] = fn
        return fn

    return register


def type_key(cls: type) -> str:
    meta = getattr(cls, "Meta", None)
    return (getattr(meta, "name", None) if meta else None) or cls.__name__


# Field renames, target_type_key -> {target_field: source_field}. When no same-named source field
# exists the copy falls back to the alias and converts with the target type, so scalar and nested
# renames work the same way. (Pre-v12 used AddInData/AddInId; the unified model uses the lowercase
# v12+ spelling.)
ALIASES: dict[str, dict[str, str]] = {
    "Project_t": {"addin_data": "add_in_data"},
    "ProjectAddinData": {"addin_data": "add_in_data"},
    "AddinData_t": {"addin_id": "add_in_id"},
    # v13 to v14 renames:
    "ProjectInstallationsInstallation": {"locations": "buildings"},  # was Buildings
    "Locations_t": {"space": "building_part"},  # was BuildingPart
    "Space_t": {"space": "building_part"},  # nested Space
    # v14 to v20 rename:
    "DeviceInstance_t": {
        "is_activity_calculated": "is_communication_object_visibility_calculated"
    },
}


# --- type hints ---

_hints_cache: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _hints_cache:
        _hints_cache[cls] = typing.get_type_hints(cls)
    return _hints_cache[cls]


def _unwrap(hint: Any) -> tuple[bool, Any]:
    """Return (is_list, base_type), stripping Optional wrappers."""

    def strip_optional(h: Any) -> Any:
        if get_origin(h) in (typing.Union, types.UnionType):
            args = [a for a in get_args(h) if a is not type(None)]
            if len(args) == 1:
                return args[0]
        return h

    hint = strip_optional(hint)
    if get_origin(hint) is list:
        (arg,) = get_args(hint)
        return True, strip_optional(arg)
    return False, hint


# --- converter ---


def _union_dataclass_members(hint: Any) -> list[type]:
    """Dataclass members of a Union hint (xs:choice), else []."""
    if get_origin(hint) in (typing.Union, types.UnionType):
        return [a for a in get_args(hint) if isinstance(a, type) and is_dataclass(a)]
    return []


_type_keys: dict[type, str] = {}


def _cached_type_key(cls: type) -> str:
    key = _type_keys.get(cls)
    if key is None:
        key = _type_keys[cls] = type_key(cls)
    return key


def _convert_choice(
    ctx: Context, value: Any, by_key: dict[str, type], is_list: bool
) -> Any:
    """Convert xs:choice items, mapping each to the IR union member sharing its type key
    (``by_key``). Items with no match pass through unchanged."""

    def one(item: Any) -> Any:
        target = by_key.get(_cached_type_key(type(item)))
        return convert(ctx, item, target) if target is not None else item

    return [one(v) for v in value] if is_list else one(value)


# How a target field is filled from its source value.
_COPY, _DATACLASS, _ENUM, _CHOICE = range(4)


@dataclass(frozen=True, slots=True)
class _FieldPlan:
    name: str
    alias: str | None
    is_list: bool
    kind: int
    base: Any
    # xs:choice: the IR union members by type key.
    members: dict[str, type] | None


@dataclass(frozen=True, slots=True)
class _ClassPlan:
    key: str
    fields: tuple[_FieldPlan, ...]
    has_puid: bool


# The plan of a target class depends on the class alone, so it is worked out once per class
# instead of once per converted element.
_plans: dict[type, _ClassPlan] = {}


def _class_plan(target_cls: type) -> _ClassPlan:
    plan = _plans.get(target_cls)
    if plan is not None:
        return plan
    hints = _hints(target_cls)
    key = type_key(target_cls)
    aliases = ALIASES.get(key, {})
    field_plans: list[_FieldPlan] = []
    for f in fields(target_cls):
        is_list, base = _unwrap(hints.get(f.name, f.type))
        members = _union_dataclass_members(base)
        if members:
            kind = _CHOICE
        elif isinstance(base, type) and is_dataclass(base):
            kind = _DATACLASS
        elif isinstance(base, type) and issubclass(base, Enum):
            kind = _ENUM
        else:
            kind = _COPY
        field_plans.append(
            _FieldPlan(
                name=f.name,
                alias=aliases.get(f.name),
                is_list=is_list,
                kind=kind,
                base=base,
                members={type_key(m): m for m in members} if members else None,
            )
        )
    plan = _plans[target_cls] = _ClassPlan(
        key=key,
        fields=tuple(field_plans),
        has_puid=any(f.name == "puid" for f in field_plans),
    )
    return plan


_MISSING = object()


def convert(ctx: Context, src: Any, target_cls: type) -> Any:
    """Construct `target_cls` from `src`: run overrides, then copy matching fields."""
    plan = _class_plan(target_cls)
    key = plan.key
    ctx.logger.debug("convert", src=type(src).__name__, target=key)
    ov = OVERRIDES.get(key)
    produced = ov(ctx, src) if ov is not None else {}
    if produced:
        ctx.logger.debug("convert.override", target=key, owned=sorted(produced))
    kwargs: dict[str, Any] = dict(produced)

    for f in plan.fields:
        if f.name in kwargs:  # claimed by an override
            continue
        src_val: Any = getattr(src, f.name, _MISSING)
        if src_val is _MISSING:
            if f.alias is None:  # no source; leave to default/puid rule
                continue
            src_val = getattr(src, f.alias, _MISSING)
            if src_val is _MISSING:
                continue
        if src_val is None:
            continue
        base = f.base
        if f.kind == _CHOICE:  # xs:choice: convert to the matching IR union type
            assert f.members is not None
            kwargs[f.name] = _convert_choice(ctx, src_val, f.members, f.is_list)
        elif f.kind == _DATACLASS:
            kwargs[f.name] = (
                [convert(ctx, v, base) for v in src_val]
                if f.is_list
                else convert(ctx, src_val, base)
            )
        elif f.kind == _ENUM:
            if f.is_list:
                kwargs[f.name] = [
                    base(v.value) if isinstance(v, Enum) and type(v) is not base else v
                    for v in src_val
                ]
            else:
                kwargs[f.name] = (
                    base(src_val.value)
                    if isinstance(src_val, Enum) and type(src_val) is not base
                    else src_val
                )
        else:
            kwargs[f.name] = src_val

    # Fill a required PUID the source never had.
    if plan.has_puid and kwargs.get("puid") is None:
        kwargs["puid"] = ctx.puid.allocate()
        ctx.logger.debug("convert.puid_synth", target=key, puid=kwargs["puid"])

    return target_cls(**kwargs)


# --- overrides (one per real per-version divergence) ---


@override("MasterData_t")
def _master_data(ctx: Context, src: Any) -> dict[str, Any]:
    """Pre-v12 master data (project/10, /11) carries no ``Id`` attribute, but the unified model
    requires one. Synthesise the canonical ``MD-1`` id used in genuine exports; the value is not referenced
    downstream (the master is consumed for its datapoint/medium/manufacturer tables, not its id)."""
    if getattr(src, "id", None) is None:
        ctx.logger.debug("convert.master_data.id_synth", version=ctx.version)
        return {"id": "MD-1"}
    return {}


@override("GroupAddress_t")
def _group_address(ctx: Context, src: Any) -> dict[str, Any]:
    """v10-v12 held DatapointType as an IDREFS list; the unified model takes a single ref.
    Allow 0 or 1 entries and raise on multi-DPT records instead of dropping data."""
    dpt = getattr(src, "datapoint_type", None)
    if isinstance(dpt, list):
        if len(dpt) > 1:
            ctx.logger.error(
                "convert.group_address.multi_dpt",
                id=getattr(src, "id", "?"),
                count=len(dpt),
                refs=dpt,
            )
            raise ConversionError(
                f"GroupAddress {getattr(src, 'id', '?')}: {len(dpt)} DatapointType refs {dpt}; "
                "unified model allows one"
            )
        return {"datapoint_type": dpt[0] if dpt else None}
    return {}


@override("DeviceInstanceAdditionalAddresses")
def _additional_addresses(ctx: Context, src: Any) -> dict[str, Any]:
    """v10/v11 held each extra address as element text (list[int]); v12+ use an <Address> element.
    Wrap the bare ints into the unified objects."""
    from xknxeditor.namespaces.intermediate.device_instance_t_additional_addresses_address import (
        DeviceInstanceAdditionalAddressesAddress as Addr,
    )

    vals = getattr(src, "address", None) or []
    if vals and all(isinstance(v, int) for v in vals):
        ctx.logger.info("convert.additional_addresses.wrapped_ints", count=len(vals))
        return {"address": [Addr(address=v) for v in vals]}
    return {}  # v12+ already objects; leave to the generic pass


@override("DeviceInstanceBinaryDataBinaryData")
def _binary_data(ctx: Context, src: Any) -> dict[str, Any]:
    """v20 used AutoCopy; v21+ renamed it to DoNotCopy with the polarity flipped.
    Carry it over as DoNotCopy = not AutoCopy."""
    if hasattr(src, "auto_copy"):
        do_not_copy = not bool(src.auto_copy)
        ctx.logger.debug(
            "convert.binary_data.auto_copy_inverted", do_not_copy=do_not_copy
        )
        return {"do_not_copy": do_not_copy}
    return {}


@override("Hardware_t")
def _hardware(ctx: Context, src: Any) -> dict[str, Any]:
    """RF capability changed location twice; the IR stores it per-Hardware2Program:
      - up to v14: RuntimeUnidirectional (transmit-only) plus RFDeviceMode;
      - v20: RFRxCapabilities + RFTxCapabilities on Hardware_t;
      - v21: that pair moved onto each Hardware2Program.
    Work out the Rx/Tx pair from the source (for up to v14, a transmitter always has Tx and has Rx
    unless unidirectional, baseline 'Ready') and push it onto every Hardware2Program missing it.
    v21+ already carry it, so the generic copy takes over."""
    from xknxeditor.namespaces.intermediate.rfrx_capabilities_t import RfrxCapabilities
    from xknxeditor.namespaces.intermediate.rftx_capabilities_t import RftxCapabilities

    rftx = getattr(src, "rftx_capabilities", None)
    rfrx = getattr(src, "rfrx_capabilities", None)
    uni = getattr(src, "runtime_unidirectional", None)
    if (
        rftx is None and rfrx is None and uni is not None
    ):  # up to v14: from RuntimeUnidirectional
        rftx = RftxCapabilities.READY
        rfrx = None if uni else RfrxCapabilities.READY

    if rftx is None and rfrx is None:
        return {}  # v21+ already per-program; generic copy handles it

    source = "runtime_unidirectional" if uni is not None else "hardware_attrs"
    h2ps = getattr(src, "hardware2_programs", None)
    if h2ps is None:
        ctx.logger.warning(
            "convert.hardware.rf_caps_dropped",
            reason="no Hardware2Program to host the capability",
            source=source,
        )
        return {}  # no program to attach it to
    from xknxeditor.namespaces.intermediate.hardware_t_hardware2_programs import (
        HardwareHardware2Programs,
    )

    h2ps_out = convert(
        ctx, h2ps, HardwareHardware2Programs
    )  # take ownership to fill children
    filled = 0
    for prog in getattr(h2ps_out, "hardware2_program", None) or []:
        if getattr(prog, "rftx_capabilities", None) is None:
            prog.rftx_capabilities = rftx
        if getattr(prog, "rfrx_capabilities", None) is None:
            prog.rfrx_capabilities = rfrx
        filled += 1
    ctx.logger.info(
        "convert.hardware.rf_caps_pushed_down",
        source=source,
        rftx=rftx,
        rfrx=rfrx,
        programs=filled,
    )
    return {"hardware2_programs": h2ps_out}


@override("ComObjectInstanceRef_t")
def _com_object_instance_ref(ctx: Context, src: Any) -> dict[str, Any]:
    """v14 used a Connectors element (one Send plus Receive entries, each a GroupAddressRefId with
    an Acknowledge flag); v20 flattened it into Links/Acknowledges attributes. Rebuild them: Links
    is every linked ref, Acknowledges the ones flagged."""
    conn = getattr(src, "connectors", None)
    if conn is None:
        return {}  # v20+ already flattened
    entries = []
    send = getattr(conn, "send", None)
    if send is not None:
        entries.append(send)
    entries += list(getattr(conn, "receive", None) or [])
    links = [
        r for r in (getattr(e, "group_address_ref_id", None) for e in entries) if r
    ]
    acks = [
        getattr(e, "group_address_ref_id", None)
        for e in entries
        if getattr(e, "acknowledge", False) and getattr(e, "group_address_ref_id", None)
    ]
    out: dict[str, Any] = {}
    if links:
        out["links"] = links
    if acks:
        out["acknowledges"] = acks
    ctx.logger.info(
        "convert.com_object_ref.connectors_flattened",
        links=len(links),
        acknowledges=len(acks),
    )
    return out


def fake_hash(value: Any) -> bytes:
    """Stand-in for the unknown loaded-credential hashing algorithm.
    Raises so callers fail loudly instead of storing a wrong hash."""
    raise NotImplementedError(
        f"loaded-credential hashing algorithm unknown; cannot hash {value!r}"
    )


@override("ParameterSeparator_t")
def _parameter_separator(ctx: Context, src: Any) -> dict[str, Any]:
    """Up to v13 used a boolean HorizontalRuler; v14+ replaced it with the UIHint enum.
    Map a set ruler flag onto the enum value."""
    if getattr(src, "horizontal_ruler", None) and getattr(src, "uihint", None) is None:
        from xknxeditor.namespaces.intermediate.parameter_separator_t_uihint import (
            ParameterSeparatorUihint,
        )

        ctx.logger.info("convert.parameter_separator.ruler_to_uihint")
        return {"uihint": ParameterSeparatorUihint.HORIZONTAL_RULER}
    return {}


@override("Security_t")
def _security(ctx: Context, src: Any) -> dict[str, Any]:
    """v14 swapped the plaintext Loaded* credentials for hashed forms; the IR keeps both.
    When only plaintext exists (up to v13) the hash would be derived, but the algorithm is
    unknown so this raises. Plaintext is still copied by the generic pass."""
    out: dict[str, Any] = {}
    for plain, hashed in (
        ("loaded_device_authentication_code", "loaded_device_authentication_code_hash"),
        ("loaded_device_management_password", "loaded_device_management_password_hash"),
    ):
        pv = getattr(src, plain, None)
        if pv is not None and getattr(src, hashed, None) is None:
            out[hashed] = fake_hash(pv)
    return out


@override("TopologyAreaLine")
def _line(ctx: Context, src: Any) -> dict[str, Any]:
    """v21 added a Segment layer: devices and medium attrs moved from the flat line onto a Segment.
    For pre-v21 lines, wrap that content into one synthesized Segment. Line-level attrs stay on the
    line via the generic copy."""
    if getattr(src, "segment", None):
        return {}  # v21+ already segmented; generic copy handles it
    from xknxeditor.namespaces.intermediate.topology_t_area_line_segment import (
        TopologyAreaLineSegment as Seg,
    )

    hints = _hints(Seg)
    seg_kwargs: dict[str, Any] = {}
    for fname in (
        "device_instance",
        "bus_access",
        "additional_group_addresses",
        "medium_type_ref_id",
        "domain_address",
    ):
        val = getattr(src, fname, None)
        if val is None or val == []:
            continue
        is_list, base = _unwrap(hints[fname])
        if isinstance(base, type) and is_dataclass(base):
            seg_kwargs[fname] = (
                [convert(ctx, v, base) for v in val]
                if is_list
                else convert(ctx, val, base)
            )
        else:
            seg_kwargs[fname] = val
    # Give the segment its own id, distinct from the line
    seg_kwargs["id"] = f"{getattr(src, 'id', 'L')}-S1"
    seg_kwargs["number"] = 1
    seg_kwargs["puid"] = ctx.puid.allocate()
    ctx.logger.info(
        "convert.line.wrapped_into_segment",
        line=getattr(src, "id", "?"),
        segment=seg_kwargs["id"],
    )
    return {"segment": [Seg(**seg_kwargs)]}


@override("ProjectInstallationsInstallation")
def _installation(ctx: Context, src: Any) -> dict[str, Any]:
    """Two relocations for the Installation:
    - MulticastTTL moved from per-Line (v10/v11) to per-Installation (v14+); when absent at the
      installation level, lift it from the first line that has one.
    - BusAccess was an installation-wide default (up to v14) that v20 dropped for per-Segment
      config. Push the installation value onto every Segment lacking its own; the per-line value
      that `_line` already placed wins."""
    out: dict[str, Any] = {}
    topo = getattr(src, "topology", None)

    # lift MulticastTTL from v10/v11 lines
    if getattr(src, "multicast_ttl", None) is None:
        for area in getattr(topo, "area", None) or []:
            for line in getattr(area, "line", None) or []:
                ttl = getattr(line, "multicast_ttl", None)
                if ttl is not None:
                    out["multicast_ttl"] = ttl
                    ctx.logger.info(
                        "convert.installation.multicast_ttl_lifted", ttl=ttl
                    )
                    break
            if "multicast_ttl" in out:
                break

    # push installation BusAccess down to segments (up to v14)
    src_ba = getattr(src, "bus_access", None)
    if src_ba is not None and topo is not None:
        from xknxeditor.namespaces.intermediate.bus_access_t import BusAccess
        from xknxeditor.namespaces.intermediate.topology_t import Topology

        topo_out = convert(ctx, topo, Topology)  # take ownership to fill segments
        for area in getattr(topo_out, "area", None) or []:
            for line in getattr(area, "line", None) or []:
                for seg in getattr(line, "segment", None) or []:
                    if getattr(seg, "bus_access", None) is None:
                        seg.bus_access = convert(ctx, src_ba, BusAccess)
                        ctx.logger.info(
                            "convert.installation.busaccess_pushed_down",
                            segment=getattr(seg, "id", "?"),
                        )
        out["topology"] = topo_out

    return out
