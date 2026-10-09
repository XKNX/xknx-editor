from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate import (
    ApplicationProgram,
    ApplicationProgramChannel,
    ApplicationProgramDynamic,
    ApplicationProgramStaticParametersUnion,
    Assign,
    BinaryDataRef,
    Button,
    ChannelChoose,
    ChannelIndependentBlock,
    ComObjectInstanceRef,
    ComObjectParameterBlock,
    ComObjectParameterChoose,
    ComObjectRefRef,
    DependentChannelChoose,
    Module,
    ModuleArg,
    ModuleDefStaticParametersUnion,
    ModuleInstance,
    ParameterInstanceRef,
    ParameterRefRef,
    ParameterSeparator,
    Rename,
    Repeat,
)
from xknxeditor.namespaces.intermediate.module_def_static_t_parameters_union_property import (
    ModuleDefStaticParametersUnionProperty,
)
from xknxeditor.namespaces.intermediate.property_union_t import PropertyUnion

from ..errors import EncodingError
from ..script.errors import ParameterValidationError
from ..script.values import check_value
from .application_indexer import ApplicationIndexer
from .calculation import (
    CalculationScope,
    ChangeSet,
    Journal,
    run_calculations,
    run_calculations_many,
    run_validations,
)
from .context import EvalCapture, EvalContext
from .encode import (
    MemWrite,
    PropertyKey,
    PropWrite,
    Writes,
    build_memory_param_map,
    build_property_param_map,
    collect_writes,
    decode_memory_parameters,
    decode_module_parameters,
    decode_property_parameters,
    encode_to_memory,
    encode_to_memory_masked,
    encode_to_properties,
    resolve_param_values,
    type_size_in_bit,
    validate_parameter_value,
    written_bit_mask,
)
from .nodes import (
    AssignNode,
    BinaryDataRefNode,
    ButtonNode,
    ChannelNode,
    ChooseWhenNode,
    ComObjectParameterBlockNode,
    ComObjectRefRefNode,
    DynamicNode,
    GenericCollectionNode,
    ModuleNode,
    ParameterRefRefNode,
    ParameterSeparatorNode,
    RenameNode,
    RepeatNode,
)
from .state import (
    GlobalState,
    ModuleState,
    ParameterState,
    compute_arg_defaults,
    compute_param_ref_defaults,
)
from .ui import UiNode

if TYPE_CHECKING:
    from ..script.compat.runtime import JScriptEnv
    from ..script.sandbox import AbortToken

__all__ = [
    "AssignNode",
    "BinaryDataRefNode",
    "ButtonNode",
    "ChooseWhenNode",
    "ComObjectRefRefNode",
    "DynamicNode",
    "DynamicTreeBuilder",
    "DynamicUI",
    "EvalContext",
    "GenericCollectionNode",
    "MemWrite",
    "ModuleNode",
    "ParameterRefRefNode",
    "ParameterSeparatorNode",
    "PropWrite",
    "PropertyKey",
    "RenameNode",
    "RepeatNode",
    "Writes",
    "build_memory_param_map",
    "build_property_param_map",
    "collect_writes",
    "decode_memory_parameters",
    "decode_module_parameters",
    "decode_property_parameters",
    "encode_to_memory",
    "encode_to_memory_masked",
    "encode_to_properties",
    "resolve_param_values",
    "written_bit_mask",
]


logger = logging.getLogger(__name__)


class _AppNode(DynamicNode):
    """Top wrapper that seeds global param-ref defaults before the app tree evaluates."""

    def __init__(
        self, subtree: DynamicNode, param_ref_defaults: dict[str, str]
    ) -> None:
        self._subtree = subtree
        self._param_ref_defaults = param_ref_defaults

    def eval(self, ctx: EvalContext) -> list[UiNode]:
        ctx.seed_param_ref_defaults(self._param_ref_defaults)
        return self._subtree.eval(ctx)


class _UnionCompetitors:
    """``parameter_ref_id -> competing parameter-ref ids``, computed when first asked for.

    Only the parameters of Choose elements are ever looked up - a small fraction of all union
    members - so the competitor sets are not built for every member up front.
    """

    __slots__ = ("_by_union", "_cache", "_member_of", "_member_refs", "_members")

    def __init__(
        self,
        members: dict[str, tuple[int, int, int | None]],
        member_refs: dict[str, set[str]],
    ) -> None:
        self._members = members
        self._member_refs = member_refs
        self._member_of = {r: mid for mid, refs in member_refs.items() for r in refs}
        self._by_union: dict[int, list[str]] = {}
        for mid, (key, _s, _z) in members.items():
            self._by_union.setdefault(key, []).append(mid)
        self._cache: dict[str, set[str] | None] = {}

    def _overlap(self, a: str, b: str) -> bool:
        _k1, s1, z1 = self._members[a]
        _k2, s2, z2 = self._members[b]
        # Unknown size (e.g. a value type whose width is implicit/encoding-derived, like a
        # float): compete only when the start bit is identical. Conservative on purpose — this
        # can miss a genuine overlap (two overlapping members both render = duplicate content),
        # but it never over-suppresses (never hides an active member). Under-suppression is the
        # safe failure mode; guessing an implicit width could wrongly hide content.
        if z1 is None or z2 is None:
            return s1 == s2
        return s1 < s2 + z2 and s2 < s1 + z1

    def get(self, ref_id: str | None) -> set[str] | None:
        """The parameter-refs competing with ``ref_id``'s union member, or None if none do."""
        mid = self._member_of.get(ref_id) if ref_id is not None else None
        if mid is None:
            return None
        if mid not in self._cache:
            competitors = {
                r
                for m2 in self._by_union[self._members[mid][0]]
                if m2 != mid and m2 in self._member_refs and self._overlap(mid, m2)
                for r in self._member_refs[m2]
            }
            self._cache[mid] = competitors or None
        return self._cache[mid]


class DynamicTreeBuilder:
    """Constructs an ApplicationProgram's eval tree, pre-resolving Module refs into
    subtrees so evaluation never re-reads the IR."""

    def __init__(self, app: ApplicationProgram) -> None:
        self.idx = ApplicationIndexer(app)
        self._app_id = app.id
        # Param-refs rendered as a widget somewhere in the app, collected during _build. A Choose/Repeat
        # gate parameter that is never a widget (a purely structural/dummy selector) can never be marked
        # active, so it must not gate the capture chain (would wrongly disqualify every object under it).
        # This set is shared by reference into the Choose/Repeat nodes and is complete once _build ends.
        self._widget_param_refs: set[str] = set()
        self._plugin_warned: set[str] = set()
        # Union members share the same memory offset; only one is the "active" overlay at a time.
        # Map each of a union member's parameter-refs to its union siblings' parameter-refs, so a
        # Choose on an inactive union member renders nothing (see ChooseWhenNode). Without this we
        # render every union member's Choose branch, duplicating content (issue: MDT Glas push
        # button "Display mode" — two ViewMode union members both rendered).
        self._union_sibling_refs = self._build_union_sibling_refs(app)
        # Some applications (e.g. simple power supplies / couplers) carry no <Dynamic> section, or
        # one that produces no tree. Such a device has no parameters/objects to show — build an
        # empty tree so it still appears in the project instead of failing to load.
        node = self._build(app.dynamic) if app.dynamic is not None else None
        if node is None:
            node = GenericCollectionNode([])
        global_param_ref_defaults = compute_param_ref_defaults(
            app.static.parameter_refs, self.idx
        )
        self.tree: DynamicNode = _AppNode(node, global_param_ref_defaults)

    def _build_union_sibling_refs(self, app: ApplicationProgram) -> _UnionCompetitors:
        """``parameter_ref_id -> the parameter-ref ids that compete with it for the same union memory``.

        Union members overlay shared memory; only the *active* overlay's Choose should render (else
        both members render, duplicating content — MDT Glas push button "Display mode"). Two members
        of the same Union compete only when their bit ranges OVERLAP — that is how KNX memory-overlays
        are mutually exclusive; members at disjoint offsets are independent sub-fields that can be
        active simultaneously (so they must NOT suppress each other). Multiple ParameterRefs to the
        SAME member are *aliases*, not competitors, so a ref never lists another ref of its own
        member. Covers both application-level and module-definition unions."""
        # member id -> (union key, start_bit, size_bits or None if unknown)
        members: dict[str, tuple[int, int, int | None]] = {}

        def _add_union(union: object, key: int) -> None:
            # RawData reserves a 4-octet length prefix only in a memory address space, not in
            # a property (see type_size_in_bit); use the union's storage kind so a property
            # RawData member is not treated as 4 octets wider than it is (which would let it
            # falsely overlap - and so suppress - an independent neighbouring member).
            for_property = isinstance(
                getattr(union, "choice", None),
                (PropertyUnion, ModuleDefStaticParametersUnionProperty),
            )
            for up in getattr(union, "parameter", []):
                start = (getattr(up, "offset", 0) or 0) * 8 + (
                    getattr(up, "bit_offset", 0) or 0
                )
                size: int | None = None
                pt_id = getattr(up, "parameter_type", None)
                pt = self.idx.parameter_types.get(pt_id) if pt_id else None
                choice = getattr(pt, "choice", None) if pt is not None else None
                if choice is not None:
                    # Sizeless types (Float/Date/IPAddress/Color/RawData) carry no
                    # size_in_bit attribute; derive their width the same way the writer
                    # does so union overlap detection sees the real octet span.
                    size = type_size_in_bit(choice, for_property=for_property)
                members[up.id] = (key, start, size)

        params = app.static.parameters
        if params is not None:
            for p in params.choice:
                if isinstance(p, ApplicationProgramStaticParametersUnion):
                    _add_union(p, id(p))
        for md in self.idx.module_defs.values():
            mstatic = getattr(md, "static", None)
            mparams = getattr(mstatic, "parameters", None) if mstatic else None
            if mparams is not None:
                for p in mparams.choice:
                    if isinstance(p, ModuleDefStaticParametersUnion):
                        _add_union(p, id(p))
        # member id -> its parameter-ref ids
        member_refs: dict[str, set[str]] = {}
        for pr in self.idx.parameter_refs.values():
            if pr.ref_id in members:  # pr.ref_id is the underlying member id
                member_refs.setdefault(pr.ref_id, set()).add(pr.id)
        return _UnionCompetitors(members, member_refs)

    def _build(self, elem: object) -> DynamicNode | None:
        if isinstance(elem, ApplicationProgramDynamic):
            return GenericCollectionNode([self._build(child) for child in elem.choice])
        elif isinstance(elem, ChannelIndependentBlock):
            return ChannelNode(
                [self._build(child) for child in elem.choice],
                id=f"{self._app_id}_general",
                name="General",
            )
        elif isinstance(elem, ApplicationProgramChannel):
            return ChannelNode(
                [self._build(child) for child in elem.choice],
                id=elem.id,
                name=elem.name,
                text=elem.text,
                number=elem.number,
                icon=elem.icon,
                text_parameter_ref_id=elem.text_parameter_ref_id,
            )
        elif isinstance(elem, ComObjectParameterBlock):
            # The importer labels a block with its heading parameter's (ParamRefId) Text when it has one — e.g.
            # a channel-prefixed "A: Drive" — in preference to the block's generic Name ("Jalousie X: …").
            heading_text: str | None = None
            if elem.param_ref_id:
                pr = self.idx.parameter_refs.get(elem.param_ref_id)
                param = self.idx.parameters.get(pr.ref_id) if pr else None
                heading_text = (getattr(pr, "text", None) or None) or (
                    param.text if param else None
                )
            return ComObjectParameterBlockNode(
                elem,
                [self._build(child) for child in elem.choice],
                heading_text,
            )
        elif isinstance(
            elem, (DependentChannelChoose, ChannelChoose, ComObjectParameterChoose)
        ):
            # Choose blocks pick content by parameter value: DependentChannelChoose
            # toggles root-level channels, ChannelChoose the content inside a channel,
            # ComObjectParameterChoose the content inside a parameter block.
            default_nodes: list[DynamicNode | None] | None = None
            condition_to_nodes: dict[str, list[DynamicNode | None]] = {}
            for when in elem.when:
                built = [self._build(node) for node in when.choice]
                if when.default:
                    # Module definitions can repeat a branch (e.g. the same test resolves per
                    # module instance); merge rather than assert so the device still loads.
                    default_nodes = (default_nodes or []) + built
                if when.test is not None:
                    condition_to_nodes.setdefault(when.test, []).extend(built)
            return ChooseWhenNode(
                elem.param_ref_id,
                condition_to_nodes,
                default_nodes,
                self._widget_param_refs,
                self._union_sibling_refs.get(elem.param_ref_id),
            )
        elif isinstance(elem, Repeat):
            # TODO: index substitution for non-Module children still missing
            return RepeatNode(
                elem,
                [self._build(child) for child in elem.choice],
                self._widget_param_refs,
            )
        elif isinstance(elem, Module):
            ref_id = elem.ref_id
            mod_def = self.idx.module_defs.get(ref_id)
            if mod_def is None or mod_def.dynamic is None:
                return None
            children = [self._build(child) for child in mod_def.dynamic.choice]
            arguments: dict[str, ModuleArg] = {arg.ref_id: arg for arg in elem.choice}
            param_ref_defaults = compute_param_ref_defaults(
                mod_def.static.parameter_refs if mod_def.static else None, self.idx
            )
            arg_defaults = compute_arg_defaults(mod_def.arguments, list(elem.choice))
            arg_names = (
                {a.id: a.name for a in mod_def.arguments.argument if a.name}
                if mod_def.arguments is not None
                else {}
            )
            return ModuleNode(
                elem.id,
                GenericCollectionNode(children),
                ref_id,
                arguments,
                param_ref_defaults,
                arg_defaults,
                arg_names,
            )
        elif isinstance(elem, ParameterRefRef):
            # Leaf parameter widget; resolve ParameterRef/Parameter/ParameterType now
            pr = self.idx.parameter_refs.get(elem.ref_id)
            assert pr is not None, f"ParameterRef {elem.ref_id!r} not found in static"
            param = self.idx.parameters.get(pr.ref_id)
            assert param is not None, f"Parameter {pr.ref_id!r} not found in static"
            pt = self.idx.parameter_types.get(param.parameter_type)
            assert pt is not None, (
                f"ParameterType {param.parameter_type!r} not found in static"
            )
            if pt.plugin and pt.id not in self._plugin_warned:
                self._plugin_warned.add(pt.id)
                logger.warning(
                    "ParameterType %s uses unsupported plugin %s; shown read-only",
                    pt.id,
                    pt.plugin,
                )
            # Record this ref as widget-rendered so Choose/Repeat gates on it count toward activeness.
            self._widget_param_refs.add(elem.ref_id)
            return ParameterRefRefNode(elem, pr, param, pt)
        elif isinstance(elem, ComObjectRefRef):
            cor = self.idx.com_object_refs.get(elem.ref_id)
            co = self.idx.com_objects.get(cor.ref_id) if cor else None
            return ComObjectRefRefNode(elem, cor, co)
        elif isinstance(elem, ParameterSeparator):
            # Leaf: label or divider between parameters
            return ParameterSeparatorNode(elem)
        elif isinstance(elem, Button):
            # Leaf: button wired to a load-procedure action
            return ButtonNode(elem)
        elif isinstance(elem, BinaryDataRef):
            # Leaf: binary blob from the application's Static section
            return BinaryDataRefNode(elem)
        elif isinstance(elem, Assign):
            # Leaf: pins a parameter to a fixed value; state-only, no UI
            return AssignNode(elem)
        elif isinstance(elem, Rename):
            # Leaf: renames a channel or element inside a choose branch
            return RenameNode(elem)
        return None


def _com_object_number(ref_id: str) -> str:
    """A com-object ref canonicalized by dropping ONLY the terminal ComObjectRef qualifier
    (``_R-<m>``), while preserving any module-instance path (``MD-…_M-…_MI-…_O-…``).

    Two refs of the SAME object differ only in ``R-``; the imported instance and the parameter-driven
    derivation can pick a different ``R-`` for one object (e.g. after a channel is deactivated and
    re-activated), so section activeness is matched on this identity, not the full ref. Stripping
    ONLY the terminal ``_R-<m>`` (not down to ``O-<n>``) keeps distinct module instances distinct —
    otherwise one stored ``O-2`` would keep every repeated module instance's ``O-2`` section alive."""
    head, sep, tail = ref_id.rpartition("_")
    if sep and tail.startswith("R-"):
        return head
    return ref_id


def _subtree_activeness(
    node: UiNode, instantiated: set[str], instantiated_nums: set[str]
) -> tuple[bool, bool]:
    """Return ``(has_com_object, has_instantiated_com_object)`` for ``node``'s subtree.

    A subtree com-object counts as instantiated if its full ref matches, OR its ComObject id
    (``O-<n>``) matches an instantiated object (see :func:`_com_object_number`)."""
    from .ui import UiComObject, UiParameterBlock, UiTab

    has_co = has_inst = False
    stack: list[UiNode] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, UiComObject):
            has_co = True
            if (
                current.ref_id in instantiated
                or _com_object_number(current.ref_id) in instantiated_nums
            ):
                has_inst = True
        elif isinstance(current, (UiTab, UiParameterBlock)):
            stack.extend(current.children)
    return has_co, has_inst


def _collect_refs(node: UiNode, params: set[str], cos: set[str]) -> None:
    """Add every UiParameter and UiComObject ref id in ``node``'s subtree."""
    from .ui import UiComObject, UiParameter, UiParameterBlock, UiTab

    stack: list[UiNode] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, UiComObject):
            cos.add(current.ref_id)
        elif isinstance(current, UiParameter):
            params.add(current.ref_id)
        elif isinstance(current, (UiTab, UiParameterBlock)):
            stack.extend(current.children)


def _prune_inactive(
    nodes: list[UiNode],
    instantiated: set[str],
    instantiated_nums: set[str],
    dropped_params: set[str],
    dropped_cos: set[str],
    kept_params: set[str],
    kept_cos: set[str],
) -> list[UiNode]:
    """Drop inactive-channel sections; record dropped vs kept parameter/com-object refs.

    A ``UiTab``/``UiParameterBlock`` whose subtree carries com objects but none is
    instantiated is dropped (its refs go to ``dropped_*``). Surviving containers are
    pruned recursively; their leaf refs go to ``kept_*`` so a ref shared with a live
    section is never deactivated."""
    import dataclasses

    from .ui import UiComObject, UiParameter, UiParameterBlock, UiTab

    result: list[UiNode] = []
    for node in nodes:
        if isinstance(node, (UiTab, UiParameterBlock)):
            has_co, has_inst = _subtree_activeness(
                node, instantiated, instantiated_nums
            )
            if has_co and not has_inst:
                _collect_refs(node, dropped_params, dropped_cos)
                continue
            pruned = _prune_inactive(
                list(node.children),
                instantiated,
                instantiated_nums,
                dropped_params,
                dropped_cos,
                kept_params,
                kept_cos,
            )
            result.append(dataclasses.replace(node, children=tuple(pruned)))
        else:
            if isinstance(node, UiComObject):
                kept_cos.add(node.ref_id)
            elif isinstance(node, UiParameter):
                kept_params.add(node.ref_id)
            result.append(node)
    return result


def _as_int(value: object) -> int | None:
    try:
        number = float(str(value))
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


class DynamicUI:
    __slots__ = ("_app", "_idx", "_state", "_tree", "_ui", "script_abort", "script_env")

    def __init__(
        self,
        app: ApplicationProgram,
        parameter_instance_refs: list[ParameterInstanceRef] | None = None,
        module_instances: list[ModuleInstance] | None = None,
        com_object_instance_refs: list[ComObjectInstanceRef] | None = None,
        tree_builder: DynamicTreeBuilder | None = None,
    ) -> None:
        # The builder (indexer + node tree) is a pure function of the app program and is read-only
        # during eval, so callers share one across all devices of the same app (see
        # Application.tree_builder) instead of rebuilding it per device.
        builder = tree_builder if tree_builder is not None else DynamicTreeBuilder(app)
        self._app = app
        self._tree = builder.tree
        self._idx = builder.idx
        self._state = GlobalState.from_project(
            parameter_instance_refs=parameter_instance_refs,
            module_instances=module_instances,
            com_object_instance_refs=com_object_instance_refs,
        )
        self._ui: list[UiNode] | None = None
        self.script_env: JScriptEnv | None = None
        self.script_abort: AbortToken | None = None

    def _discover_union_activity(self) -> None:
        """Discovery pass ahead of a render eval: evaluate the tree WITHOUT union suppression so every
        reached Union member is marked active, then freeze that as the discovery snapshot. The render
        pass reads it (is_discovered_active) to pick each Union's active overlay order-independently —
        the reached member wins, not a sibling carrying a stale explicit value from an inactive
        branch (which would drop a freshly-activated member's content)."""
        self._state.reset_active()
        self._tree.eval(EvalContext(self._state, idx=self._idx, union_suppress=False))
        self._state.snapshot_discovered_active()

    def ui(self) -> list[UiNode]:
        if self._ui is None:
            self._discover_union_activity()
            self._state.reset_active()
            self._ui = self._tree.eval(EvalContext(self._state, idx=self._idx))
            self._state.trim_to_active()
            self._prune_inactive_channels()
        return self._ui

    def _prune_inactive_channels(self) -> None:
        """Drop UI sections of channels the device did not instantiate.

        When a project provides the instantiated com objects (an imported device),
        a section that carries com objects of which none is instantiated is an
        inactive channel: the parameter-driven UI over-activates it at its defaults
        (e.g. the individual A/B/C/D channels of a dimmer set to "2x Tunable White"),
        but activeness is derived from the stored group objects. Such sections are
        removed from the UI and their parameters/com objects (those exclusive to the
        dropped sections) are deactivated, so they are neither shown nor encoded."""
        instantiated = self._state.com_obj_instance_ref_ids()
        if not instantiated or self._ui is None:
            return
        instantiated_nums = {_com_object_number(r) for r in instantiated}
        dropped_params: set[str] = set()
        dropped_cos: set[str] = set()
        kept_params: set[str] = set()
        kept_cos: set[str] = set()
        self._ui = _prune_inactive(
            self._ui,
            instantiated,
            instantiated_nums,
            dropped_params,
            dropped_cos,
            kept_params,
            kept_cos,
        )
        # Only deactivate refs that do not also occur in a surviving section.
        self._state.discard_active_refs(
            dropped_params - kept_params, dropped_cos - kept_cos
        )

    def eval_unpruned_ui(self) -> list[UiNode]:
        """Evaluate the dynamic tree against the CURRENT parameters WITHOUT the inactive-channel
        prune, returning the resulting UI nodes. Used to compute the parameter-driven "should-exist"
        com-object set — ``_prune_inactive_channels`` biases toward the stale saved instances, so it
        must be skipped here. Invalidates the cached ``ui()`` so the next call recomputes (and
        re-prunes) cleanly."""
        self._discover_union_activity()
        self._state.reset_active()
        tree = self._tree.eval(EvalContext(self._state, idx=self._idx))
        self._state.trim_to_active()
        self._ui = None
        return tree

    def com_objects_controlled_by(self, param_ref_id: str) -> set[str]:
        """The (instance-qualified) com-object ref-ids that ``param_ref_id`` gates at the CURRENT
        parameter value: every com-object emitted under a Choose/Repeat driven by that parameter.
        Invalidates the cached ``ui()`` so the next call recomputes cleanly."""
        capture = EvalCapture(param_ref_id)
        self._discover_union_activity()
        self._state.reset_active()
        self._tree.eval(EvalContext(self._state, idx=self._idx, capture=capture))
        self._state.trim_to_active()
        self._ui = None
        return capture.controlled_ref_ids()

    def active_parameter_driven_com_object_ref_ids(self) -> set[str]:
        """The com-object ref-ids the device should instantiate for its CURRENT parameter values —
        the parameter-driven active set, matching what a genuine import materialises.

        Computed in a single eval via chain-AND: an object is included iff some emission of it has EVERY
        Choose/Repeat gate on its path driven by an ACTIVE parameter (a parameter is active iff it is
        rendered as a widget somewhere), or it is ungated (an unconditional/global object). This keeps
        channels a function genuinely activates (their selector parameter is active) and the
        always-present objects, while excluding the channel over-activation of the raw tree — e.g. an
        individual channel whose objects sit under an outer selector that is NOT active because its
        widget only lives in a different function branch. Invalidates the cached ``ui()``."""
        capture = EvalCapture(None)  # record every emitted object's gate chain
        self._discover_union_activity()
        self._state.reset_active()
        self._tree.eval(EvalContext(self._state, idx=self._idx, capture=capture))
        self._state.trim_to_active()
        self._ui = None
        active = self._state.active_param_refs() or set()
        return capture.active_ref_ids(frozenset(active))

    def get_module_instances(self) -> list[tuple[str, str]]:
        """After eval, list ``(instance_id, ref_id)`` per top-level module instance."""
        self.ui()
        return [(iid, rid) for iid, rid, _ in self._state.module_instances()]

    def set_com_obj_instance_ref(self, ref_id: str, coir: ComObjectInstanceRef) -> None:
        self._state.set_com_obj_instance_ref(ref_id, coir)
        self._ui = None

    def instantiated_com_object_ref_ids(self) -> set[str]:
        """Com-object ref ids the device actually instantiated (from saved project state).

        Empty for a device configured from scratch. A download uses this as the
        authoritative set of active com objects (matching the device GroupObjectTree),
        rather than the parameter-driven visible set, which can over-activate objects
        of channel modes the device is not in."""
        return self._state.com_obj_instance_ref_ids()

    def segment_base_addrs(self) -> dict[str, int]:
        return {
            sid: self._idx.segment_base_addr(sid) for sid in self._idx.code_segments
        }

    def segment_sizes(self) -> dict[str, int]:
        """Byte length of each code segment, independent of parameter encoding.

        Recover sizes its read-back from these (how many bytes to read at each segment
        base, and the com object table extent). Unlike :meth:`encode_to_memory_masked`
        this runs no value encoding, so a default that cannot be encoded never blanks out
        the whole read.
        """
        return {
            sid: len(seg.data) if seg.data else seg.size
            for sid, seg in self._idx.code_segments.items()
        }

    def encode_to_memory(self) -> dict[str, bytes]:
        """Pack the current parameter state into per-segment byte buffers."""
        self.ui()  # refresh state
        self.validate_parameter_values()
        return encode_to_memory(
            self._app,
            self._idx,
            resolve_param_values(self._idx, self._state),
            self._state,
        )

    def encode_to_memory_masked(self) -> dict[str, tuple[bytes, bytes]]:
        """Encode into ``{segment_id: (data, mask)}``; mask marks written bytes."""
        self.ui()  # refresh state
        self.validate_parameter_values()
        return encode_to_memory_masked(
            self._app,
            self._idx,
            resolve_param_values(self._idx, self._state),
            self._state,
        )

    def decode_memory_parameters(
        self, segments: Mapping[str, bytes]
    ) -> dict[str, str | None]:
        """Best-effort inverse of :meth:`encode_to_memory` for recovering values.

        Decodes each top-level static memory parameter's value from the given
        segment bytes (read off a device). ``None`` marks a parameter whose type
        cannot be reconstructed from bytes alone; module-instanced parameters are
        skipped (see :func:`decode_memory_parameters`).
        """
        self.ui()
        return decode_memory_parameters(self._app, self._idx, segments, self._state)

    def decode_property_parameters(
        self, properties: Mapping[PropertyKey, bytes]
    ) -> dict[str, str | None]:
        """Best-effort inverse of :meth:`encode_to_properties` for recovering values.

        Decodes each top-level static property-backed parameter's value from the
        given property bytes (read off a device). ``None`` marks an unreconstructable
        value; module-instanced parameters are skipped (see
        :func:`decode_property_parameters`).
        """
        self.ui()
        return decode_property_parameters(self._app, self._idx, properties, self._state)

    def decode_module_parameters(
        self,
        segments: Mapping[str, bytes],
        properties: Mapping[PropertyKey, bytes],
    ) -> dict[str, str | None]:
        """Decode module-instance parameter values from device memory/properties.

        Uses this UI's evaluated module instances (seed it from the recovered
        top-level parameters first, so the instances match the device). Returns
        ``{qualified_parameter_ref_id: value}``; ``None`` marks unreconstructable
        values.
        """
        self.ui()
        return decode_module_parameters(
            self._app, self._idx, self._state, segments, properties
        )

    def memory_param_map(self) -> dict[str, dict[int, tuple[str, str]]]:
        """Map {seg_id: {byte_offset: (param_id, value)}} for hex-viewer hovers."""
        self.ui()
        return build_memory_param_map(
            self._app,
            self._idx,
            resolve_param_values(self._idx, self._state),
            self._state,
        )

    def written_bit_mask(self) -> dict[str, bytes]:
        """Return {seg_id: bit_mask} marking the bits an active parameter writes.

        Bit granular (one bit per written bit), unlike the byte-granular mask of
        :meth:`encode_to_memory_masked`. A pre-flight uses it to tell a real
        parameter value apart from a bit only rewritten to the segment seed.
        """
        self.ui()
        return written_bit_mask(
            self._app,
            self._idx,
            resolve_param_values(self._idx, self._state),
            self._state,
        )

    def encode_to_properties(self) -> dict[PropertyKey, bytes]:
        """Pack PropertyParameter-backed values into interface-object property data."""
        self.ui()
        self.validate_parameter_values()
        return encode_to_properties(
            self._app,
            self._idx,
            resolve_param_values(self._idx, self._state),
            self._state,
        )

    def property_param_map(self) -> dict[PropertyKey, dict[int, tuple[str, str]]]:
        """Map {(object_index, property_id, occurrence): {byte_offset: (param_id, value)}}."""
        self.ui()
        return build_property_param_map(
            self._app,
            self._idx,
            resolve_param_values(self._idx, self._state),
            self._state,
        )

    def get_parameter_ref(self, ref_id: str) -> str | None:
        """Current value of a parameter ref in this UI state."""
        return self.get_value(ref_id)

    def _locate(self, ref_id: str) -> tuple[ParameterState, str]:
        found = self._state.find_scope_for_qualified(ref_id)
        if (
            found is None
            and ref_id not in self._idx.parameter_refs
            and self._ui is None
        ):
            self.ui()
            found = self._state.find_scope_for_qualified(ref_id)
        return found if found is not None else (self._state, ref_id)

    def get_value(self, ref_id: str) -> str | None:
        """Value of a (possibly module-qualified) parameter ref, falling back to its default."""
        scope, local = self._locate(ref_id)
        value = scope.get(local)
        return value if value is not None else self._idx.default_value(local)

    def validate_parameter_values(self) -> None:
        """Check resolved values, including selectors without storage."""
        for ref_id, value in self._state.relative_param_values():
            ref = self._idx.parameter_refs.get(ref_id)
            if ref is None:
                raise EncodingError(f"unknown parameter ref {ref_id!r}")
            parameter = self._idx.parameters[ref.ref_id]
            tc = self._idx.parameter_types[parameter.parameter_type].choice
            try:
                validate_parameter_value(value, tc)
            except ValueError as exc:
                raise EncodingError(f"parameter {ref_id}: {exc}") from exc

    @property
    def text_encoding(self) -> str:
        options = self._app.static.options
        encoding = options.text_parameter_encoding if options is not None else None
        return encoding.value if encoding is not None else "iso-8859-1"

    def _journal(self) -> Journal:
        def explicit(ref_id: str) -> str | None:
            scope, local = self._locate(ref_id)
            return scope.explicit_value(local)

        def write(ref_id: str, value: str | None) -> None:
            if value is None:
                self._state.clear_instance_ref(ref_id)
            else:
                self._state.set_instance_ref(ref_id, value)

        return Journal(self.get_value, explicit, write)

    def _validate(
        self, scope: ParameterState, local: str, ref_id: str, value: str
    ) -> None:
        try:
            run_validations(
                self._idx,
                local,
                value,
                self._calc_scope(scope),
                self.script_env,
                self.script_abort,
            )
        except ParameterValidationError as exc:
            exc.ref_id = ref_id
            raise

    def _change(
        self,
        ref_id: str,
        value: str,
        *,
        strict: bool,
        validate: bool,
        raise_calc_errors: bool,
        require_active: bool,
    ) -> ChangeSet:
        scope, local = self._locate(ref_id)
        tc = self._idx.type_of(local)
        if local not in self._idx.parameter_refs or tc is None:
            raise ValueError(f"unknown parameter ref {ref_id!r}")
        if strict:
            try:
                value = check_value(value, tc, text_encoding=self.text_encoding)
            except ValueError as exc:
                text = self._idx.type_error_text(local)
                raise ParameterValidationError(text or str(exc), ref_id=ref_id) from exc
        else:
            value = validate_parameter_value(value, tc)
        if require_active:
            if strict:
                self.ui()
            active = self._state.active_param_refs()
            if active and ref_id not in active:
                raise ValueError(
                    f"parameter ref {ref_id!r} is not active in the current UI state"
                )
        journal = self._journal()
        try:
            if validate:
                self._validate(scope, local, ref_id, value)
            journal.set(ref_id, value)
            run_calculations(
                self._idx,
                local,
                self._calc_scope(scope),
                journal,
                self.script_env,
                raise_errors=raise_calc_errors,
                abort=self.script_abort,
            )
        except BaseException:
            journal.rollback()
            self._ui = None
            raise
        self._ui = None
        return journal.changes()

    def _calc_scope(self, scope: ParameterState) -> CalculationScope:
        def get(local_ref: str) -> str | None:
            v = scope.get(local_ref)
            return v if v is not None else self._idx.default_value(local_ref)

        env = self.script_env
        return CalculationScope(
            get=get,
            qualify=scope.qualify_local,
            locale=env.locale if env is not None else None,
            text_encoding=self.text_encoding,
        )

    def recalculate(self, ref_ids: Iterable[str]) -> ChangeSet:
        """Run the calculation plans of ``ref_ids`` without changing them; errors are logged.

        One union plan per scope, so a calculation shared by several of ``ref_ids`` runs once over
        their combined state instead of once per ref (which would feed intermediates back in)."""
        journal = self._journal()
        for scope, locals_ in self._group_by_scope(ref_ids):
            run_calculations_many(
                self._idx,
                locals_,
                self._calc_scope(scope),
                journal,
                self.script_env,
                raise_errors=False,
                abort=self.script_abort,
            )
        self._ui = None
        return journal.changes()

    def _group_by_scope(
        self, ref_ids: Iterable[str]
    ) -> list[tuple[ParameterState, list[str]]]:
        groups: dict[int, tuple[ParameterState, list[str]]] = {}
        for ref_id in ref_ids:
            scope, local = self._locate(ref_id)
            if local in self._idx.parameter_refs:
                groups.setdefault(id(scope), (scope, []))[1].append(local)
        return list(groups.values())

    def _apply_direct_edit(
        self, journal: Journal, ref_id: str, value: str, *, validate: bool
    ) -> tuple[ParameterState, str]:
        """Strict-check, validate and journal one user edit (no calculations)."""
        scope, local = self._locate(ref_id)
        tc = self._idx.type_of(local)
        if local not in self._idx.parameter_refs or tc is None:
            raise ValueError(f"unknown parameter ref {ref_id!r}")
        try:
            value = check_value(value, tc, text_encoding=self.text_encoding)
        except ValueError as exc:
            text = self._idx.type_error_text(local)
            raise ParameterValidationError(text or str(exc), ref_id=ref_id) from exc
        self.ui()
        active = self._state.active_param_refs()
        if active and ref_id not in active:
            raise ValueError(
                f"parameter ref {ref_id!r} is not active in the current UI state"
            )
        if validate:
            self._validate(scope, local, ref_id, value)
        journal.set(ref_id, value)
        self._ui = None
        return scope, local

    def edit_parameters(
        self,
        edits: Sequence[tuple[str, str]],
        *,
        validate: bool = True,
        skip_invalid: bool = False,
    ) -> ChangeSet:
        """Apply a batch of user edits, then run each affected calculation once.

        All direct edits are applied before any calculation runs, so a calculation reached by more
        than one edit's chain is evaluated a single time over the final input values instead of once
        per edit. ``skip_invalid`` drops the edits that fail (each applied and calculated on its own,
        so one rejected edit does not discard the rest); otherwise the batch is all-or-nothing."""
        if skip_invalid:
            return self._edit_best_effort(edits, validate=validate)
        journal = self._journal()
        groups: dict[int, tuple[ParameterState, list[str]]] = {}
        try:
            for ref_id, value in edits:
                scope, local = self._apply_direct_edit(
                    journal, ref_id, value, validate=validate
                )
                groups.setdefault(id(scope), (scope, []))[1].append(local)
            for scope, locals_ in groups.values():
                run_calculations_many(
                    self._idx,
                    locals_,
                    self._calc_scope(scope),
                    journal,
                    self.script_env,
                    raise_errors=True,
                    abort=self.script_abort,
                )
        except BaseException:
            journal.rollback()
            self._ui = None
            raise
        self._ui = None
        return journal.changes()

    def _edit_best_effort(
        self, edits: Sequence[tuple[str, str]], *, validate: bool
    ) -> ChangeSet:
        """Apply edits one at a time, dropping any that fail (value, validation or calculation)."""
        done: ChangeSet = {}
        try:
            for ref_id, value in edits:
                try:
                    changes = self.edit_parameter(ref_id, value, validate=validate)
                except ValueError:
                    continue
                for ref, (old, new) in changes.items():
                    done[ref] = (done[ref][0] if ref in done else old, new)
        except BaseException:
            self.apply_parameter_values({ref: old for ref, (old, _) in done.items()})
            raise
        return {ref: change for ref, change in done.items() if change[0] != change[1]}

    def set_parameter_ref(self, ref_id: str, value: str) -> ChangeSet:
        """Set an active parameter and run its calculations; calculation errors are logged."""
        return self._change(
            ref_id,
            value,
            strict=False,
            validate=False,
            raise_calc_errors=False,
            require_active=True,
        )

    def edit_parameter(
        self, ref_id: str, value: str, *, validate: bool = True
    ) -> ChangeSet:
        """A user edit: strict type check, validations and calculations, atomic on error."""
        return self._change(
            ref_id,
            value,
            strict=True,
            validate=validate,
            raise_calc_errors=True,
            require_active=True,
        )

    def write_parameter(self, ref_id: str, value: str) -> ChangeSet:
        """A script write: like an edit, but inactive parameters may be written."""
        return self._change(
            ref_id,
            value,
            strict=True,
            validate=True,
            raise_calc_errors=True,
            require_active=False,
        )

    def apply_parameter_values(self, values: Mapping[str, str | None]) -> None:
        """Set (or clear, for ``None``) values without checks or calculations."""
        for ref_id, value in values.items():
            if value is None:
                self._state.clear_instance_ref(ref_id)
            else:
                self._state.set_instance_ref(ref_id, value)
        self._ui = None

    def active_parameter_ref_ids(self) -> frozenset[str]:
        self.ui()
        return frozenset(self._state.active_param_refs())

    @property
    def indexer(self) -> ApplicationIndexer:
        return self._idx

    @property
    def application_name(self) -> str:
        return self._app.name

    def local_ref_id(self, ref_id: str) -> str:
        """The ParameterRef id a (possibly module-qualified) ref id stands for."""
        return self._locate(ref_id)[1]

    def find_parameter_ref(
        self, kind: str, key: object, instance_id: str | None = None
    ) -> str | None:
        """Qualified ref for a script lookup by ``name``, ``id`` or ``number``: the module
        instance's definition is searched first, then the application."""
        scopes: list[tuple[str | None, ParameterState]] = []
        if instance_id:
            found = self._state.find_scope_for_qualified(instance_id + "_")
            if (
                found is not None
                and isinstance(found[0], ModuleState)
                and found[0].ref_id is not None
            ):
                scopes.append((found[0].ref_id, found[0]))
        scopes.append((None, self._state))
        idx = self._idx
        for owner, scope in scopes:
            local: str | None = None
            if kind == "name":
                local = idx.refs_by_name.get(owner, {}).get(str(key))
            elif kind == "number":
                number = _as_int(key)
                if number is not None:
                    local = idx.refs_by_number.get(owner, {}).get(number)
            else:
                text = str(key)
                for cand in (text, f"{owner or self._app.id}_{text}"):
                    if cand in idx.parameter_refs and idx.ref_owner.get(cand) == owner:
                        local = cand
                        break
            if local is not None:
                return scope.qualify(local) if owner is not None else local
        return None

    def is_parameter_active(self, ref_id: str) -> bool:
        return ref_id in self.active_parameter_ref_ids()
