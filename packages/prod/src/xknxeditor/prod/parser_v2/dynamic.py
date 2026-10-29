from __future__ import annotations

import contextlib
import copy
import logging
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
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
from .allocator import AllocatorOverflow
from .application_indexer import ApplicationIndexer
from .calculation import (
    VALIDATION_FAILED,
    CalculationScope,
    ChangeSet,
    Journal,
    run_calculations,
    run_calculations_many,
    run_validations,
)
from .context import EvalCapture, EvalContext, Explorer, Placement
from .converted_pages import place_hidden_parameters
from .encode import (
    MaskedProperty,
    MemWrite,
    PropertyKey,
    PropWrite,
    TypedPropertyKey,
    Writes,
    build_memory_param_map,
    build_property_param_map,
    collect_writes,
    decode_memory_parameters,
    decode_module_parameters,
    decode_property_parameters,
    download_param_values,
    encode_to_memory,
    encode_to_memory_masked,
    encode_to_properties,
    encode_to_typed_properties,
    encode_to_typed_properties_masked,
    resolve_param_values,
    segment_seed,
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
    compute_param_ref_bases,
    compute_param_ref_defaults,
)
from .text_encoding import text_parameter_encoding
from .ui import UiNode

if TYPE_CHECKING:
    from ..script.compat.runtime import JScriptEnv
    from ..script.sandbox import AbortToken

# Passes after which the active parameters are taken as settled.
_MAX_GATE_PASSES = 5

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
    "TypedPropertyKey",
    "Writes",
    "build_memory_param_map",
    "build_property_param_map",
    "collect_writes",
    "decode_memory_parameters",
    "decode_module_parameters",
    "decode_property_parameters",
    "download_param_values",
    "encode_to_memory",
    "encode_to_memory_masked",
    "encode_to_properties",
    "encode_to_typed_properties",
    "encode_to_typed_properties_masked",
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
        # A module definition's subtree, built once and shared by all its instances: nodes hold no
        # per-instance state (the instance comes from the evaluation context).
        self._module_subtrees: dict[str, list[DynamicNode | None]] = {}
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
        # Page membership follows the selector chain, not XML block nesting: a parameter's page is
        # found by walking the selector of its nearest enclosing choose up the chain of selectors
        # until one names a ParameterBlock (its ParamRefId). ``_page_params`` are those block refs;
        # ``_nearest_placement`` maps each ref to the selector of the choose it is PLACED in (its
        # ParameterRefRef, or a block heading) - where the owning page is decided. A ref placed
        # outside any choose has no entry and keeps its XML-nested placement (it is never re-homed
        # from where a choose merely USES it). Computed once; used by :meth:`page_param_for`.
        self._page_params, self._nearest_placement = self._build_page_maps(app)

    def _build_page_maps(
        self, app: ApplicationProgram
    ) -> tuple[set[str], dict[str, str]]:
        """``(page-parameter refs, nearest-choose-selector per PLACEMENT)`` for selector-chain page
        assignment (see :meth:`page_param_for`)."""
        page_params: set[str] = set()
        placement: dict[str, str] = {}

        def walk(elem: object, selector: str | None) -> None:
            if isinstance(elem, ComObjectParameterBlock):
                ctx = selector
                if elem.param_ref_id:
                    page_params.add(elem.param_ref_id)
                    # A block heading's owning page comes from where the block is placed.
                    if selector is not None:
                        placement.setdefault(elem.param_ref_id, selector)
                    # A parameter placed directly in this block belongs to it - not to the choose
                    # that gates the block from outside. So descend with the block as the context;
                    # a deeper choose inside the block still overrides it for its own children.
                    ctx = elem.param_ref_id
                for child in elem.choice:
                    walk(child, ctx)
            elif isinstance(
                elem, (DependentChannelChoose, ChannelChoose, ComObjectParameterChoose)
            ):
                # A choose only resolves its selector; it does not decide the owning page of that
                # selector (that comes from where the selector's own widget is placed), so nothing
                # is recorded here - only the descent carries the new selector downward.
                inner = elem.param_ref_id or selector
                for when in elem.when:
                    for child in when.choice:
                        walk(child, inner)
            elif isinstance(elem, ParameterRefRef):
                if selector is not None:
                    placement.setdefault(elem.ref_id, selector)
            elif isinstance(
                elem,
                (
                    ApplicationProgramDynamic,
                    ChannelIndependentBlock,
                    ApplicationProgramChannel,
                    Repeat,
                ),
            ):
                for child in elem.choice:
                    walk(child, selector)
            # Modules are instanced at runtime (their ref ids become qualified), so their internal
            # page assignment stays with the XML nesting; we do not descend into them here.

        if app.dynamic is not None:
            walk(app.dynamic, None)
        return page_params, placement

    def page_param_for(self, ref_id: str) -> str | None:
        """The ParamRefId of the ParameterBlock that owns ``ref_id``, or ``None`` when the parameter
        is not gated toward a page (keep its XML-nested placement). Resolve by the placement chain:
        each ref's owning page is where its widget/block is placed, never where a choose merely uses
        it as a selector."""
        selector = self._nearest_placement.get(ref_id)
        seen: set[str] = set()
        while selector is not None and selector not in seen:
            if selector in self._page_params:
                return selector
            seen.add(selector)
            selector = self._nearest_placement.get(selector)
        return None

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
                independent=True,
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
            children = self._module_subtrees.get(ref_id)
            if children is None:
                children = [self._build(child) for child in mod_def.dynamic.choice]
                self._module_subtrees[ref_id] = children
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
                compute_param_ref_bases(
                    mod_def.static.parameter_refs if mod_def.static else None, self.idx
                ),
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


def _param_page_ids(
    nodes: Sequence[UiNode],
    page: str | None = None,
    acc: dict[str, str | None] | None = None,
) -> dict[str, str | None]:
    """Map each parameter's ref id to the id of the ParameterBlock it currently sits under (``None``
    when directly under a tab or the root). Used to tell which parameters a re-home moved."""
    from .ui import UiParameter, UiParameterBlock, UiTab

    if acc is None:
        acc = {}
    for node in nodes:
        if isinstance(node, UiParameter):
            acc[node.ref_id] = page
        elif isinstance(node, UiParameterBlock):
            _param_page_ids(node.children, node.id, acc)
        elif isinstance(node, UiTab):
            _param_page_ids(node.children, page, acc)
    return acc


def _rehome_to_pages(
    nodes: list[UiNode],
    page_for: Callable[[str], str | None],
    display_order: Callable[[str], int | None],
) -> list[UiNode]:
    """Move each parameter onto the page its selector chain names, rather than the ParameterBlock it
    is nested under in the dynamic XML.

    A parameter's page is the block whose ParamRefId its enclosing ``choose`` selectors chain up to
    (``page_for``), which can differ from the block it physically sits in. This re-homes such
    parameters so the displayed pages match the selector model. Presentation-only: it reorders an
    already-settled UI tree and changes neither which parameters/objects are active nor their values.

    Guards keep it safe to run on every application:

    * a destination must be a NON-inline, NON-hidden block whose ParamRefId is unique among the
      present blocks — an ambiguous ParamRefId (e.g. repeated blocks) is skipped so a parameter is
      never duplicated across several blocks;
    * a parameter keeps its enclosing NON-inline page, so one already on the right page is not pulled
      out of an inline/read-only group inside it;
    * a parameter under a hidden block is left where it is (a hidden page's own relocation stands);
    * moved parameters merge into the destination by display order without disturbing the block's
      existing children."""
    import dataclasses

    from .ui import UiParameter, UiParameterBlock, UiTab

    # The single eligible destination block per ParamRefId: a block is eligible only when it is a
    # real page (not inline), is shown (not hidden, nor under a hidden ancestor). A ParamRefId
    # carried by more than one such block is ambiguous and excluded (value ``None``). Insertion keys
    # on this exact block instance, so an inline/hidden namesake sharing the ParamRefId never
    # receives a copy.
    eligible_block: dict[str, UiParameterBlock | None] = {}

    def scan(ns: Iterable[UiNode], hidden_anc: bool) -> None:
        for n in ns:
            if isinstance(n, UiParameterBlock):
                hidden = hidden_anc or n.hidden
                if n.param_ref_id and not n.inline and not hidden:
                    eligible_block[n.param_ref_id] = (
                        None if n.param_ref_id in eligible_block else n
                    )
                scan(n.children, hidden)
            elif isinstance(n, UiTab):
                scan(n.children, hidden_anc)

    scan(nodes, False)
    eligible = {ref for ref, block in eligible_block.items() if block is not None}
    if not eligible:
        return nodes

    moved_ids: set[int] = set()
    moved_by_target: dict[str, list[UiParameter]] = {}

    def plan(ns: Iterable[UiNode], page: str | None, hidden_anc: bool) -> None:
        for n in ns:
            if isinstance(n, UiParameter):
                target = page_for(n.ref_id)
                if (
                    not hidden_anc
                    and target is not None
                    and target in eligible
                    and target != page
                ):
                    moved_ids.add(id(n))
                    moved_by_target.setdefault(target, []).append(n)
            elif isinstance(n, UiParameterBlock):
                # A non-inline block is the page for its contents; an inline group keeps the
                # enclosing page so its members are not lifted out of it.
                inner = n.param_ref_id if (n.param_ref_id and not n.inline) else page
                plan(n.children, inner, hidden_anc or n.hidden)
            elif isinstance(n, UiTab):
                plan(n.children, page, hidden_anc)

    plan(nodes, None, False)
    if not moved_ids:
        return nodes

    def merge(kept: tuple[UiNode, ...], extra: list[UiParameter]) -> tuple[UiNode, ...]:
        # Insert the moved parameters among the kept children by display order, leaving the existing
        # children (and any separators/sub-blocks) in their original relative positions.
        extra = sorted(extra, key=lambda p: display_order(p.ref_id) or 0)
        out: list[UiNode] = []
        i = 0
        for child in kept:
            if isinstance(child, UiParameter):
                order = display_order(child.ref_id) or 0
                while i < len(extra) and (display_order(extra[i].ref_id) or 0) < order:
                    out.append(extra[i])
                    i += 1
            out.append(child)
        out.extend(extra[i:])
        return tuple(out)

    def rebuild(n: UiNode) -> UiNode:
        if isinstance(n, UiParameterBlock):
            kept = tuple(rebuild(c) for c in n.children if id(c) not in moved_ids)
            # Insert only into the one eligible block instance for this ParamRefId - not every
            # block that happens to share it (an inline/hidden namesake would otherwise duplicate).
            if n.param_ref_id and eligible_block.get(n.param_ref_id) is n:
                extra = moved_by_target.get(n.param_ref_id, [])
                if extra:
                    kept = merge(kept, extra)
            return dataclasses.replace(n, children=kept)
        if isinstance(n, UiTab):
            kept = tuple(rebuild(c) for c in n.children if id(c) not in moved_ids)
            return dataclasses.replace(n, children=kept)
        return n

    return [rebuild(n) for n in nodes if id(n) not in moved_ids]


class DynamicUI:
    __slots__ = (
        "_active_snapshot",
        "_app",
        "_idx",
        "_page_for",
        "_state",
        "_tree",
        "_ui",
        "project_code_page",
        "script_abort",
        "script_env",
    )

    def __init__(
        self,
        app: ApplicationProgram,
        parameter_instance_refs: list[ParameterInstanceRef] | None = None,
        module_instances: list[ModuleInstance] | None = None,
        com_object_instance_refs: list[ComObjectInstanceRef] | None = None,
        tree_builder: DynamicTreeBuilder | None = None,
        project_code_page: str | None = None,
    ) -> None:
        # The builder (indexer + node tree) is a pure function of the app program and is read-only
        # during eval, so callers share one across all devices of the same app (see
        # Application.tree_builder) instead of rebuilding it per device.
        builder = tree_builder if tree_builder is not None else DynamicTreeBuilder(app)
        self._app = app
        # The device's project ``CodePage`` (None: the system language); some applications encode
        # their text parameters with it (see :attr:`text_encoding`).
        self.project_code_page = project_code_page
        self._tree = builder.tree
        self._idx = builder.idx
        self._page_for = builder.page_param_for
        self._state = GlobalState.from_project(
            parameter_instance_refs=parameter_instance_refs,
            module_instances=module_instances,
            com_object_instance_refs=com_object_instance_refs,
            app_id=app.id,
        )
        self._ui: list[UiNode] | None = None
        # The parameter-driven active com-object set, snapshotted from the same settle that builds
        # ``_ui`` (before the inactive-channel prune, matching the standalone derivation). Lets a
        # read of the active set reuse the UI settle instead of running its own, and is cleared
        # together with ``_ui`` on every state change (see ``_invalidate``).
        self._active_snapshot: tuple[EvalCapture | None, frozenset[str]] | None = None
        self.script_env: JScriptEnv | None = None
        self.script_abort: AbortToken | None = None

    def _invalidate(self) -> None:
        """Drop the cached UI and the active-set snapshot after a state change."""
        self._ui = None
        self._active_snapshot = None

    def _discover_union_activity(self) -> None:
        """Discovery pass ahead of a render eval: evaluate the tree WITHOUT union suppression so every
        reached Union member is marked active, then freeze that as the discovery snapshot. The render
        pass reads it (is_discovered_active) to pick each Union's active overlay order-independently —
        the reached member wins, not a sibling carrying a stale explicit value from an inactive
        branch (which would drop a freshly-activated member's content)."""
        self._state.clear_gate()
        self._state.reset_active()
        self._tree.eval(EvalContext(self._state, idx=self._idx, union_suppress=False))
        self._state.snapshot_discovered_active()
        self._state.update_gate_active()

    def _eval_settled(
        self, capture: Callable[[], EvalCapture] | None = None
    ) -> tuple[list[UiNode], EvalCapture | None]:
        """Evaluate until the active parameters no longer change.

        Each pass gates Chooses by the params active in the pass before, so a Choose renders
        nothing when its param is not active, regardless of where in the tree the param is
        shown or assigned. ``capture`` makes a fresh capture for each pass.
        """
        self._discover_union_activity()
        tree: list[UiNode] = []
        result: EvalCapture | None = None
        for _ in range(_MAX_GATE_PASSES):
            self._state.reset_active()
            result = capture() if capture is not None else None
            tree = self._tree.eval(
                EvalContext(self._state, idx=self._idx, capture=result)
            )
            if not self._state.update_gate_active():
                break
        return tree, result

    def _build_ui(self, *, capture_active: bool) -> None:
        """Settle the tree and cache the pruned UI. When ``capture_active`` is set, also record
        the parameter-driven active com-object set from the SAME settle (before the prune), so a
        following :meth:`active_parameter_driven_com_object_ref_ids` reuses this evaluation instead
        of running another full settle."""
        capture = (lambda: EvalCapture(None)) if capture_active else None
        self._ui, result = self._eval_settled(capture)
        self._state.trim_to_active()
        if capture_active:
            active = frozenset(self._state.active_param_refs() or set())
            self._active_snapshot = (result, active)
        self._prune_inactive_channels()
        # Final presentation step: assign each parameter to its page. State/activeness are already
        # settled above, so this only reorders the displayed tree. A parameter's page is named by
        # its selector chain where the chain places it on a different page than the block it is
        # nested under in the raw XML.
        before = _param_page_ids(self._ui)
        self._ui = _rehome_to_pages(self._ui, self._page_for, self._display_order)
        if self._app.pre_ets4_style:
            # Converted-from-older-format applications also carry a display order: a parameter the
            # selector chain did not move belongs to the page heading in front of it by display
            # order (e.g. a threshold setting left inline on its channel). Re-bucket those, keeping
            # the ones the re-home already placed on their page.
            rehomed = frozenset(
                ref
                for ref, page in _param_page_ids(self._ui).items()
                if before.get(ref) != page
            )
            self._ui = place_hidden_parameters(
                self._ui, self._display_order, pinned=rehomed
            )

    def ui(self) -> list[UiNode]:
        if self._ui is None:
            self._build_ui(capture_active=False)
        assert self._ui is not None
        return self._ui

    def _display_order(self, ref_id: str) -> int | None:
        ref = self._idx.parameter_refs.get(ref_id)
        return ref.display_order if ref is not None else None

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
        tree, _ = self._eval_settled()
        self._state.trim_to_active()
        self._invalidate()
        return tree

    def com_objects_controlled_by(self, param_ref_id: str) -> set[str]:
        """The (instance-qualified) com-object ref-ids that ``param_ref_id`` gates at the CURRENT
        parameter value: every com-object emitted under a Choose/Repeat driven by that parameter.
        Invalidates the cached ``ui()`` so the next call recomputes cleanly."""
        _, capture = self._eval_settled(lambda: EvalCapture(param_ref_id))
        self._state.trim_to_active()
        self._invalidate()
        return capture.controlled_ref_ids() if capture is not None else set()

    def active_parameter_driven_com_object_ref_ids(self) -> set[str]:
        """The com-object ref-ids the device should instantiate for its CURRENT parameter values —
        the parameter-driven active set, matching what a genuine import materialises.

        Computed in a single eval via chain-AND: an object is included iff some emission of it has EVERY
        Choose/Repeat gate on its path driven by an ACTIVE parameter (a parameter is active iff it is
        rendered as a widget somewhere), or it is ungated (an unconditional/global object). This keeps
        channels a function genuinely activates (their selector parameter is active) and the
        always-present objects, while excluding the channel over-activation of the raw tree — e.g. an
        individual channel whose objects sit under an outer selector that is NOT active because its
        widget only lives in a different function branch. Shares the UI settle via the active-set
        snapshot (see :meth:`_build_ui`), so it does not run its own evaluation when ``ui()`` is
        current for the same state."""
        if self._active_snapshot is None:
            self._build_ui(capture_active=True)
        assert self._active_snapshot is not None
        capture, active = self._active_snapshot
        return capture.active_ref_ids(active) if capture is not None else set()

    def get_module_instances(self) -> list[tuple[str, str]]:
        """After eval, list ``(instance_id, ref_id)`` per top-level module instance."""
        self.ui()
        return [(iid, rid) for iid, rid, _ in self._state.module_instances()]

    def get_module_instance_arguments(self) -> dict[str, list[tuple[str, str]]]:
        """After eval, the arguments of each top-level module instance as a project stores them:
        ``(argument ref id relative to the application, value)``, numbers resolved (allocators and
        base values applied)."""
        self.ui()
        result: dict[str, list[tuple[str, str]]] = {}
        for iid, _, args in self._state.module_instances():
            values: list[tuple[str, str]] = []
            for ref_id, arg in args.items():
                value = getattr(arg, "value", None)
                if value is not None:
                    values.append((ref_id, str(value)))
            result[iid] = values
        return result

    def set_com_obj_instance_ref(self, ref_id: str, coir: ComObjectInstanceRef) -> None:
        self._state.set_com_obj_instance_ref(ref_id, coir)
        self._invalidate()

    def instantiated_com_object_ref_ids(self) -> set[str]:
        """Com-object ref ids the device actually instantiated (from saved project state).

        Empty for a device configured from scratch. A download uses this as the
        authoritative set of active com objects (matching the device GroupObjectTree),
        rather than the parameter-driven visible set, which can over-activate objects
        of channel modes the device is not in."""
        return self._state.com_obj_instance_ref_ids()

    def declared_com_objects(self) -> list[dict[str, object]]:
        """Every com-object the application DECLARES (application- and module-level), read from the
        static index — a SUPERSET of the currently active objects, for planning before switching a
        function. Values are the static declarations: ``name``/``function_text`` are templates (no
        parameter or module-argument substitution) and ``number`` is the base (a module instance
        offsets it). This is NOT a reachability proof — a declared object may never be activatable in
        a given configuration; the active, linkable set is the UI's own com-objects."""
        from .nodes.com_object_ref_ref import dpt_codes

        out: list[dict[str, object]] = []
        for ref_id, cor in self._idx.com_object_refs.items():
            co = self._idx.com_objects.get(cor.ref_id)
            size = cor.object_size or (co.object_size if co else None)
            out.append(
                {
                    "ref_id": ref_id,
                    "number": co.number if co else 0,
                    "name": cor.text
                    or (co.text if co else None)
                    or cor.name
                    or (co.name if co else None)
                    or "",
                    "function_text": cor.function_text
                    or (co.function_text if co else None)
                    or "",
                    "dpt": list(
                        dpt_codes(
                            cor.datapoint_type or [],
                            co.datapoint_type if co else [],
                        )
                    ),
                    "object_size": size.value if size is not None else "",
                }
            )
        return out

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
            sid: len(segment_seed(seg)) for sid, seg in self._idx.code_segments.items()
        }

    def encode_to_memory(self) -> dict[str, bytes]:
        """Pack the current parameter state into per-segment byte buffers."""
        self.ui()  # refresh state
        self.validate_parameter_values()
        return encode_to_memory(
            self._app,
            self._idx,
            download_param_values(self._app, self._idx, self._state),
            self._state,
            self.text_encoding,
        )

    def encode_to_memory_masked(self) -> dict[str, tuple[bytes, bytes]]:
        """Encode into ``{segment_id: (data, mask)}``; mask marks written bytes."""
        self.ui()  # refresh state
        self.validate_parameter_values()
        return encode_to_memory_masked(
            self._app,
            self._idx,
            download_param_values(self._app, self._idx, self._state),
            self._state,
            self.text_encoding,
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
        return decode_memory_parameters(
            self._app, self._idx, segments, self._state, self.text_encoding
        )

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
        return decode_property_parameters(
            self._app, self._idx, properties, self._state, self.text_encoding
        )

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
            self._app, self._idx, self._state, segments, properties, self.text_encoding
        )

    def memory_param_map(self) -> dict[str, dict[int, tuple[str, str]]]:
        """Map {seg_id: {byte_offset: (param_id, value)}} for hex-viewer hovers."""
        self.ui()
        return build_memory_param_map(
            self._app,
            self._idx,
            download_param_values(self._app, self._idx, self._state),
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
            download_param_values(self._app, self._idx, self._state),
            self._state,
        )

    def encode_to_properties(self) -> dict[PropertyKey, bytes]:
        """Pack PropertyParameter-backed values into interface-object property data."""
        self.ui()
        self.validate_parameter_values()
        return encode_to_properties(
            self._app,
            self._idx,
            download_param_values(self._app, self._idx, self._state),
            self._state,
            self.text_encoding,
        )

    def encode_to_typed_properties(self) -> dict[TypedPropertyKey, bytes]:
        """:meth:`encode_to_properties` keyed with the object type of objects addressed by type."""
        self.ui()
        self.validate_parameter_values()
        return encode_to_typed_properties(
            self._app,
            self._idx,
            download_param_values(self._app, self._idx, self._state),
            self._state,
            self.text_encoding,
        )

    def encode_to_typed_properties_masked(
        self,
    ) -> dict[TypedPropertyKey, MaskedProperty]:
        """:meth:`encode_to_typed_properties` with what the parameters cover."""
        self.ui()
        self.validate_parameter_values()
        return encode_to_typed_properties_masked(
            self._app,
            self._idx,
            download_param_values(self._app, self._idx, self._state),
            self._state,
            self.text_encoding,
        )

    def property_param_map(self) -> dict[PropertyKey, dict[int, tuple[str, str]]]:
        """Map {(object_index, property_id, occurrence): {byte_offset: (param_id, value)}}."""
        self.ui()
        return build_property_param_map(
            self._app,
            self._idx,
            download_param_values(self._app, self._idx, self._state),
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
        """The codec of this device's ``Text`` parameters (see :func:`text_parameter_encoding`)."""
        return text_parameter_encoding(self._app, self.project_code_page)

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
            # Settle the tree so active_param_refs() reflects the CURRENT configuration — the
            # non-strict path (script set_parameter_ref) used to skip this and relied on a truthiness
            # fallthrough when the active set was still empty.
            self.ui()
            active = self._state.active_param_refs()
            # Reject when the ref is not active; a settled device with an EMPTY active set must not
            # bypass the guard (the older truthiness fallthrough did).
            if ref_id not in active:
                raise ValueError(
                    f"parameter ref {ref_id!r} is not active in the current UI state"
                )
        # The settle below also runs the Assigns of what the value turns on; a rejection returns
        # to this state, as the journal undoes only the values the change wrote.
        before = copy.deepcopy(self._state) if strict else None
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
            self._invalidate()
            if strict:
                # A value that leaves an allocator too few numbers (e.g. more channels than it
                # holds) is rejected with the allocator's message, as a failed validation. The
                # settle is the one the next read needs anyway.
                self._build_ui(capture_active=True)
        except AllocatorOverflow as exc:
            journal.rollback()
            if before is not None:
                self._state = before
            self._invalidate()
            raise self._overflow_rejection(exc, ref_id) from exc
        except BaseException:
            journal.rollback()
            self._invalidate()
            raise
        return journal.changes()

    def _overflow_rejection(
        self, exc: AllocatorOverflow, ref_id: str
    ) -> ParameterValidationError:
        """The rejection of an edit an allocator has too few numbers for, with its message."""
        message = self._idx.messages.get(exc.message_ref or "")
        return ParameterValidationError(
            (message.text if message is not None else None) or VALIDATION_FAILED,
            ref_id=ref_id,
        )

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
        self._invalidate()
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
        # Reject when the ref is not active; a settled device with an EMPTY active set must not
        # bypass the guard (an older truthiness fallthrough did).
        if ref_id not in active:
            raise ValueError(
                f"parameter ref {ref_id!r} is not active in the current UI state"
            )
        if validate:
            self._validate(scope, local, ref_id, value)
        journal.set(ref_id, value)
        self._invalidate()
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
        before = copy.deepcopy(self._state)  # see _change
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
            # Evaluated inside the batch, so an allocator overflow rejects it (see _change).
            self._invalidate()
            self._build_ui(capture_active=True)
        except AllocatorOverflow as exc:
            journal.rollback()
            self._state = before
            self._invalidate()
            raise self._overflow_rejection(exc, edits[-1][0]) from exc
        except BaseException:
            journal.rollback()
            self._invalidate()
            raise
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
        self._invalidate()

    def active_parameter_ref_ids(self) -> frozenset[str]:
        self.ui()
        return frozenset(self._state.active_param_refs())

    def explore_parameters(self) -> list[Placement]:
        """Every place of every parameter in the tree, whatever the current values select, with
        the gates leading there (see :class:`Explorer`). Evaluated on a copy of the state."""
        explorer = Explorer()
        state = copy.deepcopy(self._state)
        # The copy holds the allocator positions where the last evaluation ended.
        state.reset_alloc_positions()
        self._tree.eval(
            EvalContext(state, idx=self._idx, union_suppress=False, explore=explorer)
        )
        return explorer.placements

    @contextlib.contextmanager
    def simulation(self) -> Generator[None]:
        """Edits made inside the block are undone when it is left: the state, the UI and the
        active set are the ones from before."""
        state = self._state
        ui = self._ui
        snapshot = self._active_snapshot
        self._state = copy.deepcopy(state)
        self._invalidate()
        try:
            yield
        finally:
            self._state = state
            self._ui = ui
            self._active_snapshot = snapshot

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
                # Full, owner-relative ("P-1_R-1") or application-relative ("MD-1_P-1_R-1").
                for cand in (
                    text,
                    f"{owner or self._app.id}_{text}",
                    f"{self._app.id}_{text}",
                ):
                    if cand in idx.parameter_refs and idx.ref_owner.get(cand) == owner:
                        local = cand
                        break
            if local is not None:
                return scope.qualify(local) if owner is not None else local
        return None

    def is_parameter_active(self, ref_id: str) -> bool:
        return ref_id in self.active_parameter_ref_ids()
