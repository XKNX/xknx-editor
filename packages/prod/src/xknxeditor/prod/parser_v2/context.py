from __future__ import annotations

import threading
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xknxeditor.namespaces.intermediate import ModuleArg
from xknxeditor.namespaces.intermediate.com_object_instance_ref_t import (
    ComObjectInstanceRef,
)

if TYPE_CHECKING:
    from .application_indexer import ApplicationIndexer
    from .state import ParameterState
    from .ui import UiNode
    from .ui.parameter import UiParameter


@dataclass(frozen=True, slots=True)
class Gate:
    """A condition on the way to a part of the parameter tree: the (instance-qualified) parameter
    ``param`` has to take a value that satisfies the ``when`` test, or, for a default branch
    (``when`` is None), none of the ``unless`` tests. With ``needs_active`` (a choose) the
    parameter itself has to be shown or assigned too, or its choose selects nothing; a repeat
    count does not."""

    param: str
    when: str | None
    unless: tuple[str, ...] = ()
    needs_active: bool = True


@dataclass(frozen=True, slots=True)
class Placement:
    """One place of a parameter in the tree, with the gates leading to it and the headings of the
    tabs and blocks around it (outermost first). ``hidden_section`` is set when a block on the way
    is never shown (Access None)."""

    parameter: UiParameter
    gates: tuple[Gate, ...]
    sections: tuple[str, ...]
    hidden_section: bool = False


class Explorer:
    """Records every parameter placement of a tree evaluated in explore mode: each choose
    evaluates all its branches and each repeat at least one pass, whatever the current values,
    pushing the condition of the branch it is in (see :class:`Gate`). ``check`` is called
    every ``CHECK_EVERY`` branches and placements: an exception it raises ends the pass (the
    pass of a large application takes long)."""

    __slots__ = ("_check", "_gates", "_hidden", "_sections", "_steps", "placements")

    CHECK_EVERY = 256

    def __init__(self, check: Callable[[], None] | None = None) -> None:
        self._gates: list[Gate] = []
        self._sections: list[str] = []
        self._hidden = 0
        self._check = check
        self._steps = 0
        self.placements: list[Placement] = []

    def _step(self) -> None:
        if self._check is not None:
            self._steps += 1
            if self._steps % self.CHECK_EVERY == 0:
                self._check()

    def push_gate(self, gate: Gate) -> None:
        self._step()
        self._gates.append(gate)

    def pop_gate(self) -> None:
        self._gates.pop()

    def enter_section(self, heading: str, *, hidden: bool = False) -> None:
        self._sections.append(heading)
        self._hidden += hidden

    def leave_section(self, *, hidden: bool = False) -> None:
        self._sections.pop()
        self._hidden -= hidden

    def record(self, parameter: UiParameter) -> None:
        self._step()
        self.placements.append(
            Placement(
                parameter,
                tuple(self._gates),
                tuple(s for s in self._sections if s),
                self._hidden > 0,
            )
        )


@dataclass(slots=True)
class ObjectTreeNode:
    """A node of a device's group object tree: a channel or a parameter block shown in the group
    object tree (``kind`` "Channel" or "Folder", ``ref_id`` its instance-qualified id), with its
    group objects (instance-qualified ComObjectRef ids) and its nodes, both in document order.
    ``text`` is the evaluated heading. The tree's root has neither kind nor id."""

    kind: str
    ref_id: str
    objects: list[str]
    nodes: list[ObjectTreeNode]
    text: str = ""


class ObjectTreeCapture:
    """Records a device's group object tree while its parameters are evaluated, as the KNX tool
    builds a device's ``GroupObjectTree``: each active channel is a node (also without objects),
    each active parameter block shown in the group object tree a folder (dropped when it stays
    empty), and each active group object belongs to the nearest of these, else to the tree
    itself, once, in document order."""

    __slots__ = ("_stack", "pre_ets4_style", "root")

    def __init__(self, *, pre_ets4_style: bool = False) -> None:
        self.root = ObjectTreeNode("", "", [], [])
        self._stack = [self.root]
        # A converted application's single "Generic" channel is no node.
        self.pre_ets4_style = pre_ets4_style

    def enter(self, kind: str, ref_id: str) -> ObjectTreeNode:
        node = ObjectTreeNode(kind, ref_id, [], [])
        self._stack[-1].nodes.append(node)
        self._stack.append(node)
        return node

    def leave(self) -> None:
        node = self._stack.pop()
        if node.kind == "Folder" and not node.objects and not node.nodes:
            self._stack[-1].nodes.remove(node)

    def record(self, ref_id: str) -> None:
        objects = self._stack[-1].objects
        if ref_id not in objects:
            objects.append(ref_id)


class EvalCapture:
    """Records, per emitted com-object, the set of gating parameters on its emission path (its "gate
    chain") during a full tree eval.

    Choose/Repeat nodes ``push`` their gating parameter while evaluating their selected branch and
    ``pop`` after; a com-object leaf calls ``record_object`` to snapshot the current gate chain. An
    object is parameter-driven "active" iff some emission has every gate on its chain driven by an
    active parameter (chain-AND); an ungated object has an empty chain and is always active.

    ``tracked=None`` records every object; a str/iterable restricts recording to emissions whose gate
    chain contains one of those parameters (used to find exactly what a single parameter controls)."""

    __slots__ = ("_stack", "_tracked", "chains")

    def __init__(self, tracked: str | Iterable[str] | None = None) -> None:
        if tracked is None:
            self._tracked: frozenset[str] | None = None
        elif isinstance(tracked, str):
            self._tracked = frozenset({tracked})
        else:
            self._tracked = frozenset(tracked)
        self._stack: list[str] = []
        self.chains: dict[str, list[frozenset[str]]] = {}

    def push(self, param_ref_id: str) -> None:
        self._stack.append(param_ref_id)

    def pop(self) -> None:
        self._stack.pop()

    def record_object(self, ref_id: str) -> None:
        chain = frozenset(self._stack)
        if self._tracked is None or (self._tracked & chain):
            self.chains.setdefault(ref_id, []).append(chain)

    def controlled_ref_ids(self) -> set[str]:
        """The com-object ref-ids recorded (for a ``tracked`` capture: those the tracked parameter(s)
        gate)."""
        return set(self.chains)

    def active_ref_ids(self, active_params: frozenset[str] | set[str]) -> set[str]:
        """Ref-ids with at least one emission whose entire gate chain is active (chain-AND)."""
        return {
            ref
            for ref, chains in self.chains.items()
            if any(chain <= active_params for chain in chains)
        }


class _TextArgs(Mapping[str, str]):
    """The module arguments of a scope, plus parameter-ref values by unique number (looked up
    only when a text asks for one). ``{{0}}`` is not a number here: it is the text parameter."""

    __slots__ = ("_args", "_ctx")

    def __init__(self, ctx: EvalContext) -> None:
        self._ctx = ctx
        self._args = ctx.get_arg_defaults()

    def __getitem__(self, key: str) -> str:
        value = self.lookup(key)
        tape = self._ctx._tape  # pyright: ignore[reportPrivateUsage]
        if tape is not None:
            tape.append(("x", key, value))
        if value is None:
            raise KeyError(key)
        return value

    def lookup(self, key: str) -> str | None:
        """What ``key`` stands for, None for nothing; read without recording it."""
        if key in self._args:
            return self._args[key]
        if key.isdigit() and int(key) != 0:
            ctx = self._ctx
            ref = ctx.ref_by_number(int(key))
            if ref is not None:
                return ctx.value_or_default(ref, record=False) or ""
        return None

    def __iter__(self) -> Iterator[str]:
        return iter(self._args)

    def __len__(self) -> int:
        return len(self._args)


# The gate decisions a pass made (see :meth:`EvalContext.is_gate_active`): for each Choose
# evaluated, its scope, its parameter and whether it could select a branch.
GateChecks = list[tuple["ParameterState", str, bool]]

# What an evaluation read and did, in order (see :class:`EvalMemo`): ``(kind, key, result)``.
# Reads: "g" a value, "a" whether a Choose may select (a gate decision, recorded as one too),
# "d" whether a union member was reached in the discovery, "t" a text, "o" an instance's
# group object settings, "v" a module argument, "q" / "l" a qualified id, "x" a text
# argument (None: none). Writes: "p" / "c" a parameter / group object marked active, "u" a
# Choose on a union member reached (with its siblings), "h" a node handed out (the node), "s"
# a text set (a Rename of a block).
Event = tuple[str, object, object]

_READS = frozenset("dxtqlov")

# A recording with at most this many reads is read again rather than told unchanged from
# what changed (see EvalMemo.unchanged).
_READ_AGAIN = 8

_NONE: frozenset[object] = frozenset()


class Recording:
    """What an evaluation of a node read and did (its :data:`Event` s), grouped to check and to
    do again (see :meth:`EvalContext.replay`): none of it depends on the order (the reads give
    the same in any order, the marks are sets, the gate decisions and union chooses are only
    asked whether any or all are so). Gives the UiNodes ``output``."""

    __slots__ = (
        "checked_by_change",
        "discovered_keys",
        "events",
        "gate_keys",
        "gates",
        "handed",
        "handed_ids",
        "object_reads",
        "object_set",
        "objects",
        "output",
        "parameter_set",
        "parameters",
        "reads",
        "suppress",
        "text_args",
        "text_keys",
        "texts",
        "unions",
        "valid_in",
        "value_keys",
        "values",
    )

    def __init__(
        self, suppress: bool, events: tuple[Event, ...], output: tuple[UiNode, ...]
    ) -> None:
        self.suppress = suppress
        self.events = events
        self.output = output
        values: list[tuple[object, object]] = []
        gates: list[tuple[object, object]] = []
        reads: list[Event] = []
        texts: list[tuple[object, object]] = []
        handed: list[object] = []
        parameters: list[object] = []
        objects: list[object] = []
        unions: list[tuple[object, object]] = []
        # A text read after the evaluation set it (a block its Rename retitles) gives what
        # was set: nothing to check.
        written: set[object] = set()
        for event in events:
            kind, key, result = event
            if kind == "p":
                parameters.append(key)
            elif kind == "g":
                values.append((key, result))
            elif kind == "a":
                gates.append((key, result))
            elif kind == "c":
                objects.append(key)
            elif kind == "h":
                handed.append(key)
            elif kind == "s":
                written.add(key)
                texts.append((key, result))
            elif kind == "u":
                unions.append((key, result))
            elif kind in _READS and not (kind == "t" and key in written):
                reads.append(event)
        self.values = tuple(values)
        self.gates = tuple(gates)
        self.reads = tuple(reads)
        # In their order: of two texts set for the same, the last one stays.
        self.texts = tuple(texts)
        self.handed = tuple(handed)
        self.handed_ids = frozenset(id(node) for node in handed)
        self.parameters = tuple(parameters)
        self.parameter_set = frozenset(parameters)
        self.objects = tuple(objects)
        self.object_set = frozenset(objects)
        self.unions = tuple(unions)
        # Per kind of pass (see _Pass.slot), the pass in which each read gave what it gave
        # here last.
        self.valid_in: dict[tuple[bool, bool], int] = {}
        # Whether it is worth telling that the reads give the same without reading again
        # (see EvalMemo.unchanged): reading a few again takes less.
        self.checked_by_change = len(values) + len(gates) + len(reads) > _READ_AGAIN
        # What the reads depend on, to tell so: the values, the gate decisions, the parameters
        # reached in the discovery and the texts read; whether it reads group object
        # settings, and text arguments (values by unique number, told only by reading again).
        if self.checked_by_change:
            self.value_keys = frozenset(key for key, _result in values)
            self.gate_keys = frozenset(key for key, _result in gates)
            self.discovered_keys = frozenset(key for kind, key, _ in reads if kind == "d")
            self.text_keys = frozenset(key for kind, key, _ in reads if kind == "t")
            self.object_reads = any(kind == "o" for kind, _key, _ in reads)
            self.text_args = any(kind == "x" for kind, _key, _ in reads)
        else:
            self.value_keys = self.gate_keys = _NONE
            self.discovered_keys = self.text_keys = _NONE
            self.object_reads = self.text_args = False


class _Pass:
    """A pass over the application's scope evaluated with an :class:`EvalMemo`: what its reads
    can depend on when it starts (the values, the gates, the parameters reached in the union
    discovery, the texts and the group object settings), and once it ends what it wrote.
    Compared with the pass before of the same kind (``slot``: with union suppression, and
    whether Chooses are gated), it tells what may read otherwise than there (see
    :meth:`EvalMemo.unchanged`)."""

    __slots__ = (
        "changed",
        "clean",
        "defaults",
        "discovered",
        "discovered_changed",
        "gate",
        "gates_changed",
        "known",
        "number",
        "objects",
        "objects_same",
        "previous",
        "slot",
        "text",
        "texts_changed",
        "texts_written",
        "values",
        "written",
    )

    def __init__(self, number: int, scope: ParameterState, suppress: bool) -> None:
        known, values, defaults, gate, discovered, text, objects = scope.pass_start()
        self.number = number
        self.known = known
        self.values = values
        self.defaults = defaults
        self.gate = gate
        self.discovered = discovered
        self.text = text
        self.objects = objects
        self.slot = (suppress, gate is None)
        # Whether no other pass ran while this one did (a UI evaluated inside the evaluation
        # of another): only such a pass is compared with.
        self.clean = True
        # Of the pass before of the same kind: its number (None: none to compare with),
        # then the values written in either or that differ at the start, the gates, the
        # parameters reached in the discovery and the texts that differ, and whether the
        # group object settings are the same.
        self.previous: int | None = None
        self.changed: frozenset[object] = frozenset()
        self.gates_changed: frozenset[object] = frozenset()
        self.discovered_changed: frozenset[object] = frozenset()
        self.texts_changed: frozenset[object] = frozenset()
        self.objects_same = False
        self.written: frozenset[str] = frozenset()
        self.texts_written: frozenset[str] = frozenset()

    def compare(self, before: _Pass) -> None:
        """Take what may read otherwise than in ``before``, a clean pass of the same kind."""
        if self.defaults is not before.defaults and self.defaults != before.defaults:
            return
        self.changed = frozenset(
            {key for key, _ in self.known.items() ^ before.known.items()}
            | {key for key, _ in self.values.items() ^ before.values.items()}
            | before.written
        )
        if self.gate is not None and before.gate is not None:
            self.gates_changed = frozenset(self.gate ^ before.gate)
        self.discovered_changed = frozenset(self.discovered ^ before.discovered)
        self.texts_changed = frozenset(
            {key for key, _ in self.text.items() ^ before.text.items()}
            | before.texts_written
        )
        self.objects_same = self.objects == before.objects
        self.previous = before.number


class EvalMemo:
    """What the nodes that change nothing but the active marks (a Choose, block or tab holding
    parameters, group objects, separators, blocks and Chooses only, see
    :attr:`~xknxeditor.prod.parser_v2.nodes.base.DynamicNode.pure`) gave in earlier
    evaluations, with all they read and did on the way (see :data:`Event`): an evaluation in
    which each read gives the same gets the same, and does the same. Kept by whoever
    evaluates many alike states (a search of a device's settings), for the last ``ENTRIES``
    evaluations of each node; only used in the thread that made it.

    That each read gives the same is told without reading again where it gave the same in the
    pass before of the same kind and nothing it reads differs from then (see
    :meth:`unchanged`): a search evaluates thousands of states that differ in a few values."""

    __slots__ = ("_count", "_entries", "_last", "_open", "thread")

    ENTRIES = 2

    def __init__(self) -> None:
        # By node and module instance (empty for the application's scope): a module
        # definition's nodes are evaluated once for each of its instances.
        self._entries: dict[tuple[int, str], tuple[object, list[Recording]]] = {}
        # The thread it is used in (a UI evaluated with it may be evaluated in another).
        self.thread = threading.get_ident()
        self._count = 0
        # The passes running now, and the last clean pass that ended of each kind.
        self._open: list[_Pass] = []
        self._last: dict[tuple[bool, bool], _Pass] = {}

    def begin_pass(self, scope: ParameterState, suppress: bool) -> _Pass:
        """A pass over the application's scope ``scope`` starts (``suppress``: with union
        suppression); to be ended with :meth:`end_pass`."""
        self._count += 1
        current = _Pass(self._count, scope, suppress)
        if self._open:
            for running in self._open:
                running.clean = False
            current.clean = False
        else:
            before = self._last.get(current.slot)
            if before is not None:
                current.compare(before)
        self._open.append(current)
        return current

    def end_pass(self, current: _Pass, scope: ParameterState) -> None:
        """The pass ``current`` over ``scope`` ended: what it wrote is kept with it, and a clean
        one is the pass the next of its kind is compared with."""
        if current in self._open:
            self._open.remove(current)
        current.written, current.texts_written = scope.pass_writes()
        if current.clean:
            self._last[current.slot] = current

    @staticmethod
    def unchanged(recording: Recording, ctx: EvalContext) -> bool:
        """Whether each read of ``recording`` gives what it gave, told without reading again:
        it gave that in the pass before of this kind, and nothing it reads differs from that
        pass at the start of this one, was written in that pass or so far in this one."""
        current = ctx._pass  # pyright: ignore[reportPrivateUsage]
        if (
            current is None
            or current.previous is None
            or recording.valid_in.get(current.slot) != current.previous
            or recording.text_args
            or (recording.object_reads and not current.objects_same)
        ):
            return False
        keys = recording.value_keys
        if not keys.isdisjoint(current.changed):
            return False
        scope = ctx._scope  # pyright: ignore[reportPrivateUsage]
        written, texts_written = scope.pass_written()
        if written and not keys.isdisjoint(written):
            return False
        if recording.gate_keys and not recording.gate_keys.isdisjoint(
            current.gates_changed
        ):
            return False
        if recording.discovered_keys and not recording.discovered_keys.isdisjoint(
            current.discovered_changed
        ):
            return False
        texts = recording.text_keys
        return not texts or (
            texts.isdisjoint(current.texts_changed)
            and (not texts_written or texts.isdisjoint(texts_written))
        )

    def evaluate(
        self,
        node: object,
        ctx: EvalContext,
        evaluate: Callable[[EvalContext], list[UiNode]],
    ) -> list[UiNode]:
        """What ``evaluate`` gives ``node`` in ``ctx``: what it gave before where each read
        gives what it gave then (its effects done again), else evaluated, and kept."""
        key = (id(node), ctx._instance)  # pyright: ignore[reportPrivateUsage]
        kept = self._entries.get(key)
        if kept is not None and kept[0] is not node:
            kept = None
        suppress = ctx.union_suppress
        current = ctx._pass  # pyright: ignore[reportPrivateUsage]
        if kept is not None:
            recordings = kept[1]
            if current is not None:
                # One told to read the same without reading again first.
                for recording in recordings:
                    if (
                        recording.suppress is suppress
                        and recording.checked_by_change
                        and self.unchanged(recording, ctx)
                        and ctx.replay(recording, checked=True)
                    ):
                        recording.valid_in[current.slot] = current.number
                        return list(recording.output)
            for recording in recordings:
                if recording.suppress is suppress and ctx.replay(recording):
                    if current is not None:
                        recording.valid_in[current.slot] = current.number
                    return list(recording.output)
        # What the evaluation reads and does is recorded on the tape: of the outermost
        # evaluation a new one, those inside it record on the same.
        outermost = ctx._tape is None  # pyright: ignore[reportPrivateUsage]
        if outermost:
            ctx._tape = []  # pyright: ignore[reportPrivateUsage]
        tape = ctx._tape  # pyright: ignore[reportPrivateUsage]
        assert tape is not None
        start = len(tape)
        try:
            output = evaluate(ctx)
            entry = Recording(suppress, tuple(tape[start:]), tuple(output))
        finally:
            if outermost:
                ctx._tape = None  # pyright: ignore[reportPrivateUsage]
        if current is not None:
            entry.valid_in[current.slot] = current.number
        if kept is None:
            self._entries[key] = (node, [entry])
        else:
            kept[1].insert(0, entry)
            del kept[1][self.ENTRIES :]
        return output


class EvalContext:
    """Scope handle wrapping the active state node and the repeat index for the next module_child.

    Reads fall through the state's parent chain (submodule → module → global); writes hit only the active state.
    """

    __slots__ = (
        "_capture",
        "_explore",
        "_gate_checks",
        "_handed",
        "_idx",
        "_instance",
        "_memo",
        "_object_tree",
        "_pass",
        "_repeat_idx",
        "_repeat_path",
        "_scope",
        "_tape",
        "_union_chooses",
        "_union_suppress",
    )

    def __init__(
        self,
        scope: ParameterState,
        repeat_idx: int = 1,
        idx: ApplicationIndexer | None = None,
        capture: EvalCapture | None = None,
        union_suppress: bool = True,
        explore: Explorer | None = None,
        object_tree: ObjectTreeCapture | None = None,
        repeat_path: tuple[int, ...] = (),
        union_chooses: list[tuple[ParameterState, str, Collection[str]]] | None = None,
        handed: set[int] | None = None,
        gate_checks: GateChecks | None = None,
        memo: EvalMemo | None = None,
    ) -> None:
        self._scope = scope
        # The iteration of the Repeats of this scope it is in, counted through all of them
        # (the third iteration of a Repeat of 2 in the second of another: 5); 1 outside one.
        self._repeat_idx = repeat_idx
        # The iteration of each Repeat of this scope it is in, outermost first (empty outside
        # one).
        self._repeat_path = repeat_path
        self._idx = idx
        self._capture = capture
        # False during the union-discovery pass (render both overlapping members so the reached one
        # is recorded); True during the render pass (suppress the inactive overlay).
        self._union_suppress = union_suppress
        # Set for an explore pass (see Explorer): every branch is evaluated and recorded.
        self._explore = explore
        # Set to record the device's group object tree (see ObjectTreeCapture).
        self._object_tree = object_tree
        # Set to record the Chooses on a union member the pass reaches (see note_union_choose).
        self._union_chooses = union_chooses
        # Set when the nodes may hand out what they made in an earlier pass again: the ids of
        # what this pass has handed out (none twice in one tree, see :attr:`handed`).
        self._handed = handed
        # Set to record the gate decisions of the pass (see :meth:`record_gate_checks`).
        self._gate_checks = gate_checks
        # Set to take what nodes gave before with the same reads (see EvalMemo): what a
        # node's evaluation reads and does is recorded on the tape then, while one is.
        self._memo = memo
        # The module instance it evaluates (empty for the application's scope), as the memo
        # keys what it keeps by.
        mid = getattr(scope, "module_instance_id", None)
        self._instance = mid if isinstance(mid, str) else ""
        self._tape: list[Event] | None = None
        # The pass this context evaluates the application's scope in for the memo (see
        # EvalMemo.begin_pass), ended with end_pass.
        self._pass: _Pass | None = None
        if memo is not None and scope.is_root() and self.memo is not None:
            self._pass = memo.begin_pass(scope, union_suppress)

    @property
    def capture(self) -> EvalCapture | None:
        return self._capture

    @property
    def explore(self) -> Explorer | None:
        return self._explore

    @property
    def object_tree(self) -> ObjectTreeCapture | None:
        return self._object_tree

    @property
    def handed(self) -> set[int] | None:
        """The ids of the UiNodes this pass handed out that a node made in an earlier pass
        (an evaluation of the same values again); None when every node is made anew. A node
        hands out none twice in one pass, as a tree may tell its nodes apart by identity."""
        return self._handed

    @property
    def union_suppress(self) -> bool:
        return self._union_suppress

    @property
    def memo(self) -> EvalMemo | None:
        """What nodes gave before (see :class:`EvalMemo`), where a node may take it: in a pass
        that records nothing but the active marks and the gate decisions and hands out nodes
        made before, outside a Repeat."""
        memo = self._memo
        if (
            memo is None
            or self._explore is not None
            or self._capture is not None
            or self._object_tree is not None
            or self._handed is None
            or self._repeat_path
            or self._repeat_idx != 1
            or memo.thread != threading.get_ident()
        ):
            return None
        return memo

    def end_pass(self) -> None:
        """The pass this context evaluated ended (see :meth:`EvalMemo.end_pass`)."""
        if self._pass is not None and self._memo is not None:
            self._memo.end_pass(self._pass, self._scope)
        self._pass = None

    def replay(self, recording: Recording, *, checked: bool = False) -> bool:
        """Do again what ``recording`` records when each read gives what it gave then (else
        nothing; ``checked``: known to give the same): whether it was done."""
        handed = self._handed
        assert handed is not None
        if not checked and not self.reads_same(recording):
            return False
        return self._redo(recording, self._scope, handed)

    def reads_same(self, recording: Recording) -> bool:
        """Whether each read of ``recording`` gives what it gave then, read again."""
        scope = self._scope
        get = scope.get
        for key, value in recording.values:
            if get(key) != value:  # type: ignore[arg-type]
                return False
        gate = scope.is_gate_active
        for key, active in recording.gates:
            if gate(key) is not active:  # type: ignore[arg-type]
                return False
        args: _TextArgs | None = None
        for kind, key, result in recording.reads:
            if kind == "d":
                same = scope.is_discovered_active(key) is result  # type: ignore[arg-type]
            elif kind == "x":
                if args is None:
                    args = _TextArgs(self)
                same = args.lookup(key) == result  # type: ignore[arg-type]
            elif kind == "t":
                same = scope.get_text(key) == result  # type: ignore[arg-type]
            elif kind == "q":
                same = scope.qualify(key) == result  # type: ignore[arg-type]
            elif kind == "l":
                same = scope.qualify_local(key) == result  # type: ignore[arg-type]
            elif kind == "o":
                same = scope.get_com_obj_instance_ref(key) == result  # type: ignore[arg-type]
            else:
                arg = scope.get_arg(key)  # type: ignore[arg-type]
                value = arg.value if arg is not None and arg.value is not None else 0
                same = value == result
            if not same:
                return False
        return True

    def _redo(
        self, recording: Recording, scope: ParameterState, handed: set[int]
    ) -> bool:
        """Do again what ``recording`` records, unless a node it hands out is handed out in
        this pass already: whether it was done."""
        if recording.handed_ids and not handed.isdisjoint(recording.handed_ids):
            return False
        tape = self._tape
        if tape is not None:
            # Inside the evaluation of a node being recorded: part of what it does.
            tape.extend(recording.events)
        if recording.parameters:
            scope.mark_active_params(recording.parameters, recording.parameter_set)  # type: ignore[arg-type]
        if recording.objects:
            scope.mark_active_com_objects(recording.object_set)  # type: ignore[arg-type]
        for key, text in recording.texts:
            scope.set_text(key, text)  # type: ignore[arg-type]
        checks = self._gate_checks
        if checks is not None:
            checks.extend((scope, key, active) for key, active in recording.gates)  # type: ignore[misc]
        chooses = self._union_chooses
        if chooses is not None:
            chooses.extend((scope, key, siblings) for key, siblings in recording.unions)  # type: ignore[misc]
        handed.update(recording.handed_ids)
        return True

    def hand(self, node: UiNode) -> None:
        """Hand out ``node``, made in an earlier pass (see :attr:`handed`)."""
        handed = self._handed
        assert handed is not None
        handed.add(id(node))
        if self._tape is not None:
            self._tape.append(("h", node, None))

    def is_discovered_active(self, ref_id: str) -> bool:
        active = self._scope.is_discovered_active(ref_id)
        if self._tape is not None:
            self._tape.append(("d", ref_id, active))
        return active

    def note_union_choose(self, param_ref_id: str, siblings: Collection[str]) -> None:
        """A Choose on the union member ``param_ref_id`` (competing with ``siblings``) is reached
        in this scope; recorded when the pass records them."""
        if self._tape is not None:
            self._tape.append(("u", param_ref_id, siblings))
        if self._union_chooses is not None:
            self._union_chooses.append((self._scope, param_ref_id, siblings))

    def record_gate_checks(self, checks: GateChecks) -> None:
        """Record in ``checks`` each gate decision this pass makes (see :data:`GateChecks`), in
        this context and those made from it."""
        self._gate_checks = checks

    def is_gate_active(self, ref_id: str) -> bool:
        scope = self._scope
        active = scope.is_gate_active(ref_id)
        checks = self._gate_checks
        if checks is not None:
            checks.append((scope, ref_id, active))
        if self._tape is not None:
            self._tape.append(("a", ref_id, active))
        return active

    def get(self, ref_id: str) -> str | None:
        value = self._scope.get(ref_id)
        if self._tape is not None:
            self._tape.append(("g", ref_id, value))
        return value

    def qualify(self, ref_id: str) -> str:
        qualified = self._scope.qualify(ref_id)
        if self._tape is not None:
            self._tape.append(("q", ref_id, qualified))
        return qualified

    @property
    def module_instance_id(self) -> str | None:
        """Id of the innermost module instance being evaluated, if any."""
        return self._instance or None

    def qualify_local(self, ref_id: str) -> str:
        qualified = self._scope.qualify_local(ref_id)
        if self._tape is not None:
            self._tape.append(("l", ref_id, qualified))
        return qualified

    def set(self, ref_id: str, value: str) -> None:
        self._scope.set(ref_id, value)

    def run_assign(
        self, key: int, target: str, value: str | None, *, from_source: bool
    ) -> None:
        """The Assign ``key`` that applies writes ``value`` into ``target`` when it is its time
        (see :meth:`ParameterState.run_assign`). An explore pass writes it every time."""
        self._scope.run_assign(
            key,
            target,
            value,
            from_source=from_source,
            always=self._explore is not None,
        )

    def note_assign(self, key: int) -> None:
        """The pass reached an Assign (see :meth:`ParameterState.note_assign`)."""
        self._scope.note_assign(key)

    def set_text(self, ref_id: str, text: str) -> None:
        self._scope.set_text(ref_id, text)
        if self._tape is not None:
            self._tape.append(("s", ref_id, text))

    def get_text(self, ref_id: str) -> str | None:
        text = self._scope.get_text(ref_id)
        if self._tape is not None:
            self._tape.append(("t", ref_id, text))
        return text

    def mark_active_param(self, ref_id: str) -> None:
        self._scope.mark_active_param(ref_id)
        if self._tape is not None:
            self._tape.append(("p", ref_id, None))

    def mark_active_com_object(self, ref_id: str) -> None:
        self._scope.mark_active_com_object(ref_id)
        if self._tape is not None:
            self._tape.append(("c", ref_id, None))

    def mark_assigned_from(self, target: str, source: str) -> None:
        self._scope.mark_assigned_from(target, source)

    def mark_assigned_constant(self, target: str) -> None:
        self._scope.mark_assigned_constant(target)

    def get_com_obj_instance_ref(self, ref_id: str) -> ComObjectInstanceRef | None:
        settings = self._scope.get_com_obj_instance_ref(ref_id)
        if self._tape is not None:
            self._tape.append(("o", ref_id, settings))
        return settings

    def allocate(
        self, def_ref_id: str, alloc_id: str, arg_ref_id: str, base: int = 0
    ) -> int:
        """Take the next address from the pool, bump the scope position, and return it."""
        if self._idx is None:
            raise RuntimeError("allocate() requires an ApplicationIndexer")
        # A module argument's Allocator may be defined on the module definition, on the
        # definition a submodule is in (``..._MD-4_L-1`` for ``..._MD-4_SM-1``) OR on the
        # application (all are valid); look in the module def first, then in the enclosing
        # ones, then fall back to the application-level allocators.
        alloc = None
        owner = def_ref_id
        while alloc is None:
            alloc = (self._idx.allocators.get(owner) or {}).get(alloc_id)
            at = owner.rfind("_SM-")
            if at == -1:
                break
            owner = owner[:at]
        alloc = alloc or self._idx.app_allocators.get(alloc_id)
        arg_allocs = self._idx.arg_alloc.get(def_ref_id)
        if alloc is None or arg_allocs is None or arg_ref_id not in arg_allocs:
            # Truly unresolved (e.g. partial module data) — don't crash the device load; fall back
            # to the base address so the module's content still renders.
            return base
        allocates, alignment = arg_allocs[arg_ref_id]
        position = self._scope.alloc_position(alloc_id, alloc.start)
        # An explore pass evaluates every branch, also those no configuration has together
        # (each placing a module): its allocations may run past the allocator's end.
        address, next_position = alloc.resolve(
            position, allocates, alignment, base, bounded=self._explore is None
        )
        self._scope.set_alloc_position(alloc_id, next_position)
        return address

    def alloc_positions(self) -> dict[str, int]:
        return self._scope.alloc_positions()

    def set_alloc_positions(self, positions: dict[str, int]) -> None:
        self._scope.set_alloc_positions(positions)

    @property
    def repeat_idx(self) -> int:
        return self._repeat_idx

    def repeat_ctx(self, repeat_idx: int, count: int) -> EvalContext:
        """The context of the iteration ``repeat_idx`` of a Repeat of ``count`` iterations
        reached in this one."""
        number = (
            (self._repeat_idx - 1) * count + repeat_idx
            if self._repeat_path
            else repeat_idx
        )
        return EvalContext(
            self._scope,
            number,
            self._idx,
            self._capture,
            self._union_suppress,
            self._explore,
            self._object_tree,
            repeat_path=(*self._repeat_path, repeat_idx),
            union_chooses=self._union_chooses,
            handed=self._handed,
            gate_checks=self._gate_checks,
        )

    def get_arg_value(self, ref_id: str) -> int:
        arg = self._scope.get_arg(ref_id)
        value = arg.value if arg is not None and arg.value is not None else 0
        if self._tape is not None:
            self._tape.append(("v", ref_id, value))
        return value

    def get_arg_defaults(self) -> dict[str, str]:
        return self._scope.get_arg_defaults()

    def text_args(self) -> Mapping[str, str]:
        """What ``{{name}}`` placeholders in texts stand for: the module arguments, and for a
        number the value of the parameter-ref with that unique number (``…_R-<number>``) in the
        scope's module definition, else in the application ("Channel A: {{5889}}")."""
        return _TextArgs(self)

    def ref_by_number(self, number: int) -> str | None:
        """The parameter-ref with unique number ``number`` of this scope (see :meth:`text_args`)."""
        if self._idx is None:
            return None
        owner = getattr(self._scope, "ref_id", None)
        for scope_owner in (owner, None) if owner is not None else (None,):
            ref = self._idx.refs_by_number.get(scope_owner, {}).get(number)
            if ref is not None:
                return ref
        return None

    def value_or_default(self, ref_id: str, *, record: bool = True) -> str | None:
        """The value of ``ref_id`` in this scope, else its default (without ``record`` read
        without recording it, see EvalMemo)."""
        value = self.get(ref_id) if record else self._scope.get(ref_id)
        if value is None and self._idx is not None:
            value = self._idx.default_value(ref_id)
        return value

    def seed_param_ref_defaults(self, param_ref_defaults: dict[str, str]) -> None:
        self._scope.set_param_ref_defaults(param_ref_defaults)

    def module_ctx(
        self,
        module_id: str,
        default_arguments: dict[str, ModuleArg] | None = None,
        param_ref_defaults: dict[str, str] | None = None,
        arg_defaults: dict[str, str] | None = None,
        ref_id: str | None = None,
        param_ref_bases: dict[str, str] | None = None,
    ) -> EvalContext:
        ms = self._scope.module_child(
            module_id,
            self._repeat_idx,
            default_arguments,
            param_ref_defaults=param_ref_defaults,
            arg_defaults=arg_defaults,
            ref_id=ref_id,
            param_ref_bases=param_ref_bases,
            repeat_path=self._repeat_path,
        )
        return EvalContext(
            ms,
            idx=self._idx,
            capture=self._capture,
            union_suppress=self._union_suppress,
            explore=self._explore,
            object_tree=self._object_tree,
            union_chooses=self._union_chooses,
            handed=self._handed,
            gate_checks=self._gate_checks,
            memo=self._memo,
        )
